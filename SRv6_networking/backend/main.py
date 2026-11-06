"""Local workspace API. Run with one worker bound to loopback."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from collectors.docker_reader import DockerReader
from services.discovery_service import DiscoveryService
from services.ping_service import PingService
from services.config_service import ConfigService, Change
from services.workspace_service import workspace
from storage.json_store import JsonStore

ROOT = Path(__file__).resolve().parents[1]


class PingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: str = Field(pattern=r'^[0-9a-f]{64}$')
    destination: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_address: str = Field(max_length=39)
    destination_address: str = Field(max_length=39)


def create_app(store=None, reader=None, topology=None, runs=None):
    store = store or JsonStore(ROOT / 'backend/data/snapshots')
    reader = reader or DockerReader()
    topology = topology or Path(os.environ.get('SRV6_TOPOLOGY', ROOT / 'topology/SRv6_lab.yml'))
    discovery = DiscoveryService(store, topology, reader=reader, active_observation=False)
    ping = PingService(reader, runs or ROOT / 'backend/data/ping_runs')

    configuration = ConfigService(reader, (runs or ROOT / 'backend/data/ping_runs').parent / 'config_changes')
    preparation = asyncio.Lock()

    def stale(view):
        try:
            timestamp = datetime.fromisoformat(view['collected_at'] or '')
            age = (datetime.now(timezone.utc) - timestamp).total_seconds()
            return not 0 <= age <= 180
        except (KeyError, TypeError, ValueError):
            return True

    @asynccontextmanager
    async def lifespan(app):
        yield
        tasks = [s.task for s in (discovery, ping) if s.task and not s.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title='SRv6 Evidence Workspace', lifespan=lifespan)
    app.state.discovery, app.state.ping = discovery, ping
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'])

    @app.middleware('http')
    async def same_origin(request: Request, call_next):
        origin = request.headers.get('origin')
        if request.method == 'POST' and origin and urlparse(origin).netloc not in ('localhost:8000', '127.0.0.1:8000', 'localhost:5173', '127.0.0.1:5173'):
            return JSONResponse({'detail': 'Cross-origin lab execution is not allowed'}, status_code=403)
        return await call_next(request)

    @app.get('/api/workspace')
    def get_workspace():
        return workspace(store, topology)

    @app.get('/api/devices/{device_id}')
    def get_device(device_id: str):
        view = workspace(store, topology)
        node = next((n for n in view['nodes'] if n['id'] == device_id), None)
        if node is None:
            raise HTTPException(404, 'Device not in collected lab')
        return {'node': node, 'snapshots': {k: {**v, 'records': [r for r in v['records'] if r.get('id') == device_id]} for k, v in view['snapshots'].items()}}

    @app.get('/api/status')
    def status():
        return {'collection': discovery.status, 'ping': {k: v for k, v in ping.status.items() if k not in ('journeys', 'captures')}}

    @app.post('/api/refresh', status_code=202)
    async def refresh():
        if preparation.locked() or (ping.task and not ping.task.done()):
            raise HTTPException(409, 'Wait for the current ping job before refreshing')
        return discovery.start()

    @app.post('/api/pings', status_code=202)
    async def start_ping(body: PingRequest):
        if preparation.locked() or (ping.task and not ping.task.done()):
            raise HTTPException(409, 'A ping is already being prepared or running')
        async with preparation:
            view = await asyncio.to_thread(workspace, store, topology)
            if view['nodes']:
                try:
                    ping.validate_selection(view, body.source, body.destination, body.source_address, body.destination_address)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            # A user-triggered ping always refreshes state, including externally edited policies.
            if not discovery.task or discovery.task.done():
                discovery.start()
            await asyncio.shield(discovery.task)
            view = await asyncio.to_thread(workspace, store, topology)
            if stale(view) or discovery.status['state'] in ('failed', 'missing'):
                raise HTTPException(503, 'Cannot refresh lab state: ' + '; '.join(
                    discovery.status.get('errors') or ['No current topology available']))
            try:
                return ping.start(view, body.source, body.destination, body.source_address, body.destination_address)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(409, str(exc)) from exc

    async def configure(body: Change, apply: bool):
        if preparation.locked() or (ping.task and not ping.task.done()):
            raise HTTPException(409, 'Wait for the current lab operation to finish')
        async with preparation:
            if discovery.task and not discovery.task.done():
                await asyncio.shield(discovery.task)
            view = await asyncio.to_thread(workspace, store, topology)
            try:
                if not apply:
                    return await configuration.preview(body, view)
                result = await configuration.apply(body, view)
                discovery.start()
                await asyncio.shield(discovery.task)
                result['workspace'] = await asyncio.to_thread(workspace, store, topology)
                result['refresh_status'] = discovery.status
                return result
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.post('/api/config/preview')
    async def preview_configuration(body: Change):
        return await configure(body, False)

    @app.post('/api/config/apply')
    async def apply_configuration(body: Change):
        return await configure(body, True)

    @app.get('/api/pings/current/workspace')
    def ping_workspace():
        return ping.saved_view

    @app.get('/api/pings/current')
    def current_ping():
        return ping.status

    frontend = ROOT / 'frontend/dist'
    if frontend.is_dir():
        app.mount('/', StaticFiles(directory=frontend, html=True), name='frontend')
    return app


app = create_app()
