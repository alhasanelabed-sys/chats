"""Bounded-memory, full-range print documents with repeatable identity columns."""
from __future__ import annotations

from html import escape
from itertools import islice


def column_bands(columns: list[dict], orientation: str) -> list[list[dict]]:
    capacity = 13 if orientation == "landscape" else 7
    if len(columns) <= capacity:
        return [columns]
    identity = [col for col in columns if col["key"] in {"emp_code", "name", "department", "date"}][:3]
    remaining = [col for col in columns if col not in identity]
    width = capacity - len(identity)
    return [identity + remaining[index:index + width] for index in range(0, len(remaining), width)]


def write_document(report: dict, target, *, company: str = "", lang: str = "ar", orientation: str = "auto") -> dict:
    columns = report["columns"]
    direction = "rtl" if lang == "ar" else "ltr"
    if orientation == "auto":
        orientation = "landscape" if len(columns) > 7 else "portrait"
    if orientation not in {"portrait", "landscape"}:
        raise ValueError("invalid print orientation")
    bands = column_bands(columns, orientation)
    e = lambda value: escape("" if value is None else str(value), quote=True)
    title = report.get("title", "")
    target.write(f'''<!doctype html><html lang="{lang}" dir="{direction}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<style>@page{{size:A4 {orientation};margin:11mm}}*{{box-sizing:border-box}}body{{font-family:"Segoe UI",Tahoma,Arial,sans-serif;margin:0;color:#172332;font-size:9pt}}
.controls{{display:flex;gap:12px;align-items:center;padding:12px;background:#eef4f8}}button{{padding:10px 18px;border:1px solid #adbdc9;border-radius:8px;cursor:pointer}}
.print-section{{margin:14px auto;padding:8px;max-width:{"275" if orientation == "landscape" else "190"}mm;background:white}}
header{{display:flex;justify-content:space-between;align-items:center;gap:12px}}h1{{font-size:15pt;margin:0 0 5px}}h2{{font-size:10pt;margin:0 0 5px;font-weight:normal}}.range,footer{{font-size:8pt;margin:7px 0;color:#465565}}
table{{border-collapse:collapse;width:100%;table-layout:fixed}}th,td{{border:1px solid #9baabb;padding:4px 3px;white-space:normal;overflow-wrap:anywhere;word-break:normal;vertical-align:top;font-size:8pt;line-height:1.3}}
th{{background:#eaf1f7;font-weight:600}}thead{{display:table-header-group}}tr{{break-inside:avoid}}td[data-key="name"]{{text-align:start}}footer{{display:flex;justify-content:space-between}}
@media print{{.controls{{display:none}}.print-section{{max-width:none;margin:0;padding:0;break-after:page}}.print-section:last-child{{break-after:auto}}body{{background:white}}th{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}}}
</style></head><body data-orientation="{orientation}"><div class="controls"><button onclick="window.print()">{e("طباعة / حفظ PDF" if lang == "ar" else "Print / Save PDF")}</button><span>{e("كل النتائج المطابقة للفلاتر؛ الأعمدة العريضة موزعة مع تكرار هوية الموظف." if lang == "ar" else "All matching results; wide columns split with employee identity repeated.")}</span></div>''')
    rows = iter(report["rows"])
    count, sections = 0, 0
    while True:
        block = list(islice(rows, 32 if orientation == "landscape" else 42))
        if not block:
            break
        first = count + 1
        count += len(block)
        for index, band in enumerate(bands):
            sections += 1
            target.write(f'<section class="print-section" data-section="{sections}" data-band="{index + 1}"><header><div><h1>{e(title)}</h1><h2>{e(company)}</h2></div><div>{e(report.get("start"))} — {e(report.get("end"))}</div></header>')
            label = "الصفوف" if lang == "ar" else "Rows"
            part = "جزء الأعمدة" if lang == "ar" else "Column section"
            target.write(f'<div class="range">{label} {first}–{count} · {part} {index + 1}/{len(bands)}</div><table><thead><tr>')
            for col in band:
                target.write(f'<th data-key="{e(col["key"])}">{e(col["label"])}</th>')
            target.write('</tr></thead><tbody>')
            for number, row in enumerate(block, first):
                target.write(f'<tr data-row="{number}">')
                for col in band:
                    target.write(f'<td data-key="{e(col["key"])}">{e(row.get(col["key"], ""))}</td>')
                target.write('</tr>')
            target.write(f'</tbody></table><footer><span>{label} {first}–{count}</span><span>{part} {index + 1}/{len(bands)}</span></footer></section>')
    if not count:
        target.write(f'<section class="print-section"><h1>{e(title)}</h1><p>{e("لا توجد نتائج للفلاتر المحددة" if lang == "ar" else "No matching results")}</p></section>')
    target.write('<script>window.addEventListener("load",()=>setTimeout(()=>window.print(),200));</script></body></html>')
    return {"rows": count, "sections": sections, "orientation": orientation, "column_bands": len(bands)}
