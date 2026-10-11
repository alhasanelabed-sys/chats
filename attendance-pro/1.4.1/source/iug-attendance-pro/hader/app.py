"""FastAPI application: web UI + REST API + ADMS endpoints + background jobs."""
from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import store
from .adms import sync
from .adms.server import router as adms_router
from .api import alerts as alerts_api, attendance, devices, personnel, portal, system, experiments, intake, demo_environments, operations, report_jobs
from . import demo_guard
from .bootstrap import init_db
from .db import now, session_scope
from .version import APP_NAME, VERSION

log = logging.getLogger("hader")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def maintenance_tick(state: dict) -> None:
    """Runs every minute: re-queue unanswered commands, read/discover terminals, nightly backup."""
    with session_scope() as db:
        sync.requeue_stale(db)
        hour = int(store.get(db, "backup.hour") or 2)
        keep = int(store.get(db, "backup.keep") or 14)
    from . import tcp_pull
    tcp_pull.poll_due(state)
    tcp_pull.discover_due(state)
    from . import alerts
    try:
        alerts.tick(state)
    except Exception:  # noqa: BLE001
        log.exception("alerts failed")
    today = now().date()
    if now().hour == hour and state.get("backup_day") != today:
        from .api.system import make_backup, prune_backups
        try:
            make_backup("auto")
            prune_backups(keep)
            state["backup_day"] = today
        except Exception:
            log.exception("automatic backup failed")


def device_states() -> dict[str, tuple]:
    """state + counters of every terminal, to notice changes."""
    from sqlalchemy import select as _select
    from . import models as m
    from .api.devices import is_online
    from .db import now as _now
    with session_scope() as db:
        t = _now()
        return {d.sn: ("disabled" if not d.enabled else ("online" if is_online(d) else "offline"),
                       d.user_count, d.fp_count, d.face_count, d.palm_count, d.att_count,
                       bool(d.last_sync and (t - d.last_sync).total_seconds() <= 20))
                for d in db.scalars(_select(m.Device)).all()}


async def _watcher():
    """Every few seconds: push a 'device' event when a terminal's state or counters change,
    so status icons on every open page follow the terminals without waiting."""
    from .events import publish
    last: dict = {}
    while True:
        try:
            now_states = await run_in_threadpool(device_states)
            for sn, st in now_states.items():
                if last.get(sn) != st:
                    publish("device", sn=sn, state=st[0])
            last = now_states
        except Exception:  # noqa: BLE001
            log.exception("device watcher failed")
        await asyncio.sleep(5)


async def _dashboard_keeper():
    """Keep the dashboard snapshot fresh: rebuilt within ~3 s of a change, and every minute."""
    from .api.system import SNAP_MAX_AGE, _SNAP_DIRTY, refresh_dashboard
    await asyncio.sleep(1)
    last = 0.0
    while True:
        try:
            loop_now = asyncio.get_running_loop().time()
            if _SNAP_DIRTY.is_set() or loop_now - last >= SNAP_MAX_AGE:
                await run_in_threadpool(refresh_dashboard)
                last = loop_now
        except Exception:  # noqa: BLE001
            log.exception("dashboard refresh failed")
        await asyncio.sleep(3)


async def _worker():
    state: dict = {}
    while True:
        try:
            await run_in_threadpool(maintenance_tick, state)
        except Exception:
            log.exception("maintenance failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from .api.system import mark_dashboard_dirty
    from .events import listen
    listen(mark_dashboard_dirty)
    tasks = [asyncio.create_task(_watcher()), asyncio.create_task(_dashboard_keeper())]
    if not demo_guard.is_demo():
        tasks.append(asyncio.create_task(_worker()))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        from .experiments import shutdown_managers
        await run_in_threadpool(shutdown_managers)
        from .demo_environments import shutdown_demo_managers
        await run_in_threadpool(shutdown_demo_managers)
        from .operability import shutdown_operations
        await run_in_threadpool(shutdown_operations)
        from .report_jobs import shutdown_managers as shutdown_exports
        await run_in_threadpool(shutdown_exports)


def _build_tag() -> str:
    """Changes whenever any UI file changes, so the page always loads the current scripts."""
    stamp = max((p.stat().st_mtime_ns for p in WEB_DIR.rglob("*") if p.is_file()), default=0)
    return f"{VERSION}.{stamp % 10**9:x}"


def create_app() -> FastAPI:
    app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)

    from .adms.sync import SyncCapacityError

    @app.exception_handler(SyncCapacityError)
    async def capacity_error(_request, exc: SyncCapacityError):
        return JSONResponse(status_code=409, content={"detail": {"code": "device_capacity",
                             "device_sn": exc.device_sn, "kind": exc.kind,
                             "limit": exc.limit, "requested": exc.requested,
                             "message": "سعة الجهاز لا تكفي للمزامنة / Device capacity is insufficient"}})

    @app.middleware("http")
    async def page_language(request, call_next):
        """Names come back in the page's language (X-Lang header, or ?lang= on downloads)."""
        from .i18n import LANG
        lang = request.headers.get("x-lang") or request.query_params.get("lang") or "ar"
        token = LANG.set("en" if lang == "en" else "ar")
        try:
            return await call_next(request)
        finally:
            LANG.reset(token)
    app.include_router(adms_router)
    for r in (system.router, personnel.router, devices.router, attendance.router, alerts_api.router, portal.router,
              experiments.router, intake.router, demo_environments.router, operations.router, report_jobs.router):
        app.include_router(r)
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    from .portal_gate import gate
    app.middleware("http")(gate)
    app.middleware("http")(demo_guard.gate)

    @app.middleware("http")
    async def note_changes(request, call_next):
        """Any saved change makes cached reports stale."""
        resp = await call_next(request)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
            from .events import bump
            bump()
            system.mark_dashboard_dirty()
        return resp

    @app.middleware("http")
    async def fresh_static(request, call_next):
        """Browsers re-check the UI files on every load (a cheap 304 when unchanged), so an update
        is never hidden behind an old cached copy."""
        resp = await call_next(request)
        if request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    def page(path: Path) -> HTMLResponse:
        html = re.sub(r"\?v=[\w.]+", f"?v={_build_tag()}", path.read_text(encoding="utf-8"))
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/", include_in_schema=False)
    def index():
        return page(WEB_DIR / "index.html")

    @app.get("/me", include_in_schema=False)
    def portal_page():
        return page(WEB_DIR / "me" / "index.html")

    @app.get("/me/sw.js", include_in_schema=False)
    def portal_sw():
        return FileResponse(WEB_DIR / "me" / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/me"})

    @app.get("/login", include_in_schema=False)
    def login_page():
        return RedirectResponse("/#login")

    return app


app = create_app()
