"""End-to-end order check against a running stack (WRITES to the live Sales Doctor with --live).

Flow: shop login -> draft -> +1 piece of one product -> submit -> print SD id and stock
before/after -> shop cancel -> admin delete (removes the order from SD).

Without ``--live`` nothing is sent: the script only prints the plan (dry run).

Environment
  E2E_BASE_URL          API base URL (default http://localhost:8000)
  E2E_SHOP_PHONE        phone of a TEST client (required with --live)
  E2E_LINK_SLUG         slug of an access link that client may use (required with --live)
  E2E_ADMIN_CREDENTIALS path to a file with the admin login (required with --live). Accepted:
                        two lines ``username=...`` / ``password=...`` (also user/login keys), or a
                        single ``username:password`` line. The file is never printed.
  E2E_PRODUCT           product SD id (default d0_2784)
  E2E_PRICE_TYPE        price type SD id (default: the first one the link allows)

Usage:  python scripts/e2e_order.py            # dry run
        python scripts/e2e_order.py --live     # really creates, cancels and deletes one order
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import httpx

DEFAULT_PRODUCT = "d0_2784"


def read_credentials(path: Path) -> tuple[str, str]:
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    pairs: dict[str, str] = {}
    for ln in lines:
        for sep in ("=", ":"):
            if sep in ln:
                key, _, value = ln.partition(sep)
                pairs[key.strip().lower()] = value.strip().strip("'\"")
                break
    user = next((pairs[k] for k in ("username", "user", "login") if k in pairs), None)
    password = pairs.get("password") or pairs.get("pass")
    if user is None and len(lines) == 1 and ":" in lines[0]:
        user, _, password = lines[0].partition(":")
    if not user or not password:
        raise SystemExit("could not read username/password from the credentials file")
    return user, password


class Session:
    """httpx client that echoes the CSRF token on writes and fails loudly on HTTP errors."""

    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base, timeout=60)
        self.csrf = ""

    def call(self, method: str, url: str, expect: set[int] | None = None, **kw: Any) -> Any:
        headers = {"X-CSRF-Token": self.csrf} if self.csrf and method != "GET" else {}
        r = self.http.request(method, url, headers=headers, **kw)
        if r.status_code not in (expect or {200, 201, 204}):
            raise SystemExit(f"{method} {url} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else None


def stock_of(shop: Session, warehouse: str, product: str) -> Any:
    body = shop.call(
        "GET",
        "/api/shop/stock",
        params={"warehouse": warehouse, "products": product, "fresh": "1"},
    )
    return body["items"][0]["quantity"] if body["items"] else None


def plan(product: str) -> list[str]:
    return [
        "1. POST /api/shop/auth/login               (phone + link slug)",
        "2. POST /api/shop/orders                   (draft; header auto-filled / first allowed)",
        f"3. POST .../lines/scan product_ref={product} (+1 piece)",
        f"4. GET  /api/shop/stock?fresh=1            (stock BEFORE for {product})",
        "5. POST .../submit                         (setOrder status from the link, code WEB-<id>)",
        "6. GET  /api/shop/stock?fresh=1            (stock AFTER: expected BEFORE - 1)",
        "7. POST .../cancel                         (setOrder status 5, stock restored)",
        "8. POST /api/admin/auth/login + DELETE /api/admin/orders/<id> (setDeletedOrder)",
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--live", action="store_true", help="really talk to SD (default: dry run)")
    args = ap.parse_args()
    product = os.environ.get("E2E_PRODUCT", DEFAULT_PRODUCT)
    base = os.environ.get("E2E_BASE_URL", "http://localhost:8000")

    if not args.live:
        print("DRY RUN (add --live to execute). Planned steps:")
        print("\n".join(plan(product)))
        return 0

    try:
        phone, slug = os.environ["E2E_SHOP_PHONE"], os.environ["E2E_LINK_SLUG"]
        creds_path = Path(os.environ["E2E_ADMIN_CREDENTIALS"])
    except KeyError as exc:
        raise SystemExit(f"missing environment variable {exc.args[0]}") from None
    admin_user, admin_password = read_credentials(creds_path)

    shop = Session(base)
    login = shop.call("POST", "/api/shop/auth/login", json={"slug": slug, "phone": phone})
    if login.get("choose"):
        raise SystemExit("the phone maps to several clients; use a phone with exactly one client")
    shop.csrf = login["csrf_token"]

    order = shop.call("POST", "/api/shop/orders", json={})
    price_type = os.environ.get("E2E_PRICE_TYPE")
    if price_type is None and order["price_type"] is None:
        me = shop.call("GET", "/api/shop/me")
        price_type = me["options"]["price_types"]["options"][0]["id"]
    if price_type:
        order = shop.call(
            "PATCH",
            f"/api/shop/orders/{order['public_id']}",
            json={"version": order["version"], "price_type": price_type},
        )
    pid, warehouse = order["public_id"], (order["warehouse"] or {}).get("id")
    if warehouse is None:
        raise SystemExit("no warehouse selected for the order (link offers a choice)")
    print(f"draft {order['code']} ({pid}) warehouse={warehouse} price_type={price_type}")

    order = shop.call(
        "POST",
        f"/api/shop/orders/{pid}/lines/scan",
        json={"version": order["version"], "product_ref": product, "pieces": 1},
    )
    print(f"line added: total={order['totals']['sum']} problems={order.get('problems')}")

    before = stock_of(shop, warehouse, product)
    submitted = shop.call(
        "POST", f"/api/shop/orders/{pid}/submit", json={"version": order["version"]},
        expect={200, 202},
    )  # fmt: skip
    print(f"submitted: state={submitted['state']} sd_id={submitted['sd_id']}")
    after = stock_of(shop, warehouse, product)
    print(f"stock {product}: before={before} after={after}")

    cancelled = shop.call("POST", f"/api/shop/orders/{pid}/cancel")
    print(f"cancelled: state={cancelled['state']} sd_status={cancelled['sd_status']}")
    restored = stock_of(shop, warehouse, product)
    print(f"stock {product}: after cancel={restored}")

    admin = Session(base)
    res = admin.call(
        "POST", "/api/admin/auth/login", json={"username": admin_user, "password": admin_password}
    )
    admin.csrf = res["csrf_token"]
    deleted = admin.call("DELETE", f"/api/admin/orders/{pid}")
    print(f"admin delete: state={deleted['state']}")

    ok = (
        before is not None
        and after == before - 1
        and restored == before
        and deleted["state"] == "deleted"
    )
    print("RESULT:", "OK" if ok else "CHECK MANUALLY")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
