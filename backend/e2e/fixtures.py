"""Deterministic data shared by the fake SD and the seeder (E2E + load tests)."""

WAREHOUSE = "d0_1"
PRICE_TYPE = "d0_2"
AGENT = "d0_3"
TRADE = "1"
LINK_SLUG = "e2e-shop-link"
SHOP_PHONE = "998902996625"  # «Тест Дилшод» in the real SD; here a local fixture only
SHOP_PHONE_KEY = SHOP_PHONE[3:]

# (sd_id, name, barcode, pack, price, stock, category)
PRODUCTS: list[tuple[str, str, str | None, int | None, int, int, str]] = [
    ("d0_2784", "DEMAX toothbrush", "4780000000011", None, 1300, 50, "c1"),
    ("e2e_p2", "Cream Alpha 50ml", "4780000000028", 12, 1000, 120, "c1"),
    ("e2e_p3", "Soap Beta", "4780000000035", None, 500, 8, "c2"),
    ("e2e_p4", "Shampoo Gamma", None, 6, 2500, 30, "c2"),
]
LOAD_PRODUCTS = 300  # extra generated products for load tests (--load)
LOAD_CLIENTS = 60

INITIAL_STOCK: dict[str, float] = {p[0]: float(p[5]) for p in PRODUCTS} | {
    f"load_p{i}": 1_000_000.0 for i in range(LOAD_PRODUCTS)
}


def load_phone(i: int) -> str:
    """Distinct, valid-looking phones for generated load-test shops (+998 93 ii 123 45)."""
    return f"99893{i:02d}12345"
