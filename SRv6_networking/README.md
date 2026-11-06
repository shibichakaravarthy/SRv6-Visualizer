# SRv6 Evidence Workspace

A local React/FastAPI network workspace integrated with the existing Containerlab collectors and thesis packet decoders. It discovers the lab, inspects devices, sends actual IPv6 pings, and replays capture-supported physical transfers in both directions.

## Start

Dependencies and the frontend build have been prepared in this checkout:

```bash
cd /home/shibi/Networking/SRv6_networking
./run-web.sh
```

Open **http://127.0.0.1:8000**. Run one server worker as a user with Docker access.

1. Click **Refresh topology**. Missing FRR information on hosts is reported rather than invented.
2. Select a source and destination plus their collected data-plane IPv6 addresses.
3. Click **Run ping**. Topology and installed state refresh automatically before every execution; the button shows **Checking lab…** while preparing. Collection and ping execution are serialized. Capture lasts about 12 seconds.
4. Select an Echo Request or Echo Reply, then Play. Pause, Resume, Reset, speed, and timeline selection share one playback clock.
5. Click a device for interfaces, routes, IS-IS, installed SRv6 state, configuration, and declared intent. The Packet tab explains the selected event and its raw evidence.
6. **Live topology / Saved replay** switches between current state and the run's original topology. The latest verified run survives a backend restart.

In this lab, client1/client2/client3 share a LAN. To exercise the SRv6 core, select **client1 → webserver**. The observed reply takes a different core path. This is discovered from captures, not configured in the application.

## Setup on another checkout

Keep the adjacent `Prototype/my thesis/src` tree, or set `SRV6_THESIS_SRC` to its `src` directory. Set `SRV6_TOPOLOGY` for a different Containerlab YAML. Existing lab asset symlinks must remain valid.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-web.txt
cd frontend
npm ci
npm run build
cd ..
./run-web.sh
```

Node 22.6+ is needed for the dependency-free TypeScript playback tests (Node 24 was used here). For frontend development, run `npm run dev` in `frontend` alongside the API; Vite proxies `/api` to port 8000.

For a node without tcpdump, capture falls back to a temporary, read-only Netshoot container sharing that node's network namespace, with only NET_RAW added. It uses `--pull never`, runs for a bounded window, and removes itself. `SRV6_CAPTURE_IMAGE` can select another **already installed** compatible image. Missing images or capture capabilities are displayed as evidence limitations. Ping and refresh do not change policies. Explicit Apply actions in the Edit tab change runtime configuration and preserve before/after records.

## Configure routers

Select a router → **Edit** → load an entry or enter a new one → **Preview change** → **Apply to router**. Supports SRv6 policies, local SIDs, normal IPv6 routes, and FRR IPv6/IS-IS snippets. Changes are runtime-only; download the command for your deployment workflow. See [configuration guide](docs/CONFIGURATION.md).

## Evidence and limitations

- Paired, dual-decoded captures must match packet identity, payload nonce, original IPv6 bytes, direction, and verified endpoint interfaces before a physical link animates.
- Original PCAPs, frame hashes, decoded observations, topology/state snapshot, ping output, result, and SHA-256 manifest live under `backend/data/ping_runs/<id>/`. Latest-run reload verifies all manifest files.
- Capture-supported transfers are observed; specific kernel SID actions remain inferred from header changes and matching installed LocalSIDs. The UI keeps those categories separate.
- Replay is deliberately slowed, with original timestamps shown. It does not claim that animation speed measures latency. Ping statistics provide measured RTT and loss.
- Missing, ambiguous, truncated, or disagreeing evidence does not produce guessed movement. Loops/replication may yield partial coverage rather than a falsely complete linear path. Fragmented packets and unsupported decoder forms may be unavailable.
- The installed-state context is collected immediately before each ping, but is not an atomic kernel trace. Source and destination addresses are checked again immediately before sending.
- Original thesis experiments and manifest-based evidence imports remain available through their existing CLI. Web ping evidence is a separate format and does not claim full five-level thesis certification. The Edit tab supports runtime route, SRv6 policy/SID, and bounded FRR changes; see the configuration guide.
- This is a loopback-only local tool; it is not an authenticated remote multi-user service. Use the provided launcher, one worker, and retain its origin/host checks.

## Verification

```bash
.venv/bin/python -m unittest discover -s tests -v
cd frontend && npm test && npm run build
```

Explicit live verification (sends three pings, starts captures):

```bash
.venv/bin/python tests/verify_workspace_live.py
```

Read-only rederivation of the most recent complete routed test:

```bash
.venv/bin/python tests/replay_saved_evidence.py
```

See [integration analysis and phase-by-phase changes](docs/INTEGRATION.md) and [verification results](docs/VERIFICATION.md).

---

# Collector CLI reference

The collector CLI remains available alongside the new web workspace. Its existing active observation behavior is preserved; web topology refresh uses passive collection.

```text
SRv6_networking/
  collect.py                 # Collection CLI
  run-collector.sh
  requirements.txt           # PyYAML and Pydantic only
  backend/
    collectors/              # Docker, IPv6, IS-IS, SRv6, topology, configuration
    services/                # Collection orchestration
    models/                  # Snapshot schema
    storage/                 # Immutable JSON publication
    data/snapshots/
      *.json                 # Seven latest collector snapshots
