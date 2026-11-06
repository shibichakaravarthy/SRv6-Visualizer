# Verification — 6 November 2026

## Results

- **39 Python tests pass**, including all existing collector, storage, and thesis-methodology regressions plus new API, SLL2 decoder, nonce validation, and physical-journey tests.
- **3 animation mapping/clock tests pass**: only observed reciprocal links animate; reply interfaces reverse correctly; the shared clock clamps stalls and stops at the end.
- **TypeScript check and Vite production build pass.** React/React Flow dependencies were retained from the prototype.
- **Live discovery:** 21 running Containerlab devices and 32 reciprocal-interface-verified links.
- **Live UI execution:** client1 → webserver, 3 transmitted / 3 received, 0% loss, 0.187 ms average RTT in the final browser-driven run.
- **Six complete packet journeys:** three Echo Requests and three Echo Replies. Each has 10 physical link transfers and 9 intermediate processing events (19 timeline events).
- **SRv6:** captured encapsulation, intermediate destination/Segments Left transitions, and decapsulation. Installed End/End.DX6 context is displayed as inferred execution rather than kernel instrumentation proof.
- **Browser QA:** 21 rendered nodes, six packet choices, timeline mapping, pause freezes the shared progress clock, reset returns to zero, reply selection updates packet details, node drag, zoom, and a 390px mobile viewport without horizontal page overflow. No browser page errors.
- The API served the built frontend successfully on localhost. The owned QA server was stopped after testing; use `./run-web.sh` to start it again.
- Temporary Netshoot capture sidecars were absent after testing (`docker ps --filter name=srv6-capture-`).

Machine-readable details: [live-verification.json](live-verification.json).

Screenshots: [desktop](workspace-desktop.png), [mobile](workspace-mobile.png).

## Actual observed paths

Request:

```text
client1 → sw1 → ce1 → edge1 → R1 → R7 → R8 → edge4 → ce2 → sw2 → webserver
```

Reply:

```text
webserver → sw2 → ce2 → edge4 → R6 → R7 → R4 → edge1 → ce1 → sw1 → client1
```

These are test results, not application constants. The frontend contains no lab node IDs or configured packet paths.

Final evidence directory: `backend/data/ping_runs/179636cc431d46a2b6e430fc5c2af7ea/`. Original PCAPs, frame hashes, decoded observations, snapshot, result, and manifest remain available there. New runs receive new directories.

## Bugs/limitations discovered during verification

1. The first client1 → client2 test stayed within the LAN. It correctly did not show an SRv6 core journey. Use client1 → webserver for a routed test in this lab.
2. Alpine switch containers lack tcpdump. The capture transport now uses an already-installed Netshoot image in a temporary shared network namespace. It does not install packages into or change the switches.
3. Linux bridges mark ingress unicast destined for another device as `PACKET_OTHERHOST` (3). The decoder now accepts this as ingress while retaining the original cooked-header direction.
4. Hosts naturally lack FRR; switches use BusyBox tools without JSON support. Those collector snapshots remain explicitly partial/unsupported with text evidence retained. This does not imply ping failure.
5. Browser selectors exposed ambiguous form names; endpoint selectors now have explicit accessible labels.
6. Sandbox restrictions blocked Docker, Scapy's interface socket initialization, and async thread wakeups in existing tests. The suite and live checks passed when run with the required host access. No application workaround bypasses those restrictions.
7. Capture windows are finite; unsupported packets, ambiguous duplicates, loops, decoder disagreement, or unavailable capture transports can yield partial journeys. The UI reports gaps and never replaces them with shortest paths or SID-list shortcuts.

## Reproduce

From `SRv6_networking`:

```bash
.venv/bin/python -m unittest discover -s tests -v
cd frontend
npm test
npm run build
cd ..
.venv/bin/python tests/verify_workspace_live.py
.venv/bin/python tests/replay_saved_evidence.py
./run-web.sh
```

The live verification script sends traffic. The rederivation script only reads saved evidence and asserts that the latest routed test has six complete journeys. It is intended for the verification fixture, not as a generic assertion that every future failed/partial experiment must succeed.

## Ping 422 follow-up fix

