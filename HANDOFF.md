# Handoff — Auto-Shop ↔ Sales Doctor

Status as of 2026-10-14 (updated at the end of the second session). Read this file fully, then `PLAN.md` (Uzbek; sections 1.1–1.3 hold the owner's decisions and verified live API facts), `README.md`, `frontend/README.md`.

## 1. What the product is

A web app where shops (= "clients" in the dealer's Sales Doctor (SD) CRM, server `https://buxdiler.salesdoc.io/api/v2/`) open an admin-created link `/s/<slug>`, log in with **phone number only** (owner's decision: no SMS, no PIN), and create orders exactly like SD's "Новый заказ" screen (agent / price type / warehouse / trade direction, barcode scanner, add from list, import from file, blocks + pieces). Submitting pushes the order to SD via `setOrder`, which **immediately reserves stock** in SD. An admin panel gives full control (unlimited links with per-link rules, orders, products, clients, sync, admins, audit).

## 2. Owner decisions (binding)

| Topic | Decision |
|---|---|
| Links | Pre-configured access links; unlimited; QR codes |
| Admin edits an order | Must also update SD (resend `setOrder` with same `code_1C`) |
| Stock | Must decrease immediately → orders are sent with status 1 (Новый) by default (verified: status 1 already reserves stock; status 2 optional per link) |
| Agent / warehouse / price type / trade | Shop chooses, limited to what the link allows |
| 1C | None — we own `code_1C` (`WEB-000123`), which makes `setOrder` idempotent |
| Auth for shops | Phone only — compensate with rate limits, suspicious-phone blocking, per-client block, audit |
| Prices | Shops can never change prices; server always uses SD price for the chosen price type |
| Admin min/max order sum | Admins bypass link min/max on edit/resend; stock validation still applies |
| UI language | Russian default + Uzbek (Latin) switch |

## 3. Verified Sales Doctor API facts (live)

- Single endpoint, JSON body with `"method"`. **Requests with the default Python/httpx User-Agent get HTTP 403** — the client sends a browser-like UA.
- **Every `login` invalidates the previous token**; tokens never expire otherwise. Token lives in Redis with a distributed lock (`app/integrations/salesdoctor`). Never call `login` ad hoc from scripts against production without reason.
- Errors arrive as HTTP 200 with `error.code` 401/402/400, or a plain-string error (`getStockForDate` only works 20:00–08:00).
- `getOrder`: `include` goes **inside** `filter`; with no filter it returns only status-1 orders. Rows: `orderProducts[].product.SD_id`, `quantity` (pieces), `block`.
- `setOrder` with the same `code_1C` updates the same order; quantity edits adjust stock; status 5 restores stock; cancelling a status-2 order is booked as a return; `setDeletedOrder` leaves it visible in `getOrder` with status 5.
- `getBalance` is paginated (limit 1000); clients without transactions are absent (balance 0). `balance` = sum of `by-currency` (all UZS payment types).
- `getPaymentType` result key is `currency`; `getAgent.user` may be list or object; `getBrand` is legitimately empty; `getFilials` empty → no `filial` param.
- Data: 2682 products (921 active, 540 with barcode, 41 duplicate barcodes), 6482 clients (996 without phone, 810 phones shared by several clients, 69 suspicious placeholder numbers like 999999999), 21 price types (only `type=sale` are exposed; "Приходная цена" is purchase/cost — never shown or stored).
- **Test fixtures in live SD** (use only these for live write tests, and only with explicit owner approval): client «Тест Дилшод» `w3_7448` (phone `998902996625`), agent MANSUR `d0_3`, warehouse Основной склад `d0_1`, price type НАЛ `d0_2`, trade direction `1`, product DEMAX toothbrush `d0_2784` (1 300 UZS). A previous manual test left SD order `d0_53145` (`WEB-TEST-0001`) in status 5 — harmless.

## 4. Architecture & repo layout

- `backend/` — FastAPI (Python 3.12 in Docker), SQLAlchemy 2 async + Alembic (head **0007**), Postgres 16 (pg_trgm), Redis 7, arq worker. Key packages: `app/integrations/salesdoctor` (client, errors incl. `SDOutcomeUnknownError`, token store), `app/sync` (mirror sync with completeness checks + deactivation cap), `app/domain` (phone, link_settings, permissions, quantity, order_rules), `app/services` (sessions, eligibility, admin_auth, audit, retention, catalog, orders, order_flow, order_jobs, order_import, stock_fresh), `app/api` (shop, shop_catalog, shop_orders, admin/*, schemas.py), `app/workers/settings.py` (crons), `app/cli.py` (`create-superadmin`), `scripts/e2e_order.py` (live E2E, dry-run unless `--live`).
- `frontend/` — Vite + React 18 + TS strict + MUI v6 + TanStack Query v5 + react-router v6 + RHF/zod + i18next (ru/uz) + MSW/Vitest; `npm run gen:api` regenerates `src/api/schema.d.ts` from the running API. Docker: nginx serving SPA + `/api` proxy (`web` service, profile `web`, 127.0.0.1:8080).
- Commands: `docker compose up -d --build` · `make migrate` · `make test` · `make lint` · `make superadmin u=NAME` · `docker compose --profile web up -d --build --no-deps web` · frontend: `npm run lint && npm run typecheck && npm test -- --run && npm run build`.
- Secrets: `.env` (SD_BASE_URL, SD_LOGIN, SD_PASSWORD, APP_SECRET_KEY, TOTP_ENC_KEY, DB creds; **not in git**), `.admin-credentials` (dev superadmin; **not in git, never print it**). In a fresh environment recreate `.env` from `.env.example` + secrets provided by the owner, then `make superadmin u=admin`.

## 5. Quality status at handoff

| Area | State |
|---|---|
| Backend tests | 687+ passing; ruff + format + mypy (app, tests, e2e) clean |
| Frontend tests | 101 passing (vitest); eslint/typecheck/prettier/build clean |
| E2E | 7 Playwright tests (desktop + mobile) green against the docker stack with the fake SD |
| Load | 50 shops: realistic profile all p95 within budget, 0 errors (`docs/LOAD_TEST.md`) |
| Reviews done & all findings fixed | SD client (18), DB/sync (17), auth security (11), order-flow integrity (12), admin remainder (10 + 10 in a second round), E2E/load/prod (see git log) |
| Live verification | Read-only SD calls, full mirror sync, shop login on a smoke link. **Not yet done: live end-to-end order through the app (item 2 below, needs owner approval)** |

Working method that produced this quality (keep it): implement → independent review agent → fix every finding with a regression test → orchestrator re-runs tests itself → table report to the owner (owner reads Uzbek; reports in Uzbek, code/docs in English).

## 6. Remaining work (in order)

1. ~~Frontend adaptation~~ — done.
2. **Live E2E order (requires the owner's explicit approval in chat before running):** create a temporary link for «Тест Дилшод», run `backend/scripts/e2e_order.py --live` (login → draft → +1 `d0_2784` → submit → verify SD order + stock −1 → cancel → stock restored → admin delete → archive link). Report stock before/after. Needs the real secrets in the environment (SD_BASE_URL, SD_LOGIN, SD_PASSWORD, APP_SECRET_KEY, TOTP_ENC_KEY).
3. ~~Admin panel remainder~~ — done: audit log (API + UI), sales reports + xlsx, settings page (SD status, DB-configurable sync intervals, phone-flag threshold, Telegram notifier with delivery health), link statistics.
4. ~~Playwright E2E~~ — done (`frontend/e2e`, `backend/e2e/fake_sd.py`, `make e2e`).
5. ~~Load test~~ — done (`backend/e2e/load.py`, `make load`); fixed: multi-process API, submit no longer locks the whole warehouse's stock rows.
6. ~~Production readiness~~ — done (`docker-compose.prod.yml`, `deploy/`, `docs/DEPLOY.md`, `.github/workflows/ci.yml`). Still to do on a real server: DNS + `deploy/init-letsencrypt.sh`, off-server copy of `BACKUP_DIR`, `SENTRY_DSN`, Telegram bot.
7. ~~Docs~~ — done (`docs/operator-manual-uz.md`, `docs/shop-guide.md`, Uzbek UI terminology pass).

Next ideas (not requested yet): CI run on GitHub (workflow runs on PRs to main), owner walkthrough of the UI in Uzbek, per-product `getStock` filtering if SD supports it (submit currently reads the whole warehouse).

### Local cloud-sandbox notes
- Docker Hub may rate-limit pulls: pull from `mirror.gcr.io/library/<image>` and `docker tag`.
- Builds behind the session's TLS-intercepting proxy need its CA: a local, uncommitted compose override (scratchpad) adds the CA to the build stages only.

## 7. Rules

- Never print or commit secrets (`.env`, `.admin-credentials`, SD token). Never send credentials to third-party services.
- **No live SD write calls (`setOrder`, `setStatus`, `setDeletedOrder`, `set*`) without the owner's explicit approval in chat**, and then only with the test fixtures above. Read-only SD calls are fine.
- Keep migrations forward-only (new revision per change) with roundtrip test; keep API changes backward compatible or coordinate frontend in the same change.
- Commit in small logical commits on a feature branch; open PRs; don't push to main directly.
