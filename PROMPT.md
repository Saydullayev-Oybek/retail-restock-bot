# POVTOR BOT — coding agent uchun prompt

> Quyidagi matnni Claude Code / Cursor / Claude'ga to'liq nusxalab bering.
> Oxirida qo'shimcha prompt variantlari bor.

---

## ASOSIY PROMPT

Sen tajribali Python backend dasturchisisan. Telegram bot yozasan.

### Kontekst

O'zbekistondagi kiyim-poyabzal chakana savdo tarmog'i uchun bot kerak. Tarmoqda
5 ta filial bor: ANDALUS, BERUNIY, INTEGRO, MAGNIT, SHAXRISTON.

Har kuni tizim "POVTOR" ro'yxatini yasaydi — bu qayta buyurtma qilinishi kerak
bo'lgan tovarlar ro'yxati (oxirgi 7 kunda kelgan va yaxshi sotilgan pozitsiyalar).
Ro'yxat `POVTOR_YYYY-MM-DD.xlsx` faylida keladi, har bir filial alohida sheet.

Sheet tuzilishi (sarlavha 3-qatorda, undan yuqorida izoh matni bor):

| Artikul | Rang | Podkategoriya | Tur | Postavshik | Asos | Sotilgan | Foiz | 50% ga yetgan kun | Daraja | Bugun beriladi (dona) | Izoh | OLINDI (dona) | BOZORDA YO'Q (×) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 39666 | Белый | Рубашка с дл/р | Однотонный | Sharof M255 | 5 | 4 | 80 | 3 | ishonchli | 10 | 80% sotildi | | |

Hozir jarayon qo'lda: buyer bozorga boradi, telefonda Excel ochib, oxirgi ikkita
ustunni to'ldiradi. Sekin va xatoga moyil. Kunlik hajm — 120-150 pozitsiya.

### Bot nima qilishi kerak

1. **Import** — admin botga xlsx yuboradi. Bot har bir sheetni alohida `task`
   sifatida bazaga yozadi. Har bir qator — `task_item`.