```

## Collect

```bash
cd /home/shibi/Networking/SRv6_networking
./run-collector.sh
```

The command reads the running lab, saves a new collection, refreshes the seven named latest JSON files, and exits. It does not start a server. The local Python environment is ready.

Optional paths:

```bash
./run-collector.sh --topology /path/to/lab.yml --output /path/to/snapshots
```

Each collection contains `topology.json`, `ipv6_routes.json`, `srv6_policies.json`, `docker.json`, `isis.json`, `configurations.json`, and `observations.json`. All share a collection ID and timestamp. Previous snapshots are retained. Raw command evidence and explicit missing/unsupported statuses are preserved. Configured links are references until confirmed from live interfaces.

For a fresh environment (Python with venv support required):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Docker must be accessible to the user running the collector. Exit code 0 means a published, validated collection with discovered devices; it may be partial if some observations are unavailable. A failed collection or no discovered containers returns exit code 1.

## Historical reorganization before this integration

Before this integration, the earlier web app files, frontend dependencies, API code, experiment data, older source trees and previous documentation were moved into `../Prototype/SRv6_networking/`, preserving their relative directory structures. Existing archive files were not overwritten, and original files were not deleted.

The lab assets also reside there. `configs/`, `deployment/`, `topology/` and `docker-file/` in this main folder are directory links to those preserved assets. These links keep original lab paths usable; no lab configuration content was changed and no containers were deployed or restarted. The collector only reads the topology reference and the running containers.

Older snapshot generations and historical copies were moved out of the main folder into `../Prototype/SRv6_networking/cleanup/`. The six latest JSON files remain in `backend/data/snapshots/`. Future collection runs create new history as documented below. Reorganization details live at `../Prototype/SRv6_networking/phase1-reorganization.json`.

## Run an individual collector

Use the active project files, not the archived copies under `Prototype/`:

```bash
cd /home/shibi/Networking/SRv6_networking
.venv/bin/python backend/collectors/configuration_collector.py
```

Every run collects only the selected category and atomically replaces its latest JSON file under `backend/data/snapshots/`. It does not append duplicate device rows. Earlier runs are retained under `backend/data/snapshots/history/<name>/`.

| Script in backend/collectors/ | Updated file in backend/data/snapshots/ |
| --- | --- |
| configuration_collector.py | configurations.json |
| docker_reader.py | docker.json |
| ipv6_collector.py | ipv6_routes.json |
| isis_collector.py | isis.json |
| srv6_collector.py | srv6_policies.json |
| topology_collector.py | topology.json |

Use `--output /path/to/data` to change the destination. Topology collection also accepts `--topology /path/to/lab.yml`. Paths default relative to the project, so running from another working directory works. In your IDE, select `SRv6_networking/.venv/bin/python` as the interpreter.

Each latest file represents its own most recent run; independently collected files can have different IDs/timestamps. Full coordinated generations remain under `collections/<id>/` and are still read coherently by `JsonStore.read_many()`. Running a standalone collector does not alter a full generation. A failed observation updates the latest JSON with an explicit failure status; previous observations remain in history.

Collector runner tests (no Docker access):

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Five-level observation report

See [the collector guide](backend/collectors/README.md) for the report methodology, beginner explanations and evidence limitations. `observations.json` reports all five thesis levels per imported event. Without `--evidence-run`, it explicitly reports missing packet evidence; live configuration alone cannot establish packet execution.

```bash
.venv/bin/python collect.py --evidence-run ../Prototype/SRv6_networking/srv6-studio-source/srv6-studio/lab/results/P01/run_002
```

The original experiment run identity stays separate from the current live collection. Existing six-file historical generations remain readable and report the absent observation snapshot as missing.
