-- POVTOR bot ichki bazasi (SQLite).
-- Nega SQLite: kuniga ~150 pozitsiya va 3-5 menejer — bitta faylli baza yetarli,
-- tashqi servis (Postgres/Redis) ishga tushirish shartini olib tashlaydi.
-- Barcha SQL repo.py da to'plangan, ORM ishlatilmaydi.

PRAGMA journal_mode = WAL;   -- yozuv va o'qish bir-birini bloklamasin
PRAGMA foreign_keys = ON;

-- ───────────────────────── candidate ─────────────────────────
-- "Qayta buyurtma nomzodi" — botning asosiy jadvali.
-- Bir qator = bir (kun, filial, artikul, rang) kombinatsiyasi.
CREATE TABLE IF NOT EXISTS candidate (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_date     TEXT    NOT NULL,          -- YYYY-MM-DD, qaysi kuni aniqlangani
    shop_id           TEXT    NOT NULL,
    shop_name         TEXT    NOT NULL,          -- filial nomi (ANDALUS, ...)
    category_group    TEXT    NOT NULL DEFAULT '',  -- Billz level_1 (Obuv, Poyasnaya...)
    subcategory       TEXT    NOT NULL DEFAULT '',  -- podkategoriya (Рубашка с дл/р...)
    kind              TEXT    NOT NULL DEFAULT '',  -- Вид / Tur (Однотонный, Полоска...)
    product_name      TEXT    NOT NULL DEFAULT '',
    sku               TEXT    NOT NULL,          -- artikul
    color             TEXT    NOT NULL DEFAULT '',
    supplier          TEXT    NOT NULL DEFAULT '',
    product_id        TEXT    NOT NULL DEFAULT '',  -- Billz product UUID (variatsiya)
    image_url         TEXT    NOT NULL DEFAULT '',
    supply_price      REAL    NOT NULL DEFAULT 0,   -- manba valyutasidagi tannarx
    supply_currency   TEXT    NOT NULL DEFAULT 'UZS',
    price_uzs         INTEGER NOT NULL DEFAULT 0,   -- ko'rsatish uchun — HAR DOIM so'mda
    base_qty          INTEGER NOT NULL,             -- Asos: oxirgi transferda kelgan
    sold_qty          INTEGER NOT NULL,             -- Sotilgan
    percent           REAL    NOT NULL,
    days_to_50        INTEGER NOT NULL,             -- 50% ga necha kunda yetgan (0-based)
    grade             TEXT    NOT NULL,             -- 'ishonchli' | 'oddiy'
    recommended_qty   INTEGER NOT NULL,
    note              TEXT    NOT NULL DEFAULT '',  -- Izoh matni
    arrived_date      TEXT    NOT NULL,             -- skladdan kelgan sana
    -- Band QAYSI OYNA bilan topilgani. Menejer /tekshir da oynani o'zgartira
    -- oladi, shuning uchun "eskirgan" belgisi umumiy sozlamaga emas, aynan
    -- shu bandni topgan oynaga solishtiriladi.
    window_days       INTEGER NOT NULL DEFAULT 5,
    -- Band OXIRGI marta qaysi tekshiruvda topilgani (tekshiruv raqami).
    -- Menyu faqat eng oxirgi tekshiruv natijasini ko'rsatadi: menejer
    -- qoidani o'zi tanlagan ekan, ro'yxat aynan shu qoidaga javob berishi
    -- kerak. Aks holda eski tekshiruvlar natijasi ustma-ust to'planadi.
    last_run          INTEGER NOT NULL DEFAULT 0,

    status            TEXT    NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'taken', 'not_found')),
    transfer_hint     TEXT    NOT NULL DEFAULT '',  -- "BOZORDA YO'Q" da: qayerdan olsa bo'ladi
    -- Shu (filial, artikul, rang) uchun YANGI partiya kelgan sana.
    -- To'ldirilgan bo'lsa band menyudan chiqadi: ehtiyoj allaqachon qondirilgan,
    -- va uning raqamlari eski partiyaga tegishli. Aks holda bot "yana ol" deb
    -- turaverardi, sklad esa allaqachon yuborgan bo'lardi.
    superseded_at     TEXT,
    answered_by       INTEGER,
    answered_at       TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),

    -- Idempotentlik PARTIYAga bog'langan, kunga emas.
    --
    -- Nega arrived_date, detected_date emas: bitta partiya oyna ichida bir necha
    -- kun turadi va har kungi /tekshir uni qayta topadi. Kalitda detected_date
    -- bo'lsa har kuni YANGI qator yaratilardi — menejer bir bandni bir necha
    -- marta ko'rib, bir necha marta buyurtma berib yuborishi mumkin edi.
    --
    -- Yangi partiya kelsa (boshqa arrived_date) — bu haqiqatan yangi qaror,
    -- va u alohida qator sifatida qo'shiladi.
    UNIQUE (shop_id, sku, color, arrived_date)
);

