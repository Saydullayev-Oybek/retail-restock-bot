# POVTOR ZAKAZ boti

Kiyim-poyabzal do'konlar tarmog'ida tez sotilayotgan tovarlarni **qayta buyurtma
qilish** jarayonini avtomatlashtiruvchi Telegram bot.

Bot Billz'dan ma'lumotni o'zi tortadi, qaysi tovar tez sotilayotganini o'zi
hisoblaydi, menejerga kaskadli menyu orqali ko'rsatadi va javoblarni ("OLINDI" /
"BOZORDA YO'Q") bazaga yozib boradi.

---

## Tez ishga tushirish

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # keyin .env ni to'ldiring
python -m povtor_bot.main
```

Telegram'da botga `/tekshir` yuboring — **bu birinchi qadam**, aks holda
ko'rsatadigan hech narsa bo'lmaydi.

### `.env` ni to'ldirish

Majburiy: `BOT_TOKEN`, `ALLOWED_USER_IDS`, `BILLZ_SECRET_TOKEN`,
`WAREHOUSE_SHOP_ID`.

`WAREHOUSE_SHOP_ID` (SKLAD'ning Billz'dagi ID'si) ni bilish uchun:

```bash
python scripts/billz_probe.py --only shops
```

Skript do'konlar ro'yxatini `id  nom` ko'rinishida chiqaradi.

---

## Ishga tushirish haqida

**Bitta nusxa qulfi.** Bot ishga tushganda `var/povtor.lock` faylini band
qiladi. Ikkinchi nusxa darhol to'xtaydi va ishlab turgan jarayonning PID'ini
ko'rsatadi. Nega kerak: Telegram bitta token bilan faqat bitta `getUpdates`
iste'molchisiga ruxsat beradi, va har nusxada o'z cron'i bo'ladi — sinov
davrida 10 ta jarayon bir vaqtda ishlab qolgan edi.

**Kunlik zaxira nusxa.** 09:00 tekshiruvidan OLDIN olinadi (`var/backup/`,
14 kun saqlanadi) — bugungi tekshiruv nimanidir buzsa orqaga qaytish mumkin.

## Buyruqlar

| Buyruq | Ish |
|---|---|
| `/tekshir` | Qoidani so'raydi (oyna va chegara), keyin Billz'dan tortib nomzodlarni hisoblaydi |
| `/buyurtma` | Kaskadli menyu: kategoriya → ta'minotchi → artikul → karta |
| `/yangi` | Yangi kelgan tovarlarni umumiy guruhga e'lon qiladi |
| `/export` | Kunning javoblarini Excel'ga chiqaradi (har filialga alohida varaq) |

Bundan tashqari bot **har kuni `SCHEDULE_TIME` da** (default 09:00,
`Asia/Tashkent`) `/tekshir` ni o'zi ishga tushiradi va natijani menejerlarga
xabar qiladi.

---

## `/tekshir` — qoidani menejer tanlaydi

```
/tekshir
   ↓
📅 Necha kunlik oyna?     [3] [• 5] [7] [10] [14]
   ↓
📊 Sotuv chegarasi?       [40%] [• 50%] [60%] [70%] [80%]
   ↓
✅ Tekshiruv tugadi
   oyna 7 kun · chegara 60%
```

`•` — `.env` dagi sukut qiymat. Qisqa yo'l: `/tekshir 7` yoki `/tekshir 7 60`.

Nega kerak: bir xil ma'lumotdan turli savollar chiqadi — *"bugun nima tez
ketdi"* uchun `3 kun / 60%`, *"nimalar to'planib qoldi"* uchun `14 kun / 50%`.
Ilgari buni o'zgartirish uchun `.env` ni tahrirlab botni qayta ishga tushirish
kerak edi.

Tanlangan qiymat **eslab qolinmaydi** — har tekshiruvda qaytadan so'raladi.
Kunlik 09:00 tekshiruvi hech kimdan so'ray olmaydi, u `.env` qiymatlari
bilan ishlaydi.

Band **qaysi oyna bilan topilgani** o'z qatorida saqlanadi (`window_days`),
shuning uchun 10 kunlik tekshiruvdan chiqqan 7 kunlik band `⚠️ eskirgan`
deb belgilanmaydi.

### `/buyurtma` oxirgi tekshiruv natijasini ko'rsatadi

Menejer qoidani o'zi tanlagan ekan, ro'yxat aynan **shu qoidaga** javob
berishi kerak. Har band qaysi tekshiruvda topilgani saqlanadi (`last_run`),
va menyu faqat eng oxirgisini ko'rsatadi.

Ilgari natijalar **to'planib borardi**: `3 kun / 70%` bilan 0 ta topilsa ham
menyuda oldingi tekshiruvlardan qolgan 101 ta band turaverardi, va bugungi
ish ular orasida ko'rinmasdi.

Bandlar **o'chirilmaydi** — ular bazada, kartada va `/export` da qoladi.
Kengroq qoida bilan qayta tekshirilsa ro'yxatga qaytadi.

## Nomzod aniqlash qoidasi

Har bir `(filial, artikul, rang)` uchun:

```
arrived_date = tovar SKLAD'dan filialga OXIRGI marta kelgan sana
base_qty     = o'sha transferda kelgan miqdor          ("Asos")
sold_qty     = arrived_date dan bugungacha sotilgani   ("Sotilgan")
percent      = sold_qty / base_qty * 100
days_to_50   = kunlik sotuvlar 50% ga yetgan birinchi kun (0 = kelgan kuni)
```

**Nomzod** ⇔ `base_qty ≥ MIN_BASE_QTY (5)` **VA** `o'tgan kun ≤ WINDOW_DAYS (5)`
**VA** `percent ≥ 50%` **VA** kategoriya `ALLOWED_CATEGORY_GROUPS` ichida.

