# Auto-Shop — operator qo'llanmasi (admin panel)

Bu qo'llanma admin panel bilan ishlaydigan xodimlar uchun: havolalar, QR-kodlar, do'konlar va
telefonlar, buyurtmalar, sinxronlash, hisobotlar va sozlamalar.

Admin panel manzili: `https://<sizning-domeningiz>/admin`. Interfeys tili yuqori o'ng
burchakda almashtiriladi: **RU / UZ**.

---

## 1. Tizim qanday ishlaydi (qisqacha)

1. Siz **havola** (link) yaratasiz, masalan `https://shop.uz/s/k7m2x9qa`, va uni do'konlarga
   yuborasiz yoki QR-kod qilib chop etasiz.
2. Do'kon havolani ochadi va Sales Doctor'da (SD) do'konga yozilgan **telefon raqami** bilan
   kiradi. SMS ham, parol ham yo'q.
3. Do'kon buyurtma tuzadi (shtrixkod skaneri, kamera, ro'yxatdan yoki fayldan) va **«Отправить
   заказ / Buyurtmani yuborish»** tugmasini bosadi.
4. Buyurtma darhol Sales Doctor'ga tushadi va **ombordagi qoldiq darhol band qilinadi**.
   SD'dagi buyurtma raqami `WEB-000123` ko'rinishida bo'ladi.
5. Narxni do'kon o'zgartira olmaydi: narx har doim SD'dagi tanlangan narx turidan olinadi.

> Muhim: telefon raqami yagona himoya. Shuning uchun shubhali raqamlar avtomatik ajratiladi,
> do'konni yoki raqamni bloklash mumkin va har bir harakat auditda yoziladi.

---

## 2. Kirish va xavfsizlik

- Login va parol bilan kiring. Birinchi kirishda vaqtinchalik parolni o'zgartirish so'raladi.
- **Ikki bosqichli autentifikatsiyani yoqing:** Profil → «Включить 2FA / 2FA ni yoqish».
  Telefoningizdagi autentifikator ilovasi (Google Authenticator, Aegis va h.k.) bilan QR-kodni
  skanerlang va 6 xonali kodni kiriting.
- 5 marta noto'g'ri paroldan keyin kirish vaqtincha bloklanadi (15 daqiqa).
- Ishni tugatganda **«Выйти / Chiqish»** tugmasini bosing. Sessiya 60 daqiqa harakatsizlikdan
  keyin o'zi tugaydi.

### Rollar

| Rol | Nima qila oladi |
|---|---|
| Superadmin | Hammasi, jumladan adminlarni boshqarish |
| Admin | Hammasi (adminlarni boshqarishdan tashqari): havolalar, buyurtmalar, do'konlar, mahsulotlar, sinxronlash, sozlamalar, audit, hisobotlar |
| Operator | Buyurtmalarni ko'rish, o'zgartirish va bekor qilish; do'konlar, mahsulotlar va havolalarni ko'rish |
| Kuzatuvchi (viewer) | Faqat ko'rish va hisobotlar |

Yangi xodim qo'shish: **Adminlar** → «Новый администратор / Yangi administrator». Parolni
bo'sh qoldirsangiz, tizim vaqtinchalik parol yaratadi. U faqat bir marta ko'rsatiladi.

---

## 3. Havolalar (Ссылки / Havolalar)

### 3.1. Yangi havola yaratish

**Havolalar → «Новая ссылка / Yangi havola»**