-- Kaskad menyu shu indekslar ustida ishlaydi (kategoriya -> postavshik -> artikul)
CREATE INDEX IF NOT EXISTS idx_candidate_open
    ON candidate (status, detected_date, category_group, supplier, sku);
CREATE INDEX IF NOT EXISTS idx_candidate_sku
    ON candidate (sku, color);

-- ───────────────────────── product_cache ─────────────────────────
-- Rasm va tannarx birinchi so'ralganda shu yerga yoziladi.
-- Nega: /v2/products chaqiruvi 5 daqiqada 1 marta tavsiya etilgan, har karta
-- ochilganda API'ga borish mumkin emas.
CREATE TABLE IF NOT EXISTS product_cache (
    sku             TEXT NOT NULL,
    color           TEXT NOT NULL DEFAULT '',
    product_id      TEXT NOT NULL DEFAULT '',
    product_name    TEXT NOT NULL DEFAULT '',
    category_group  TEXT NOT NULL DEFAULT '',
    subcategory     TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL DEFAULT '',
    supplier        TEXT NOT NULL DEFAULT '',
    image_url       TEXT NOT NULL DEFAULT '',
    -- Billz CDN'dan to'g'ridan-to'g'ri ko'rsatish taqiqlangan, shuning uchun rasm
    -- bir marta Telegram'ga yuklanadi va keyin faqat shu file_id ishlatiladi
    tg_file_id      TEXT NOT NULL DEFAULT '',
    image_missing   INTEGER NOT NULL DEFAULT 0,  -- 1 => qayta urinilmaydi
    supply_price    REAL NOT NULL DEFAULT 0,
    supply_currency TEXT NOT NULL DEFAULT 'UZS',
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (sku, color)
);

