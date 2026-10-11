"""Stream complete reports into one page width, with vertical pagination."""
from __future__ import annotations

from html import escape
import re


def _column_weight(column: dict) -> float:
    """Reserve room for identities; month cells usually contain short symbols."""
    key = str(column["key"])
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}|day_\d+", key):
        return 1.0
    return {
        "name": 5.0, "department": 3.2, "emp_code": 2.1, "date": 3.0,
        "timetable": 3.0, "sched": 3.4, "punch_list": 4.0,
        "_p": 1.3, "_a": 1.3, "_l": 1.3,
    }.get(key, 2.2)


def write_document(report: dict, target, *, company: str = "", lang: str = "ar",
                   orientation: str = "auto", paper: str = "A4") -> dict:
    columns = report["columns"]
    if not columns:
        raise ValueError("print report has no columns")
    if orientation == "auto":
        orientation = "landscape" if len(columns) > 7 else "portrait"
    if orientation not in {"portrait", "landscape"} or paper not in {"A4", "A3"} or lang not in {"ar", "en"}:
        raise ValueError("invalid print orientation, paper or language")
    direction = "rtl" if lang == "ar" else "ltr"
    compact = len(columns) > 20
    margin = 8 if compact else 11
    page_width = {"A4": {"portrait": 210, "landscape": 297},
                  "A3": {"portrait": 297, "landscape": 420}}[paper][orientation] - 2 * margin
    font = 8.2
    if compact:
        font = 7.0 if orientation == "landscape" else 6.0
        if paper == "A3":
            font += 1.2
        if len(columns) > 50:
            font = max(5.0, font - 1.0)
    weights = [_column_weight(column) for column in columns]
    total_weight = sum(weights)
    e = lambda value: escape("" if value is None else str(value), quote=True)
    title = report.get("title", "")
    legend = ' · '.join(f'{symbol}: {label}' for symbol, label in report.get("legend", {}).items())
    note = ("جميع الأعمدة في عرض صفحة واحدة؛ الصفوف تمتد إلى صفحات متتالية."
            if lang == "ar" else "All columns fit one page width; rows continue on subsequent pages.")
    if compact:
        note += (" استخدم الاتجاه الأفقي، أو ورق A3 لتكبير النص إذا كانت طابعتك تدعمه."
                 if lang == "ar" else " Use landscape, or A3 for larger text if your printer supports it.")
    target.write(f'''<!doctype html><html lang="{lang}" dir="{direction}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<style>@page{{size:{paper} {orientation};margin:{margin}mm}}*{{box-sizing:border-box}}body{{font-family:"Segoe UI",Tahoma,Arial,sans-serif;margin:0;color:#172332;font-size:9pt;background:#f2f5f8}}
.controls{{display:flex;flex-wrap:wrap;gap:12px;align-items:center;padding:12px;background:#eef4f8}}button{{padding:10px 18px;border:1px solid #adbdc9;border-radius:8px;cursor:pointer}}
.print-section{{margin:14px auto;padding:8px;width:100%;max-width:{page_width}mm;background:white}}
header{{display:flex;justify-content:space-between;align-items:center;gap:12px}}h1{{font-size:15pt;margin:0 0 5px}}h2{{font-size:10pt;margin:0 0 5px;font-weight:normal}}.date-range,.legend,footer{{font-size:8pt;margin:7px 0;color:#465565}}
table{{border-collapse:collapse;width:100%;table-layout:fixed}}th,td{{min-width:0;border:1px solid #9baabb;padding:{'2px 0.5px' if compact else '4px 3px'};white-space:normal;overflow-wrap:anywhere;word-break:normal;vertical-align:top;text-align:center;font-size:{font:.1f}pt;line-height:1.3}}
th{{background:#eaf1f7;font-weight:600}}thead{{display:table-header-group}}tr{{break-inside:avoid;page-break-inside:avoid}}td[data-key="name"],td[data-key="department"]{{text-align:start}}
.document-heading{{border:0;padding:0 0 7px;background:white;text-align:start}}footer{{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px}}
@media print{{.controls{{display:none}}.print-section{{max-width:none;width:100%;margin:0;padding:0}}body{{background:white}}th{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}footer{{position:fixed;left:0;right:0;bottom:-6mm;margin:0;font-size:7pt;line-height:1.2}}}}
</style></head><body data-orientation="{orientation}" data-paper="{paper}" data-layout="fit-width" data-column-count="{len(columns)}"><div class="controls"><button onclick="window.print()">{e('طباعة / حفظ PDF' if lang == 'ar' else 'Print / Save PDF')}</button><span>{e(note)}</span></div>
<section class="print-section" data-section="1" data-band="1"><table><colgroup>''')
    for column, weight in zip(columns, weights):
        target.write(f'<col data-key="{e(column["key"])}" style="width:{100 * weight / total_weight:.5f}%">')
    target.write(f'''</colgroup><thead><tr><th class="document-heading" colspan="{len(columns)}"><header><div><h1>{e(title)}</h1><h2>{e(company)}</h2></div><div class="date-range">{e(report.get('start'))} — {e(report.get('end'))}</div></header>''')
    if legend:
        target.write('<div class="legend">' + e(legend) + '</div>')
    target.write('</th></tr><tr>')
    for column in columns:
        target.write(f'<th data-key="{e(column["key"])}">{e(column["label"])}</th>')
    target.write('</tr></thead><tbody>')
    count = 0
    for count, row in enumerate(report['rows'], 1):
        target.write(f'<tr data-row="{count}">')
        for column in columns:
            target.write(f'<td data-key="{e(column["key"])}">{e(row.get(column["key"], ""))}</td>')
        target.write('</tr>')
    if not count:
        target.write(f'<tr><td colspan="{len(columns)}">{e("لا توجد نتائج للفلاتر المحددة" if lang == "ar" else "No matching results")}</td></tr>')
    target.write('</tbody></table>')
    label = 'عدد الصفوف' if lang == 'ar' else 'Rows'
    target.write(f'<footer><span>{label}: {count}</span><span>{e(paper)} · {e("أفقي" if orientation == "landscape" else "عمودي") if lang == "ar" else orientation}</span></footer></section>')
    target.write('<script>window.addEventListener("load",()=>setTimeout(()=>window.print(),200));</script></body></html>')
    return {'rows': count, 'sections': 1, 'orientation': orientation, 'column_bands': 1,
            'layout': 'fit-width', 'paper': paper, 'columns': len(columns)}
