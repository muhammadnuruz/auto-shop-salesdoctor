"""Waybill (накладная): read back from Sales Doctor, local fallback, cache, PDF, access."""

from typing import Any

import httpx
import respx

from app.services.invoice import order_number
from app.services.invoice_pdf import fetch_images, fmt_date, fmt_num
from tests.api.conftest import ApiFactory, make_settings
from tests.orders.conftest import World


async def sent(w: World) -> dict[str, Any]:
    o = await w.draft([("pa", 1, 2), ("pb", 0, 1)])
    r = await w.submit(o)
    assert r.status_code == 200 and r.json()["state"] == "sent", r.text
    body: dict[str, Any] = r.json()
    return body


def url(o: dict[str, Any], suffix: str = "") -> str:
    return f"/api/shop/orders/{o['public_id']}/invoice{suffix}"


async def test_not_sent_order_has_no_invoice(world: World) -> None:
    o = await world.draft()
    r = await world.shop.get(url(o))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_sent"


async def test_invoice_is_read_back_from_sales_doctor(world: World) -> None:
    o = await sent(world)
    r = await world.shop.get(url(o))
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["source"] == "sd" and inv["warning"] is None
    assert inv["sd_id"] == "d0_53000" and inv["number"] == "53000" and inv["code"] == o["code"]
    assert inv["client"]["name"] == "Тест Дилшод"
    assert inv["warehouse"] and inv["price_type"] and inv["agent"]
    pa, pb = inv["lines"]
    assert pa["blocks"] == 1 and pb["blocks"] == 0  # 14 pcs of 12 = 1 whole block
    assert (pa["product_ref"], pa["name"], pa["quantity"], pa["price"], pa["sum"]) == (
        "pa",
        "Cream Alpha",
        14,
        1000,
        14000,
    )
    assert pa["pack_quantity"] == 12 and pb["sum"] == 500
    assert inv["total_qty"] == 15 and inv["total_sum"] == 14500 and inv["total_due"] == 14500
    assert len(world.sd_server.calls_of("getOrder")) == 1


async def test_invoice_uses_sd_numbers_not_ours(world: World) -> None:
    """SD is the source of truth: its prices, quantities, blocks, discount and dates win."""
    o = await sent(world)
    stored = world.sd_server.orders[o["code"]]
    stored["orderProducts"] = [
        {
            "product": {"SD_id": "pa", "name": "Cream Alpha (SD name)"},
            "quantity": 24,
            "price": 950,
            "discount": 0,
            "summa": 22800,
            "block": 2,
        }
    ]
    stored["sd_extra"] = {
        "dateCreate": "2026-10-15 09:46:06",
        "dateDocument": "2026-10-16 09:00:00",
        "totalSumma": 22800,
        "discountSumma": 800,
        "totalSummaAfterDiscount": 22000,
        "comment": "SD comment",
    }
    inv = (await world.shop.get(url(o))).json()
    (line,) = inv["lines"]
    assert line["name"] == "Cream Alpha (SD name)" and line["blocks"] == 2
    assert (line["quantity"], line["price"], line["sum"]) == (24, 950, 22800)
    assert inv["date"] == "2026-10-15 09:46:06" and inv["shipment_date"] == "2026-10-16"
    assert (inv["total_sum"], inv["discount_sum"], inv["total_due"]) == (22800, 800, 22000)
    assert inv["comment"] == "SD comment"


async def test_invoice_falls_back_to_local_copy(world: World) -> None:
    o = await sent(world)
    world.sd_server.orders_down = True
    inv = (await world.shop.get(url(o))).json()
    assert inv["source"] == "local" and inv["warning"] == "sd_unavailable"
    assert [x["product_ref"] for x in inv["lines"]] == ["pa", "pb"] and inv["total_sum"] == 14500

    world.sd_server.orders_down = False
    world.sd_server.orders.clear()
    inv = (await world.shop.get(url(o))).json()
    assert inv["source"] == "local" and inv["warning"] == "sd_not_found"


async def test_invoice_is_cached_briefly(world: World) -> None:
    o = await sent(world)
    assert (await world.shop.get(url(o))).status_code == 200
    assert (await world.shop.get(url(o))).status_code == 200
    assert (await world.shop.get(url(o, ".pdf"))).status_code == 200
    assert len(world.sd_server.calls_of("getOrder")) == 1


async def test_invoice_pdf(world: World) -> None:
    o = await sent(world)
    r = await world.shop.get(url(o, ".pdf"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and len(r.content) > 1000
    assert 'filename="nakladnaya-53000.pdf"' in r.headers["content-disposition"]
    r = await world.shop.get(url(o, ".pdf"), params={"lang": "uz"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert (await world.shop.get(url(o, ".pdf"), params={"lang": "en"})).status_code == 422


async def test_invoice_access(world: World, app_factory: ApiFactory) -> None:
    o = await sent(world)
    anon = await app_factory(make_settings())
    assert (await anon.get(url(o))).status_code == 401
    a = f"/api/admin/orders/{o['public_id']}/invoice"
    assert (await world.admin.get(a)).json()["number"] == "53000"
    r = await world.admin.get(a + ".pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    await world.seed.admin("inv-viewer", role="viewer")
    v = await app_factory(make_settings(), sd=world.sd, jobs=world.jobs)
    assert (await v.admin_login("inv-viewer")).status_code == 200
    assert (await v.get(a)).status_code == 200
    # another shop's order is not visible to this shop
    ghost = "/api/shop/orders/00000000-0000-0000-0000-000000000000/invoice"
    assert (await world.shop.get(ghost)).status_code == 404


def test_whole_blocks() -> None:
    from decimal import Decimal

    from app.services.invoice import _whole_blocks

    assert _whole_blocks(Decimal(2), 10, 0.2) == 0  # SD's fractional block is ignored
    assert _whole_blocks(Decimal(25), 12, 2.08) == 2
    assert _whole_blocks(Decimal(24), None, 2) == 2 and _whole_blocks(Decimal(5), None, 0.5) == 0


def test_order_number_and_amount_format() -> None:
    assert order_number("d0_30676", "WEB-1") == "30676"
    assert order_number("x30676", "WEB-1") == "x30676"
    assert order_number(None, "WEB-000001") == "WEB-000001"
    assert fmt_date("2026-10-16 09:46:06") == "16.10.2026 09:46"
    assert fmt_date("2026-10-17") == "17.10.2026" and fmt_date(None) == "" and fmt_date("x") == "x"
    assert fmt_num(1614000) == "1 614 000"
    assert fmt_num("2.500") == "2,5" and fmt_num(0) == "0" and fmt_num(None) == ""
    assert fmt_num(-1500) == "-1 500"


@respx.mock
async def test_fetch_images_only_from_sd_host() -> None:
    good = "https://sd.example/upload/a.jpg"
    respx.get(good).mock(
        return_value=httpx.Response(
            200, content=b"\xff\xd8img", headers={"content-type": "image/jpeg"}
        )
    )
    html = "https://sd.example/upload/b.jpg"
    respx.get(html).mock(
        return_value=httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
    )
    missing = "https://sd.example/upload/c.jpg"
    respx.get(missing).mock(return_value=httpx.Response(404))
    out = await fetch_images(
        [good, html, missing, "https://evil.example/x.jpg", "http://sd.example/upload/a.jpg", ""],
        allowed_origin="https://sd.example",
        user_agent="UA",
    )
    assert out == {good: b"\xff\xd8img"}