| Bo'lim | Maydon | Izoh |
|---|---|---|
| Asosiy | Nomi | Ichki nom, masalan «Buxoro markaz bozori» |
| | Slug | Bo'sh qoldiring: tizim tasodifiy 8 belgili slug yaratadi (tavsiya etiladi). «shop», «test» kabi oson topiladigan so'zlar ogohlantirish beradi |
| | Havola faol | O'chirsangiz, havola darhol ishlamay qoladi |
| | Boshlanish / tugash vaqti | Masalan aksiya davri uchun. Ixtiyoriy |
| | Maks. kirishlar soni | Bo'sh — cheklovsiz |
| Kimlar kira oladi | Kimlarga ochiq | **Barcha klientlar**, **Tanlangan klientlar**, **Hudud bo'yicha** yoki **Klient kategoriyasi bo'yicha** |
| Buyurtma parametrlari | Omborlar, Narx turlari, Agentlar, Savdo yo'nalishlari | Har biri uchun rejim: **Qat'iy belgilangan** (bitta qiymat, do'kon tanlamaydi) yoki **Ro'yxatdan tanlash** (do'kon ruxsat etilganlardan tanlaydi). Agent uchun yana **Klientga biriktirilgan** rejimi bor (SD'dagi do'kon agenti) |
| | Buyurtma statusi | **1 — Yangi** (standart, qoldiq baribir darhol band qilinadi) yoki **2 — Jo'natilgan** |
| Cheklovlar | Min./maks. buyurtma summasi | Do'kon bu chegaradan tashqari buyurtma yubora olmaydi |
| | Qoldiqdan ortiq buyurtmaga ruxsat | Odatda o'chiq |
| Ko'rinadigan mahsulotlar | Qaysi mahsulotlar ko'rinadi | Hammasi, kategoriyalar, brendlar yoki tanlangan mahsulotlar |
| Ko'rinish | Sarlavha, logotip, rang, xush kelibsiz matni | Do'kon kirish sahifasida ko'rinadi |

ID maydonlarida ro'yxatdan tanlang yoki SD ID'ni yozib **Enter** bosing. Faqat SD'da faol
bo'lgan omborlar va **sotuv** narx turlari qabul qilinadi. «Приходная цена» (kirim narxi)
hech qachon ko'rsatilmaydi.

### 3.2. Havolani ulashish va QR-kod

Havola sahifasida **«Kirish»** paneli bor:

- **To'liq havola**: «Havolani nusxalash» tugmasi bilan nusxalang va Telegram yoki SMS orqali
  yuboring.
- **QR-kod**: «PNG yuklab olish» tugmasi. QR-kodni chop etib do'kon kassasiga yopishtirish
  mumkin. Do'kon telefon kamerasi bilan skanerlaydi.

### 3.3. Statistika

Havola sahifasidagi **«Havola statistikasi»** paneli. Davrni tanlang: 7 / 30 / 90 kun.

- davrdagi buyurtmalar soni va summasi, kirishlar soni, kirgan do'konlar soni;
- jami buyurtmalar;
- kunlar bo'yicha grafik: ko'k ustun — buyurtmalar, kulrang — kirishlar;
- top do'konlar.

### 3.4. Boshqa amallar

- **Klonlash**: bir xil sozlamali yangi havola, yangi slug bilan.
- **Arxivlash**: havola butunlay to'xtatiladi, do'konlar u orqali kira olmaydi. Arxivdan
  qaytarish mumkin.
- «Tanlangan klientlar» rejimida havola sahifasining pastida **«Havola klientlari»** ro'yxati
  bor. U yerda do'kon qo'shiladi yoki olib tashlanadi.

> Maslahat: har bir hudud yoki agent uchun alohida havola yarating. Shunda hisobotlarda
> «havolalar bo'yicha» kesim foydali bo'ladi va kerak bo'lsa bitta havolani to'xtatish oson.

---

## 4. Do'konlar va telefonlar (Магазины / Do'konlar)

Do'konlar Sales Doctor'dan avtomatik olinadi. Ularni qo'lda yaratish shart emas.

### 4.1. Do'konni bloklash

**Do'konlar → do'kon qatori → «Bloklash»** (sababini yozish mumkin). Do'konning barcha ochiq
sessiyalari darhol tugatiladi va u boshqa kira olmaydi. **«Blokdan chiqarish»** bilan qaytariladi.

**«Sessiyalarni tugatish»**: do'konni bloklamasdan, faqat hozirgi kirishlarni yopadi. Masalan,
telefon yo'qolgan bo'lsa.

### 4.2. Telefon raqamlari (Telefonlar yorlig'i)

| Holat | Ma'nosi |
|---|---|
| **Oddiy** | Kirish mumkin |
| **Shubhali** | Avtomatik ajratilgan: raqam juda ko'p do'konga bog'langan (chegara: Sozlamalar → «Bitta raqamga maks. do'konlar», standart 3) yoki 999999999 kabi soxta raqam. Bunday raqam bilan kirish bloklanadi |
| **Bloklangan** | Siz qo'lda blokladingiz |
| **Ruxsat etilgan** | Siz qo'lda ruxsat berdingiz (shubhali bo'lsa ham) |

