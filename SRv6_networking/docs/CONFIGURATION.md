# Editing the running lab

Select a router in the topology, open **Edit**, choose an installed entry or create a new one, then **Preview change → Apply to router**. The preview shows the exact command and affected configuration. Successful route changes are checked against a new kernel route read. Every apply refreshes the normal collectors.

## Supported changes

- SRv6 steering routes: destination prefix, table, metric, interface, segment visit order, and encap / encap.red / inline mode.
- Local SIDs: End, End.X, End.DX6, End.DT6; next hop or lookup table as appropriate; optional End NEXT-CSID with block/node lengths.
- Normal IPv6 static routes: destination, interface, optional next hop, table and metric.
- FRR configuration snippets: interface IPv6 addresses, IPv6 IS-IS membership, IS-IS metric/passive/circuit type; router isis NET, IS type and metric style. `no` forms are accepted for supported commands. Use `exit` between contexts.

For example, after choosing a real router interface:

```text
interface eth1
 isis metric 20
exit
```

The route editor identifies entries by **prefix + table + metric**. Changing that identity creates another entry; delete the old one separately if desired. Connected routes and routes owned by IS-IS/FRR are protected from direct kernel edits. Configure those through their owning FRR settings instead. Source-selection policy rules are preserved; a new route in a custom table only affects traffic when a rule already selects that table.

## Runtime versus persistent configuration

Changes affect the running container. They do **not** automatically edit archived deployment scripts or `frr.conf`, and a lab redeployment may replace them. **Download command** exports a shell script to run inside that router; incorporate reviewed commands into your existing deployment workflow when you want persistence. The UI does not offer an unrestricted shell or arbitrary configuration-file editor.

The deployment files shown under **Intent**, the live state under **Routes / SRv6 / Config**, and previous captures remain separate. Existing thesis policy/experiment commands still work.

## New policies and ordinary IPv6

Every explicit ping now collects fresh state before starting captures. Changes made through the app or externally are therefore included in the next run. **Refresh topology** updates the inspector without sending traffic. Old replays continue to use their original state rather than pretending a new policy applied to an old packet.

There is no SRv6-only animation mode. The same packet identity and paired-link evidence reconstruct normal IPv6 forwarding. If no SRH or encapsulation was captured, the packet inspector reports that absence. Removing a steering route can expose the existing underlay IPv6 route, but only if the lab actually has a working underlay route and any applicable policy rules permit it. No route is invented by the UI.

## Operational behavior

Only one configuration/ping preparation operation runs at a time. Preview reads current state; apply checks it again and refuses a changed preview. Container identity, lab membership, running state, interface names, addresses, route parameters and supported command contexts are validated. Commands use argument arrays, never a shell.

Before/after state and command results are saved under `backend/data/config_changes/`. FRR multi-line updates are **not atomic**: on an error, inspect the result and collected running configuration because earlier lines may have applied. There is no automatic rollback claim. A failed verification is shown as a failure even if a command returned zero.

Syntax references: [iproute2 routes and SRv6](https://man7.org/linux/man-pages/man8/ip-route.8.html), [FRR VTY shell](https://docs.frrouting.org/en/latest/vtysh.html).
