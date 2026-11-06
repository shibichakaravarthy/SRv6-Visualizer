# Understanding the collectors

The collectors answer two different questions:

1. **What is installed now?** Read the running containers, interfaces, routing tables and FRR state.
2. **What happened to a particular packet?** Read a recorded experiment containing captures, decoded frames and the operational state saved during that experiment.

A route in a configuration script does not prove that it was installed. An installed route does not prove that a packet used it.

## Run the complete collection

From `/home/shibi/Networking/SRv6_networking`:

```bash
.venv/bin/python collect.py
```

This publishes seven JSON snapshots with one collection ID. `configurations.json`
includes exact topology, `configs/` and `deployment/` file contents and SHA-256
hashes under `configured_files`, even when Docker is unavailable. Container
configuration remains separate from declared files.

Commands use Python `subprocess.run` with argument lists, timeouts and captured
stdout/stderr, dispatched through worker threads. Full collection selects only
the lab named in the topology.

`observations.json` now includes live acquisition: a ten-second IPv6 tcpdump
window on each running node (maximum 200 packets per node), three echo probes
per source node to another discovered node's non-management unicast address,
and pre/post forwarding state. Captures include timestamps, interface/direction
where provided by tcpdump, verbose decoding and full packet hex. Nodes need
`timeout`, `tcpdump`, and `ping`; unavailable tools and unsuccessful probes remain
explicit results. Probe coverage is a sample, not an exhaustive SRv6 path test.
Capture startup uses a one-second allowance, not a verified readiness barrier.

The five level names are retained, but live packet correlation and independent
decoding are **not implemented**. Raw acquisition therefore remains `partial`
and does not claim five-level execution coverage. A supplied archive still
undergoes the existing correlation and validation, separately from live data.

To include the existing archived experiment:

```bash
.venv/bin/python collect.py \
  --evidence-run ../Prototype/SRv6_networking/srv6-studio-source/srv6-studio/lab/results/P01/run_002
```

The archive keeps its **original run ID and dates** in `observations.json`. Its packet evidence is never combined with today's routes to claim historical execution. The snapshot's collection ID identifies this import operation.

Use `--output /tmp/my-network-check` to try the collector without changing the latest project snapshots.

## How this follows the report

Source: *Senthil_Master_Thesis_Report.pdf*, Chapter 4, §4.4, Table 4.3 (printed page 20), and §4.5 (printed page 22).

| Level | Meaning | Evidence used |
| --- | --- | --- |
| OCP | Installed control-plane context | The event's matched route, present in the same run's archived node state and covering the arriving destination |
| ON | Processing-node boundary | Matching ingress and egress frames at the same node |
| ODP | Forwarding context | Archived matched forwarding entry and agreement with the observed egress interface |
| OP | Packet-state change | Before/after inner and outer IPv6 headers and SRH fields, with recorded agreement of two independent decoders |
| ONW | Subsequent network effect | The same packet observed on ingress at the next link's other node, at a non-earlier timestamp |

Each event has a `levels` dictionary containing all five levels. Each level says `available`, `missing`, or `not_applicable` and retains its evidence. `coverage_complete` means all required evidence levels are present. It is **not an independent RFC conformance or behaviour-execution verdict**. The original experiment's interpretation remains separately labelled `upstream_evidence_status`.

Important rules implemented in Python:

- Match run ID, PacketID, direction, ICMPv6 type, identifier, sequence, payload hash, and inner IPv6 endpoints.
- Verify manifest hashes, capture frame numbers and individual frame hashes.
- Reject ambiguous correlation, quoted ICMPv6 errors, mismatched decoder output and decoder disagreement.
- Preserve packet headers separately. An outer-header Hop Limit before encapsulation cannot be subtracted from a different outer header after encapsulation.
- An absent SRH is not `Segments Left = 0`. An SRH field present on only one side is not comparable.
- NEXT-CSID can change the destination address without decreasing Segments Left. The code does not use Segments Left as a universal instruction counter.
- A terminal event only receives `ONW: not_applicable` if the event explicitly supplies `terminal_leaves_observed_domain: true` and a terminal End.DX/End.DT behaviour. A behaviour name alone is insufficient.
- Missing egress or downstream evidence means execution is **not established**, rather than proof of failure.

