#!/usr/bin/env python3

import argparse
import ipaddress
import json
import platform
import re
import sys
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY_FILE = REPOSITORY_ROOT / "topology" / "SRv6_lab.yml"

# Container-side paths that identify what a bind mount is for.
FRR_CONFIG_MARKER = "/etc/frr/frr.conf"
PROVISIONING_MARKER = "/deployment/"

# The kernel release of the machine this runs on. Containerlab nodes are
# containers, so they execute this kernel rather than one of their own.
KERNEL_RELEASE_FILE = Path("/proc/sys/kernel/osrelease")


# ── Helpers ─────────────────────────────────────────────────────────────────

def classify_node_role(node_spec):
    """Human-readable label only. Nothing downstream branches on this value."""
    if "labels" in node_spec and "role" in node_spec["labels"]:
        return node_spec["labels"]["role"]
    for command in node_spec.get("exec", []) or []:
        if "type bridge" in command:
            return "bridge"
    if find_bind(node_spec, FRR_CONFIG_MARKER):
        return "router"
    return "host"


def read_logical_lines(script_path):
    """Yield lines from a shell script, joining backslash continuations."""
    try:
        lines = script_path.read_text().splitlines()
    except OSError:
        return
    buffer = ""
    for raw_line in lines:
        stripped = raw_line.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        yield buffer + raw_line
        buffer = ""
    if buffer:
        yield buffer


