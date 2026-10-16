"""Render a waybill (see ``invoice.build_invoice``) as an A4 PDF, product photos included."""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import Flowable

log = structlog.get_logger(__name__)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
MAX_IMAGES = 300
MAX_IMAGE_BYTES = 2_000_000
THUMB = 11 * mm
NARROW_NBSP = chr(0x202F)  # thousands separator that never wraps

LABELS: dict[str, dict[str, str]] = {
    "ru": {
        "title": "НАКЛАДНАЯ № {n}",
        "date": "Дата",
        "shipment": "Дата отгрузки",
        "client": "Клиент",
        "phone": "Телефон",
        "address": "Адрес",
        "agent": "Агент",
        "price_type": "Тип цены",
        "warehouse": "Склад",
        "trade": "Направление",
        "code": "Код заказа",
        "no": "№",
        "photo": "Фото",
        "product": "Товар",
        "unit": "Ед.",
        "blocks": "Блоки",
        "qty": "Кол-во",
        "price": "Цена",
        "sum": "Сумма",
        "total": "Итого",
        "discount": "Скидка",
        "due": "К оплате",
        "comment": "Комментарий",
        "released": "Отпустил",
        "received": "Получил",
        "page": "Стр. {p}",
        "local": "Sales Doctor недоступен — накладная составлена по копии заказа в Auto-Shop.",
        "currency": "сум",
    },
    "uz": {
        "title": "YUK XATI № {n}",
        "date": "Sana",
        "shipment": "Yetkazish sanasi",
        "client": "Mijoz",
        "phone": "Telefon",
        "address": "Manzil",
        "agent": "Agent",
        "price_type": "Narx turi",
        "warehouse": "Ombor",
        "trade": "Savdo yo'nalishi",
        "code": "Buyurtma kodi",
        "no": "№",
        "photo": "Rasm",
        "product": "Mahsulot",
        "unit": "O'lch.",
        "blocks": "Blok",
        "qty": "Soni",
        "price": "Narx",
        "sum": "Summa",
        "total": "Jami",
        "discount": "Chegirma",
        "due": "To'lovga",
        "comment": "Izoh",
        "released": "Topshirdi",
        "received": "Qabul qildi",
        "page": "{p}-bet",
        "local": "Sales Doctor javob bermadi — yuk xati Auto-Shop'dagi nusxa asosida tuzildi.",
        "currency": "so'm",
    },
}

_fonts_ready = False


def _fonts() -> tuple[str, str]:
    """DejaVu (Cyrillic + Uzbek Latin) when installed, else the built-in Helvetica."""
    global _fonts_ready
    regular, bold = FONT_DIR / "DejaVuSans.ttf", FONT_DIR / "DejaVuSans-Bold.ttf"
    if not (regular.exists() and bold.exists()):
        return "Helvetica", "Helvetica-Bold"
    if not _fonts_ready:
        pdfmetrics.registerFont(TTFont("DejaVu", str(regular)))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(bold)))
        _fonts_ready = True
    return "DejaVu", "DejaVu-Bold"


def fmt_date(value: Any) -> str:
    """``2026-10-16 09:46:06`` -> ``16.10.2026 09:46``; ``2026-10-17`` -> ``17.10.2026``."""
    text = str(value or "")
    day, _, clock_part = text.partition(" ")
    parts = day.split("-")
    if len(parts) != 3 or not all(x.isdigit() for x in parts):
        return text
    out = f"{parts[2]}.{parts[1]}.{parts[0]}"
    return f"{out} {clock_part[:5]}" if clock_part else out


def _printable(font: str) -> Callable[[str], str]:
    """Drop characters the font has no glyph for (decorative symbols in SD names)."""
    try:
        # TTF fonts expose their cmap on .face; the built-in Type 1 fonts do not
        cmap = getattr(pdfmetrics.getFont(font), "face").charToGlyph  # noqa: B009
    except (AttributeError, KeyError):
        return lambda text: text
    return lambda text: "".join(ch for ch in text if ord(ch) in cmap or ch in "\n\t ").strip()


def fmt_num(value: Any) -> str:
    """1 614 000 / 2.5 — thin-space thousands, no trailing zeros."""
    if value is None or value == "":
        return ""
    d = Decimal(str(value)).quantize(Decimal("0.001")).normalize()
    whole, _, frac = f"{d:f}".partition(".")
    sign = "-" if whole.startswith("-") else ""
    whole = whole.lstrip("-")
    groups: list[str] = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    out = sign + NARROW_NBSP.join(groups or ["0"])
    return f"{out},{frac}" if frac else out


async def fetch_images(
    urls: list[str], *, allowed_origin: str, user_agent: str
) -> dict[str, bytes]:
    """Download product thumbnails (only from the SD host; failures are just skipped)."""
    wanted = list(dict.fromkeys(u for u in urls if u))[:MAX_IMAGES]
    origin = urlsplit(allowed_origin)
    wanted = [
        u
        for u in wanted
        if (p := urlsplit(u)).scheme == origin.scheme and p.netloc == origin.netloc
    ]
    out: dict[str, bytes] = {}
    if not wanted:
        return out
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(
        timeout=5, headers={"User-Agent": user_agent}, follow_redirects=False
    ) as http:

        async def one(url: str) -> None:
            async with sem:
                try:
                    r = await http.get(url)
                except httpx.HTTPError:
                    return
                if (
                    r.status_code == 200
                    and r.headers.get("content-type", "").startswith("image/")
                    and len(r.content) <= MAX_IMAGE_BYTES
                ):
                    out[url] = r.content

        await asyncio.gather(*(one(u) for u in wanted))
    return out