-- ───────────────────────── product_variant ─────────────────────────
-- Billz product_id -> (artikul, rang, ...) xaritasi.
--
-- Nega kerak: transfer va sotuv hisobotlarida RANG yo'q (product_attributes
-- bo'sh keladi), faqat product_id bor. Rang esa katalogdagi
-- custom_fields["Цвет"] da. Nomzod (filial, artikul, rang) bo'yicha
-- aniqlanadigani uchun bu xarita majburiy.
--
-- Bir artikulda o'nlab variatsiya bo'ladi: rang x o'lcham setkasi x sezon.
CREATE TABLE IF NOT EXISTS product_variant (
    product_id     TEXT PRIMARY KEY,
    sku            TEXT NOT NULL,
    color          TEXT NOT NULL DEFAULT '',
    subcategory    TEXT NOT NULL DEFAULT '',
    kind           TEXT NOT NULL DEFAULT '',
    supplier       TEXT NOT NULL DEFAULT '',
    product_name   TEXT NOT NULL DEFAULT '',
    category_group TEXT NOT NULL DEFAULT '',
    image_file     TEXT NOT NULL DEFAULT '',   -- Billz faqat fayl nomini beradi
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_variant_sku ON product_variant (sku, color);

-- ───────────────────────── sku_sync ─────────────────────────
-- Qaysi artikul katalogdan qachon o'qilgani.
-- Katalog 76 000+ tovardan iborat va /v2/products 5 daqiqada 1 marta
-- tavsiya etilgan — shuning uchun har artikul faqat bir marta so'raladi
-- va SKU_SYNC_DAYS kun davomida qayta so'ralmaydi.
CREATE TABLE IF NOT EXISTS sku_sync (
    sku        TEXT PRIMARY KEY,
    synced_at  TEXT NOT NULL DEFAULT (datetime('now')),
    variants   INTEGER NOT NULL DEFAULT 0
);

-- ───────────────────────── card_msg ─────────────────────────
-- Telegram matnli xabarni rasmli xabarga TAHRIRLAY OLMAYDI. Shu sababli
-- oxirgi yuborilgan karta rasmli edimi yoki matnli — eslab qolinadi; tur
-- o'zgarsa eski xabar o'chirilib yangisi yuboriladi.
CREATE TABLE IF NOT EXISTS card_msg (
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    sku        TEXT    NOT NULL,
    has_photo  INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (chat_id, message_id)
);

-- ───────────────────────── item_event ─────────────────────────
-- Append-only audit. candidate.status faqat OXIRGI holatni saqlaydi;
-- "kim, qachon, nimani bosdi" tahlili uchun to'liq tarix kerak.
CREATE TABLE IF NOT EXISTS item_event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidate (id) ON DELETE CASCADE,
    user_id      INTEGER,
    action       TEXT    NOT NULL,        -- 'taken' | 'not_found' | 'detected' | ...
    payload      TEXT    NOT NULL DEFAULT '{}',  -- JSON
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_event_candidate ON item_event (candidate_id);

-- ───────────────────────── announced_arrival ─────────────────────────
-- "Yangi tovar e'loni" ikki marta yuborilmasin.
CREATE TABLE IF NOT EXISTS announced_arrival (
    arrived_date TEXT NOT NULL,
    shop_id      TEXT NOT NULL,
    sku          TEXT NOT NULL,
    color        TEXT NOT NULL DEFAULT '',
    announced_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (arrived_date, shop_id, sku, color)
);

-- ───────────────────────── stock_snapshot ─────────────────────────
-- Har bir /tekshir da Billz'dan olingan joriy qoldiq (barcha filiallar).
-- Nega alohida jadval: "BOZORDA YO'Q" bosilganda "boshqa filialda bormi?" degan
-- savolga darhol javob berish kerak, lekin har bosishda Billz'ga borish mumkin
-- emas (hisobotlar 30 daqiqada 1 marta tavsiya etilgan).
CREATE TABLE IF NOT EXISTS stock_snapshot (
    shop_id      TEXT    NOT NULL,
    shop_name    TEXT    NOT NULL DEFAULT '',
    sku          TEXT    NOT NULL,
    color        TEXT    NOT NULL DEFAULT '',
    quantity     INTEGER NOT NULL DEFAULT 0,
    snapshot_date TEXT   NOT NULL,
    PRIMARY KEY (shop_id, sku, color)
);
CREATE INDEX IF NOT EXISTS idx_stock_lookup ON stock_snapshot (sku, color, quantity);

-- ───────────────────────── ref ─────────────────────────
-- callback_data 64 baytdan oshmasligi kerak, lekin kategoriya/postavshik nomlari
-- uzun ("Dilshod Трико M424"). Shu sababli menyuda nom emas, qisqa int ID uzatiladi.
CREATE TABLE IF NOT EXISTS ref (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind  TEXT NOT NULL,   -- 'cat' | 'sup' | 'sku'
    value TEXT NOT NULL,
    UNIQUE (kind, value)
);

-- ───────────────────────── billz_raw ─────────────────────────
-- Xom API javoblari — hujjat bilan haqiqiy javob farq qilganda tekshirish uchun.
CREATE TABLE IF NOT EXISTS billz_raw (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint   TEXT NOT NULL,
    params     TEXT NOT NULL DEFAULT '{}',
    status     INTEGER NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_raw_created ON billz_raw (created_at);

-- ───────────────────────── kv ─────────────────────────
-- Token, refresh_token, ularning muddati va oxirgi USD kursi.
CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
