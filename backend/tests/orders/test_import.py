"""Order import (.xlsx/.csv): column autodetection, matching buckets, limits, apply."""

import io
from typing import Any

from openpyxl import Workbook

from tests.orders.conftest import World

URL = "/api/shop/orders"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx(rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def upload(
    w: World, order: dict[str, Any], name: str, content: bytes, ctype: str = XLSX
) -> Any:
    return await w.shop.http.post(
        f"{URL}/import",
        data={"order_id": order["public_id"]},
        files={"file": (name, content, ctype)},
        headers={"X-CSRF-Token": w.shop.csrf or ""},
    )


async def test_xlsx_preview_buckets(world: World) -> None:
    order = await world.new_order()
    content = xlsx(
        [
            ["Штрихкод", "Наименование", "Блок", "Шт"],
            ["4780000000011", None, 1, 2],  # barcode -> pa (12 + 2)
            ["CA", None, 0, 1],  # code_1C of pa -> same product, quantities add up
            ["4780000000035", None, 0, 1],  # ambiguous (two products)
            [None, "soap beta", 0, 3],  # by exact name (case-insensitive)
            ["0000000000000", None, 0, 1],  # unknown
            ["4780000000073", None, 0, 1],  # hidden -> not orderable
            ["4780000000028", None, 1, 0],  # pb has no pack -> blocks refused
            ["4780000000028", None, "x", 1],  # bad number
        ]
    )
    r = await upload(world, order, "order.xlsx", content)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"] == 8
    matched = {m["product_ref"]: m for m in body["matched"]}
    assert set(matched) == {"pa", "pb"}
    assert matched["pa"]["rows"] == [2, 3] and matched["pa"]["quantity"] == 15
    assert matched["pa"]["blocks"] == 1 and matched["pa"]["pieces"] == 3
    assert matched["pb"]["quantity"] == 3 and matched["pb"]["price"] == 500
    (amb,) = body["ambiguous"]
    assert amb["row"] == 4 and {c["product_ref"] for c in amb["candidates"]} == {"pc1", "pc2"}
    reasons = {u["row"]: u["reason"] for u in body["unmatched"]}
    assert reasons == {6: "not_found", 7: "not_orderable", 8: "blocks_not_allowed", 9: "bad_number"}


async def test_preview_does_not_modify_the_order(world: World) -> None:
    order = await world.new_order()
    await upload(world, order, "o.xlsx", xlsx([["barcode", "qty"], ["4780000000028", 2]]))
    got = (await world.shop.get(f"{URL}/{order['public_id']}")).json()
    assert got["lines"] == [] and got["version"] == order["version"]


async def test_csv_semicolon_and_cp1251_and_english_headers(world: World) -> None:
    order = await world.new_order()
    text = "Штрих-код;Название;Блок;Штук\n4780000000028;Мыло;0;2\n"
    r = await upload(world, order, "o.csv", text.encode("cp1251"), "text/csv")
    assert r.status_code == 200 and r.json()["matched"][0]["product_ref"] == "pb"
    csv = b"barcode,qty\n4780000000028,4\n4780000000011,1\n"
    r = await upload(world, order, "o.csv", csv, "text/csv")
    got = {m["product_ref"]: m["quantity"] for m in r.json()["matched"]}
    assert got == {"pb": 4, "pa": 1}


async def test_headerless_two_column_file(world: World) -> None:
    order = await world.new_order()
    r = await upload(world, order, "o.csv", b"4780000000028,5\n", "text/csv")
    assert r.status_code == 200 and r.json()["matched"][0]["quantity"] == 5


async def test_barcode_stored_as_float_in_xlsx(world: World) -> None:
    order = await world.new_order()
    r = await upload(world, order, "o.xlsx", xlsx([["barcode", "qty"], [4780000000028.0, 1]]))
    assert r.json()["matched"][0]["product_ref"] == "pb"


async def test_unsupported_and_corrupt_files(world: World) -> None:
    order = await world.new_order()
    cases = [
        ("o.pdf", b"%PDF-1.4", "unsupported_type"),
        ("o.xlsx", b"not a zip at all", "bad_file"),
        ("o.xlsx", b"", "empty_file"),
        ("o.csv", b"PK\x03\x04junk", "bad_file"),
        ("o.csv", b"foo,bar\n1,2\n", "columns_not_detected"),
        ("o.csv", b"barcode,qty\n", "empty_file"),
        ("o.xlsx", b"PK\x03\x04" + b"\x00" * 50, "bad_file"),
    ]
    for name, content, code in cases:
        r = await upload(world, order, name, content)
        assert r.status_code == 422, (name, r.text)
        assert r.json()["detail"]["code"] == code, name


async def test_too_many_rows(world: World) -> None:
    order = await world.new_order()
    rows = "\n".join(f"478000000{i:04d},1" for i in range(2001))
    r = await upload(world, order, "o.csv", ("barcode,qty\n" + rows).encode())
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_many_rows"
    ok = "\n".join(f"478000000{i:04d},1" for i in range(2000))
    r = await upload(world, order, "o.csv", ("barcode,qty\n" + ok).encode())
    assert r.status_code == 200 and r.json()["rows"] == 2000


async def test_too_many_rows_xlsx(world: World) -> None:
    order = await world.new_order()
    content = xlsx([["barcode", "qty"], *[[f"x{i}", 1] for i in range(2001)]])
    r = await upload(world, order, "o.xlsx", content)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_many_rows"


async def test_file_larger_than_2mb(world: World) -> None:
    order = await world.new_order()
    big = b"barcode,qty\n" + b"1,1\n" * 600_000
    r = await upload(world, order, "o.csv", big)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "file_too_large"


async def test_import_scoping(world: World) -> None:
    order = await world.new_order()
    r = await upload(world, {"public_id": "00000000-0000-0000-0000-000000000000"}, "o.csv", b"a,b")
    assert r.status_code == 404
    r = await world.shop.http.post(
        f"{URL}/import",
        data={"order_id": order["public_id"]},
        files={"file": ("o.csv", b"barcode,qty\n1,1", "text/csv")},
    )
    assert r.status_code == 403  # CSRF


async def test_import_needs_price_type_and_editable_order(world: World) -> None:
    bare = (await world.shop.post(URL, {})).json()
    r = await upload(world, bare, "o.csv", b"barcode,qty\n4780000000028,1")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "price_type_required"
    draft = await world.draft()
    await world.submit(draft)
    r = await upload(world, draft, "o.csv", b"barcode,qty\n4780000000028,1")
    assert r.status_code == 409


async def test_apply_adds_to_existing_lines(world: World) -> None:
    order = await world.draft([("pb", 0, 2)])
    body = {
        "order_id": order["public_id"],
        "version": order["version"],
        "lines": [
            {"product_ref": "pb", "pieces": "1"},
            {"product_ref": "pa", "blocks": 1, "pieces": "0"},
        ],
    }
    r = await world.shop.post(f"{URL}/import/apply", body)
    assert r.status_code == 200, r.text
    lines = {ln["product_ref"]: ln["quantity"] for ln in r.json()["lines"]}
    assert lines == {"pb": 3, "pa": 12} and r.json()["version"] == order["version"] + 1


async def test_apply_validates_and_versions(world: World) -> None:
    order = await world.draft()
    base = {"order_id": order["public_id"], "version": order["version"]}
    r = await world.shop.post(
        f"{URL}/import/apply", {**base, "lines": [{"product_ref": "phidden", "pieces": "1"}]}
    )
    assert r.status_code == 422
    r = await world.shop.post(
        f"{URL}/import/apply",
        {**base, "version": 1, "lines": [{"product_ref": "pb", "pieces": "1"}]},
    )
    assert r.status_code == 409
    r = await world.shop.post(f"{URL}/import/apply", {**base, "lines": []})
    assert r.status_code == 422