2. **Tarqatish** — `/tarqat YYYY-MM-DD` buyrug'i bilan har bir filialning
   ro'yxati o'sha filial guruhiga kartochka ko'rinishida yuboriladi.
   Kartochka: rasm (bo'lsa) + artikul + rang + postavshik + "Bugun kerak: N dona"
   + sotilish statistikasi. Ostida uchta inline tugma:
   `✅ Oldim` · `➗ Qisman` · `❌ Bozorda yo'q`

3. **Javob yig'ish** — buyer tugma bosadi:
   - `✅ Oldim` → `taken_qty = plan_qty`, status `taken`
   - `➗ Qisman` → raqamli inline pad chiqadi (1..plan_qty), tanlanadi
   - `❌ Bozorda yo'q` → status `not_found`
   - Javobdan keyin `↩️ Bekor qilish` tugmasi qoladi (xato bosishni tuzatish uchun)

4. **Filialdan chaqirtirish** — `❌ Bozorda yo'q` bosilganda bot `stock`
   jadvalidan o'sha artikul+rang qoldig'i bor filiallarni topadi (eng ko'pi
   birinchi, maksimum 3 ta) va ularning guruhiga so'rov yuboradi:
   `✅ Beraman` / `❌ Yo'q`. Javob so'ragan filialga qaytariladi.

5. **Hisobot** — `/holat` jonli progress (nechta javob berilgan), `/hisobot`
   Excel fayl qaytaradi: kirish fayl bilan bir xil ko'rinish, lekin `OLINDI` va
   `BOZORDA YO'Q` ustunlari to'ldirilgan + kim va qachon javob bergani.

6. **Rasm biriktirish** — admin botga rasm yuborib, caption'ga `#40499 Белый`
   yozsa, Telegram qaytargan `file_id` katalogga saqlanadi va keyingi
   yuborishlarda qayta ishlatiladi.

### Texnik talablar

- Python 3.12, **aiogram 3.13+** (aiogram 2 yoki pyTelegramBotAPI EMAS)
- PostgreSQL + **asyncpg**, toza SQL. ORM ishlatma — mantiq SQL'da toza chiqadi
- FSM storage — Redis
- `pydantic-settings` orqali `.env`. Kod ichida token/parol bo'lmasin
- Excel — pandas + openpyxl
- Docker Compose: db + redis + bot

### Majburiy best practice'lar

Bularning har biri aniq sabab bilan — chetlab o'tma:

1. **Holat botning xotirasida emas, bazada.** Bot restart bo'lsa 128 ta zakazning
   javobi yo'qolmasligi kerak. `task_item.message_id` ustuni bo'lsin — qayta
   ishga tushganda faqat `message_id IS NULL` bo'lganlar yuboriladi.

2. **Flood limit.** 128 ta xabarni oddiy `for` loop bilan yuborsang, Telegram
   429 (`TelegramRetryAfter`) qaytaradi va bot yarim yo'lda to'xtaydi. Sekundiga
   20 xabar throttle qil, `TelegramRetryAfter` da `retry_after` kutib 3 martagacha
   qayta urin. **Har bir muvaffaqiyatli yuborishni darhol bazaga yoz** — crash
   bo'lsa dublikat ketmasin.

3. **Yangi xabar emas, `edit_caption` / `edit_text`.** Guruhga 128 ta zakaz ustiga
   yana 128 ta "qabul qilindi" xabari tushsa chat o'qib bo'lmas holga keladi.
   `"message is not modified"` xatosini alohida ushlab, e'tiborsiz qoldir.

4. **`callback_data` — aiogram'ning `CallbackData` factory'si orqali.** Qo'lda
   `f"ans:{id}:{action}"` yig'ish va `split(":")` qilish taqiqlanadi: 64 bayt
   chegarasi, tip tekshiruvi yo'q, prefikslar to'qnashadi.

5. **Idempotentlik uch joyda:**
   - `UNIQUE (task_date, filial_id)` — bir kunlik ro'yxat ikki marta yuklanmaydi
   - `message_id IS NULL` filtri — kartochka ikki marta yuborilmaydi
   - javob yozishdan oldin `status = 'pending'` tekshiruvi — ikki kishi bir
     vaqtda bossa birinchi javob kuchda qoladi, ikkinchisiga alert chiqadi

6. **Append-only audit jadval** (`item_event`): kim, qachon, qanday amal, JSONB
   payload. `task_item.status` faqat oxirgi holatni saqlaydi — tahlil uchun
   to'liq tarix kerak.

7. **`parse_mode=HTML`, Markdown emas.** Postavshik nomlarida `_`, `-`, `*` bor
   (`ABUSAXIY 8-22 M64`) — Markdown ularni formatlash deb o'qib xato beradi.
   Barcha dinamik matnni `html.escape()` dan o'tkaz.

8. **Miqdorni matn bilan so'rama.** Guruhda "javob yozing" ishlamaydi — kimning
   javobi ekani chalkashadi. Inline raqamli pad ishlat.

9. **Excel parsingda sarlavha qatorini QIDIR**, `skiprows=2` deb qattiq berma.
   Ustunlarni nom bo'yicha ol, indeks bo'yicha emas. Sheet'da majburiy ustun
   yetishmasa — o'sha sheetni butunlay rad et, yarim yuklama.

10. **`callback_query`ga har doim `answer()` ber** — aks holda tugma
    foydalanuvchi telefonida 30 sekund aylanadi.

11. **Middleware orqali auth** — har bir handler'da "bu kim?" deb takrorlama.
    Ro'yxatdan o'tmagan foydalanuvchi handler'gacha yetib bormasin.

### Struktura

```
bot/
├── config.py          # pydantic-settings
├── db.py              # asyncpg pool + repository funksiyalari
├── callbacks.py       # CallbackData klasslari
├── keyboards.py       # inline klaviaturalar
├── texts.py           # kartochka matni shablonlari
├── middlewares.py     # AuthMiddleware
├── handlers/
│   ├── admin.py       # import, /tarqat, /holat, /hisobot, rasm biriktirish
│   └── buyer.py       # tugma javoblari, transfer
├── services/
│   ├── importer.py    # xlsx → dict
│   ├── sender.py      # throttle bilan yuborish
│   └── report.py      # natija → xlsx
└── main.py
sql/schema.sql
```

### Yetkazib berish

- `sql/schema.sql` — to'liq DDL, izohlari bilan
- Barcha modullar, ishlaydigan holatda
- `requirements.txt` (versiyalari qadalgan), `.env.example`, `Dockerfile`,
  `docker-compose.yml`
- `README.md` — ishga tushirish qadamlari va har bir arxitektura qarorining sababi

Kod izohlari **o'zbek tilida**, texnik atamalar inglizcha qolsin.
Izohda "nima qilyapti" emas, **"nega shunday"** yozilsin.

### Qabul qilish mezonlari

- [ ] 128 pozitsiyali fayl to'liq yuklanadi, hech bir qator tushib qolmaydi
- [ ] Tarqatishda 429 xatosi bo'lsa bot to'xtamaydi, kutib davom etadi
- [ ] Botni tarqatish o'rtasida `Ctrl+C` qilib qayta ishga tushirsak — dublikat
      kartochka ketmaydi
- [ ] Bir kartochkani ikki kishi bir vaqtda bossa — bitta javob yoziladi
- [ ] `/hisobot` kirish fayl bilan bir xil sheet'lar va ustunlarni qaytaradi

---

## QO'SHIMCHA PROMPTLAR

### Billz API integratsiyasi uchun

> `services/importer.py` o'rniga `services/billz.py` yoz. `fetch_povtor(filial_code,
> days=7)` funksiyasi Billz API'dan oxirgi `days` kun ichida kelgan va sotilgan
> tovarlarni oladi. **Chiqish formati `parse_workbook()` bilan aynan bir xil
> bo'lsin** — qolgan kod o'zgarmasligi kerak. Retry (tenacity, exponential
> backoff), rate limit, token refresh, va API javobini `raw_billz_response`
> jadvaliga JSONB sifatida saqlash bo'lsin (debug uchun). Alohida
> `sync_stock()` funksiyasi `stock` jadvalini yangilasin.

### Airflow avtomatlashtirish uchun

> Ikkita DAG yoz: (1) `povtor_daily` — har kuni 07:30 da Billz'dan ma'lumot
> olib `task` yaratadi, 08:00 da botga tarqatish signalini beradi;
> (2) `sync_stock_hourly` — har soatda `stock` jadvalini yangilaydi.
> Idempotent bo'lsin (backfill xavfsiz), `on_failure_callback` orqali admin
> Telegramiga xabar bersin.

### Test yozish uchun

> `pytest` + `pytest-asyncio` bilan testlar yoz: (1) importer — haqiqiy xlsx
> fayl fixture'i bilan, buzilgan fayl holatlari ham; (2) `answer_item()`
> tranzaksiyasi — ikki parallel chaqiruvda bitta javob yozilishini tekshir;
> (3) sender — mock bot bilan `TelegramRetryAfter` holatida qayta urinishni
> tekshir. Baza uchun `testcontainers` yoki alohida test schema.