def relative_to_root(path):
    """Repository-relative path string, for readable output."""
    try:
        return str(Path(path).resolve().relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(path)


def find_bind(node_spec, marker):
    """Host-side path of the bind mount whose container target contains marker.

    A Containerlab bind is 'src:dst' or 'src:dst:ro'.
    """
    for bind in node_spec.get("binds", []) or []:
        if not isinstance(bind, str):
            continue
        parts = bind.split(":", 2)
        target = parts[1] if len(parts) > 1 else ""
        if target == marker or (marker.endswith("/") and target.startswith(marker)):
            return parts[0]
    return None


def walk_usid_carrier(segment, block_len, node_len):
    """Yield (uSID, destination address) for each hop a carrier encodes.

    A uSID carrier is one IPv6 address holding a block prefix followed by a
    list of fixed-width uSIDs. The list ends at the first all-zero uSID (the
    End-of-Carrier marker), so trailing zeros are not hops.

    Each uN hop shifts the carrier left by one uSID, so the address yielded
    with hop N is the block prefix followed by the uSIDs still pending - the
    destination that hop actually sees.

    The split is purely positional: it cannot tell a node uSID from the
    function bits of a terminating SID, so a carrier ending in an End.DX6
    SID yields one entry too many. resolve_usid_path() is what resolves
    this - it stops at the first terminating behavior, which consumes those
    bits as part of the matched LocalSID rather than reading them as a hop.
    """
    if (not isinstance(block_len, int) or not isinstance(node_len, int)
            or block_len < 0 or block_len >= 128 or node_len <= 0
            or block_len + node_len > 128 or node_len % 4 != 0):
        raise ValueError("Invalid uSID block_len/node_len")
    try:
        value = int(ipaddress.IPv6Address(segment))
    except ValueError:
        return
    block = (value >> (128 - block_len)) << (128 - block_len)
    tail_mask = (1 << (128 - block_len)) - 1

    # Read the uSIDs out of the carrier and repack them on their own, so the
    # bits past the End-of-Carrier marker cannot ride along in the shift.
    usids = []
    pending = 0
    for position, offset in enumerate(range(block_len, 128 - node_len + 1, node_len)):
        usid = (value >> (128 - offset - node_len)) & ((1 << node_len) - 1)
        if usid == 0:
            break
        usids.append(usid)
        pending |= usid << (128 - block_len - (position + 1) * node_len)

    for usid in usids:
        yield usid, ipaddress.IPv6Address(block | pending)
        pending = (pending << node_len) & tail_mask


# ── SRv6 endpoint behaviors ─────────────────────────────────────────────────
# Every seg6local behavior the collector understands, with the two properties
# downstream code needs. Recognising a new behavior is a single row here;
# nothing else special-cases a behavior name.
#
#   forwarding : shifts/forwards the carrier on. False means it terminates the
#                encapsulation (decap or egress), which ends the uSID walk.
#   bound      : its `dev`/`nh6` is the forwarding decision, so the SID is
#                recorded as bound to that interface. FIB- and table-driven
#                behaviors take a `dev` argument iproute2 does not forward on,
#                so it is not part of the SID.
#
# `End` with the next-csid flavor is the uSID End (reported as uN) and `End.X`
# with next-csid is the uSID End.X (reported as uA); either keyword without the
# flavor is the uncompressed behavior. Keys are the names the collector reports,
# which for every behavior but uN/uA are also the token the kernel route carries.
SEG6LOCAL_BEHAVIORS = {
    "uN":      {"forwarding": True,  "bound": False},
    "uA":      {"forwarding": True,  "bound": True},
    "End":     {"forwarding": True,  "bound": False},
    "End.X":   {"forwarding": True,  "bound": True},
    "End.T":   {"forwarding": True,  "bound": False},
    "End.DX6": {"forwarding": False, "bound": True},
    "End.DX4": {"forwarding": False, "bound": True},
    "End.DT6": {"forwarding": False, "bound": False},
    "End.DT4": {"forwarding": False, "bound": False},
    "End.DX2": {"forwarding": False, "bound": True},
}

# A behavior not in the table: assume it terminates the carrier (so the uSID
# walk stops rather than reading past a real egress) and that its dev matters.
UNKNOWN_BEHAVIOR = {"forwarding": False, "bound": True}


def behavior_properties(behavior):
    """The forwarding/bound properties of a behavior, or the safe default."""
    return SEG6LOCAL_BEHAVIORS.get(behavior, UNKNOWN_BEHAVIOR)


def canonical_behavior(action, has_next_csid):
    """Map a seg6local `action` keyword to the behavior name we report.

    The next-csid flavor turns the two forwarding endpoints into their uSID
    (carrier-shifting) forms: `End` becomes uN and `End.X` becomes uA. Without
    the flavor each keyword is the uncompressed behavior (End, End.X), reported
    under its own name — the token that also appears in the kernel route the
    validator reads back. Every other keyword (End.DX6, ...) is unaffected.
    """
    if has_next_csid:
        if action == "End":
            return "uN"
        if action == "End.X":
            return "uA"
    return action


def is_terminating_behavior(behavior):
    """Whether a behavior consumes the carrier rather than forwarding it on.

    A forwarding behavior (uN, End.X, End.T, ...) shifts the carrier and hands
    the packet on; a terminating one (End.DX6, End.DT6, ...) ends the SRv6
    path. The rule is data in SEG6LOCAL_BEHAVIORS so it lives in one place, and
    both the uSID walk and the policy's `resolved` flag ask it here.
    """
    return bool(behavior) and not behavior_properties(behavior)["forwarding"]


def drop_covered_localsids(localsids):
    """Remove SIDs already covered by a broader entry with the same behavior.

    A script may declare both a locator-wide uN and a longer-prefix uN inside
    it; the narrower one adds no observable behavior and would appear in the
    output as a second, apparently distinct LocalSID.
    """
    kept = []
    for candidate in localsids:
        try:
            network = ipaddress.IPv6Network(candidate["sid"], strict=False)
        except ValueError:
            kept.append(candidate)
            continue
        covered = False
        for other in localsids:
            if (other is candidate or other["behavior"] != candidate["behavior"]
                    or other.get("interface") != candidate.get("interface")
                    or other.get("method") != candidate.get("method")):
                continue
            try:
                wider = ipaddress.IPv6Network(other["sid"], strict=False)
            except ValueError:
                continue
            if wider.prefixlen < network.prefixlen and network.subnet_of(wider):
                covered = True
                break
        if not covered:
            kept.append(candidate)
    return kept


def match_localsid(address, localsids):
    """Longest-prefix match of `address` against every declared LocalSID.

    Longest-prefix, because a node's uN entry covers its whole locator and
    would otherwise shadow the /128 End.DX6 entries inside it.
    """
    best = None
    for node_name, entry in localsids.items():
        for localsid in entry["localsids"]:
            try:
                network = ipaddress.IPv6Network(localsid["sid"], strict=False)
            except ValueError:
                continue
            if address in network and (best is None or network.prefixlen > best[0]):
                best = (network.prefixlen, node_name, localsid)
    return (best[1], best[2]) if best else None


def match_locator(address, routers):
    """Owner of the locator that contains `address`, or None."""
    best = None
    for node_name, router in routers.items():
        locator = (router.get("locator") or {}).get("prefix")
        if not locator:
            continue
        try:
            network = ipaddress.IPv6Network(locator, strict=False)
            if address in network and (best is None or network.prefixlen > best[0]):
                best = (network.prefixlen, node_name)
        except ValueError:
            continue
    return best[1] if best else None


def derive_locator_block(routers):
    """Common locator block (e.g. 'fcbb:3000::/32') from the parsed locators."""
    locators = [r["locator"] for r in routers.values() if r.get("locator")]
    if not locators:
        return None
    first = locators[0]
    try:
        network = ipaddress.IPv6Network(first["prefix"], strict=False)
        return str(network.supernet(new_prefix=first["block_len"]))
    except (ValueError, KeyError):
        return None


# ── Readers ─────────────────────────────────────────────────────────────────

def read_topology_file(topology_file):
    """Load the first YAML document from the topology file."""
    with topology_file.open() as handle:
        data = next(yaml.safe_load_all(handle), None)
    if not isinstance(data, dict):
        raise ValueError(f"{topology_file} does not contain a topology mapping")
    data.setdefault("name", topology_file.stem)
    topology = data.setdefault("topology", {})
    topology["nodes"] = topology.get("nodes") or {}
    topology["links"] = topology.get("links") or []
    if not isinstance(topology["nodes"], dict) or not isinstance(topology["links"], list):
        raise ValueError("Topology nodes must be a mapping and links must be a list")
    return data


def parse_frr_config(config_path):
    """Extract the IS-IS NET, interfaces and SRv6 locator from frr.conf."""
    parsed = {"net": None, "interfaces": {}, "locator": None}
    try:
        lines = config_path.read_text().splitlines()
    except OSError:
        return parsed

    current_interface = None
    inside_locator_block = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line in ("exit", "!"):
            current_interface = None
            inside_locator_block = False
            continue

        # Inside an interface stanza consume only interface sub-commands, so a
        # stray top-level keyword cannot leak out of the block.
        if current_interface is not None:
            match = re.match(r"ipv6 address (\S+)", line)
            if match:
                current_interface["ipv6"] = match.group(1)
            elif line.startswith("ipv6 router isis"):
                current_interface["isis"] = True
            elif line == "isis passive":
                current_interface["isis_passive"] = True
            continue

        match = re.match(r"interface (\S+)", line)
        if match:
            current_interface = {"isis": False, "isis_passive": False}
            parsed["interfaces"][match.group(1)] = current_interface
            inside_locator_block = False
            continue

        match = re.match(r"net (\S+)", line)
        if match:
            parsed["net"] = match.group(1)
            continue

        if inside_locator_block:
            match = re.match(r"prefix (\S+) block-len (\d+) node-len (\d+)", line)
            if match:
                parsed["locator"] = {
                    "prefix": match.group(1),
                    "block_len": int(match.group(2)),
                    "node_len": int(match.group(3)),
                }
                continue
            match = re.match(r"behavior (\S+)", line)
            if match and parsed["locator"]:
                parsed["locator"]["behavior"] = match.group(1)
                inside_locator_block = False
                continue

        # 'locator NAME' opens the block; anchored so it cannot match the
        # 'prefix ...' line inside it.
        if re.match(r"locator (\S+)$", line):
            inside_locator_block = True

    return parsed


# The action keyword and the output interface are the two fixed points of a
# seg6local route; between them iproute2 accepts the behavior's arguments
# (nh6, flavors, lblen, nflen, ...) in any order and any combination, so the
# middle is captured whole rather than enumerated. The behavior itself decides
# what that middle means - see SEG6LOCAL_BEHAVIORS.
SEG6LOCAL_ROUTE_REGEX = re.compile(
    r"ip -6 route (?:add|replace) (\S+)\s+encap seg6local action (\S+)(.*?)\s+dev (\S+)"
)

SEG6_ENCAP_ROUTE_REGEX = re.compile(
    r"ip -6 route (?:add|replace) (\S+)\s+encap seg6 mode (\S+)\s+"
    r"segs (\S+)\s+dev (\S+)"
)

# '# [BASIC]  client -> Site B servers' - the tag names the policy, the rest
# is its declared intent. Phase 3 keys its result directories on this name.
POLICY_LABEL_REGEX = re.compile(r"#\s*\[([^\]]+)\]\s*(.*)")


def parse_provisioning_script(script_path):
    """Extract the declared LocalSIDs: sid, behavior, interface, method.

    A LocalSID is a configured My-Local-SID-Table entry: a concrete SID value
    bound to a behavior and an output interface on one node.
    """
    localsids = []
    seen = set()
    for logical_line in read_logical_lines(script_path):
        line = logical_line.strip()
        if line.startswith("#") or "route del" in line:
            continue
        match = SEG6LOCAL_ROUTE_REGEX.search(line)
        if not match:
            continue
        sid, action, options, device = match.groups()
        behavior = canonical_behavior(action, "next-csid" in options)
        # iproute2 accepts a bare address for a host route; record it as an
        # explicit /128 so every SID in the output carries a prefix length.
        if "/" not in sid:
            sid = f"{sid}/128"
        key = (sid, behavior, device)
        if key in seen:
            continue
        seen.add(key)
        entry = {"sid": sid, "behavior": behavior}
        # The interface is part of the SID only for behaviors that forward on
        # it (End.X, End.DX6, ...); for FIB- and table-driven behaviors (uN,
        # End.DT6) the `dev` argument is not a forwarding decision.
        if behavior_properties(behavior)["bound"]:
            entry["interface"] = device
        # A hedged install (`if ip -6 route add ...; then` or a trailing
        # `|| true`) means the script itself treats the kernel route as
        # optional, because the container's iproute2 may lack the next-csid
        # flavor and an nftables SID-shift is the declared substitute. Absent
        # this marker the install is unconditional.
        if line.startswith("if ") or "|| true" in line:
            entry["method"] = "fallback"
        localsids.append(entry)
    return drop_covered_localsids(localsids)


def parse_policy_script(script_path):
    """Extract the declared steering policies: destination, segments, interface.

    A policy is an `encap seg6` route: a packet whose own destination address
    matches `destination` leaves this node inside an SRv6 encapsulation
    carrying `segments`. `destination` is therefore an address of the original
    packet, not an SRv6 SID - a /128 steers one host, a shorter prefix steers
    a subnet.

    Only the nodes that steer traffic declare these, so in this lab the result
    is empty for every node except the two provider edges - found by what the
    script declares, not by node name.
    """
    policies = []
    pending_name = None
    pending_description = None

    for logical_line in read_logical_lines(script_path):
        line = logical_line.strip()

        if line.startswith("#"):
            match = POLICY_LABEL_REGEX.match(line)
            if match:
                pending_name = match.group(1).strip()
                pending_description = match.group(2).strip() or None
            continue

        if "route del" in line:
            continue
        match = SEG6_ENCAP_ROUTE_REGEX.search(line)
        if not match:
            continue

        destination, mode, segments, device = match.groups()
        policy = {
            "name": pending_name,
            # The rest of the label comment: the intent the experimenter
            # recorded for this policy. Retained rather than consumed, because
            # impact assessment can only judge a measured effect against a
            # declared objective, and this is where one is declared.
            "description": pending_description,
            "destination": destination,
            "mode": mode,
            # iproute2 takes a comma-separated segment list; a uSID policy
            # normally carries the whole path in one segment.
            "segments": segments.split(","),
            "interface": device,
        }
        # The comment states the intended match width. Where it disagrees with
        # the destination actually declared, the route is authoritative and the
        # comment is stale - record it rather than silently emitting both.
        note = describe_destination_mismatch(destination, pending_description)
        if note:
            policy["destination_note"] = note
        policies.append(policy)
        pending_name = None
        pending_description = None

    return policies


def describe_destination_mismatch(destination, description):
    """Note explaining a description claiming a width the destination does not.

    Returns None when the comment says nothing about width or agrees with it.
    """
    if not description:
        return None
    match = re.search(r"/(\d+)", description)
    if not match:
        return None
    claimed = int(match.group(1))
    try:
        declared = ipaddress.IPv6Network(destination, strict=False).prefixlen
    except ValueError:
        return None
    if claimed == declared:
        return None
    return (f"comment describes a /{claimed} but the route declares "
            f"/{declared}; the route is authoritative")


# The Dockerfile is where the FRR version is actually decided: the branch it
# clones, the extra-version string the build stamps onto it, and the patch it
# applies. The image tag only names the result.
FRR_BRANCH_REGEX = re.compile(r"--branch\s+frr-(\S+)")
FRR_EXTRA_VERSION_REGEX = re.compile(r"--with-pkg-extra-version=(\S+)")
FRR_PATCH_REGEX = re.compile(r"git apply\b[^\n]*?(\S+\.patch)")


def read_kernel_release():
    """Kernel release of the host, or None if it cannot be read.

    Containerlab nodes are containers, so every node's SRv6 data plane is this
    one kernel - which behaviors exist at all (End flavors next-csid, End.DX6)
    is a property of it, not of anything the topology declares. Nothing in the
    repository records it, so unlike every other field here it is read from the
    running host, and it describes the machine the collector runs on rather
    than the lab.
    """
    try:
        return KERNEL_RELEASE_FILE.read_text().strip() or None
    except OSError:
        return platform.release() or None


def parse_dockerfile(dockerfile_path):
    """Extract the FRR version and applied patches from an image Dockerfile."""
    parsed = {"base_version": None, "extra_version": None, "patches": []}
    try:
        text = dockerfile_path.read_text()
    except OSError:
        return parsed

    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    body = "\n".join(lines)

    match = FRR_BRANCH_REGEX.search(body)
    if match:
        parsed["base_version"] = match.group(1)
    else:
        match = re.search(r"^FROM\s+quay\.io/frrouting/frr:([^\s]+)", body, re.M)
        if match:
            parsed["base_version"] = match.group(1)
    match = FRR_EXTRA_VERSION_REGEX.search(body)
    if match:
        parsed["extra_version"] = match.group(1)
    parsed["patches"] = sorted({
        Path(name).name for name in FRR_PATCH_REGEX.findall(body)
    })
    return parsed


def parse_host_exec(execution_commands):
    """Extract a host's IPv6 address and the interface it is assigned to."""
    host = {}
    for command in execution_commands:
        match = re.search(r"ip -6 addr add (\S+) dev (\S+)", command)
        if match:
            host["ipv6"] = match.group(1)
            host["interface"] = match.group(2)
    return host


# ── Collectors ──────────────────────────────────────────────────────────────

def collect_topology(topology_data, topology_file):
    """Nodes (kind, role, interfaces) and links, from the YAML alone."""
    nodes = topology_data["topology"]["nodes"]
    links = [
        list(link["endpoints"])
        for link in topology_data["topology"]["links"]
        if isinstance(link, dict) and len(link.get("endpoints", []) or []) == 2
    ]

    node_interfaces = {}
    for endpoint_a, endpoint_b in links:
        for endpoint in (endpoint_a, endpoint_b):
            node_name, _, interface_name = endpoint.partition(":")
            node_interfaces.setdefault(node_name, set()).add(interface_name)

    collected_nodes = {}
    for node_name, node_spec in nodes.items():
        node_spec = node_spec or {}
        collected_nodes[node_name] = {
            "kind": node_spec.get("kind", "linux"),
            "role": classify_node_role(node_spec),
            "interfaces": sorted(node_interfaces.get(node_name, ())),
        }

    return {
        "name": topology_data["name"],
        "source": relative_to_root(topology_file),
        "node_count": len(collected_nodes),
        "link_count": len(links),
        "nodes": collected_nodes,
        "links": links,
    }


def collect_routers(topology_data, config_dir):
    """Per-router IS-IS identity, locator and IS-IS interfaces, from frr.conf."""
    routers = {}
    for node_name, node_spec in topology_data["topology"]["nodes"].items():
        node_spec = node_spec or {}
        config_bind = find_bind(node_spec, FRR_CONFIG_MARKER)
        if not config_bind:
            continue

        config_path = (config_dir / config_bind).resolve()
        frr = parse_frr_config(config_path)
        interfaces = frr["interfaces"]

        router = {
            "isis_net": frr["net"],
            "loopback": interfaces.get("lo", {}).get("ipv6"),
            # ce1/ce2 run IS-IS but declare no locator: they are IS-IS
            # participants outside the SRv6 domain, not misconfigured routers.
            "srv6": frr["locator"] is not None,
            "isis_interfaces": sorted(
                name for name, spec in interfaces.items()
                if name != "lo" and spec.get("isis") and not spec.get("isis_passive")
            ),
        }
        if frr["locator"]:
            router["locator"] = frr["locator"]
        routers[node_name] = router
    return routers


def provisioning_scripts(node_spec, config_dir):
    """Map each declared shell script inside a container to its host bind mount."""
    import shlex

    for command in node_spec.get("exec", []) or []:
        for token in shlex.split(command):
            if not token.startswith("/") or not token.endswith(".sh"):
                continue
            for bind in node_spec.get("binds", []) or []:
                source, separator, target = bind.partition(":")
                if not separator:
                    continue
                target = target.split(":", 1)[0].rstrip("/")
                if token == target or token.startswith(target + "/"):
                    host_path = config_dir / source
                    if token != target:
                        host_path = host_path / token[len(target):].lstrip("/")
                    yield host_path.resolve()
                    break


def collect_localsids(topology_data, config_dir):
    """Declared LocalSIDs per node, from each uSID provisioning script."""
    localsids = {}
    for node_name, node_spec in topology_data["topology"]["nodes"].items():
        node_spec = node_spec or {}
        scripts = list(provisioning_scripts(node_spec, config_dir))
        if scripts:
            localsids[node_name] = {
                "source": [relative_to_root(path) for path in scripts],
                "localsids": [sid for path in scripts for sid in parse_provisioning_script(path)],
            }
    return localsids


def resolve_usid_path(policy, locator, routers, localsids):
    """Walk one policy's segments, naming the node each uSID belongs to.

    Each hop is resolved against the *declared* state only - the LocalSIDs of
    every node, then the locators as a fallback - so this stays a reading of
    the configuration and never consults the running lab. The walk stops at
    the first behavior that terminates the encapsulation (End.DX6), which is
    the policy's egress.
    """
    block_len = locator.get("block_len", 32)
    node_len = locator.get("node_len", 16)

    width = node_len // 4

    hops = []
    for segment in policy["segments"]:
        for usid, address in walk_usid_carrier(segment, block_len, node_len):
            hop = {"usid": f"{usid:0{width}x}", "address": str(address),
                   "node": None, "behavior": None}
            found = match_localsid(address, localsids)
            if found:
                hop["node"], localsid = found
                hop["behavior"] = localsid["behavior"]
            else:
                hop["node"] = match_locator(address, routers)
            hops.append(hop)
            if is_terminating_behavior(hop["behavior"]):
                return hops
    return hops


def collect_policies(topology_data, config_dir, routers, localsids):
    """Declared SRv6 steering policies per node, from the same scripts.

    Where collect_localsids() answers "what does this node do to traffic that
    arrives encapsulated?", this answers "what does this node encapsulate, and
    along which path?". Each policy carries the raw declaration plus the uSID
    path decoded from the carrier.
    """
    policies = {}
    for node_name, node_spec in topology_data["topology"]["nodes"].items():
        node_spec = node_spec or {}
        scripts = list(provisioning_scripts(node_spec, config_dir))
        declared = [policy for path in scripts for policy in parse_policy_script(path)]
        if not declared:
            continue

        locator = (routers.get(node_name, {}).get("locator")) or {}
        for policy in declared:
            hops = resolve_usid_path(policy, locator, routers, localsids) if locator else []
            last = hops[-1] if hops else None
            policy["usid_path"] = hops
            policy["usid_count"] = len(hops)
            # Resolved means the walk reached a terminating behavior, i.e. an
            # egress was identified rather than the path running out of uSIDs.
            policy["resolved"] = bool(last and is_terminating_behavior(last["behavior"]))
            policy["egress"] = last["node"] if policy["resolved"] else None

        policies[node_name] = {
            "source": [relative_to_root(path) for path in scripts],
            "policies": declared,
        }
    return policies


def collect_frr(topology_data):
    """FRR version and build provenance of the routers, from the Dockerfile.

    The routers run locally built images, named by their tags in the topology
    and defined by `docker/<repository>/Dockerfile`. The version is taken from
    that Dockerfile rather than from the tag, because it is the build that
    decides it: an upstream branch plus the extra-version suffix the configure
    step stamps on, which is what `show version` reports.

    Only the image that builds FRR carries these markers; the service images
    (usid-fw, usid-ids) derive FROM it and so run the same FRR, which is why
    every router image is listed but one supplies the version.
    """
    images = sorted({
        node_spec.get("image") for node_spec in
        (spec or {} for spec in topology_data["topology"]["nodes"].values())
        if find_bind(node_spec, FRR_CONFIG_MARKER) and node_spec.get("image")
    })
    frr = {"images": images}

    for image in images:
        repository, _, tag = image.partition(":")
        dockerfile = REPOSITORY_ROOT / "docker-file" / repository / "Dockerfile"
        if not dockerfile.exists():
            dockerfile = REPOSITORY_ROOT / "docker-file" / "Dockerfile"
        build = parse_dockerfile(dockerfile) if dockerfile.exists() else None
        if not build or not build["base_version"]:
            continue
        frr.update({
            "image": image,
            "source": relative_to_root(dockerfile),
            "version": build["base_version"] + (build["extra_version"] or ""),
            "base_version": build["base_version"],
            "extra_version": build["extra_version"],
            "patches": build["patches"],
        })
        # The tag is a label the build was given, not something the build
        # enforces; where the two disagree the Dockerfile is authoritative.
        if tag and tag not in ("latest", build["base_version"]):
            frr["image_note"] = (f"image tagged {tag} but the Dockerfile builds "
                                 f"{build['base_version']}; the Dockerfile is "
                                 f"authoritative")
        break
    return frr


def collect_platform(topology_data):
    """The kernel and FRR versions the declared behaviors actually run on."""
    return {
        "kernel": {
            "release": read_kernel_release(),
            # Recorded so a reader can tell this apart from the fields that
            # come out of the repository: it is the host at collection time.
            "source": "collection host",
        },
        "frr": collect_frr(topology_data),
    }


def collect_hosts(topology_data):
    """Endpoint addressing for every non-router, non-bridge node."""
    hosts = {}
    for node_name, node_spec in topology_data["topology"]["nodes"].items():
        node_spec = node_spec or {}
        if find_bind(node_spec, FRR_CONFIG_MARKER):
            continue
        if classify_node_role(node_spec) == "bridge":
            continue
        host = parse_host_exec(node_spec.get("exec", []) or [])
        if host:
            hosts[node_name] = host
    return hosts


def collect(topology_file=TOPOLOGY_FILE):
    """Read declared intent only; this function never proves live execution."""
    topology_file = Path(topology_file).resolve()
    topology_data = read_topology_file(topology_file)
    config_dir = topology_file.parent

    topology = collect_topology(topology_data, topology_file)
    routers = collect_routers(topology_data, config_dir)
    localsids = collect_localsids(topology_data, config_dir)
    # Policies are resolved against the locators and LocalSIDs of the other
    # nodes, so they must be collected after both.
    policies = collect_policies(topology_data, config_dir, routers, localsids)
    hosts = collect_hosts(topology_data)
    platform_versions = collect_platform(topology_data)

    return {
        "provenance": {"source": "declared configuration",
                       "execution_evidence": False,
                       "note": "Script declarations do not prove installed state or packet execution."},
        "experiment": {
            "routing_protocol": "ISIS",
            "locator_block": derive_locator_block(routers),
        },
        "platform": platform_versions,
        "topology": topology,
        "routers": routers,
        "localsids": localsids,
        "policies": policies,
        "hosts": hosts,
    }


# ── Output ──────────────────────────────────────────────────────────────────

def write_state(state, outdir):
    """Write one JSON file per section, returning the paths written."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for section, content in state.items():
        path = outdir / f"{section}.json"
        path.write_text(json.dumps(content, indent=2) + "\n")
        written.append(path)
    return written


def print_summary(state, written):
    routers = state["routers"]
    localsid_count = sum(len(e["localsids"]) for e in state["localsids"].values())
    policies = [p for e in state["policies"].values() for p in e["policies"]]
    print(f"collected from {state['topology']['source']}")
    print(f"  nodes: {state['topology']['node_count']} "
          f"(routers {len(routers)}, hosts {len(state['hosts'])})")
    print(f"  links: {state['topology']['link_count']}")
    print(f"  locators: {sum(1 for r in routers.values() if r['srv6'])}")
    print(f"  LocalSIDs: {localsid_count}")
    print(f"  policies: {len(policies)} on {len(state['policies'])} nodes "
          f"({sum(1 for p in policies if p['resolved'])} resolved to an egress)")
    print(f"  locator block: {state['experiment']['locator_block']}")
    frr = state["platform"]["frr"]
    print(f"  kernel: {state['platform']['kernel']['release'] or 'unknown'}")
    print(f"  FRR: {frr.get('version') or 'unknown'} "
          f"(image {frr.get('image') or 'none'})")
    for path in written:
        print(f"  wrote {path}")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Phase 1, Environment Characterization: describe the "
                    "prepared uSID environment from its static configuration "
                    f"({relative_to_root(TOPOLOGY_FILE)})")
    parser.add_argument(
        "-o", "--outdir", default=str(Path(__file__).parent / "collected"),
        help="directory for the generated JSON files")
    parser.add_argument("--topology", type=Path, default=TOPOLOGY_FILE)
    arguments = parser.parse_args()

    if not arguments.topology.exists():
        print(f"error: topology file not found: {arguments.topology}", file=sys.stderr)
        return 1

    try:
        state = collect(arguments.topology)
        written = write_state(state, arguments.outdir)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"error: unable to collect environment: {exc}", file=sys.stderr)
        return 1
    print_summary(state, written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
