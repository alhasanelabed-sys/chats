"""Create a full isolated application and serve it until its owner or session stops."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def isolate(job, config, access):
    for key in tuple(os.environ):
        if key.startswith("HADER_"):
            del os.environ[key]
    folder = job / "data"
    folder.mkdir(mode=0o700, exist_ok=True)
    ini = folder / "demo.ini"
    ini.write_text("[server]\n", encoding="utf-8")
    os.environ.update({"HADER_CONFIG": str(ini), "HADER_DATA_DIR": str(folder),
        "HADER_DATABASE_URL": f"sqlite:///{(folder / 'hader.db').as_posix()}",
        "HADER_SECRET_KEY": secrets.token_hex(32), "HADER_HOST": config["host"], "HADER_WEB_PORT": "0",
        "HADER_ADMS_PORTS": "", "HADER_PORTAL_PORT": "0", "HADER_SESSION_HOURS": "2",
        "HADER_DEMO_MODE": "1", "HADER_SESSION_COOKIE": "hader_demo_" + config["id"],
        "HADER_PORTAL_SESSION_COOKIE": "hader_demo_employee_" + config["id"],
        "HADER_TIMEZONE": config.get("time_zone", ""),
        "HADER_DEMO_ACCESS_TOKEN": access["token"], "HADER_DEMO_EXPIRES": config["expires_at"]})
    for key in ("employees", "devices", "days"):
        os.environ[f"HADER_DEMO_{key.upper()}"] = str(config[key])
    return folder


def run(job, supervised):
    server = None
    alive = threading.Event()
    if supervised:
        def owner_watch():
            try:
                while os.read(sys.stdin.fileno(), 1):
                    pass
            except OSError:
                pass
            alive.set()
            if server is not None:
                server.should_exit = True
            # Seeding cannot be interrupted safely inside an arbitrary SQL call.
            # The whole database is disposable and stays under this private job.
            def stop():
                os._exit(3)
            timer = threading.Timer(3, stop)
            timer.daemon = True
            timer.start()
        threading.Thread(target=owner_watch, daemon=True).start()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    config = json.loads((job / "config.json").read_text(encoding="utf-8"))
    access = json.loads((job / "access.json").read_text(encoding="utf-8"))
    folder = isolate(job, config, access)
    from hader.experiments import _write_json
    from hader.demo_environments import validate_options
    from tools.benchmark_scale import seed
    from sqlalchemy import delete, func, select, update
    from hader import models as m, store
    from hader.db import engine, now, session_scope
    from hader.security import hash_password
    options = validate_options({k: config[k] for k in ("employees", "devices", "days")})
    args = argparse.Namespace(**options)
    current = now()
    end = current.date()
    start = end - timedelta(days=args.days - 1)
    _write_json(job / "progress.json", {"stage": "seeding", "percent": 10, "message": "إنشاء موظفين وأجهزة وحركات مستقلة / Generating isolated employees, devices and punches"})
    seed(args, start, end)
    with session_scope() as db:
        # A day's generated check-out is not a live event before it happens.
        db.execute(delete(m.Transaction).where(m.Transaction.punch_time > current))
        admin = db.scalar(select(m.User).where(m.User.username == "admin"))
        admin.password_hash = hash_password(secrets.token_urlsafe(32))
        admin.must_change_password = False
        admin.full_name = "مدير البيئة التجريبية / Demo administrator"
        store.set_(db, "company.name", "Demo attendance")
        store.set_(db, "company.name_ar", "الحضور التجريبي")
        store.set_(db, "security.admin_from", "")
        for key in ("tcp.write_back", "tcp.read_bio", "discovery.enabled", "alerts.enabled", "adms.auto_add"):
            store.set_(db, key, False)
        db.execute(update(m.Employee).values(first_name="موظف تجريبي", name_en="Demo employee " + m.Employee.emp_code))
        department = db.scalar(select(m.Department).where(m.Department.code == "SCALE"))
        department.name, department.name_en = "القسم التجريبي", "Demo department"
        device_counts = dict(db.execute(select(m.Transaction.device_sn, func.count()).group_by(m.Transaction.device_sn)).all())
        for index, device in enumerate(db.scalars(select(m.Device)).all(), 1):
            device.alias = f"جهاز تجريبي {index}"
            device.alias_en = f"Demo device {index}"
            device.last_activity = current
            device.last_sync = current
            device.att_count = device_counts.get(device.sn, 0)
            device.options = json.dumps({"synthetic": True, "physical_device_access": False})
            db.add(m.DeviceCommand(device_sn=device.sn, content="DEMO: no command transmitted", title="محاكاة: جهاز → الخادم — استلام حضور",
                    status="done", attempts=0, return_code="DEMO", result="Synthetic history only; no physical device contacted",
                    created_at=current - timedelta(seconds=15), sent_at=current - timedelta(seconds=10), returned_at=current))
            db.add(m.DeviceOpLog(device_sn=device.sn, op_code=0, admin="demo", op_time=current,
                    obj1="محاكاة استلام حضور", obj2=str(device.att_count)))
        counts = {"employees": args.employees, "devices": args.devices,
                  "transactions": db.scalar(select(func.count()).select_from(m.Transaction)) or 0,
                  "today_transactions": db.scalar(select(func.count()).select_from(m.Transaction).where(m.Transaction.punch_time >= datetime.combine(end, datetime.min.time()))) or 0}
    (folder / "initial_admin_password.txt").unlink(missing_ok=True)
    if alive.is_set():
        engine.dispose()
        return 3
    from hader.app import create_app
    import uvicorn
    app = create_app()
    listener = socket.socket(socket.AF_INET6 if ":" in config["host"] else socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((config["host"], 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    from hader.config import settings
    settings.web_port = port
    server = uvicorn.Server(uvicorn.Config(app, host=config["host"], port=port, log_level="warning", access_log=False))
    def ready_watch():
        while not server.started and not alive.is_set():
            time.sleep(0.02)
        if server.started:
            _write_json(job / "ready.json", {"port": port, "counts": counts,
                        "date_range": {"start": start.isoformat(), "end": end.isoformat()}})
            _write_json(job / "progress.json", {"stage": "ready", "percent": 100, "message": "البرنامج التجريبي جاهز / Demo application ready"})
    threading.Thread(target=ready_watch, daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()
        engine.dispose()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--supervised", action="store_true")
    args = parser.parse_args()
    try:
        return run(args.job_dir.resolve(), args.supervised)
    except Exception as exc:
        # Remove credentials/paths from the message returned to the parent UI.
        message = str(exc)
        for private in (str(args.job_dir), os.environ.get("HADER_SECRET_KEY", ""), os.environ.get("HADER_DEMO_ACCESS_TOKEN", "")):
            if private:
                message = message.replace(private, "[private]")
        from hader.experiments import _write_json
        _write_json(args.job_dir / "error.json", {"error": f"{type(exc).__name__}: {' '.join(message.split())[:400]}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
