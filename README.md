# Auto-Shop <-> Sales Doctor

Do'konlar telefon raqami bilan kirib, buyurtmani Sales Doctor (SD) API orqali yuboradigan veb-ilova.
Reja: `PLAN.md`. **Phase 1-2**: backend skeleti, SD klienti, sinxronizatsiya. **Phase 3**: do'kon (telefon) va admin auth, RBAC, linklar, audit.

## Stack
Python 3.12, FastAPI, SQLAlchemy 2 (async) + Alembic, PostgreSQL 16, Redis 7, arq (worker),
httpx, structlog.

## Ishga tushirish
```bash
cp .env.example .env      # SD_* va parollarni to'ldiring
make up                   # docker compose up -d --build
curl localhost:8000/health
curl localhost:8000/health/ready
# SD ulanishini tekshirish: GET /api/admin/health/sd (admin sessiyasi, sync.read)
```

## Test / lint
```bash
make test     # docker ichida pytest (`test` servisi, dev bog'liqliklar bilan) (tarmoqsiz: respx + fakeredis)
make lint     # ruff + mypy
make fmt
```

## Muhim eslatmalar
- SD serveri standart Python User-Agent'ga 403 qaytaradi: `SD_USER_AGENT` har doim yuboriladi.
- Har `login` oldingi tokenni bekor qiladi: token Redis'da (`sd:auth`), login faqat Redis lock
  (`sd:auth:lock`) ostida. SD'da shu loyiha uchun alohida API foydalanuvchi oching.
- Migratsiyalar: `make migrate`, `make revision m="..."`.
- `ENV` berilmasa `production` deb olinadi (qat'iy tekshiruv). `/docs`, `/redoc`, `/openapi.json` faqat
  `ENV=local|test` da yoqiladi. `api` porti faqat `127.0.0.1` ga bog'langan.
- **Yozish (set*) so'rovlari:** tarmoq/5xx/timeout holatida qayta urinilmaydi (faqat ulanish o'rnatilmagan
  bo'lsa) -- `SDOutcomeUnknownError` olinsa, qayta yuborishdan oldin `getOrder` (code_1C) bilan tekshiring.
- `getOrder` faqat sana oralig'i bilan (`date_from` + `date_to`) chaqiriladi; filtrsiz SD faqat status 1 qaytaradi.

## Auth (Phase 3)
- Do'konlar: `/api/shop/*` (faqat telefon, admin yaratgan link `/s/<slug>` orqali). Admin: `/api/admin/*`
  (parol + ixtiyoriy TOTP, RBAC). Sync endpointlari: `/api/admin/sync/*` (`sync.read` / `sync.run`).
- Cookie'lar: `shop_session` (`/api/shop`) va `admin_session` (`/api/admin`), HttpOnly, SameSite=Lax, Secure
  (faqat `ENV=local|test` da `COOKIE_SECURE=false` mumkin). Holatni o'zgartiruvchi so'rovlar `X-CSRF-Token`
  (login/`me` javobida) va `Origin`/`Referer` (`ALLOWED_ORIGINS`) tekshiruvidan o'tadi.
- `.env`: `APP_SECRET_KEY` (production'da majburiy, >= 32 belgi), `ALLOWED_ORIGINS`, `TRUSTED_PROXIES`
  (`X-Forwarded-For` faqat shu proksilardan ishoniladi), ixtiyoriy `TOTP_ENC_KEY`, `RL_*` limitlar.
- Birinchi superadmin: `make superadmin u=admin` (parol so'raladi) yoki `ADMIN_PASSWORD=... make superadmin u=admin`.

## Hujjatlar / Docs
- `HANDOFF.md` — holat, qarorlar, qolgan ishlar (start here)
- `docs/DEPLOY.md` — production (TLS, backup, monitoring, CI)
- `docs/LOAD_TEST.md` — yuklama testi natijalari
- `docs/operator-manual-uz.md` — admin panel bo'yicha operator qo'llanmasi (o'zbekcha)
- `docs/shop-guide.md` — do'konlar uchun qisqa eslatma (RU/UZ)

## E2E / yuklama testi
```bash
make e2e              # docker stek + soxta SD + Playwright (frontend/e2e)
make e2e-seed LOAD=1 && make load   # 50 do'kon, realistik profil
make e2e-down
```
