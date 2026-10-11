"""Validate a synthetic attendance workload in a disposable database.

    python tools/benchmark_scale.py --export --ingestion --output scale.json

Defaults: 10,000 employees, 20 simulated SpeedFace-V5L terminals, 30 completed
days and 600,000 punches. No existing data folder or database is used. The
temporary database and CSV are removed on exit; only the optional JSON remains.
Install requirements.txt for --ingestion (FastAPI TestClient/httpx).
This measures software behavior, not physical terminal compatibility or capacity.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from threading import Barrier


PAGE_SIZE = 200


def positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def require(condition: bool, message: str) -> None:
    # Deliberately active with python -O too: failed validation must exit nonzero.
    if not condition:
        raise RuntimeError(message)


def device_sn(index: int) -> str:
    return f"SCALE{index:04d}"


def late_minutes(code: str | int) -> int:
    return 15 if int(code) % 20 == 0 else 0


def timings(samples: list[float]) -> dict:
    return {"median_seconds": round(statistics.median(samples), 6),
            "min_seconds": round(min(samples), 6),
            "max_seconds": round(max(samples), 6),
            "samples_seconds": [round(value, 6) for value in samples]}


def seed(args, start: date, end: date) -> dict[str, int]:
    from sqlalchemy import insert, select
    from hader import models as m, store
    from hader.bootstrap import init_db
    from hader.db import session_scope

    init_db()
    with session_scope() as db:
        # These are set before app startup. No discovery, polling, device writes,
        # notifications or scheduled backup may interfere with this isolated run.
        for key, value in {"discovery.enabled": False, "tcp.write_back": False,
                           "tcp.read_bio": False, "alerts.enabled": False,
                           "adms.auto_add": False,
                           "backup.hour": (datetime.now().hour + 1) % 24}.items():
            store.set_(db, key, value)
        department = m.Department(code="SCALE", name="Scale", name_en="Scale")
        db.add(department)
        db.flush()
        db.execute(insert(m.Employee), [
            {"emp_code": str(i), "first_name": "Employee", "last_name": str(i),
             "department_id": department.id, "hire_date": start - timedelta(days=1)}
            for i in range(1, args.employees + 1)])
        db.execute(insert(m.Device), [
            {"sn": device_sn(i), "alias": device_sn(i), "alias_en": device_sn(i),
             "model": "SpeedFace-V5L", "managed_by": "adms",
             "tcp_poll": False, "ip": "", "enabled": True,
             "user_count": len(range(i + 1, args.employees + 1, args.devices)),
             "face_count": len(range(i + 1, args.employees + 1, args.devices))}
            for i in range(args.devices)])
        timetable = m.TimeTable(alias="Scale 08:00-16:00", check_in=clock_time(8),
                                check_out=clock_time(16), late_grace=0, early_grace=0)
        db.add(timetable)
        db.flush()
        shift = m.Shift(alias="Scale every day", cycle_unit="day", cycle=1,
                        details=[m.ShiftDetail(day_index=0, timetable_id=timetable.id)])
        db.add(shift)
        db.flush()
        db.add(m.DeptSchedule(department_id=department.id, shift_id=shift.id,
                              start_date=start, end_date=end))
        ids = dict(db.execute(select(m.Employee.emp_code, m.Employee.id)).all())
        # Bound the insertion buffer to one day, rather than the whole month.
        for day_number in range(args.days):
            day = start + timedelta(days=day_number)
            rows = []
            for i in range(1, args.employees + 1):
                for stamp, state in ((clock_time(8, late_minutes(i)), 0), (clock_time(16), 1)):
                    rows.append({"emp_code": str(i), "employee_id": ids[str(i)],
                                 "punch_time": datetime.combine(day, stamp),
                                 "punch_state": state, "verify_type": 15,
                                 "device_sn": device_sn((i - 1) % args.devices)})
            db.execute(insert(m.Transaction), rows)
    return ids


def validate_report(report: dict, key: str, args, start: date, end: date,
                    *, expected_total: int, offset: int = 0) -> None:
    require(report["total"] == expected_total, f"{key}: wrong total {report['total']}")
    require(report["offset"] == offset and report["limit"] == PAGE_SIZE,
            f"{key}: wrong page metadata")
    require(len(report["rows"]) == min(PAGE_SIZE, max(0, expected_total - offset)),
            f"{key}: wrong page size")
    require(report["has_more"] == (offset + len(report["rows"]) < expected_total),
            f"{key}: wrong has_more")
    for row in report["rows"]:
        late = late_minutes(row["emp_code"])
        if key == "transactions":
            require(start.isoformat() <= row["date"] <= end.isoformat(), "punch outside range")
            require((row["time"], row["state"]) in
                    ((f"08:{late:02d}:00", "Check-In"), ("16:00:00", "Check-Out")),
                    f"transactions: unexpected time/state {row}")
            require(row["device"] == device_sn((int(row["emp_code"]) - 1) % args.devices),
                    "transactions: wrong device")
        elif key == "summary":
            expected = {"scheduled_days": args.days, "present_days": args.days,
                        "required": args.days * 480, "worked": args.days * (480 - late),
                        "late": args.days * late, "late_count": args.days if late else 0,
                        "early": 0, "absent_days": 0, "partial_absent_days": 0,
                        "absent_minutes": 0, "ot": 0, "leave": 0, "missed_punch": 0}
            require(all(row[field] == value for field, value in expected.items()),
                    f"summary: incorrect calculation for {row['emp_code']}: {row}")
        else:
            expected = {"required": 480, "worked": 480 - late, "late": late,
                        "early": 0, "absent": 0, "ot": 0, "leave": 0,
                        "clock_in": f"08:{late:02d}", "clock_out": "16:00",
                        "sched": "08:00-16:00", "status": "late" if late else "present"}
            require(all(row[field] == value for field, value in expected.items()),
                    f"daily: incorrect calculation for {row['emp_code']}: {row}")
            require(start.isoformat() <= row["date"] <= end.isoformat(), "day outside range")


def benchmark_reports(args, start: date, end: date, ids: dict[str, int]) -> dict:
    from hader import reports
    from hader.db import session_scope

    totals = {"transactions": args.employees * args.days * 2,
              "summary": args.employees, "daily": args.employees * args.days}
    results = {}
    for key, total in totals.items():
        samples = []
        for _ in range(args.repeat):
            # Fresh ORM sessions avoid retaining one page's loaded objects.
            with session_scope() as db:
                before = time.perf_counter()
                report = reports.build(db, key, start, end, limit=PAGE_SIZE, lang="en")
                samples.append(time.perf_counter() - before)
                validate_report(report, key, args, start, end, expected_total=total)
        # Verify the last page too; totals and boundaries must stay truthful.
        with session_scope() as db:
            offset = max(0, total - PAGE_SIZE)
            report = reports.build(db, key, start, end, offset=offset, limit=PAGE_SIZE, lang="en")
            validate_report(report, key, args, start, end, expected_total=total, offset=offset)
        results[key] = {**timings(samples), "total": total,
                        "page_size": min(PAGE_SIZE, total), "validation": "passed"}
    # The first daily page may contain only on-time employees. Explicitly probe
    # both lateness classes and the last employee, independently of sort order.
    probes = sorted({1, min(20, args.employees), args.employees})
    with session_scope() as db:
        for number in probes:
            selected = [ids[str(number)]]
            for key, total in (("summary", 1), ("daily", args.days)):
                report = reports.build(db, key, start, end, employee_ids=selected,
                                       limit=PAGE_SIZE, lang="en")
                validate_report(report, key, args, start, end, expected_total=total)
    results["calculation_probe_employee_codes"] = [str(number) for number in probes]
    return results


def benchmark_export(args, folder: Path, start: date, end: date) -> dict:
    from hader import reports
    from hader.db import session_scope

    csv_path = folder / "transactions.csv"
    before = time.perf_counter()
    with session_scope() as db:
        report = reports.build(db, "transactions", start, end, stream=True, lang="en")
        require(not isinstance(report["rows"], list), "export unexpectedly materialized all rows")
        with csv_path.open("wb") as output:
            for chunk in reports.iter_csv(report):
                output.write(chunk)
    elapsed = time.perf_counter() - before
    # Check every exported row, with a bounded generator of independently known
    # chronological punches. This catches missing, duplicated or altered rows.
    checked = 0
    validation_start = time.perf_counter()
    with csv_path.open(encoding="utf-8-sig", newline="") as source:
        rows = csv.DictReader(source)
        require(rows.fieldnames == ["ID", "Name", "Department", "Date", "Time", "State",
                                   "Verify", "Device", "Temp."], "unexpected CSV header")
        for day_number in range(args.days):
            day = (start + timedelta(days=day_number)).isoformat()
            for slot in ("on_time", "late", "out"):
                for number in range(1, args.employees + 1):
                    late = late_minutes(number)
                    if slot == "on_time" and late or slot == "late" and not late:
                        continue
                    row = next(rows, None)
                    require(row is not None, "CSV ended before all seeded punches were exported")
                    stamp = "16:00:00" if slot == "out" else f"08:{late:02d}:00"
                    state = "Check-Out" if slot == "out" else "Check-In"
                    require((row["ID"], row["Date"], row["Time"], row["State"], row["Device"]) ==
                            (str(number), day, stamp, state, device_sn((number - 1) % args.devices)),
                            f"CSV row {checked + 2}: incorrect punch {row}")
                    checked += 1
        require(next(rows, None) is None, "CSV contains extra punches")
    require(checked == args.employees * args.days * 2, "wrong CSV data row count")
    return {"seconds": round(elapsed, 6), "size_bytes": csv_path.stat().st_size,
            "data_rows": checked, "rows_including_header": checked + 1,
            "validation_seconds": round(time.perf_counter() - validation_start, 6),
            "validation": "passed", "temporary_csv_removed_on_exit": True}


def benchmark_ingestion(args, start: date) -> dict:
    try:
        from fastapi.testclient import TestClient
    except ImportError as exc:
        raise RuntimeError("--ingestion needs requirements.txt (including httpx)") from exc
    from sqlalchemy import func, select
    from hader.app import app
    from hader import models as m, store
    from hader.db import session_scope

    with session_scope() as db:
        require(store.get(db, "discovery.enabled") is False, "discovery must be disabled")
        require(not db.scalar(select(func.count()).select_from(m.Device).where(m.Device.tcp_poll)),
                "TCP polling must be disabled")
        before_count = db.scalar(select(func.count()).select_from(m.Transaction))
    payloads = []
    for index in range(args.devices):
        lines = []
        for number in range(index + 1, args.employees + 1, args.devices):
            for stamp, state in ((clock_time(8, late_minutes(number)), 0), (clock_time(16), 1)):
                lines.append(f"{number}\t{datetime.combine(start, stamp):%Y-%m-%d %H:%M:%S}"
                             f"\t{state}\t15\t0\t0\t0\n")
        payloads.append(("".join(lines).encode("utf-8"), len(lines)))
    barrier = Barrier(args.devices)
    with TestClient(app, raise_server_exceptions=True) as client:
        def upload(index: int) -> dict:
            body, expected_count = payloads[index]
            barrier.wait(timeout=60)
            before = time.perf_counter()
            response = client.post("/iclock/cdata", params={"SN": device_sn(index),
                                   "table": "ATTLOG", "Stamp": "1"}, content=body)
            elapsed = time.perf_counter() - before
            require(response.status_code == 200,
                    f"{device_sn(index)} upload: HTTP {response.status_code}: {response.text[:200]}")
            require(response.text.strip() == f"OK: {expected_count}",
                    f"{device_sn(index)}: incorrect acknowledgement {response.text[:200]}")
            return {"seconds": elapsed, "status": response.status_code, "records": expected_count}

        before = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.devices) as pool:
            replies = list(pool.map(upload, range(args.devices)))
        elapsed = time.perf_counter() - before
    with session_scope() as db:
        after_count = db.scalar(select(func.count()).select_from(m.Transaction))
    require(after_count == before_count, f"duplicate reupload changed count: {before_count} -> {after_count}")
    return {"seconds": round(elapsed, 6), "parallel_devices": args.devices,
            "requests": len(replies), "http_200_replies": sum(r["status"] == 200 for r in replies),
            "reuploaded_records": sum(r["records"] for r in replies),
            "punches_before": before_count, "punches_after": after_count,
            "request_latency": timings([r["seconds"] for r in replies]),
            "validation": "passed", "physical_devices_verified": False}


def write_result(result: dict, output: Path | None) -> None:
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--employees", type=positive, default=10000)
    parser.add_argument("--devices", type=positive, default=20)
    parser.add_argument("--days", type=positive, default=30)
    parser.add_argument("--repeat", type=positive, default=3)
    parser.add_argument("--export", action="store_true", help="write and validate a temporary streaming CSV")
    parser.add_argument("--ingestion", action="store_true", help="reupload one day in parallel through ADMS TestClient")
    parser.add_argument("--output", type=Path, help="save the benchmark JSON here")
    args = parser.parse_args()
    result = {"status": "running", "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "employees": args.employees, "devices": args.devices, "days": args.days,
              "punches": args.employees * args.days * 2, "repeat": args.repeat,
              "environment": {"python": platform.python_version(), "platform": platform.platform(),
                              "cpu_count": os.cpu_count()},
              "isolated_temporary_database": True, "physical_devices_verified": False}
    engine = None
    overall_start = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="hader_scale_") as temporary:
            folder = Path(temporary)
            # Override even an inherited DATABASE_URL: running this tool must
            # never connect to the operator's configured production database.
            os.environ["HADER_DATA_DIR"] = str(folder)
            os.environ["HADER_DATABASE_URL"] = f"sqlite:///{(folder / 'hader.db').as_posix()}"
            os.environ["HADER_CONFIG"] = str(folder / "benchmark.ini")
            (folder / "benchmark.ini").write_text("[server]\n", encoding="utf-8")
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from sqlalchemy import func, select
            from hader import models as m
            from hader.db import engine, session_scope
            from hader.version import VERSION

            try:
                result["version"] = VERSION
                end = date.today() - timedelta(days=2)
                start = end - timedelta(days=args.days - 1)
                result["date_range"] = {"start": start.isoformat(), "end": end.isoformat()}
                before = time.perf_counter()
                ids = seed(args, start, end)
                result["seed_seconds"] = round(time.perf_counter() - before, 6)
                with session_scope() as db:
                    for model, expected in ((m.Employee, args.employees), (m.Device, args.devices),
                                            (m.Transaction, result["punches"])):
                        actual = db.scalar(select(func.count()).select_from(model))
                        require(actual == expected, f"{model.__name__} seed count: {actual} != {expected}")
                result["reports"] = benchmark_reports(args, start, end, ids)
                if args.export:
                    result["export"] = benchmark_export(args, folder, start, end)
                if args.ingestion:
                    result["ingestion"] = benchmark_ingestion(args, start)
                result["status"] = "passed"
            finally:
                # Windows cannot remove an open SQLite file.
                engine.dispose()
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc(file=sys.stderr)
    result["total_seconds"] = round(time.perf_counter() - overall_start, 6)
    write_result(result, args.output)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