## What each Python file does

| File | Job |
| --- | --- |
| `docker_reader.py` | The command boundary. Discovers lab containers and runs fixed read-only commands with timeouts. |
| `configuration_collector.py` | Reads the running FRR configuration, configuration files and IPv6/SRv6 switches. |
| `ipv6_collector.py` | Reads addresses, routes from all tables, neighbours and policy-routing rules. |
| `isis_collector.py` | Reads FRR IS-IS neighbours and routes; retains text if JSON is unsupported. |
| `srv6_collector.py` | Extracts installed headend policies and LocalSIDs from route observations. Retains NEXT-CSID flavor fields and raw routes. |
| `topology_collector.py` | Compares configured endpoints with running interface evidence. Administrative UP alone does not mean the link has carrier. |
| `packet_collector.py` | Reads and validates archived packet evidence; compares corresponding packet headers. It is a helper module. |
| `observation_collector.py` | Joins packet observations and archived forwarding context into the five-level report. |
| `info_collector_improved.py` | Optional static reference reader. Reads declared topology, FRR files and shell-script route declarations. Its output is explicitly labelled as configuration, not execution evidence. |
| `_cli.py` | Shared command-line runner and snapshot publication for individual collectors. |
| `__init__.py` | Marks the folder as a Python package; it has no collection work to run. |

`backend/services/discovery_service.py` coordinates the live collectors, reads packet evidence if requested, and publishes the complete generation. At most four containers are collected concurrently. `await` means “wait for this operation to finish without blocking the other container tasks.” A dictionary such as `{'status': 'missing'}` is a labelled result, not a Python exception.

## Run one collector

```bash
.venv/bin/python backend/collectors/topology_collector.py
.venv/bin/python backend/collectors/observation_collector.py \
  --evidence-run ../Prototype/SRv6_networking/srv6-studio-source/srv6-studio/lab/results/P01/run_002
.venv/bin/python backend/collectors/info_collector_improved.py \
  --topology topology/SRv6_lab.yml --outdir /tmp/declared-network
```

The live collectors and observation collector support `--output DIRECTORY` and archive previous snapshots. Running the observation collector without an archive performs the complete live collection, including probes. Keep the static reference output in a separate directory; its files describe declared intent.

## Evidence input and limits

The importer supports the repository's existing `srv6obs` experiment layout:

```text
run.yaml
manifest.sha256
derived/frames.jsonl
derived/observations.jsonl
derived/execution_events.jsonl
raw/pcaps/*.pcap
raw/node_state/pre/<node>/*
raw/node_state/post/<node>/*
```

It reads recorded agreement between the experiment pipeline's independent readers; it does not run a second decoder itself. It supports classic PCAP, primary ICMPv6 echo probes and direct same-node ingress/egress brackets. PCAPNG, other transport identities and topology-inferred brackets need additional implementation. Missing Traffic Class or Flow Label values in an older decoded archive remain missing. Logical equivalence between Classic and NEXT-CSID programs and thesis experiment metrics are outside this collector's scope.

The archive importer remains the route to validated five-level evidence. Live acquisition captures traffic and generates probes, but does not yet produce the independently decoded and correlated archive format. Reading an old archive cannot verify today's network. Scripts containing shell variables or dynamically constructed routes are not fully evaluated by the static reference reader.

## Checks performed during this review

Offline tests cover successful five-level coverage, missing/ambiguous evidence, decoder disagreement, altered captures, unrelated routes, terminal boundaries, encapsulation, NEXT-CSID field comparisons and coordinated publication. Run them with:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The live lab check on 4 November 2026 discovered 21 containers, but found no observed data links and no installed SRv6 policies or LocalSIDs. Missing FRR on hosts and unsupported JSON commands on bridge containers remain explicit partial observations. The collector does not repair or deploy the lab.
