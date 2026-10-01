"""FastAPI application lifecycle, route registration, and dashboard entry point."""

import asyncio
import contextlib
import hashlib
import pathlib
import threading

import fastapi
import fastapi.staticfiles
import fastapi.templating
import starlette.requests

from app.api import auth as auth_routes
from app.api import devices as device_routes
from app.api import diagnostics as service_routes
from app.core import state
from app.services import database as database_service
from app.services import snmp as snmp_service
from app.services import syslog as syslog_service
from app.services.xml_status import poll_once, xml_reader_loop


async def syslog_heartbeat_loop() -> None:
    """Emit periodic lifecycle heartbeats using the current runtime interval."""

    while True:
        with state.state_lock:
            interval = int(state.service_settings["syslog_heartbeat_seconds"])
        try:
            if interval <= 0:
                await service_routes.heartbeat_settings_changed.wait()
            else:
                await asyncio.wait_for(
                    service_routes.heartbeat_settings_changed.wait(),
                    timeout=interval,
                )
            service_routes.heartbeat_settings_changed.clear()
            continue
        except TimeoutError:
            pass
        device_ids = state.active_device_ids()
        database_status = database_service.get_runtime_status(device_ids[0] if device_ids else "")
        stored_records = sum(
            database_service.get_runtime_status(device_id)["records"] for device_id in device_ids
        )
        syslog_service.send_lifecycle(
            "heartbeat",
            database=database_status["state"],
            stored_records=stored_records,
        )


@contextlib.asynccontextmanager
async def lifespan(_app: fastapi.FastAPI):
    """Start independent profile acquisition, SNMP, and heartbeat resources."""

    database_service.init_database()
    state.save_persisted_state()
    snmp_service.init_snmp()
    state.stop_event.clear()
    poll_once()
    worker = threading.Thread(target=xml_reader_loop, name="xml-reader", daemon=True)
    worker.start()
    syslog_service.send_lifecycle("started")
    service_routes.heartbeat_settings_changed.clear()
    heartbeat_task = asyncio.create_task(syslog_heartbeat_loop())

    yield

    heartbeat_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await heartbeat_task
    syslog_service.send_lifecycle("stopped", reason="graceful_shutdown")
    state.stop_event.set()
    worker.join(timeout=2)
    snmp_service.close_snmp()
    database_service.close_database()


app = fastapi.FastAPI(lifespan=lifespan)
app.mount("/static", fastapi.staticfiles.StaticFiles(directory="static"), name="static")
app.include_router(auth_routes.router)
app.include_router(service_routes.router)
app.include_router(device_routes.router)

templates = fastapi.templating.Jinja2Templates(directory="templates")


def static_asset_version() -> str:
    """Return one cache key covering every locally served frontend asset."""
    digest = hashlib.sha256()
    asset_paths = [
        pathlib.Path("static/css/style.css"),
        pathlib.Path("static/vendor/chart.js/chart.umd.min.js"),
        *sorted(pathlib.Path("static/js").glob("dashboard*.js")),
    ]
    for path in asset_paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


STATIC_ASSET_VERSION = static_asset_version()


@app.get("/")
def home(request: starlette.requests.Request, device: str | None = None):
    """Render one browser-selected device without switching the running workers."""

    device_ids = state.active_device_ids()
    selected = device or (device_ids[0] if device_ids else "")
    if selected and selected not in device_ids:
        raise fastapi.HTTPException(status_code=404, detail="Device is not present in status.xml")
    selected_definition = (
        state.device_definition(selected)
        if selected
        else {"view_profile": "station", "label": "No device profiles found"}
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "static_asset_version": STATIC_ASSET_VERSION,
            "device_profile": selected_definition["view_profile"],
            "selected_device": selected,
            "device_label": selected_definition["label"],
            "devices": [state.device_definition(device_id) for device_id in device_ids],
            "has_devices": bool(device_ids),
        },
    )
