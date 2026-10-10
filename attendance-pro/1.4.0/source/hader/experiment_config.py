"""Validated workload options shared by the experiment API and its isolated worker."""
from __future__ import annotations

LIMITS = {"employees": 50000, "devices": 100, "days": 365, "repeat": 5, "punches": 2000000}
DEFAULTS = {"employees": 1000, "devices": 4, "days": 14, "repeat": 3,
            "export_csv": True, "ingestion": True, "keep_write_off": True}


def validate_options(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) - set(DEFAULTS):
        raise ValueError("خيارات التجربة غير صالحة / Invalid experiment options")
    options = DEFAULTS | data
    for key in ("employees", "devices", "days", "repeat"):
        value = options[key]
        if type(value) is not int or not 1 <= value <= LIMITS[key]:
            raise ValueError(f"{key}: يجب إدخال عدد صحيح من 1 إلى {LIMITS[key]} / Enter a whole number within the limit")
    for key in ("export_csv", "ingestion", "keep_write_off"):
        if type(options[key]) is not bool:
            raise ValueError(f"{key}: يجب اختيار نعم أو لا / Must be true or false")
    if options["devices"] > options["employees"]:
        raise ValueError("عدد الأجهزة لا يتجاوز عدد الموظفين / Devices cannot exceed employees")
    if 2 * options["employees"] * options["days"] > LIMITS["punches"]:
        raise ValueError("التجربة تتجاوز مليوني حركة؛ قلل الموظفين أو الأيام / Workload exceeds two million punches")
    return options