def _image(data: bytes | None) -> Flowable | str:
    if not data:
        return ""
    try:
        img = Image(io.BytesIO(data))
        ratio = min(THUMB / img.imageWidth, THUMB / img.imageHeight)
        img.drawWidth, img.drawHeight = img.imageWidth * ratio, img.imageHeight * ratio
        return img
    except Exception:  # a broken/unsupported image must not break the waybill
        return ""


def render_pdf(inv: dict[str, Any], images: dict[str, bytes], lang: str = "ru") -> bytes:
    L = LABELS.get(lang, LABELS["ru"])
    font, bold = _fonts()
    base = ParagraphStyle("base", fontName=font, fontSize=8.5, leading=10.5)
    small = ParagraphStyle("small", parent=base, fontSize=7.5, leading=9)
    head = ParagraphStyle("head", parent=base, fontName=bold, fontSize=8, leading=10)
    title = ParagraphStyle(
        "title", parent=base, fontName=bold, fontSize=15, leading=19, alignment=TA_CENTER
    )
    warn = ParagraphStyle("warn", parent=small, textColor=colors.HexColor("#9a5b00"))

    printable = _printable(font)

    def p(text: Any, style: ParagraphStyle = base) -> Paragraph:
        safe = (
            printable(str(text if text is not None else ""))
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return Paragraph(safe, style)

    story: list[Flowable] = [p(L["title"].format(n=inv["number"]), title), Spacer(1, 4 * mm)]
    if inv.get("source") == "local":
        story += [p(L["local"], warn), Spacer(1, 2 * mm)]

    client = inv.get("client") or {}
    info = [
        (L["date"], fmt_date(inv.get("date"))),
        (L["shipment"], fmt_date(inv.get("shipment_date"))),
        (L["client"], client.get("name")),
        (L["phone"], client.get("phone")),
        (L["address"], client.get("address")),
        (L["agent"], inv.get("agent")),
        (L["price_type"], inv.get("price_type")),
        (L["warehouse"], inv.get("warehouse")),
        (L["trade"], inv.get("trade")),
        (L["code"], inv.get("code")),
    ]
    info_table = Table(
        [[p(k + ":", head), p(v)] for k, v in info if v],
        colWidths=[34 * mm, None],
        hAlign="LEFT",
    )
    info_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 0.6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0.6),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story += [info_table, Spacer(1, 4 * mm)]

    num_style = ParagraphStyle("num", parent=base, alignment=2)  # right
    rows: list[list[Any]] = [
        [
            p(L["no"], head),
            p(L["photo"], head),
            p(L["product"], head),
            p(L["unit"], head),
            p(L["blocks"], head),
            p(L["qty"], head),
            p(L["price"], head),
            p(L["sum"], head),
        ]
    ]
    for line in inv["lines"]:
        rows.append(
            [
                p(line["position"]),
                _image(images.get(line.get("thumb_url") or "")),
                p(line["name"]),
                p(line.get("unit") or ""),
                p(fmt_num(line.get("blocks")) if line.get("blocks") else "", num_style),
                p(fmt_num(line["quantity"]), num_style),
                p(fmt_num(line["price"]), num_style),
                p(fmt_num(line["sum"]), num_style),
            ]
        )
    head_bold = ParagraphStyle("hb", parent=num_style, fontName=bold)
    rows.append(
        [
            "",
            "",
            p(L["total"], head),
            "",
            "",
            p(fmt_num(inv["total_qty"]), head_bold),
            "",
            p(f"{fmt_num(inv['total_sum'])}\u00a0{L['currency']}", head_bold),
        ]
    )
    totals_from = len(rows) - 1
    if Decimal(str(inv.get("discount_sum") or 0)) > 0:
        due = f"{fmt_num(inv['total_due'])}\u00a0{L['currency']}"
        rows.append(
            [
                "",
                "",
                p(L["discount"], head),
                "",
                "",
                "",
                "",
                p(fmt_num(inv["discount_sum"]), head_bold),
            ]
        )
        rows.append(["", "", p(L["due"], head), "", "", "", "", p(due, head_bold)])

    width = A4[0] - 30 * mm
    widths = [8 * mm, 14 * mm, None, 12 * mm, 14 * mm, 16 * mm, 19 * mm, 28 * mm]
    fixed = sum(w for w in widths if w is not None)
    widths[2] = width - fixed
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#555555")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                ("BACKGROUND", (0, totals_from), (-1, -1), colors.HexColor("#f6f6f6")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 1), (1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story += [table, Spacer(1, 5 * mm)]
    if inv.get("comment"):
        story += [p(f"{L['comment']}: {inv['comment']}"), Spacer(1, 5 * mm)]
    signatures = Table(
        [
            [
                p(f"{L['released']}: ____________________"),
                p(f"{L['received']}: ____________________"),
            ]
        ],
        colWidths=[width / 2, width / 2],
    )
    signatures.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(KeepTogether([Spacer(1, 6 * mm), signatures]))

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont(font, 7)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawRightString(
            A4[0] - 15 * mm, 9 * mm, f"{inv.get('code') or ''}  ·  {L['page'].format(p=doc.page)}"
        )
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=L["title"].format(n=inv["number"]),
        author="Auto-Shop",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


async def pdf_response(
    inv: dict[str, Any], *, sd_origin: str, user_agent: str, lang: str
) -> Response:
    images = await fetch_images(
        [line.get("thumb_url") or "" for line in inv["lines"]],
        allowed_origin=sd_origin,
        user_agent=user_agent,
    )
    data = await run_in_threadpool(render_pdf, inv, images, lang)
    name = f"nakladnaya-{inv['number']}.pdf"
    return Response(
        data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
