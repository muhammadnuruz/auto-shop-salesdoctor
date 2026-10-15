"""Load test: N concurrent shops browsing the catalog, editing drafts and submitting orders.

Runs inside the e2e compose network against the fake SD (never against production)::

    make e2e-up && make e2e-seed LOAD=1
    docker compose -p autoshop-e2e -f docker-compose.yml -f docker-compose.e2e.yml \
        run --rm api python -m e2e.load --shops 50 --duration 120 --sd-latency 150

Each virtual shop logs in with its own phone, then loops: catalog page / search / category
filter / barcode lookup, create a draft, add lines in several saves, and every few loops
submits (which reserves stock in the fake SD). Prints per-operation latency percentiles plus
Postgres and Redis load figures, and exits non-zero when a p95 budget is exceeded or the
error rate is above 1 %.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import httpx
from redis.asyncio import Redis

from e2e.fixtures import LINK_SLUG, LOAD_CLIENTS, LOAD_PRODUCTS, load_phone

# p95 budgets (ms) per operation; SD-bound submit gets the injected SD latency on top
BUDGET_MS = {
    "login": 800,
    "me": 300,
    "catalog": 400,
    "search": 500,
    "barcode": 300,
    "draft": 400,
    "lines": 500,
    "submit": 1500,
}


@dataclass
class Stats:
    samples: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    statuses: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, op: str, ms: float, status: int) -> None:
        self.samples[op].append(ms)
        self.statuses[f"{op}:{status}"] += 1
        if not 200 <= status < 300:  # 409/422 too: "0 errors" must mean every step worked
            self.errors[op] += 1


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


class Shop:
    def __init__(self, base: str, origin: str, index: int, stats: Stats) -> None:
        self.http = httpx.AsyncClient(base_url=base, timeout=30, headers={"Origin": origin})
        self.index = index
        self.stats = stats
        self.csrf = ""

    think: tuple[float, float] = (0.0, 0.0)  # seconds between user actions

    async def call(self, op: str, method: str, url: str, **kw: Any) -> httpx.Response | None:
        if self.think[1] > 0:
            await asyncio.sleep(random.uniform(*self.think))
        headers = {"X-CSRF-Token": self.csrf} if method != "GET" and self.csrf else {}
        t0 = time.perf_counter()
        try:
            r = await self.http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError:
            self.stats.add(op, (time.perf_counter() - t0) * 1000, 0)
            return None
        self.stats.add(op, (time.perf_counter() - t0) * 1000, r.status_code)
        return r

    async def login(self) -> bool:
        r = await self.call(
            "login",
            "POST",
            "/api/shop/auth/login",
            json={"slug": LINK_SLUG, "phone": f"+{load_phone(self.index)}"},
        )
        if r is None or r.status_code != 200:
            return False
        self.csrf = r.json()["csrf_token"]
        return True

    async def session(self, deadline: float, submit_every: int) -> None:
        if not await self.login():
            return
        await self.call("me", "GET", "/api/shop/me")
        loop = 0
        while time.monotonic() < deadline:
            loop += 1
            await self.call("catalog", "GET", "/api/shop/catalog", params={"page": loop % 10 + 1})
            await self.call(
                "search",
                "GET",
                "/api/shop/catalog",
                params={"q": f"product {random.randint(0, 99)}"},
            )
            await self.call("catalog", "GET", "/api/shop/catalog", params={"category": "c1"})
            i = random.randrange(LOAD_PRODUCTS)
            await self.call("barcode", "GET", f"/api/shop/catalog/barcode/47900{i:08d}")
            r = await self.call("draft", "POST", "/api/shop/orders", json={})
            if r is None or r.status_code != 201:
                await asyncio.sleep(1)
                continue
            order = r.json()
            version = order["version"]
            lines: list[dict[str, Any]] = []
            for _ in range(3):  # three saves while "editing"
                ref = f"load_p{random.randrange(LOAD_PRODUCTS)}"
                if all(line["product_ref"] != ref for line in lines):
                    lines.append(
                        {"product_ref": ref, "blocks": random.randint(0, 2), "pieces": "3"}
                    )
                r = await self.call(
                    "lines",
                    "PUT",
                    f"/api/shop/orders/{order['public_id']}/lines",
                    json={"version": version, "lines": lines},
                )
                if r is None or r.status_code != 200:
                    break
                version = r.json()["version"]
            if loop % submit_every == 0:
                await self.call(
                    "submit",
                    "POST",
                    f"/api/shop/orders/{order['public_id']}/submit",
                    json={"version": version},
                )
            else:  # abandon the draft like a real user would, then think a little
                await self.call("draft", "DELETE", f"/api/shop/orders/{order['public_id']}")
            await asyncio.sleep(random.uniform(0.2, 1.0))

    async def close(self) -> None:
        await self.http.aclose()


async def db_snapshot(dsn: str) -> dict[str, float]:
    conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        row = await conn.fetchrow(
            "SELECT xact_commit + xact_rollback AS xacts, tup_returned, tup_fetched, "
            "tup_inserted + tup_updated + tup_deleted AS writes, blks_read, blks_hit "
            "FROM pg_stat_database WHERE datname = current_database()"
        )
        active = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        )
        if row is None:
            raise RuntimeError("pg_stat_database has no row for the current database")
        return {**{k: float(v) for k, v in dict(row).items()}, "connections": float(active)}
    finally:
        await conn.close()


async def redis_snapshot(url: str) -> dict[str, float]:
    r = Redis.from_url(url)
    try:
        info = await r.info()
        return {
            "commands": float(info["total_commands_processed"]),
            "clients": float(info["connected_clients"]),
            "used_memory_mb": float(info["used_memory"]) / 1_048_576,
        }
    finally:
        await r.aclose()


async def main_async(args: argparse.Namespace) -> int:
    stats = Stats()
    async with httpx.AsyncClient() as c:
        await c.post(f"{args.fake_sd}/__reset")
        await c.post(f"{args.fake_sd}/__latency", json={"ms": args.sd_latency})
    db0 = await db_snapshot(os.environ["DATABASE_URL"])
    r0 = await redis_snapshot(os.environ["REDIS_URL"])
    shops = [Shop(args.base, args.origin, i, stats) for i in range(args.shops)]
    for shop in shops:
        shop.think = (args.think_min, args.think_max)
    t0 = time.monotonic()
    deadline = t0 + args.duration

    async def run(shop: Shop, delay: float) -> None:
        await asyncio.sleep(delay)  # ramp up
        await shop.session(deadline, args.submit_every)

    await asyncio.gather(*(run(s, i * args.ramp / max(1, args.shops)) for i, s in enumerate(shops)))
    elapsed = time.monotonic() - t0
    db1 = await db_snapshot(os.environ["DATABASE_URL"])
    r1 = await redis_snapshot(os.environ["REDIS_URL"])
    for s in shops:
        await s.close()
    async with httpx.AsyncClient() as c:
        sd = (await c.get(f"{args.fake_sd}/__state")).json()
        await c.post(f"{args.fake_sd}/__latency", json={"ms": 0})

    total = sum(len(v) for v in stats.samples.values())
    errors = sum(stats.errors.values())
    print(f"\n{args.shops} shops, {elapsed:.0f} s, {total} requests ({total / elapsed:.1f} req/s)")
    print(
        f"{'operation':<10}{'count':>8}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}{'err':>6}  budget"
    )
    failed = False
    for op in BUDGET_MS:
        v = stats.samples.get(op, [])
        if not v:
            continue
        budget = BUDGET_MS[op] + (args.sd_latency * 2 if op == "submit" else 0)
        p95 = pct(v, 95)
        ok = p95 <= budget
        failed |= not ok
        print(
            f"{op:<10}{len(v):>8}{statistics.median(v):>9.0f}{p95:>9.0f}{pct(v, 99):>9.0f}"
            f"{max(v):>9.0f}{stats.errors.get(op, 0):>6}  {'OK ' if ok else 'OVER'} {budget} ms"
        )
    print("\nstatus codes:", dict(sorted(stats.statuses.items())))
    print(f"fake SD: {len(sd['orders'])} orders, calls {sd['calls']}")
    span = max(elapsed, 1)
    print(
        "postgres: {:.0f} xact/s, {:.0f} rows read/s, {:.0f} rows written/s, cache hit {:.1%}, "
        "connections {:.0f} -> {:.0f}".format(
            (db1["xacts"] - db0["xacts"]) / span,
            (db1["tup_fetched"] - db0["tup_fetched"]) / span,
            (db1["writes"] - db0["writes"]) / span,
            (db1["blks_hit"] - db0["blks_hit"])
            / max(1.0, db1["blks_hit"] - db0["blks_hit"] + db1["blks_read"] - db0["blks_read"]),
            db0["connections"],
            db1["connections"],
        )
    )
    print(
        "redis: {:.0f} cmd/s, clients {:.0f} -> {:.0f}, memory {:.1f} MB".format(
            (r1["commands"] - r0["commands"]) / span,
            r0["clients"],
            r1["clients"],
            r1["used_memory_mb"],
        )
    )
    error_rate = errors / max(1, total)
    print(f"error rate: {error_rate:.2%}")
    if error_rate > 0.01:
        failed = True
    accepted = stats.statuses.get("submit:200", 0)
    if len(sd["orders"]) != accepted:  # every accepted submit is exactly one SD order
        print(f"MISMATCH: {accepted} submits answered 200 but fake SD holds {len(sd['orders'])}")
        failed = True
    if args.report_only and error_rate <= 0.01 and len(sd["orders"]) == accepted:
        return 0  # shared CI runners: latency is reported, not enforced
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="http://web")  # through nginx, like real users
    ap.add_argument("--origin", default="http://127.0.0.1:8080")
    ap.add_argument("--fake-sd", default="http://fake-sd:9000")
    ap.add_argument("--shops", type=int, default=50)
    ap.add_argument("--duration", type=int, default=120, help="seconds")
    ap.add_argument("--ramp", type=float, default=10.0, help="seconds to start all shops")
    ap.add_argument("--submit-every", type=int, default=3, help="submit every Nth loop")
    ap.add_argument("--sd-latency", type=int, default=150, help="fake SD delay per call, ms")
    ap.add_argument("--think-min", type=float, default=0.0, help="min seconds between actions")
    ap.add_argument("--think-max", type=float, default=0.0, help="max seconds (0 = stress)")
    ap.add_argument(
        "--report-only", action="store_true", help="do not fail on p95 budgets (CI runners)"
    )
    args = ap.parse_args()
    if args.shops > LOAD_CLIENTS:
        raise SystemExit(f"--shops must be <= {LOAD_CLIENTS} (seeded load shops)")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