Kuzatiladigan kategoriyalar (skladdan filiallarga kelgan hajm, 30 kun):

| Kategoriya | Ulush | Kuzatiladi |
|---|---|---|
| Плечевые одежды | 28.7% | ✅ |
| Нижнее белье | 24.4% | ✅ |
| Поясные одежды | 23.5% | ✅ |
| Обувь | 10.0% | ✅ |
| Верхняя одежда | 7.7% | ✅ |
| Кроссовки | 2.4% | ✅ |
| Аксессуары | 3.3% | ❌ |

Ya'ni skladdan kelgan tovarning **~97%** i qamrab olinadi. Qolgan
kategoriyalar (parfyumeriya, kosmetika, bijuteriya, paket, atelye va h.k.)
POVTOR jarayoniga kirmaydi.

`MIN_BASE_QTY` nima uchun kerak: skladdan 1-2 donalik "to'ldirish" transferlari
juda ko'p keladi va ularda "100% sotildi" statistik ma'noga ega emas. Namuna
POVTOR faylida Asos hech qachon 5 dan kichik emas (128 qatordan 74 tasi aynan 5).

Foiz **100% bilan cheklanadi**: filialda oldingi qoldiq bo'lsa sotuv oxirgi
partiyadan ko'p chiqadi (5 keldi, 6 sotildi). Bunday holatda partiya to'g'ri
maxraj emas — namuna faylda ham foiz hech qachon 100 dan oshmagan. "Sotilgan"
ustunida esa haqiqiy raqam saqlanadi.

**Daraja** — texnik topshiriqdagi qoida (`GRADE_RULE=speed`, sukut):

> *"Qancha tezroq 50%ga yetgan bo'lsa, shuncha ishonchli. 2 kun ichida yetsa —
> yuqori tavsiya miqdori, 3-5 kun ichida — o'rtacha."*

| 50%ga yetgan kun | Daraja | Tavsiya |
|---|---|---|
| 0–2 | `ishonchli` | **10 dona** |
| 3–5 | `oddiy` | **5 dona** |

### Ikkinchi rejim: `GRADE_RULE=speed_and_volume`

Namuna POVTOR faylidan teskari muhandislik qilingan variant:

```
ishonchli ⇔ sotilgan ≥ 4 VA (foiz ≥ 80 YOKI 50%ga ≤ 3 kunda)
```

U namuna fayldagi **128 qatorning hammasiga** mos keladi
(`tests/fixtures/golden_rules.json`), texnik topshiriq qoidasi esa
**28 qatorda farq qiladi** — chunki amaldagi qo'lda ishlaydigan jarayon
tezlikdan tashqari **hajmni** ham hisobga olar ekan:

```
asos=6  sotilgan=6  foiz=100%  50%ga 3-kunda  →  Excel: ishonchli
asos=5  sotilgan=4  foiz=80%   50%ga 4-kunda  →  Excel: ishonchli
```

Topshiriqda `masalan` deb yozilgani uchun u misol, aniq formula emas.
**Loyiha egasi texnik topshiriqni asos qilib tanladi.**

Real ma'lumotda farq: 105 banddan 36 tasi darajasini o'zgartiradi, umumiy
tavsiya miqdori −5% (745 → 715 dona).

Barcha raqamlar `.env` orqali sozlanadi.

---

