import json
from io import BytesIO

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .extractor import ExtractError, extract
from .models import ScanRecord


def _body(request):
    try:
        return json.loads(request.body or "{}")
    except ValueError:
        return {}


def _row(r):
    return {
        "id": r.id, "po": r.po_number, "grn": r.grn_number, "url": r.url, "title": r.title,
        "details": r.details, "scanned": timezone.localtime(r.created_at).strftime("%d %b %Y %H:%M"),
    }


def index(request):
    return render(request, "receiving/index.html")


@require_POST
def api_extract(request):
    try:
        return JsonResponse(extract(_body(request).get("url", "")))
    except ExtractError as e:
        return JsonResponse({"error": str(e)}, status=400)


@require_POST
def api_save(request):
    d = _body(request)
    po, grn = (d.get("po") or "").strip(), (d.get("grn") or "").strip()
    url = (d.get("url") or "").strip()
    if not po or not grn:
        return JsonResponse({"error": "Enter both the PO number and the GRN number."}, status=400)
    if d.get("confirmed") is not True:
        return JsonResponse({"error": "The receptionist must confirm the item before a GRN can be entered."}, status=400)
    if not url or not isinstance(d.get("details"), dict) or not d["details"]:
        return JsonResponse({"error": "Scan a QR code first."}, status=400)
    if ScanRecord.objects.filter(po_number__iexact=po, grn_number__iexact=grn, url=url).exists():
        return JsonResponse({"error": f"This item is already saved under PO {po}, GRN {grn}."}, status=409)
    rec = ScanRecord.objects.create(
        po_number=po, grn_number=grn, url=url, title=(d.get("title") or "")[:300], details=d["details"]
    )
    return JsonResponse(_row(rec), status=201)


@require_GET
def api_records(request):
    qs = ScanRecord.objects.all()
    pos = {}
    for r in qs:
        pos[r.po_number] = pos.get(r.po_number, 0) + 1
    po = request.GET.get("po", "").strip()
    if po:
        qs = qs.filter(po_number__iexact=po)
    return JsonResponse({"records": [_row(r) for r in qs[:500]], "pos": pos})


@require_POST
def api_delete(request, pk):
    get_object_or_404(ScanRecord, pk=pk).delete()
    return JsonResponse({"ok": True})


@require_GET
def export_xlsx(request):
    qs = ScanRecord.objects.order_by("po_number", "grn_number", "created_at")
    po = request.GET.get("po", "").strip()
    if po:
        qs = qs.filter(po_number__iexact=po)
    records = list(qs)

    keys = []
    for r in records:
        for k in r.details:
            if k not in keys:
                keys.append(k)
    fixed = ["PO Number", "GRN Number", "Product", "Source URL", "Scanned At"]

    wb = Workbook()
    ws = wb.active
    ws.title = "Received items"
    ws.append(fixed + keys)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E46")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for r in records:
        ws.append([
            r.po_number, r.grn_number, r.title, r.url,
            timezone.localtime(r.created_at).strftime("%Y-%m-%d %H:%M"),
        ] + [r.details.get(k, "") for k in keys])
    for i in range(1, len(fixed) + len(keys) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 22
    ws.freeze_panes = "C2"

    buf = BytesIO()
    wb.save(buf)
    name = f"GRN_{po}.xlsx" if po else "GRN_all_POs.xlsx"
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp
