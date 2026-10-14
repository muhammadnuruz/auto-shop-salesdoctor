# Auto-Shop frontend

React 18 + TypeScript (strict) + Vite, MUI v6, TanStack Query v5, react-router v6 (data router),
react-hook-form + zod, i18next (ru default, uz-Latin), dayjs, notistack, qrcode.
Tests: Vitest + Testing Library + MSW.

## Run

```bash
npm ci
npm run dev          # http://localhost:5173, /api proxied to http://127.0.0.1:8000
```

`VITE_API_PROXY` overrides the proxy target. The API allows the origin `http://localhost:5173`;
keep using `localhost` (not 127.0.0.1) in the browser so cookies and CORS line up.

| Script              | Purpose                                               |
| ------------------- | ----------------------------------------------------- |
| `npm run lint`      | ESLint (zero warnings)                                |
| `npm run typecheck` | `tsc -b --noEmit`                                     |
| `npm test -- --run` | Vitest + MSW                                          |
| `npm run build`     | typecheck + production build into `dist/`             |
| `npm run gen:api`   | regenerate `src/api/schema.d.ts` from `/openapi.json` |
| `npm run format`    | Prettier                                              |

## Auth model

HttpOnly, path-scoped cookies (`/api/shop`, `/api/admin`). Requests use `credentials: 'include'`.
Login and `/me` return `csrf_token`, kept in memory only and sent as `X-CSRF-Token` on mutating
requests (a stale token is refreshed once via `/me`). 401 sends the user to the relevant login,
403 shows a no-permission screen, 409/422 map to form fields, 429 shows the `Retry-After` wait.

## Layout

- `src/api` typed fetch layer (`http.ts`), endpoint modules, query key factory (`keys.ts`)
- `src/features/shop` landing, phone login, choose-shop, home, orders list (`/s/:slug/orders`),
  order screen (`/s/:slug/order/new` creates a server-side draft, then `/s/:slug/orders/:id`)
- `src/features/orders` components shared by shop and admin (lines table/cards, qty steppers,
  catalog dialog, summary, helpers)
- `src/features/admin` layout, guards, links, clients/phones, sync, admins, profile, dashboard,
  orders (list + detail/editor/timeline), products (grid + drawer)
- `src/components`, `src/hooks`, `src/lib`, `src/theme`, `src/i18n`, `src/test`

## Reference data (link form)

`useRefs()` in `src/features/admin/queries.ts` loads `GET /api/admin/refs`: named groups
`{warehouses, price_types, agents, trade_directions, categories, brands}` as `[{id, name}]`
(active only) and `territories` / `client_categories` as `[{id, clients}]`, shown as
"id (клиентов: N)". If the endpoint is missing or fails, the form falls back to free-text chips.

## Order screen notes

- Every edit is applied locally at once, debounced 300 ms, then sent with the order `version`;
  `409 version_conflict` refetches and re-applies the latest intent once (`useShopOrder`).
- Submit: 200 sent, 202 / outcome_unknown polls every 3 s, 422 maps errors per line / header,
  502-503 offers retry, 409 order_busy retries a few times.
- Settings (columns, scanner sound, scanner auto-focus) live in localStorage
  (`autoshop.order.settings`). The table windows rows above 200 lines.
- Camera scanning loads `@zxing/browser` on demand (mobile only).

## Docker

```bash
docker compose --profile web up -d --build web   # or: make web
```

Serves the SPA on http://127.0.0.1:8080 (nginx, SPA fallback, gzip, security headers) and proxies
`/api/*` and `/health` to `api:8000`. Note the API's CORS allow-list contains the dev origin only;
the proxy keeps the browser same-origin so this is not an issue.

Screenshots of public flows live in `docs/screenshots/`.