## Arxitektura

```
povtor_bot/
├── config.py          # .env (pydantic-settings)
├── db/                # SQLite: schema.sql + toza SQL (ORM yo'q)
├── billz/
│   ├── client.py      # HTTP: auth, rate limit, 429/401 retry
│   └── gateway.py     # Billz JSON -> domen modellari
├── core/
│   ├── models.py      # dataclass'lar
│   └── rules.py       # ☆ nomzod mantiqi — SOF funksiyalar, I/O yo'q
├── services/          # check, cards, media, announce, export, transfer_hint
├── bot/               # handlers, keyboards, texts, middlewares, callbacks
└── scheduler.py       # kunlik 09:00
```

**Qatlamlar sababi:** `core/rules.py` Billz haqida hech narsa bilmaydi — u faqat
raqamlar bilan ishlaydi. Shuning uchun uni real API'siz test qilish mumkin, va
ertaga Billz o'rniga boshqa savdo tizimi kelsa faqat `billz/gateway.py`
qayta yoziladi.

### Muhim texnik qarorlar

**Rasm Telegram'ga yuklab olinadi, URL uzatilmaydi.** Billz hujjati CDN'dan
rasmni uchinchi tomon resurslarida ko'rsatishni taqiqlaydi. Shu sababli rasm bir
marta yuklab olinib Telegram'ga yuboriladi, qaytgan `file_id` `product_cache`ga
yoziladi va undan keyin faqat shu ishlatiladi. Rasm topilmasa `image_missing=1`
qo'yiladi — qayta urinilmaydi.

Billz `main_image_url` da faqat fayl nomini beradi, shuning uchun
`BILLZ_IMAGE_BASE_URL` sozlanmaguncha kartalar **matn ko'rinishida** chiqadi.
Qolgan hamma narsa (nom, artikul, rang, tannarx, tavsiya, tugmalar) ishlaydi.

**Rasm ↔ matn almashinuvi.** Telegram matnli xabarni rasmli xabarga tahrirlay
olmaydi. `card_msg` jadvali oxirgi kartaning turini eslab qoladi; tur o'zgarsa
eski xabar o'chirilib yangisi yuboriladi (`services/cards.py`).

**Tannarx har doim so'mda.** Asosiy manba — transfer hisobotidagi dona narxi;
u `display_currency=UZS` bilan so'ralgani uchun allaqachon so'mda. Zaxira
manbada valyuta boshqa bo'lsa `/v2/company-currency-rates` kursi bilan
o'giriladi. Kurs noma'lum bo'lsa narx ko'rsatilmaydi — noto'g'ri raqamdan ko'ra
bo'shlik afzal.

**Rate limit.** Billz sekundiga 2 so'rovga ruxsat beradi va evristik DDoS
analizatoriga ega. Klient token-bucket bilan default **1.5 rps** da ishlaydi,
429 da `Retry-After` ni kutadi, 401 da avtomatik refresh/login qiladi.

**Idempotentlik.** `UNIQUE(detected_date, shop_id, sku, color)` — `/tekshir`
kuniga necha marta ishlasa ham dublikat tug'ilmaydi va berilgan javob
yo'qolmaydi. Javob yozish esa `... AND status = 'pending'` sharti bilan bitta
`UPDATE` ichida — ikki menejer bir vaqtda bossa faqat birinchisi yozadi.

**HTML, Markdown emas.** Postavshik nomlarida `_`, `-`, `*` uchraydi
(`ABUSAXIY 8-22 M64`) — Markdown ularni formatlash belgisi deb o'qib xato
beradi. Barcha dinamik matn `html.escape()` dan o'tadi.

---

## Billz API

| Vazifa | Endpoint |
|---|---|
| Auth | `POST /v1/auth/login`, `POST /v2/auth/refresh` |
| Filiallar | `GET /v1/shop?only_allowed=true` |
| Kelgan sana + Asos + tannarx | `GET /v1/transfer-report-table` |
| Kunlik sotuv | `GET /v1/product-general-table?detalization=day` |
| Rang / podkategoriya / rasm | `GET /v2/products?search=<artikul>` |
| Qoldiq | `GET /v1/stock-report-table` |
| USD kursi | `GET /v2/company-currency-rates` |

### Haqiqiy javob hujjatdan farq qiladi

Quyidagilar real akkauntda `scripts/billz_probe.py` bilan aniqlangan va kod
aynan shunga qurilgan. Boshqa Billz akkauntida sozlash boshqacha bo'lishi
mumkin — probe'ni birinchi bo'lib ishlating.

