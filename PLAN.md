# Auto-Shop ↔ Sales Doctor — loyiha rejasi

> Manba: `Sales Doctor Public API 2.postman_collection.json` (95 ta so'rov, 11 bo'lim) tahlili.
> Server: `https://buxdiler.salesdoc.io/api/v2/`

---

## 1. Maqsad (qisqacha)

Do'kon (Sales Doctor'dagi **Клиент**) veb-saytga **telefon raqami** bilan kiradi (SMS yo'q) →
skrinshotdagidek "Новый заказ" oynasida sklad, agent, narx turi, savdo yo'nalishini tanlaydi →
mahsulotlarni **barcode skaner / qidiruv / ro'yxatdan** qo'shadi (blok + dona) → saqlaganda
Sales Doctor'da **buyurtma (setOrder)** yaratiladi va **sklad qoldig'i avtomatik kamayadi**.

---

## 1.1 Qabul qilingan qarorlar (2026-10-14)

| # | Savol | Qaror | Rejaga ta'siri |
|---|---|---|---|
| 1 | "Link" | Sozlamalari oldindan belgilangan kirish havolasi | 5.1 bo'yicha |
| 2 | Admin buyurtmani tahrirlasa | SD'dagi buyurtma ham o'zgaradi | `setOrder` qayta yuboriladi (shu `SD_id`/`code_1C` bilan → update) |
| 3 | Qoldiq | **Darhol kamayadi** | buyurtma kerakli statusda yuboriladi (status 0-bosqich testida aniqlanadi) |
| 4 | Agent/sklad/narx turi | **Do'kon o'zi tanlaydi** (link ruxsat bergan ro'yxatdan) | |
| 5 | 1C | **Yo'q** | `code_1C` = bizning noyob kodimiz (`WEB-<id>`) → idempotentlik tabiiy: takroriy `setOrder` yangi buyurtma yaratmaydi, mavjudini yangilaydi |
| 6 | PIN | **Yo'q, faqat telefon** | rate-limit + qalbaki raqamlarni chiqarib tashlash + admin bloklashi (pastga qarang) |
| 7 | Narxni o'zgartirish | **Yo'q** | narx har doim SD `getPrice`dan, backend'da qayta tekshiriladi |

## 1.2 Jonli tekshiruv natijalari (0-bosqich, faqat o'qish)

| Topilma | Qiymat | Xulosa |
|---|---|---|
| **User-Agent bloklash** | Python standart UA → **HTTP 403**; brauzer UA → 200 | backend HTTP klientida UA sarlavhasi majburiy |
| Filiallar | `getFilials` → bo'sh | `filial` parametri kerak emas |
| Skladlar | Основной склад (faol), SALES СКЛАД (nofaol) | |
| Savdo yo'nalishlari | Parfum Brend, Parfum Brend1 | |
| Narx turlari | 21 ta, faol: НАЛ, А-ЦЕНА, Бот цена, НАЛИЧНИЙ $, БРОН, ЦЕНА БОНУС, **Приходная цена (type=purchase)** | ⚠️ `type=purchase` (tannarx) do'konlarga **hech qachon** ko'rsatilmaydi; faqat `type=sale` va `active=Y` |
| Mahsulotlar | 2682 ta, faol 921; faollardan **540 tasida barcode** (59%); 41 ta takroriy barcode; 2156 tasida packQuantity>1 | barcode'siz mahsulotlar uchun admin'da qo'shimcha barcode biriktirish + nom bo'yicha qidiruv shart; takroriy barcode → tanlash oynasi |
| Klientlar | 6482 ta (hammasi faol); **996 tasida telefon yo'q**; 4357 noyob raqam; **810 raqam bir necha klientga tegishli** | do'kon tanlash ekrani shart |
| Qalbaki raqamlar | `+998 99 999 99 99` → 12 ta do'kon, `89 999 99 99` → 10 ta, ... | ⚠️ faqat telefon bilan kirishda bu raqamlar **bloklanadi** (bir xil raqamlar, 4+ do'konga tegishli raqamlar — admin ro'yxatida qo'lda tasdiqlanadi) |
| Telefon formati | `+998 (91) 921-67-22` va `+998905146655` aralash | oxirgi 9 raqam kalit — ishlaydi |
| Hajm/tezlik | getProduct 500/sahifa ≈0.9s; getClient ≈1.4s; getStock (to'liq) 0.7s / 350KB; getBalance 2.7s / 585KB; getPrice 0.5s | to'liq sinxron < 30s; qoldiqni har buyurtma oldidan to'g'ridan-to'g'ri olish mumkin |
| Buyurtma (getOrder) | `orderProducts[].block` maydoni bor, `quantity` — dona hisobida | blok = quantity / packQuantity |
| Balans | `balance` + `by-currency` (valyuta bo'yicha) | |

## 1.3 Test buyurtma natijalari (2026-10-14, ruxsat bilan)

Klient «Тест Дилшод» (w3_7448), agent MANSUR (d0_3), Основной склад, НАЛ, DEMAX tish cho'tkasi (d0_2784), `code_1C = WEB-TEST-0001` → SD_id `d0_53145`.

| Qadam | Qoldiq (getStock) | Natija |
|---|---|---|
| Boshlang'ich | 576 | |
| `setOrder` status=1, 1 dona | **575** | ✅ "Новый" statusida ham sotuvga mavjud qoldiq **darhol** kamayadi |
| Xuddi shu `code_1C` bilan qayta `setOrder` | 575 | ✅ takror yaratilmadi, o'sha `SD_id` qaytdi → **idempotentlik ishlaydi** |
| `setOrder` status=2 (update) | 575 | ✅ status o'zgardi, qoldiq ikkinchi marta ayirilmadi |
| `setOrder` miqdor 2 ga (admin tahriri) | **574** | ✅ tahrir SD'da ham o'zgaradi, qoldiq farq bo'yicha moslashadi |
| `setOrder` status=5 (bekor) | **576** | ✅ qoldiq to'liq qaytdi; ⚠️ yuborilgan (2) buyurtmani bekor qilish SD'da **vozvrat** sifatida yoziladi (`totalReturnsSumma`) |
| `setDeletedOrder` | 576 | ✅ `completed:1`; buyurtma `getOrder`da status=5 bilan ko'rinishda qoladi |

**Qo'shimcha topilmalar:**
- `getOrder` da `include` parametri `filter` ichida beriladi: `"filter": {"include": "all", ...}`.
- `getStockForDate` (jismoniy qoldiq) **faqat 20:00–08:00** oralig'ida ishlaydi — kunduzi foydalanib bo'lmaydi.
- Klient `getBalance` ro'yxatida faqat tranzaksiyasi bo'lsa chiqadi → yo'q bo'lsa balans = 0.

**Status bo'yicha tavsiya:** do'kon buyurtmalari **status=1** bilan yuboriladi — qoldiq darhol kamayadi (qaror №3 bajariladi), bekor qilish esa toza (vozvrat yaratmaydi). Status=2 linklar sozlamasida ixtiyoriy variant sifatida qoladi.

---

## 2. API tahlili — muhim xulosalar

### 2.1 Umumiy protokol
- Barcha so'rovlar bitta URL'ga: `POST https://buxdiler.salesdoc.io/api/v2/`, body'da `"method": "..."`.
- Avtorizatsiya: `method: login` → `{userId, token}`. Keyingi har bir so'rovda `"auth": {userId, token}`.
- ⚠️ **Har bir `login` chaqiruvi oldingi tokenni bekor qiladi**, token muddati cheksiz.
  → Token faqat backend'da, bitta joyda saqlanadi (DB/Redis), qayta login faqat xatoda va lock bilan.
  → Agar `OPERATOR` foydalanuvchisi boshqa integratsiyada (masalan 1C) ishlatilsa, ular bir-birining tokenini o'chiradi.
  **Tavsiya: Sales Doctor'da shu loyiha uchun alohida API foydalanuvchi ochish.**
- Filial: ko'p so'rovlarda `"filial": {"filial_id": ...}` — `getFilials` orqali aniqlanadi.
- ID'lar: har bir obyekt `CS_id / SD_id / code_1C` uchligi bilan aniqlanadi (EntityID).
- Javob: `{status, result, pagination}`; yozish metodlarida `result.completed / error` + `error.data[...]` ichida qatorma-qator xatolar.

### 2.2 Loyihaga kerakli metodlar

| Vazifa | Metod | Izoh |
|---|---|---|
| Kirish (server) | `login` | token backend'da |
| Filiallar | `getFilials` | filial_id |
| Do'konlar (klientlar) | `getClient` | ⚠️ **telefon bo'yicha filtr yo'q** — faqat CS/SD/1C id, onlyNew, sana. Shuning uchun lokal sinxron + telefon indeksi kerak |
| Qo'shimcha telefonlar | `setClientPhone` (faqat POST) | o'qish metodi kollektsiyada yo'q — tekshirish kerak |
| Mahsulotlar | `getProduct` | `barCode`, `packQuantity` (blokdagi dona), `unit`, `category`, `brand`, `trade`, `imageID` |
| Rasm | `Get Photo` | `imageID` bo'yicha |
| Kategoriya / brend / birlik | `getProductCategory`, `getBrand`, `getUnit` | jadvaldagi "Категория" ustuni |
| Skladlar | `getWarehouse` | sklad tanlash |
| Qoldiq (sotuvga mavjud) | `getStock` | sklad → products[{SD_id, quantity}] — "На складе" ustuni |
| Narx turlari | `getPriceType` | "Тип цены" (НАЛ, OPTOM...) |
| Narxlar | `getPrice` | priceType bo'yicha barcha mahsulot narxlari |
| Agentlar | `getAgent` | klientga biriktirilgan agentlar `getClient.agents[]` da |
| Savdo yo'nalishi | `getTradeDirection` | "Направление торговли" |
| To'lov turi | `getPaymentType` | |
| Balans | `getBalance` | barcha klientlar ro'yxati — "Баланс: 0" uchun |
| **Buyurtma yaratish** | `setOrder` | client, agent, priceType, warehouse, trade, orderProducts[{product, quantity, price, discountSumma}], status, comment, `auto_attach_expeditor` |
| Buyurtmalar tarixi | `getOrder` | status, dateCreate/dateLoad/dateUpdate filtrlari |
| Status o'zgartirish | `setStatus` | 1 Новый, 2 Отгружен, 3 Доставлен, 4 Возврат, 5 Отменен |
| Bekor qilish / o'chirish | `setDeletedOrder` | |
| Qaytarish | `setOrderDefect` / `getOrderDefect` | 2-bosqich |

Loyihaga kerak bo'lmaydiganlar (faqat admin/1C uchun): `setProduct`, `setPrice`, `setPurchase`, `setMovement*`,
`setExcretion`, `setVsExchange`, kassa prixod/rasxod, foto-hisobotlar, vizitlar.

### 2.3 "Skladdan avtomatik ayirish" qanday ishlaydi
- Bizning tomonda sklad hisobini **yuritmaymiz** — haqiqat manbai Sales Doctor.
- `setOrder` (status=1 "Новый") → SD'da buyurtma paydo bo'ladi va `getStock` (*"доступные для продаж"*) qoldig'i kamayishi kutiladi (rezerv).
- Jismoniy hisobdan chiqish odatda **"Отгружен" (2)** statusida bo'ladi.
- ⚠️ **Bosqich 0 da jonli tekshiriladi:** status=1 da qoldiq kamayadimi? Agar yo'q bo'lsa — buyurtmani darhol `status: 2/3` bilan yuborish yoki keyin `setStatus` chaqirish varianti tanlanadi (bu biznes qaror: kim yuk jo'natishni tasdiqlaydi?).
- `setExcretion` (списание) sotuv uchun **ishlatilmaydi** — u brak/yo'qotish uchun, moliyaviy hisobotni buzadi.

### 2.4 Topilgan xavf / noaniqliklar
1. ~~**`code_1C` va 1C integratsiyasi**~~ — *hal bo'ldi: 1C yo'q, `code_1C` idempotentlik uchun ishlatiladi (1.1-bo'lim).* Eski matn: `getOrder` default `include: "sd"` — ya'ni `code_1C` bo'sh buyurtmalarni qaytaradi. Agar dilerning 1C'si buyurtmalarni shu yo'l bilan olsa, biz `code_1C` to'ldirsak 1C ularni **ko'rmay qoladi**. → `code_1C` bo'sh qoldiriladi, idempotentlik lokal (bizning `order.uuid` + saqlangan `SD_id`) orqali.
2. **Takroriy buyurtma**: tarmoq uzilsa `setOrder` ikki marta ketishi mumkin. → lokal holat mashinasi (`draft → sending → sent / failed`), qayta yuborishdan oldin `getOrder` (client + dateCreate oralig'i) bilan tekshirish.
3. **Telefon formati** SD'da erkin matn (`"tel": "123"`, `"+99890"`). → normalizatsiya: faqat raqamlar, oxirgi 9 raqam (`90 123 45 67`) kalit sifatida.
4. **Bir telefon — bir nechta klient** bo'lishi mumkin → login'dan keyin "do'kon tanlash" ekrani.
5. **Faqat telefon bilan kirish xavfsiz emas** (raqamni bilgan har kim buyurtma bera oladi). Qaror: PIN yo'q. Yumshatish: rate-limit, qalbaki/umumiy raqamlarni bloklash, admin panelda klientni bloklash, barcha harakatlar logi.
6. **Rate limit / hajm**: `getStock`, `getPrice`, `getBalance` filtrsiz — butun bazani qaytaradi. → fon sinxronizatsiyasi + kesh, har so'rovda SD'ga bormaymiz.

---

## 3. Arxitektura

```
[Brauzer / TSD skaner / telefon kamera]
        │  HTTPS (session cookie)
        ▼
[Frontend: React + Vite + TS]  ──►  [Backend: FastAPI (Python 3.12)]
                                         │
                     ┌───────────────────┼──────────────────────┐
                     ▼                   ▼                      ▼
              [PostgreSQL]        [Redis: kesh, lock,     [SalesDoctorClient
              klientlar, mahsulot  rate-limit, navbat]     (httpx, retry,
              narx, qoldiq kesh,                            token-lock)]
              buyurtmalar, log           ▲                      │
                                         │                      ▼
                                  [Worker: arq/Celery]   buxdiler.salesdoc.io
                                  sinxron: har 5–15 min
```

- **Nega lokal kesh**: getClient'da telefon filtri yo'q; barcode bo'yicha qidiruv ham yo'q → mahsulot/klientni lokal indekslash shart.
- **Qoldiq**: savatga qo'shishda va yuborishdan oldin `getStock` dan yangilanadi (sklad bo'yicha, 30–60 s kesh).
- **Token**: Redis'da, `login` faqat `SalesDoctorClient` ichida, distributed lock bilan (bir vaqtda ikki login bo'lmasligi uchun).

### Texnologiya (taklif)
- Backend: **FastAPI**, SQLAlchemy 2 + Alembic, httpx, pydantic-settings, arq (yoki Celery)
- DB: PostgreSQL 16 (`pg_trgm` — nom bo'yicha qidiruv)
- Frontend: **React + TypeScript + Vite**, TanStack Query, MUI (skrinshotdagi Material ko'rinishiga mos)
- Barcode: USB/TSD skaner = klaviatura emulyatsiyasi (input + Enter); telefon kamerasi uchun `@zxing/browser`
- Deploy: Docker Compose (api, worker, db, redis, nginx)

---

## 4. Ma'lumotlar modeli (lokal)

| Jadval | Asosiy maydonlar |
|---|---|
| `sd_client` | sd_id, cs_id, code_1c, name, firm_name, address, tel_raw, **phone_key** (index), active, agents(jsonb), expeditor_id, synced_at |
| `sd_client_phone` | client_id, phone_key — qo'shimcha raqamlar |
| `sd_product` | sd_id, code_1c, name, **barcode** (index), pack_quantity, unit_id, category_id, brand_id, trade_id, image_id, active |
| `sd_warehouse`, `sd_price_type`, `sd_trade`, `sd_agent`, `sd_category`, `sd_unit` | ma'lumotnomalar |
| `sd_price` | price_type_id, product_id, price |
| `sd_stock` | warehouse_id, product_id, quantity, synced_at |
| `shop_session` | client_id, device, created_at, expires_at |
| `order` | uuid, client_id, agent_id, warehouse_id, price_type_id, trade_id, date_doc, date_load, comment, **status_local**, sd_id, sd_status, total, error_json |
| `order_line` | order_id, product_id, blocks, pieces, **quantity** (=blocks·pack+pieces), price, discount, comment |
| `audit_log` | actor (admin/do'kon), action, entity, old/new (jsonb), ip, created_at |
| `sd_request_log` | method, request (token yashirilgan), response, ms, status |
| `admin_user` | login, password_hash (argon2), totp_secret, role, permissions(jsonb), active, last_login |
| `access_link` | slug, name, settings(jsonb: sklad/narx turi/agent/trade, ruxsatlar, cheklovlar), allowed_scope, starts_at, ends_at, max_uses, active, branding, created_by |
| `link_client` | link_id, client_id (tanlangan klientlar rejimi uchun) |
| `client_settings` | client_id, blocked, pin_hash, note |
| `product_override` | product_id, hidden, extra_barcodes[] |
| `app_setting` | key, value (SD credentials — shifrlangan) |
`order` jadvaliga qo'shiladi: `link_id`, `created_by_admin_id` (admin tahrirlasa).

---

## 5. Ekranlar (skrinshot asosida)

1. **Login** — telefon (+998 mask) → (ixtiyoriy PIN) → bir nechta do'kon bo'lsa tanlash.
2. **Bosh sahifa** — balans (`getBalance`), oxirgi buyurtmalar (`getOrder`), "Yangi buyurtma" tugmasi.
3. **Yangi buyurtma** (skrinshotdagidek):
   - Sarlavha: do'kon nomi, telefon, **Баланс**
   - Maydonlar: Дата заявки (avto), Дата отгрузки, **Агент** (klientga biriktirilganlar), **Тип цены** (+tarix tugmasi), **Склад**, **Направление торговли**
   - Asboblar: **Добавить из списка** (modal: kategoriya filtri, qidiruv), **Импорт из файла** (Excel: barcode/kod + miqdor), **ТСД сканер** inputi, **Поиск в таблице**, **Настройки** (ustunlarni yashirish)
   - Jadval: #, Фото, Товар, Категория, Цена, На складе, **В блоках ±**, **В штуках ±**, Сумма, Комментарий
   - O'ng yuqori: kategoriya bo'yicha jami (Кол-во / Объём / Сумма) + Итого
   - Validatsiya: miqdor > qoldiq bo'lsa qizil; narxi yo'q mahsulot qo'shilmaydi
   - Qoralama avtomatik saqlanadi (localStorage + server)
4. **Buyurtmalar ro'yxati** — status, summa, filtr (sana/status), ko'rish, (Новый bo'lsa) bekor qilish.
5. **Admin panel** — to'liq boshqaruv, batafsil: **5.1-bo'lim**.

---

## 5.1 Admin panel (to'liq boshqaruv)

Alohida yo'l: `/admin`, alohida login (login + parol + ixtiyoriy 2FA/TOTP), do'konlar sessiyasidan butunlay ajratilgan.

### Rollar va ruxsatlar (RBAC)
| Rol | Huquqlar |
|---|---|
| **Superadmin** | hammasi, adminlarni yaratish/o'chirish, SD sozlamalari (server, login, filial) |
| **Admin** | linklar, do'konlar, buyurtmalar, sozlamalar |
| **Operator** | buyurtmalarni ko'rish/tasdiqlash/bekor qilish, do'konlarni ko'rish |
| **Kuzatuvchi** | faqat o'qish va hisobotlar |
Ruxsatlar granulyar (`links.create`, `orders.cancel`, ...) — rolga qo'shimcha ravishda alohida berilishi mumkin.

### Linklar (cheksiz miqdorda)
Har bir link — do'konlar kiradigan alohida kirish nuqtasi: `https://<domen>/s/<slug>`
| Sozlama | Tavsif |
|---|---|
| Nom, slug | `bux-markaz`, yoki avtomatik tasodifiy (`/s/k8Fq2x`) |
| **Sklad** | qat'iy belgilangan / ro'yxatdan tanlash / do'kon o'zi tanlaydi |
| **Narx turi** | qat'iy yoki ruxsat etilganlar ro'yxati |
| **Agent** | qat'iy / klientga biriktirilgan agent / tanlash |
| **Savdo yo'nalishi** | qat'iy yoki ro'yxat |
| Kimlar kira oladi | barcha klientlar / tanlangan klientlar / hudud (territory) / klient kategoriyasi bo'yicha |
| Buyurtma statusi | status 1 (Новый) yoki 2 (Отгружен — darhol skladdan chiqim) |
| Cheklovlar | min/max buyurtma summasi, qoldiqdan ortiq buyurtma taqiqlanadimi, narxni o'zgartirish ruxsati, chegirma ruxsati |
| Ko'rinadigan mahsulotlar | barchasi / kategoriya / brend / tanlangan mahsulotlar |
| Muddat | boshlanish–tugash sanasi, maksimal kirishlar soni |
| Holat | faol / to'xtatilgan; PIN talab qilinadimi |
| Ko'rinish | sarlavha, logo, rang, xush kelibsiz matni |
Har bir link uchun: **QR-kod** (yuklab olish/chop etish), nusxalash, statistika (kirishlar, buyurtmalar, summa), klonlash, arxivlash.
Har bir buyurtma qaysi link orqali kelgani saqlanadi.

### Boshqa bo'limlar
- **Dashboard** — bugungi/haftalik buyurtmalar, summa, eng faol do'konlar va mahsulotlar, link bo'yicha kesim, SD sinxron holati, xatoli buyurtmalar soni.
- **Buyurtmalar** — barcha buyurtmalar (filtr: sana, status, do'kon, link, sklad, agent); tafsilot; tahrirlash (Новый bo'lsa) → `setOrder` qayta; status o'zgartirish (`setStatus`); bekor qilish/o'chirish (`setDeletedOrder`); xatolilarni qayta yuborish; Excel/PDF eksport; chop etish (nakladnoy).
- **Do'konlar (klientlar)** — SD'dan sinxronlangan ro'yxat; qo'shimcha telefonlar; bloklash/ochish; PIN'ni tiklash; qaysi linklarga ruxsat; sessiyalarni majburan tugatish; buyurtmalar tarixi va balans.
- **Mahsulotlar** — ro'yxat, qoldiq (sklad bo'yicha), narxlar (narx turi bo'yicha); saytda yashirish; barcode'i yo'q mahsulotlarni ko'rsatish; lokal qo'shimcha barcode biriktirish (SD'da bo'lmasa).
- **Sklad qoldig'i** — sklad × mahsulot jadvali, kam qolganlar ogohlantirishi.
- **Sinxronizatsiya** — har bir obyekt bo'yicha oxirgi vaqt, davomiylik, xatolar; qo'lda ishga tushirish; interval sozlamasi.
- **Sozlamalar** — SD ulanishi (server, login/parol — shifrlangan, filial), standart qiymatlar, telefon formati, sayt nomi/logo, bildirishnomalar (Telegram bot: yangi buyurtma, xato).
- **Adminlar** — yaratish, rol berish, bloklash, parolni tiklash.
- **Audit log** — kim, qachon, nima qildi (eski/yangi qiymat), IP; SD so'rov/javob logi (token yashirilgan).
- **Hisobotlar** — davr bo'yicha sotuv: do'kon, mahsulot, kategoriya, link, agent kesimida; eksport.

### Skaner mantig'i
- Input doimo fokusda; skaner kodni yozib `Enter` bosadi → `barcode` bo'yicha lokal qidiruv →
  topilsa: qator bor bo'lsa `+1 dona`, yo'q bo'lsa yangi qator; topilmasa — ovozli signal + xabar.
- Kamera rejimi (telefon): ZXing, EAN-13/EAN-8/Code128.
- Barcode bo'sh mahsulotlar uchun `code_1C` yoki nom bo'yicha qidiruv zaxira.

---

## 6. Buyurtma yuborish oqimi

```
[Saqlash] → backend validatsiya (klient aktiv, agent klientga biriktirilgan, narxlar SD bilan mos,
            qoldiq yetarli — getStock yangilanadi)
         → order.status_local = sending  (uuid bilan, takrorlanmaydi)
         → setOrder { status: 1, client, agent, priceType, warehouse, trade,
                      orderProducts:[{product:{SD_id}, quantity, price, discountSumma}],
                      comment, auto_attach_expeditor: true }
         → muvaffaqiyat: SD_id saqlanadi, status_local = sent, qoldiq keshi yangilanadi
         → xato: error.data dagi qatorma-qator xatolar UI'da ko'rsatiladi (status_local = failed)
         → timeout: worker getOrder bilan tekshiradi, keyin qaror qiladi (qayta yuborish yoki sent)
```

---

## 7. Bosqichlar (aniq reja)

### Bosqich 0 — Jonli API tekshiruvi (0.5–1 kun)
- [ ] `.env` ga login/parol (kodga emas!), alohida API foydalanuvchi so'rash
- [ ] `login`, `getFilials` → filial_id aniqlash
- [ ] Har bir GET metodidan namuna olish: klientlarda `tel` formati, mahsulotlarda `barCode` to'ldirilganligi, `packQuantity`
- [ ] Test klient + test sklad bilan **1 ta test buyurtma**: status=1 da `getStock` kamayadimi? → `setDeletedOrder` bilan tozalash
- [ ] 1C integratsiyasi bor-yo'qligini aniqlash (code_1C qarori)
- **Natija:** `docs/api-findings.md` + qarorlar

### Bosqich 1 — Backend asos (2–3 kun)
- [ ] Loyiha skeleti (FastAPI, Docker Compose, Alembic, pydantic-settings)
- [ ] `SalesDoctorClient`: bitta POST, token-lock, avtomatik relogin, retry/backoff, pagination iterator, xato modeli
- [ ] Pydantic sxemalar (EntityID, Client, Product, Stock, Order...)
- [ ] Unit testlar (mock javoblar kollektsiyadagi namunalardan)

### Bosqich 2 — Sinxronizatsiya (2 kun)
- [ ] Worker job'lari: klientlar (+onlyNew/period inkremental), mahsulotlar, narxlar, skladlar, qoldiq, balans, ma'lumotnomalar
- [ ] Telefon normalizatsiyasi + indeks; barcode indeksi
- [ ] Admin: oxirgi sinxron vaqti, qo'lda ishga tushirish

### Bosqich 3 — Auth (1 kun)
- [ ] `POST /auth/phone` → klient(lar) topish → httpOnly session cookie
- [ ] Rate limit (IP + telefon), ixtiyoriy PIN, bloklash
- [ ] Do'kon tanlash (bir raqam — bir necha klient)

### Bosqich 4 — Buyurtma API (2–3 kun)
- [ ] `GET /catalog` (qidiruv, kategoriya, barcode), `GET /stock?warehouse=`, `GET /prices?priceType=`
- [ ] `POST /orders` (draft), `PUT /orders/{id}`, `POST /orders/{id}/submit`
- [ ] Idempotentlik, holat mashinasi, timeout reconcile job
- [ ] `GET /orders` (lokal + `getOrder` statuslarini yangilash), bekor qilish
- [ ] Excel import (barcode/kod, blok, dona)

### Bosqich 5 — Frontend (4–5 kun)
- [ ] Login, do'kon tanlash, bosh sahifa
- [ ] "Новый заказ" ekrani (jadval, ± tugmalar, blok/dona, jami, kategoriya xulosasi)
- [ ] ТСД skaner inputi + kamera skaner
- [ ] "Добавить из списка" modal, jadval qidiruvi, ustun sozlamalari
- [ ] Buyurtmalar ro'yxati va tafsiloti
- [ ] Mobil (telefon/TSD) uchun moslashuvchan dizayn

### Bosqich 5A — Admin panel (5–7 kun)
- [ ] Admin auth (argon2, TOTP 2FA, sessiya), RBAC middleware, audit log yozuvchi
- [ ] Linklar: CRUD, sozlamalar formasi, QR-kod, statistika, klonlash; do'kon tomonida link sozlamalarini qo'llash
- [ ] Buyurtmalar: ro'yxat/filtr, tahrirlash, status, bekor qilish, qayta yuborish, eksport, chop etish
- [ ] Do'konlar: bloklash, PIN tiklash, sessiyalar, link ruxsatlari
- [ ] Mahsulotlar/qoldiq/narxlar ko'rinishi, yashirish, qo'shimcha barcode
- [ ] Dashboard + hisobotlar
- [ ] Sinxron monitoring, sozlamalar (SD ulanish — shifrlangan), adminlar boshqaruvi
- [ ] Telegram bildirishnomalar

### Bosqich 6 — Sinov va ishga tushirish (2 kun)
- [ ] E2E: login → skan → buyurtma → SD'da ko'rinishi → qoldiq kamayishi
- [ ] Yuklama: 50 ta do'kon parallel
- [ ] Deploy (VPS, nginx, HTTPS, backup), monitoring (Sentry), loglar
- [ ] Operator uchun qisqa qo'llanma

### Bosqich 7 — Keyingi imkoniyatlar (ixtiyoriy)
- Qaytarish (`setOrderDefect`), bonuslar (`getBonus`), to'lovlar tarixi (`getPayment`),
  offline rejim (PWA, navbatdagi buyurtmalar), Telegram bildirishnomalar.

**Taxminiy jami: 20–25 ish kuni** (admin panel bilan).

---

## 8. Ochiq savollar (javob kerak)

1. Buyurtmadan keyin qoldiq **darhol** kamayishi kerakmi (status 2 "Отгружен" bilan yuborish) yoki dilerning ombori tasdiqlashi kerakmi (status 1)?
2. Do'kon agent/sklad/narx turini **o'zi tanlaydimi** yoki bu klient sozlamalaridan qat'iy belgilanadimi?
3. Dilerda 1C integratsiyasi bormi (code_1C masalasi)?
4. `OPERATOR` foydalanuvchisi boshqa joyda API orqali ishlatiladimi? (token konflikti)
5. Telefon + PIN qo'shsak bo'ladimi, yoki faqat telefon?
6. Narxni do'kon o'zgartira oladimi yoki faqat SD narxi?
7. "Link" deganda nima nazarda tutilgan: sozlamalari oldindan belgilangan kirish havolasi (5.1 dagi taxmin), yoki boshqa narsa (masalan, bitta do'kon uchun shaxsiy havola, yoki agent uchun havola)?
8. Admin panelda buyurtmani tahrirlash SD'dagi buyurtmani ham o'zgartirishi kerakmi?
