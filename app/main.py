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
from app.api import dashboard as dashboard_routes
from app.api import diagnostics as service_routes
from app.api import history as history_routes
from app.core import config, state
from app.services import database as database_service
from app.services import serial as serial_reader
from app.services import snmp as snmp_service
from app.services import syslog as syslog_service
async def syslog_heartbeat_loop() -> None:
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
        database_status = database_service.get_runtime_status()
        syslog_service.send_lifecycle(
            "heartbeat",
            database=database_status["state"],
            stored_records=database_status["records"],
        )
@contextlib.asynccontextmanager
async def lifespan(_app: fastapi.FastAPI):
    database_service.init_database()
    snmp_service.init_snmp()
    state.stop_event.clear()
    serial_thread = threading.Thread(target=serial_reader.serial_reader_loop, daemon=True)
    serial_thread.start()
    syslog_service.send_lifecycle("started")
    service_routes.heartbeat_settings_changed.clear()
    heartbeat_task = asyncio.create_task(syslog_heartbeat_loop())
    yield
    heartbeat_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await heartbeat_task
    syslog_service.send_lifecycle("stopped", reason="graceful_shutdown")
    state.stop_event.set()
    serial_thread.join(timeout=2)
    snmp_service.close_snmp()
    database_service.close_database()
app = fastapi.FastAPI(lifespan=lifespan)
app.mount("/static", fastapi.staticfiles.StaticFiles(directory="static"), name="static")
app.include_router(auth_routes.router)
app.include_router(dashboard_routes.router)
app.include_router(history_routes.router)
app.include_router(service_routes.router)
templates = fastapi.templating.Jinja2Templates(directory="templates")
def static_asset_version() -> str:
    digest = hashlib.sha256()
    for path in (pathlib.Path("static/css/style.css"), pathlib.Path("static/js/dashboard.js")):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]
STATIC_ASSET_VERSION = static_asset_version()
@app.get("/")
def home(request: starlette.requests.Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "static_asset_version": STATIC_ASSET_VERSION,
            "port_count": config.PORT_COUNT,
        },
    )