| Nima | Hujjatda | Aslida |
|---|---|---|
| **Rang** | `product_attributes[]` | `custom_fields` → **`"Цвет"`**. `product_attributes` hamma joyda bo'sh |
| **Podkategoriya / Tur** | `level_2` | `custom_fields` → `"Подкатегория"` / `"Вид"` |
| **Sotuv javobi kaliti** | `rows` | `products_stats_by_date` |
| **Tannarx** | `/v2/products` → `supply_price` | U yerda **0**. Ishonchlisi transferdagi `sum_supply_price / sent_quantity` (u `display_currency=UZS` bilan so'raladi) |
| **Rasm** | to'liq URL | faqat fayl nomi (`<uuid>.jpg`) — `BILLZ_IMAGE_BASE_URL` kerak |
| **Kategoriya nomlari** | — | **kirill**: `Поясные одежды`, `Плечевые одежды`, `Верхняя одежда`, `Обувь` |

Yana ikkita muhim nuqta:

**Transfer hisobotida rang yo'q**, faqat `product_id` bor. Shuning uchun
transfer va sotuv qatorlari `product_variant` jadvali orqali rangga
bog'lanadi (`product_id` → `Цвет`).

**Filiallar bir-biriga ham tovar yuboradi** — real ma'lumotda 30 kunlik
o'lchovda filiallarga kelgan transferlarning **~30%** i boshqa filialdan. Bu
sotilmay qolgan tovarni qayta taqsimlash, ya'ni qayta buyurtmaga asos emas
(aksincha — tovar boshqa joyda sotilmagan). Shuning uchun `from_shop_id`
majburiy tarzda `WAREHOUSE_SHOP_IDS` ro'yxati bilan solishtiriladi.

Bu qoida biznes tomonidan tasdiqlangan: *"filialdan filialga transfer qilingan
tovar tez sotilsa ham bozordan olinmaydi — faqat skladdan kelgani"*.

Manba taqsimoti (30 kun, filiallarga kelgan 20 522 qator):

| Yuboruvchi | Ulush |
|---|---|
| `СКЛАД ПРИХОДА` (import) | 56.8% |
| `BUTTON СКЛАД MEN` (sezon) | 12.9% |
| filiallardan | ~30% |

### Hisobotlar KUN-KUN so'raladi

Billz hisobotlari **yangisidan eskisiga** tartiblangan va **tirik**: do'kon
ishlayotgan paytda har yangi yozuv ro'yxat boshiga qo'shilib, sahifa
chegaralarini suradi. Bir xil so'rov turli javob qaytaradi.

O'lchov (`page=2` ni ketma-ket 3 marta):

```
1-so'rov: 1000 qator
2-so'rov: -4 / +4     <- boshqa qatorlar
3-so'rov: -7 / +7
```

Natijada ba'zi qatorlar ikki marta tushardi, ba'zilari **umuman tushmasdi** —
nomzodlar soni har tekshiruvda 92..96 orasida sakrardi.

O'tgan kunlar esa o'zgarmaydi, shuning uchun har kun **alohida** so'raladi.
Faqat bugungi kun tirik, va u bitta sahifaga sig'adi.

Natija (uch marta ketma-ket): `99, 99, 99` — farq `-0/+0`.

⚠️ **Sotuv hisoboti DEDUPE QILINMAYDI.** Billz bir (kun, filial, tovar) uchun
bir nechta qator qaytaradi — har xil narx/chegirma bo'yicha, va ularning har
biri alohida haqiqiy sotuv (bir artikulda 11 tagacha qator kuzatilgan).
`(date, shop_id, product_id)` bo'yicha birlashtirish sotuvning **16%** ini
yo'q qiladi. Transfer uchun esa `(transfer_id, product_id)` xavfsiz —
tekshirilgan, takror yo'q.

### Tezlik

Billz hisobot dvigateli bitta sahifani ~3 sekund hisoblaydi, va bu vaqt
**qator soniga bog'liq emas** (o'lchov: 500 qator 4.2s, 2000 qator 3.3s).
Shundan uchta qaror kelib chiqadi:

| Sozlama | Nima qiladi |
|---|---|
| `BILLZ_PAGE_LIMIT=1000` | Katta sahifa deyarli bepul — so'rovlar soni yarmiga tushadi |
| `BILLZ_CONCURRENCY=4` | Sahifalar guruh-guruh so'raladi. Tezlik chegarasini **oshirmaydi** (token-bucket baribir 1.5 rps da ushlab turadi) — faqat Billz javobini kutish vaqtlari ustma-ust tushadi. Aks holda bot 0.3 rps da ishlaydi, Billz ruxsat bergan 2 dan olti barobar kam |
| `STOCK_REFRESH_HOURS=6` | Qoldiq hisoboti sahifalarning ~57% i, lekin faqat "boshqa filialda bormi?" uchun kerak — bir necha soatlik eskilik zarar qilmaydi |

