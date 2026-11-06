# SRv6 workspace integration

## Analysis before implementation

The active main project is collector-only. Its older collector-app and studio applications are archived in Prototype/SRv6_networking. The developed prototype is Prototype/srv6-studio-source/srv6-studio: React 19, React Flow, FastAPI, a policy player, and an isolated thesis experiment runner. Prototype/my thesis contains the research pipeline (policy, installed_state, observe, extract, correlate, events, journey, analysis). Main lab asset symlinks point into the preserved archive.

Reuse: active DockerReader validation and fixed commands; all six state collectors; info_collector_improved role classification; Snapshot and atomic JsonStore; packet_state/compare_packets; thesis dual packet readers; prototype React Flow dependencies, physical-edge and device-icon components. Preserve all archive files and experiment entry points.

Gaps: no active API/frontend; prototype playback connects logical SID steps, not physical links; discovery generates unsolicited traffic; current live observation report explicitly lacks correlation; raw text captures lack durable binary provenance. Existing snapshots can be stale or partial. Missing tcpdump/JSON support must remain visible. Switches and transit routers require observation too. A configured SID list cannot establish a physical route.

## Architecture and ordered plan

1. Passive DiscoveryService -> coherent immutable snapshots -> FastAPI snapshot/device API.
2. React Flow workspace -> discovered IDs/interfaces, configured references distinguished from observed links; saved layout, selection, device inspection.
3. Explicit bounded ping job -> validated same-lab endpoints -> binary captures on every running node -> existing DockerReader probe.
4. Evidence adapter -> original PCAP hashes, frame numbers, Linux cooked interface/direction -> existing independent thesis packet decoders -> unique probe correlation.
5. Journey reconstruction -> matching packet bytes on reciprocal live interfaces -> observed link events; same-node ingress/egress header comparisons -> qualified processing events. Missing/ambiguous evidence creates no synthetic edge.
6. Single playback clock -> event timeline, active physical link, packet inspector, pause/resume/reset/speed. Replay is slowed evidence playback, not measured wire-time animation.
7. Unit/API/frontend checks followed by actual Containerlab discovery and ping/capture verification. Document failures as well as successes.

Policy intent, collected installed state, and observed execution have separate UI views and persisted provenance. No policy installation, topology deployment, or experiment rewrite is necessary. The original collect.py evidence import remains supported. Web refresh is passive; existing CLI collection behavior is preserved explicitly.

## Phase implementation and how to test

| Phase | Files changed/added | Behavior and verification |
| --- | --- | --- |
| 1 — Analysis | `docs/INTEGRATION.md` | Records active vs archived project structures, reusable collectors/components, thesis pipeline, and missing physical-path correlation. No archive or lab configuration was edited. |
| 2 — Architecture | `backend/main.py`, `services/workspace_service.py`, `requirements-web.txt`, `run-web.sh` | FastAPI wraps the existing store and discovery service. Snapshot reads are separate from explicit job execution. `/api/workspace`, `/api/devices/{id}`, `/api/status`, `/api/refresh`, `/api/pings`, `/api/pings/current`, `/api/pings/current/workspace`; OpenAPI at `/docs`. Check API tests for input validation and origin restrictions. |
| 3 — Network UI | `frontend/src/components/Topology.tsx`, `DeviceIcon.tsx`, `PhysicalEdge.tsx`, `Inspector.tsx`, `App.tsx`, `api.ts`, `types.ts`, `styles.css`, build configuration | Reuses prototype icons, physical-edge renderer, and React Flow dependencies. Graph traversal lays out discovered topology; saved positions use container IDs. Configured-only links are dashed. Inspector exposes existing collector outputs. Refresh, drag, zoom, select devices, toggle ports/addresses. Optional updates run once per minute only while visible and idle. |
| 4 — Execution | `services/ping_service.py`, `services/capture_service.py`, `collectors/docker_reader.py`, `services/discovery_service.py` | Existing probe gains validated source address and nonce options. Passive API discovery preserves CLI behavior through an explicit option. Ping is three echoes between distinct running same-lab endpoints; management addresses and arbitrary commands are excluded. Capture startup is observed from tcpdump readiness. Switches can use ephemeral namespace capture containers. Run live verification or the UI ping. |
| 5 — End-to-end replay | `services/evidence_service.py`, `services/journey_service.py`, `frontend/src/playback.ts`, `usePlayback.ts`, topology/timeline integration | SLL2 provenance adapter calls both original thesis decoders. Bridge OTHERHOST ingress is supported. Unique packet/byte/interface pairs establish observed transfers; unique same-node ingress/egress establishes processing. Neither routes nor segment lists generate missing links. Tests cover full forward/reply journeys, gaps, duplicates, wrong bytes, unrelated packets, and animation mapping. |
| 6 — SRv6 | `journey_service.py`, `Inspector.tsx` | Reuses `packet_state` and `compare_packets` for layer-aware transitions. Depth changes show encapsulation/decapsulation. Segments Left and destination transitions remain observed facts; longest-prefix installed SID matches provide explicitly inferred behavior. Packet panel shows addresses, segment list, SID match, before/after fields, and capture provenance. Select encapsulation or decapsulation in a live timeline. |
| 7 — Verification | `tests/test_workspace.py`, `tests/verify_workspace_live.py`, `tests/replay_saved_evidence.py`, `frontend/src/playback.test.ts`, `docs/VERIFICATION.md` | Existing regression tests plus new API/decoder/reconstruction tests; frontend typecheck/build and animation tests; routed live test; browser interaction verification. Saved captures can be rederived without another ping. |

### Important reuse boundaries

The existing six state collectors and storage format are retained. `info_collector_improved.classify_node_role` supplies configured role labels only; it is not packet evidence. `observation_collector` and its thesis methodology tests remain intact. The decoder adapter imports `srv6obs.extract` from the preserved thesis tree rather than copying or rewriting its decoders. Its synthetic Ethernet envelope is only an input adapter; the original SLL2 bytes and hashes are retained separately. Old experiment directories are never overwritten.

## Runtime editing follow-up

`services/config_service.py` owns typed edits, command construction, live previews, optimistic conflict checking, before/after audit records and route verification. `DockerReader.configure` revalidates container identity before executing argument arrays. `main.py` serializes editing with ping preparation and refresh. `ConfigEditor.tsx` adds a small reusable editor to `Inspector.tsx`; `App.tsx` switches back to the updated live view after applying. `PingService.validate_selection` separates endpoint validation from task creation so every real ping can collect fresh state before execution. No existing collector or thesis decoder is duplicated.

Tests are in `tests/test_config.py`, the normal-IPv6 regression in `tests/test_workspace.py`, and the explicitly mutating/cleaning live check `tests/verify_config_live.py`. Scope and persistence are documented in `docs/CONFIGURATION.md`.