Holatni o'zgartirish: raqam qatori → **«Holatni o'zgartirish»** → **Ruxsat berish**,
**Bloklash** yoki **Avto (qoidalar bo'yicha)**.

> Bir raqam bir nechta do'konga yozilgan bo'lsa (masalan, bitta egasining ikki do'koni),
> kirishda do'kon tanlash oynasi chiqadi. Agar bu haqiqiy holat bo'lsa, raqamga ruxsat bering.

---

## 5. Buyurtmalar (Заказы / Buyurtmalar)

### 5.1. Ro'yxat

Filtrlar: buyurtma holati, SD'dagi holat, do'kon, havola, ombor, agent, qidiruv (raqam
`WEB-…`, SD id yoki do'kon). Yuqorida jami: soni va summasi. **«Excel'ga eksport»** tugmasi
filtrlangan ro'yxatni .xlsx qilib beradi.

### 5.2. Buyurtma holatlari

| Holat | Ma'nosi | Nima qilish kerak |
|---|---|---|
| Qoralama | Do'kon hali yubormagan | — |
| Yuborilmoqda | Hozir SD'ga ketmoqda | Kuting |
| **Yuborilgan** | SD qabul qildi, qoldiq band qilindi | — |
| **Yuborishda xato** | SD rad etdi (masalan, mahsulot bloklangan yoki qoldiq yetarli emas) | Xatoni o'qing, buyurtmani tuzating, «Qayta yuborish» tugmasini bosing |
| **Natija noma'lum** | Aloqa uzildi, SD qabul qilganmi yoki yo'qmi aniq emas | Tizim o'zi tekshiradi (bir xil `WEB-` raqam bilan, dublikat bo'lmaydi). Agar «Avtomatik solishtirish to'xtadi» yozuvi chiqsa, **«Qayta yuborish»** tugmasini bosing |
| Bekor qilingan | SD'da 5-holat (bekor). Qoldiq qaytarilgan | — |
| O'chirilgan | SD'da ham o'chirilgan | — |

### 5.3. Buyurtmani o'zgartirish (admin)

Buyurtmani oching:

- miqdorni **+ / −** tugmalari bilan yoki maydonga yozib o'zgartiring, qator qo'shing yoki
  olib tashlang;
- pastdagi summa saqlanmagan o'zgarishlar bilan qayta hisoblanadi (jonli hisob);
- **«Saqlash va SD'ga yuborish»**: o'zgarish SD'dagi **o'sha** buyurtmaga yoziladi (yangi
  buyurtma yaratilmaydi). Qoldiq ham farqqa qarab o'zgaradi;
- admin min./maks. summa cheklovini chetlab o'tishi mumkin, lekin qoldiq tekshiruvi baribir
  ishlaydi;
- narxlar o'zgargan bo'lsa, ogohlantirish chiqadi. **«Narxlarni qayta hisoblash»** joriy SD
  narxlarini qo'llaydi.

### 5.4. Holatni o'zgartirish, bekor qilish, o'chirish

- **«Holatni o'zgartirish»** → yangi holat (2 — Jo'natilgan, 5 — Bekor va h.k.). Diqqat:
  jo'natilgandan keyin bekor qilish SD'da **qaytarish** yaratadi.
- Bekor qilish uchun «orders.cancel» huquqi kerak.
- **«Buyurtmani o'chirish»**: SD'da ham o'chiriladi va qoldiq qaytariladi.
- Har bir buyurtmada **«Tarix»** bor: kim, qachon, nima qildi.

---

## 6. Mahsulotlar (Товары / Mahsulotlar)

Mahsulotlar, narxlar va qoldiqlar SD'dan olinadi. Bu yerda:

- mahsulotni **do'konlardan yashirish** mumkin (SD'ga ta'sir qilmaydi);
- **shtrixkod qo'shish** mumkin: SD'da shtrixkodi yo'q mahsulotlar uchun (8–14 raqam). Bitta
  shtrixkod bir nechta mahsulotda bo'lsa, ogohlantirish chiqadi va skanerlashda do'kon
  tanlaydi;
- omborlar bo'yicha qoldiq va narx turlari bo'yicha narxlarni ko'rish mumkin.

---

## 7. Sinxronlash (Синхронизация / Sinxronlash)

Tizim SD bilan o'zi sinxronlanadi. Standart oraliqlar: qoldiq — 1 daqiqa, balanslar —
5 daqiqa, mahsulotlar va narxlar — 10 daqiqa, klientlar va ma'lumotnomalar — 30 daqiqa.

- Sahifada har bir obyektning oxirgi sinxronlashi va natijasi ko'rinadi. Sahifa har 10 soniyada
  yangilanadi.
- **«Запустить / Ishga tushirish»**: kutmasdan hozir sinxronlash (masalan, SD'da yangi do'kon
  qo'shilgandan keyin «Klientlar»).
- Yuqoridagi panel **Sales Doctor bilan aloqa** holatini ko'rsatadi.
- «Xato» bo'lsa, xabarni o'qing. Odatda SD vaqtincha ishlamayotgan bo'ladi va keyingi
  sinxronlash o'zi tiklaydi. Telegram bildirishnomasi yoqilgan bo'lsa, xato va tiklanish
  haqida xabar keladi.

---

## 8. Hisobotlar (Отчёты / Hisobotlar)

Faqat yuborilgan buyurtmalar hisobga olinadi. Sana — yuborilgan vaqt (Toshkent vaqti).

1. **Guruhlash**ni tanlang: do'konlar, mahsulotlar, kategoriyalar, havolalar, agentlar yoki
   kunlar bo'yicha.
2. Davrni tanlang: sanalar yoki tayyor tugmalar (Bugun, 7 kun, 30 kun, Shu oy).
3. Jadvalda: buyurtmalar soni, miqdor, summa va ulush (%). Pastda — jami.
4. **«Excel yuklab olish»**: shu hisobot .xlsx faylda.

Bosh sahifadagi **Dashboard**: bugun, 7 va 30 kunlik ko'rsatkichlar, top do'konlar va
mahsulotlar, muammoli buyurtmalar soni.

---

## 9. Audit jurnali (Аудит)

Tizimdagi har bir muhim harakat yoziladi: adminlarning kirishi, havola o'zgarishi,
do'kon/raqam bloklanishi, buyurtma yuborilishi va o'zgarishi, sozlamalar, eksportlar,
do'konlarning kirishi.

Filtrlar: subyekt turi (Administrator / Do'kon / Tizim), administrator, amal, obyekt turi,
obyekt ID, sana oralig'i. Qatorni bossangiz, **«oldin / keyin»** tafsilotlari, IP va so'rov
ID'si ochiladi. Parollar, tokenlar va to'liq telefon raqamlari auditga hech qachon yozilmaydi.

---

## 10. Sozlamalar (Настройки / Sozlamalar)

- **Sales Doctor ulanishi**: server va login/parol o'rnatilganmi (parolning o'zi
  ko'rsatilmaydi).
- **Sinxronlash oraliqlari**: har bir obyekt uchun o'z qiymatingiz (soniyada). Bo'sh qoldirsangiz,
  standart qiymat ishlaydi. O'zgarish 10 soniya ichida kuchga kiradi.
- **Shubhali raqamlar**: bitta raqamga nechta do'kon bog'lanishi mumkin. Keyingi klientlar
  sinxronlashida qo'llaniladi.
- **Telegram bildirishnomalari**:
  1. @BotFather'da bot yarating va tokenini oling.
  2. Botni xodimlar guruhiga qo'shing. Guruh ID'sini (masalan `-1001234567890`) yoki kanal
     nomini (`@kanal`) **«Chat ID lari»** maydoniga yozib Enter bosing.
  3. Tokenni kiriting, kerakli **hodisalar**ni belgilang: yangi buyurtma, buyurtma xatosi,
     natija noma'lum, operator tekshiruvi kerak, sinxronlash xatolari.
  4. **«Bildirishnomalarni yuborish»**ni yoqing, **Saqlash**ni bosing, so'ng **«Test
     yuborish»**ni bosing.
  - Token shifrlangan holda saqlanadi va qaytib ko'rsatilmaydi. Almashtirish uchun yangisini
    kiriting, o'chirish uchun **«Tokenni o'chirish»**.
  - «Oxirgi xato» yozuvi chiqsa (masalan «bot token rejected»), token noto'g'ri yoki bekor
    qilingan bo'ladi. Xabarlar yo'qolmaydi: token tuzatilgach yuboriladi. Faqat 6 soatdan eski
    xabarlar tashlab yuboriladi.

---

## 11. Tez-tez uchraydigan holatlar

| Holat | Yechim |
|---|---|
| Do'kon «Bu raqam bilan do'kon topilmadi» deydi | SD'da do'konga shu raqam yozilganini tekshiring. «Klientlar» sinxronlashini ishga tushiring. Raqam holati «Shubhali» yoki «Bloklangan» bo'lmasin. Do'kon bloklanmagan bo'lsin. Havola do'konga ochiq bo'lsin (Kimlarga ochiq) |
| «Havola mavjud emas» | Havola o'chirilgan, muddati tugagan, kirish limiti tugagan yoki arxivda |
| Mahsulot do'konda ko'rinmayapti | SD'da faolmi, tanlangan narx turida narxi bormi, yashirilmaganmi, havolaning «Ko'rinadigan mahsulotlar» filtriga kiradimi |
| Shtrixkod topilmayapti | Mahsulotlar sahifasida shtrixkod qo'shing |
| Buyurtma «Natija noma'lum»da qoldi | Bir necha daqiqa kuting. «Avtomatik solishtirish to'xtadi» bo'lsa, «Qayta yuborish». Dublikat paydo bo'lmaydi |
| SD bilan aloqa yo'q | Sinxronlash sahifasidagi holatni ko'ring. SD ishlamasa, do'konlar buyurtma yubora olmaydi («Sales Doctor vaqtincha ishlamayapti»). Qoralamalar saqlanib qoladi |

---

## 12. Xavfsizlik qoidalari

- Admin parolini hech kimga bermang, 2FA'ni yoqing.
- Havolani ochiq ijtimoiy tarmoqlarga joylamang. Uni faqat do'konlarga yuboring.
- Shubhali faollik bo'lsa (bir havolaga juda ko'p muvaffaqiyatsiz kirish), tizim o'zi
  cheklaydi va auditga yozadi. Havolani arxivlang va yangisini yarating.
- Xodim ishdan ketsa: **Adminlar** sahifasida uni o'chiring yoki sessiyalarini tugating.
