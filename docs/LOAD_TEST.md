# Load test

Tool: `backend/e2e/load.py` (asyncio + httpx, no extra dependencies), run inside the e2e
compose network against the fake Sales Doctor (`backend/e2e/fake_sd.py`), going through nginx
like real browsers. Never point it at production or at the real Sales Doctor.

```bash
make e2e-up && make e2e-seed LOAD=1        # 304 products, 61 shops
make load                                   # realistic profile (see below)
make load ARGS="--think-max 0"              # stress profile
```

Each virtual shop logs in with its own phone and loops: catalog page, search, category
filter, barcode lookup, create a draft, three line saves, then submits every 3rd loop (stock
is reserved in the fake SD) or discards the draft. The script prints p50/p95/p99 per
operation, status codes, Postgres (xact/s, rows/s, cache hit, connections) and Redis
(commands/s, clients) figures, and exits non-zero if a p95 budget is exceeded or more than
1 % of requests fail.

## Results (2026-10-14, 4 vCPU sandbox, everything on one host)

API: 4 uvicorn processes (`WEB_CONCURRENCY=4`), DB pool 5+5 per process.

### Realistic: 50 shops, 1–4 s between actions, SD latency 300 ms, 90 s

| Operation | Count | p50 ms | p95 ms | p99 ms | Budget p95 |
|---|---:|---:|---:|---:|---:|
| login | 50 | 26 | 46 | 49 | 800 |
| me | 50 | 27 | 42 | 50 | 300 |
| catalog | 394 | 27 | 40 | 46 | 400 |
| search | 197 | 32 | 49 | 63 | 500 |
| barcode | 197 | 24 | 35 | 42 | 300 |
| draft | 344 | 30 | 50 | 57 | 400 |
| lines (save) | 591 | 53 | 77 | 110 | 500 |
| submit | 50 | 689 | 1108 | 1359 | 2100 (incl. 2 SD calls) |

15.7 req/s, 0 errors. Postgres 49 xact/s, 26 connections, 100 % cache hit; Redis 66 cmd/s.

### Stress: 50 shops, no think time, SD latency 150 ms, 60 s

| Setup | req/s | catalog p95 | lines p95 | submit p50 / p95 | errors |
|---|---:|---:|---:|---:|---:|
| 1 API process (before) | 33 | 1507 | 1624 | 5780 / 7569 | 0 |
| 4 API processes | 68 | 760 | 1208 | 5496 / 7592 | 0 |
| 4 processes + submit stock fix | 75 | 815 | 1236 | 1835 / 2930 | 0 |

At this point the host is CPU-bound (API ~220 %, Postgres ~100 %, load generator, nginx on
4 vCPUs). Latency grows with queueing, but nothing fails: no 5xx, no 429, no lost orders (fake
SD order count = submit count).

## Findings fixed

1. **Single API process.** `uvicorn` ran one process, so the API capped at ~1 CPU core.
   Production runs `WEB_CONCURRENCY` processes (default 4 in `docker-compose.prod.yml`), and
   `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` are configurable so that
   `processes × (size + overflow)` stays below Postgres `max_connections`.
2. **Submits serialised per warehouse.** At submit, the fresh SD stock of the *whole
   warehouse* was upserted into `sd_stock` inside the submit transaction. That transaction
   stays open across the SD `setOrder` call, so every concurrent submit to the same
   warehouse waited on the same row locks (submit p50 5.5 s). Now only the order's own
   products are written, in a fixed (product id) order; the full warehouse refresh still
   runs as a background job after each send. Submit p50 dropped from 5.5 s to 1.8 s under stress.
   A regression test covers it (`tests/orders/test_submit.py`).

## Capacity note

Expected real load is tens of shops ordering during the day, with people pausing between
actions. That matches the realistic profile, which is far below the measured capacity. With the
real Sales Doctor, submit latency is dominated by SD itself: two calls, a full-warehouse
`getStock` and `setOrder`.
