"""Fill an EMPTY data folder with a demo company, to show the program or take screenshots.

    python tools/demo_seed.py --data C:\\HaderDemo
    python run.py --data C:\\HaderDemo

Never point it at the real data folder: it adds made-up employees, terminals and punches.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

FIRST = ["محمد", "أحمد", "عبد الرحمن", "خالد", "يوسف", "عمر", "إبراهيم", "سامي", "حسن", "علي", "محمود", "مصطفى",
         "طارق", "ياسر", "رامي", "باسل", "هشام", "وائل", "فاطمة", "مريم", "سارة", "آمنة", "نور", "هبة", "رنا",
         "إسراء", "دعاء", "ريم", "أسماء", "لينا"]
FATHER = ["محمد", "أحمد", "سالم", "خليل", "إبراهيم", "عبد الله", "حسين", "يوسف", "جمال", "سعيد", "كمال", "ناصر"]
FAMILY = ["العابد", "النجار", "الحداد", "الشوا", "أبو شعبان", "الخطيب", "الأغا", "حمدان", "السقا", "البنا",
          "عاشور", "الكرد", "شاهين", "مهنا", "عودة", "الحلو", "سكيك", "الزعانين", "أبو ريا", "الطويل"]
DEPTS = [("الموارد البشرية", "Human Resources"), ("الشؤون المالية", "Finance"), ("تقنية المعلومات", "IT"),
         ("الشؤون الأكاديمية", "Academic Affairs"), ("الخدمات العامة", "General Services"),
         ("العلاقات العامة", "Public Relations")]
AREAS = [("المبنى الرئيسي", "Main Building"), ("مبنى الوسطى", "Middle Building")]
DEVICES = [("DEMO5L0001", "البوابة الرئيسية", "Main Gate", 0, "10.0.0.21"),
           ("DEMO5L0002", "مدخل الموظفين", "Staff Entrance", 0, "10.0.0.22"),
           ("DEMO5L0003", "جهاز الوسطى", "Middle Building", 1, "10.0.0.23")]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="an empty data folder for the demo")
    ap.add_argument("--employees", type=int, default=140)
    ap.add_argument("--days", type=int, default=21)
    a = ap.parse_args()
    folder = Path(a.data)
    if (folder / "hader.db").exists():
        sys.exit(f"{folder} already has a database; use an empty folder for the demo")
    os.environ["HADER_DATA_DIR"] = str(folder)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sqlalchemy import select

    from hader import models as m
    from hader.bootstrap import init_db
    from hader.db import now, session_scope

    init_db()
    rnd = random.Random(7)
    with session_scope() as db:
        db.query(m.Department).delete()
        db.query(m.Area).delete()
        db.flush()
        depts = [m.Department(code=str(i + 1), name=ar, name_en=en) for i, (ar, en) in enumerate(DEPTS)]
        areas = [m.Area(code=str(i + 1), name=ar, name_en=en) for i, (ar, en) in enumerate(AREAS)]
        db.add_all(depts + areas)
        db.flush()
        shift = db.scalar(select(m.Shift))
        today = now().date()
        for d in depts:
            db.add(m.DeptSchedule(department_id=d.id, shift_id=shift.id, start_date=today - timedelta(days=a.days + 30),
                                  end_date=today + timedelta(days=365)))
        live = now() + timedelta(hours=3)   # shown as online while the demo is being looked at
        devs = []
        for sn, ar, en, area, ip in DEVICES:
            dv = m.Device(sn=sn, alias=ar, alias_en=en, area_id=areas[area].id, ip=ip, model="SpeedFace-V5L",
                          last_activity=live, firmware="Ver 6.60")
            db.add(dv)
            devs.append(dv)
        emps, used = [], set()
        for i in range(a.employees):
            while True:
                name = (rnd.choice(FIRST), rnd.choice(FATHER), rnd.choice(FAMILY))
                if name not in used:
                    used.add(name)
                    break
            e = m.Employee(emp_code=str(1001 + i), first_name=name[0], last_name=f"{name[1]} {name[2]}",
                           department_id=depts[i % len(depts)].id, hire_date=today - timedelta(days=400))
            e.areas = [areas[0 if i % 4 else 1]]
            db.add(e)
            emps.append(e)
        db.flush()
        n = 0
        for back in range(a.days, -1, -1):
            day = today - timedelta(days=back)
            if day.weekday() in (4, 5):   # Friday, Saturday
                continue
            for e in emps:
                r = rnd.random()
                if r < 0.05:
                    continue   # absent
                late = r > 0.86
                t_in = datetime.combine(day, time(7, 30)) + timedelta(minutes=rnd.randint(0, 28) + (rnd.randint(12, 45) if late else 0))
                t_out = datetime.combine(day, time(16, 0)) + timedelta(minutes=rnd.randint(-3, 40))
                dev = devs[2] if areas[1] in e.areas else devs[rnd.randint(0, 1)]
                for t, st in ((t_in, 0), (t_out, 1)):
                    if t > now():
                        continue
                    db.add(m.Transaction(emp_code=e.emp_code, employee_id=e.id, punch_time=t, punch_state=st,
                                         verify_type=rnd.choice((15, 15, 15, 1, 25)), device_sn=dev.sn, source="device"))
                    n += 1
        for dv in devs:
            dv.user_count = sum(1 for e in emps if (areas[1] in e.areas) == (dv.sn == "DEMO5L0003"))
            dv.face_count, dv.fp_count, dv.palm_count = dv.user_count, int(dv.user_count * 1.6), dv.user_count // 3
            dv.att_count = db.query(m.Transaction).filter(m.Transaction.device_sn == dv.sn).count()
        lt = db.scalar(select(m.LeaveType))
        for e in emps[:3]:
            start = datetime.combine(today + timedelta(days=3), time(8))
            db.add(m.Leave(employee_id=e.id, leave_type_id=lt.id, start_time=start, end_time=start + timedelta(hours=8),
                           reason="ظرف عائلي", status="pending", source="portal"))
    print(f"demo company ready in {folder}: {a.employees} employees, {len(DEVICES)} terminals, {n} punches")


if __name__ == "__main__":
    main()