Natija (kunlik run, katalog keshda): **208s → 69s**.

### Katalog to'liq tortilmaydi

Akkauntda 76 000+ tovar bor va Billz `/v2/products` ni 5 daqiqada 1 marta
chaqirishni tavsiya qiladi. Bir tekshiruvda esa atigi ~200 ta artikul keladi,
shuning uchun katalog **artikul bo'yicha** o'qiladi va `product_variant` +
`sku_sync` jadvallarida keshlanadi (`SKU_SYNC_DAYS` kun). Birinchi `/tekshir`
shu sababli sekinroq (~3 daqiqa), keyingilari tez.

### Probe

```bash
python scripts/billz_probe.py --out var/probe
```

Har bir endpoint'dan bitta kichik sahifa olinadi va JSON qilib saqlanadi.
Bundan tashqari bot ishlaganda **xato** javoblar `billz_raw` jadvaliga
yoziladi (`RAW_RETENTION_DAYS` kundan keyin tozalanadi).

⚠️ Muvaffaqiyatli javoblar ATAYLAB saqlanmaydi: bitta hisobot sahifasi
~100 KB, va bir kunlik sinovda `billz_raw` 168 MB bo'lgan — butun bazaning
93% i, hammasi `HTTP 200`. Muammo chiqqanda kerak bo'ladigani xato javob.

Billz javob shakli o'zgarganini tekshirish kerak bo'lsa, vaqtincha:

```
BILLZ_RAW_LOG_ALL=true
```

## Testlar

```bash
pip install -r requirements-dev.txt
pytest
```

| Fayl | Nima qotiriladi |
|---|---|
| `test_rules.py` | Nomzod qoidasi — **128 qatorli haqiqiy dataset** |
| `test_flow.py` | Uchdan-uchgacha: Update → Dispatcher → handler → baza |
| `test_repo.py` | Idempotentlik va parallel javob poygasi |
| `test_client.py` | 429 backoff, 401 refresh, token keshi |
| `test_cards.py` | Rasm ↔ matn almashinuvi, `file_id` keshi |
| `test_check.py` | `/tekshir` orkestratsiyasi |
| `test_export.py` | Excel — namuna fayl bilan bir xil 14 ustun |
| `test_auth.py` | Ruxsat nazorati |
| `test_config.py` | `.env` o'qilishi (CSV ro'yxatlar) |
| `test_gateway.py` | Billz JSON normalizatsiyasi |
| `test_texts.py` | HTML escaping |

---

## Baza

SQLite, `var/povtor.db` (WAL rejimi). Asosiy jadvallar:

| Jadval | Vazifa |
|---|---|
| `candidate` | Nomzodlar va ularning holati (`pending`/`taken`/`not_found`) |
| `product_cache` | Rasm `file_id`, tannarx, postavshik, kategoriya |
| `product_variant` | `product_id` → artikul + **rang**. Hisobotlarda rang yo'q — bog'lash shu orqali |
| `sku_sync` | Qaysi artikul katalogdan qachon o'qilgani (76k tovarni qayta tortmaslik uchun) |
| `stock_snapshot` | Joriy qoldiq — "BOZORDA YO'Q" da transfer taklifi uchun |
| `item_event` | Append-only audit: kim, qachon, nima qildi |
| `card_msg` | Kartaning oxirgi turi (rasmli/matnli) |
| `announced_arrival` | E'lon idempotentligi |
| `ref` | Uzun nomlar → qisqa int ID (callback_data 64 bayt chegarasi) |
| `billz_raw` | **Faqat XATO** API javoblari (`HTTP >= 400`) |
| `kv` | Token, refresh_token, muddat |

Sxema `povtor_bot/db/schema.sql` da, `CREATE TABLE IF NOT EXISTS` bilan —
har ishga tushishda idempotent qo'llanadi.

---

## Xavfsizlik

- Botdan faqat `ALLOWED_USER_IDS` dagilar foydalana oladi (`AuthMiddleware`).
- `.env` va `var/` git'ga tushmaydi (`.gitignore`).
- Haqiqiy `POVTOR_*.xlsx` fayllarida mijoz ma'lumoti bor — commit qilmang.
  `tests/fixtures/` da faqat raqamli, anonim dataset saqlanadi.