Reproduced the rejection with a valid client1 → webserver request: the snapshot was older than three minutes. `POST /api/pings` now automatically performs passive collection for stale, absent, malformed, or timezone-less timestamps, then validates endpoints against the refreshed view. A preparation lock prevents overlapping executions; refresh failures return an actionable 503 without sending traffic. The UI displays Checking lab… and uses the refreshed run snapshot for replay. The regression suite now passes 41 tests, including expired/missing timestamp recovery and failed-refresh handling.

## Runtime configuration editor and normal IPv6

- Added validated live preview/apply endpoints and the router **Edit** tab for SRv6 steering policies, LocalSIDs, plain IPv6 routes, and FRR IPv6/IS-IS snippets.
- All 53 Python tests pass, along with three playback tests and the production build.
- Live API verification added, edited, and deleted steering policies; added a LocalSID, changed it to NEXT-CSID, and deleted it; added/deleted a normal IPv6 route. All changes were verified in the kernel table. Temporary entries used an unused table and were removed.
- NEXT-CSID JSON compatibility was verified against this lab's list-valued `flavors` with sibling `lblen`/`nflen` fields, as well as the nested geometry form in unit tests.
- client1 → backup-server returned 3/3 replies with 0% loss. All six request/reply journeys were complete (19 events each); captured packets had no SRH or IPv6 encapsulation. The same capture/reconstruction code handled this ordinary IPv6 forwarding.
- Each new ping now refreshes installed state, including policies edited outside the UI. The saved ping snapshot in the live test included the changed policy.
- Browser verification added and deleted a temporary normal route using Preview/Apply. FRR verification reapplied the existing `ipv6 router isis CORE` membership unchanged. This lab's pre-existing vtysh configuration-file warnings remain visible; they are distinguished from new command errors. FRR command acceptance is not presented as an atomic multi-line transaction.
- See [configuration verification data](config-verification.json), [editor screenshot](config-editor.png), and [usage and limitations](CONFIGURATION.md).

The updated application is left running on localhost:8000. Reload the browser to load the new Edit tab; do not start a second server on that port.

## Clos-style workspace layout

The frontend now draws the discovered network from the client boundary through centered router stages to the server boundary. `frontend/src/topologyLayout.ts` derives stages from real connections, groups clients sharing an access device, and gives disconnected components separate space. Device order is stable when Docker discovery order changes. No link or packet path is added by the layout algorithm.

The Clos layout button restores the arrangement and fits the canvas; dragged positions use a new layout storage key so older drawings do not obscure the new default. Links attach to the appropriate left/right handles, and server devices use server icons.

Verified: production build, stable layout under reversed input order, unique positions for all 21 devices, and browser checks showing 32 physical links and three core-router columns. Screenshot: [Clos layout](clos-layout.png). This changes the drawing, not Containerlab wiring or routing configuration.


## Smooth evidence playback

`frontend/src/motion.ts` maps observed transfers and processing events to captured ingress/egress ports. A persistent `PacketMarker.tsx` moves along verified links and through the device center, with a short processing pause. Missing interface/link evidence produces no fabricated movement. Request and reply markers have separate colors and directional arrows.

`usePlayback.ts` updates marker coordinates on animation frames while React controls update at event boundaries or at 10 Hz. `Topology.tsx` caches edge geometry, and `Inspector.tsx` avoids unnecessary renders. Pause/resume retains exact progress, hidden tabs pause playback, and `App.tsx` keeps the timeline's current event visible. Playback durations are deliberately slowed for inspection; they do not represent measured link latency or internal router processing time.

Verification: all six tests in `frontend/src/playback.test.ts` passed, including interface continuity, reverse direction, missing evidence, and event-clock boundaries. The production build passed. Browser verification used saved real request/reply evidence without generating new traffic: 205 sampled frames, continuous marker visibility through processing, maximum sampled frame displacement 7.41 canvas units, working pause/resume/reset, and no browser errors. Screenshot: [smooth playback](smooth-playback.png).

To check manually, reload localhost:8000, select a captured packet and press Play. Watch it enter and leave a router, pause/resume it, change speed, reset, and select an Echo Reply. Partial captures must still show their evidence limitations.
