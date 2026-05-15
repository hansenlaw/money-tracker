# =============================================================================
# main.py — Backend API untuk Dompet Finance Tracker
# Framework  : FastAPI (Python)
# Database   : PostgreSQL via Supabase (koneksi langsung pakai psycopg2)
# Deploy     : Railway
#
# Cara jalankan lokal:
#   1. Copy .env.example → .env, isi DATABASE_URL
#   2. pip install -r requirements.txt
#   3. uvicorn main:app --reload
#   4. Buka http://localhost:8000/docs untuk Swagger UI
# =============================================================================

from contextlib import asynccontextmanager, contextmanager
# asynccontextmanager → untuk lifespan handler (pengganti @on_event yang sudah deprecated)
# contextmanager      → untuk helper koneksi database

from typing import Optional, List
# Optional[X] = bisa None atau bertipe X
# List[X]     = list yang isinya bertipe X

import os           # baca environment variable (DATABASE_URL)
import psycopg2     # driver PostgreSQL untuk Python
import psycopg2.extras  # RealDictCursor → hasil query otomatis jadi dict

from datetime import date, datetime  # tipe data tanggal & waktu
from dotenv import load_dotenv       # baca file .env saat development lokal

from fastapi import FastAPI, HTTPException, Query
# FastAPI       = class utama aplikasi
# HTTPException = kirim error response dengan status code tertentu
# Query         = deklarasikan query parameter di URL (?month=2025-01)

from fastapi.middleware.cors import CORSMiddleware
# Middleware yang mengizinkan browser request ke domain berbeda

from pydantic import BaseModel, field_validator
# BaseModel      = class dasar untuk validasi data input
# field_validator = decorator untuk validasi custom per field


# -----------------------------------------------------------------------------
# LOAD ENVIRONMENT VARIABLES
# load_dotenv() membaca file .env di folder yang sama.
# Di Railway/production, env var langsung diset di dashboard — .env tidak wajib ada.
# -----------------------------------------------------------------------------
load_dotenv()

# DATABASE_URL TIDAK di-load di sini (top-level) karena Railway belum punya
# env var saat BUILD phase — hanya tersedia saat RUNTIME (server start).
# Solusi: akses os.environ["DATABASE_URL"] di dalam fungsi get_conn(),
# sehingga dibaca saat pertama kali koneksi dibuat (runtime), bukan saat import.


# -----------------------------------------------------------------------------
# DATABASE CONNECTION HELPER
# Context manager ini memastikan koneksi selalu ditutup meski ada error.
# Mencegah "connection leak" yang bisa bikin database kehabisan koneksi.
#
# Pola pakai:
#   with get_conn() as conn:
#       with conn.cursor() as cur:
#           cur.execute("SELECT ...")
# -----------------------------------------------------------------------------
@contextmanager
def get_conn():
    """Buka koneksi PostgreSQL, commit jika sukses, rollback jika error, tutup selalu."""
    # Baca DATABASE_URL di sini (runtime), bukan di top-level module (build time).
    # Railway: env var tersedia saat server jalan, BUKAN saat build.
    # Format: postgresql://postgres:PASSWORD@db.xxxx.supabase.co:5432/postgres
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL environment variable tidak ditemukan. "
            "Set di Railway dashboard → Variables → DATABASE_URL"
        )
    conn = psycopg2.connect(
        database_url,
        cursor_factory=psycopg2.extras.RealDictCursor
        # RealDictCursor: row hasil query bisa diakses seperti dict
        # contoh: row["amount"] bukan row[0]
    )
    try:
        yield conn       # berikan koneksi ke blok 'with'
        conn.commit()    # simpan semua perubahan ke database
    except Exception:
        conn.rollback()  # batalkan perubahan jika ada error
        raise            # lempar ulang error ke FastAPI untuk dikirim ke client
    finally:
        conn.close()     # tutup koneksi — SELALU dijalankan apapun yang terjadi


# -----------------------------------------------------------------------------
# LIFESPAN — Kode yang dijalankan saat server START dan STOP
# Ini pengganti @app.on_event("startup") yang sudah deprecated di FastAPI 0.93+.
# Membuat tabel 'transactions' jika belum ada → tidak perlu buat manual di Supabase.
# -----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inisialisasi database saat server pertama kali start."""
    # ── STARTUP ──────────────────────────────────────────────
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id          UUID          DEFAULT gen_random_uuid() PRIMARY KEY,
                    type        TEXT          NOT NULL CHECK (type IN ('income', 'expense')),
                    amount      NUMERIC(14,2) NOT NULL CHECK (amount > 0),
                    category    TEXT          NOT NULL,
                    description TEXT,
                    date        DATE          NOT NULL,
                    created_at  TIMESTAMPTZ   DEFAULT NOW()
                )
            """)
            # CREATE TABLE IF NOT EXISTS → tidak error jika tabel sudah ada

    yield  # ← titik di mana server berjalan & melayani request

    # ── SHUTDOWN (tambah cleanup di sini jika perlu) ──────────


# -----------------------------------------------------------------------------
# INISIALISASI FASTAPI
# title, version, description tampil di /docs (Swagger UI)
# -----------------------------------------------------------------------------
app = FastAPI(
    title="Dompet API",
    version="1.0.0",
    description="REST API untuk Finance Tracker — kelola pemasukan & pengeluaran harian.",
    lifespan=lifespan,  # daftarkan lifespan handler
)


# -----------------------------------------------------------------------------
# CORS MIDDLEWARE
# CORS = Cross-Origin Resource Sharing
# Browser memblokir request ke domain yang berbeda secara default (security policy).
# Frontend di Netlify (domain A) → API di Railway (domain B) → butuh CORS.
#
# allow_origins=["*"] = semua domain boleh akses → aman untuk personal use.
# Untuk produksi lebih aman, ganti dengan domain spesifik:
#   allow_origins=["https://nama-kamu.netlify.app"]
# -----------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://hanz-money-tracker.netlify.app"],      # domain yang diizinkan
    allow_credentials=True,   # izinkan cookie & authorization header
    allow_methods=["*"],      # izinkan semua HTTP method (GET, POST, PATCH, DELETE, dll)
    allow_headers=["*"],      # izinkan semua request header
)


# -----------------------------------------------------------------------------
# SCHEMA / MODEL DATA (Pydantic)
# TransactionIn = blueprint data yang dikirim frontend saat create/update transaksi.
# FastAPI otomatis validasi & kirim error 422 Unprocessable Entity jika tidak sesuai.
# -----------------------------------------------------------------------------
class TransactionIn(BaseModel):
    """Data input untuk membuat atau mengupdate transaksi."""
    type: str
    amount: float
    category: str
    description: Optional[str] = None
    date: date

    # Whitelist kategori yang diizinkan — konsisten dengan frontend CATEGORIES
    VALID_CATEGORIES = {
        "expense": [
            "Makan & Minum", "Transport", "Belanja", "Tagihan",
            "Kesehatan", "Hiburan", "Pendidikan", "Lainnya"
        ],
        "income": [
            "Gaji", "Freelance", "Investasi", "Bonus",
            "Transfer Masuk", "Lainnya"
        ],
    }

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validasi: type hanya boleh 'income' atau 'expense'."""
        if v not in ("income", "expense"):
            raise ValueError("type harus 'income' atau 'expense'")
        return v

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        """Validasi: amount harus lebih dari nol."""
        if v <= 0:
            raise ValueError("amount harus lebih dari 0")
        return round(v, 2)

    @field_validator("category")
    @classmethod
    def sanitize_category(cls, v: str) -> str:
        """Sanitasi dan validasi kategori: strip whitespace, cek whitelist."""
        v = v.strip()
        if not v:
            raise ValueError("category tidak boleh kosong")
        if len(v) > 100:
            raise ValueError("category terlalu panjang (maks 100 karakter)")
        return v

    @field_validator("description")
    @classmethod
    def sanitize_description(cls, v: Optional[str]) -> Optional[str]:
        """Sanitasi deskripsi: strip whitespace, batasi panjang."""
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None  # string kosong → None
        if len(v) > 500:
            raise ValueError("description terlalu panjang (maks 500 karakter)")
        return v

    def validate_category_for_type(self) -> None:
        """
        Validasi kategori terhadap type-nya.
        Dipanggil manual di endpoint karena butuh akses ke field type & category sekaligus.
        Jika kategori tidak ada di whitelist → tetap diterima (Lainnya sebagai fallback),
        tapi di-log sebagai warning agar tidak terlalu strict untuk user.
        """
        allowed = self.VALID_CATEGORIES.get(self.type, [])
        if self.category not in allowed:
            # Fallback ke "Lainnya" jika kategori tidak dikenal
            object.__setattr__(self, "category", "Lainnya")


# -----------------------------------------------------------------------------
# HELPER: BUILD WHERE CLAUSE UNTUK FILTER BULAN
# Dibuat sebagai fungsi terpisah karena dipakai di banyak endpoint.
# Menghindari duplikasi kode (DRY — Don't Repeat Yourself).
# -----------------------------------------------------------------------------
def build_month_where(month: Optional[str]) -> tuple:
    """
    Bangun WHERE clause SQL untuk filter berdasarkan bulan.

    Args:
        month (str|None): format "YYYY-MM", contoh "2025-01"

    Returns:
        tuple: (where_string, params_list)
        - Jika month diisi : ("WHERE DATE_TRUNC(...) = %s::date", ["2025-01-01"])
        - Jika month kosong: ("", [])

    Raises:
        HTTPException 400: jika format month salah
    """
    if not month:
        return "", []  # tidak ada filter → kembalikan string kosong & list kosong

    try:
        year, m = month.split("-")  # pisah "2025-01" → year="2025", m="01"
        # DATE_TRUNC('month', date) = potong tanggal ke awal bulan
        # Contoh: DATE_TRUNC('month', '2025-01-15') → '2025-01-01'
        # Membandingkan dengan '2025-01-01' → filter semua transaksi di bulan itu
        return "WHERE DATE_TRUNC('month', date) = %s::date", [f"{year}-{m}-01"]
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Format month salah. Gunakan YYYY-MM, contoh: 2025-01"
        )


# =============================================================================
# ROUTES / ENDPOINTS
# Urutan definisi penting! FastAPI mencocokkan URL dari atas ke bawah.
# /transactions/summary harus didefinisikan SEBELUM /transactions/{tx_id}
# agar "summary" tidak salah dibaca sebagai tx_id.
# =============================================================================

# -----------------------------------------------------------------------------
# GET / — Health check
# -----------------------------------------------------------------------------
@app.get("/")
def root():
    """Cek apakah API berjalan normal. Dipakai untuk verifikasi setelah deploy."""
    return {"status": "ok", "app": "Dompet API", "version": "1.0.0"}


# -----------------------------------------------------------------------------
# GET /transactions — Ambil daftar transaksi dengan filter opsional
#
# Query parameters (semua opsional, bisa dikombinasikan):
#   ?month=2025-01      → hanya transaksi bulan Januari 2025
#   ?type=expense       → hanya pengeluaran
#   ?category=Makan     → hanya kategori "Makan"
#   ?search=warteg      → cari di deskripsi & kategori (case-insensitive)
#
# Contoh URL lengkap:
#   /transactions?month=2025-01&type=expense&search=makan
# -----------------------------------------------------------------------------
@app.get("/transactions", response_model=List[dict])
def get_transactions(
    month:    Optional[str] = Query(None, description="Filter bulan, format YYYY-MM"),
    type:     Optional[str] = Query(None, description="Filter tipe: income atau expense"),
    category: Optional[str] = Query(None, description="Filter kategori spesifik"),
    search:   Optional[str] = Query(None, description="Cari teks di deskripsi dan kategori"),
):
    """Ambil semua transaksi. Mendukung filter bulan, tipe, kategori, dan pencarian teks."""
    conditions: list = []  # kumpulan kondisi WHERE
    params: list = []      # nilai untuk placeholder %s di query

    # ── Filter bulan ───────────────────────────────────────────
    where_month, month_params = build_month_where(month)
    if where_month:
        # Hapus "WHERE " dari depan karena akan digabung dengan kondisi lain
        conditions.append(where_month.replace("WHERE ", ""))
        params.extend(month_params)

    # ── Filter tipe (income/expense) ───────────────────────────
    if type:
        if type not in ("income", "expense"):
            raise HTTPException(status_code=400, detail="type harus 'income' atau 'expense'")
        conditions.append("type = %s")
        params.append(type)

    # ── Filter kategori (exact match) ──────────────────────────
    if category:
        conditions.append("category = %s")
        params.append(category)

    # ── Filter search (partial, case-insensitive) ───────────────
    if search:
        # ILIKE = LIKE tapi case-insensitive (fitur PostgreSQL)
        # % = wildcard, jadi %warteg% cocok dengan "Makan di warteg", "warteg dekat kantor", dll
        conditions.append("(description ILIKE %s OR category ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    # Gabung semua kondisi dengan AND
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, type, amount, category, description, date, created_at
                FROM transactions
                {where_clause}
                ORDER BY date DESC, created_at DESC
                """,
                # psycopg2 butuh None (bukan list kosong []) kalau tidak ada params
                params if params else None
            )
            rows = cur.fetchall()

    # Konversi tipe data ke format yang bisa di-JSON-encode
    result = []
    for r in rows:
        row = dict(r)
        row["amount"]     = float(row["amount"])           # Decimal → float
        row["date"]       = str(row["date"])               # date → "YYYY-MM-DD"
        row["created_at"] = row["created_at"].isoformat()  # datetime → ISO 8601 string
        result.append(row)

    return result


# -----------------------------------------------------------------------------
# GET /transactions/summary — Ringkasan finansial bulan tertentu
#
# Response:
#   {
#     "total_income":  1500000.0,
#     "total_expense": 800000.0,
#     "balance":       700000.0,
#     "total_tx":      12
#   }
# -----------------------------------------------------------------------------
@app.get("/transactions/summary")
def get_summary(month: Optional[str] = Query(None)):
    """Hitung total pemasukan, pengeluaran, saldo, dan jumlah transaksi."""
    where_clause, params = build_month_where(month)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    -- COALESCE: jika SUM = NULL (tidak ada data) → kembalikan 0
                    COALESCE(SUM(CASE WHEN type = 'income'  THEN amount ELSE 0 END), 0) AS total_income,
                    COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS total_expense,
                    COUNT(*) AS total_tx
                FROM transactions
                {where_clause}
                """,
                params if params else None
            )
            row = dict(cur.fetchone())

    total_income  = float(row["total_income"])
    total_expense = float(row["total_expense"])

    return {
        "total_income":  total_income,
        "total_expense": total_expense,
        "balance":       round(total_income - total_expense, 2),  # saldo = masuk - keluar
        "total_tx":      int(row["total_tx"]),
    }


# -----------------------------------------------------------------------------
# GET /transactions/chart — Data cashflow harian untuk grafik
#
# Response: list data per hari
#   [
#     { "date": "2025-01-01", "income": 0, "expense": 25000 },
#     { "date": "2025-01-05", "income": 5000000, "expense": 0 },
#     ...
#   ]
# Hanya hari yang ada transaksinya yang muncul (bukan semua hari dalam bulan).
# -----------------------------------------------------------------------------
@app.get("/transactions/chart")
def get_chart(month: Optional[str] = Query(None)):
    """Ambil data agregasi harian untuk line chart cashflow."""
    where_clause, params = build_month_where(month)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    date,
                    COALESCE(SUM(CASE WHEN type = 'income'  THEN amount ELSE 0 END), 0) AS income,
                    COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS expense
                FROM transactions
                {where_clause}
                GROUP BY date      -- satu baris per tanggal unik
                ORDER BY date ASC  -- urut kronologis dari awal bulan
                """,
                params if params else None
            )
            rows = cur.fetchall()

    return [
        {
            "date":    str(r["date"]),
            "income":  float(r["income"]),
            "expense": float(r["expense"]),
        }
        for r in rows
    ]


# -----------------------------------------------------------------------------
# GET /transactions/categories — Breakdown per kategori untuk analitik
#
# Response:
#   [
#     { "type": "expense", "category": "Makan & Minum", "total": 350000, "count": 8 },
#     { "type": "income",  "category": "Gaji",          "total": 5000000, "count": 1 },
#     ...
#   ]
# -----------------------------------------------------------------------------
@app.get("/transactions/categories")
def get_by_category(month: Optional[str] = Query(None)):
    """Agregasi total dan jumlah transaksi per kategori, dipisah income & expense."""
    where_clause, params = build_month_where(month)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    type,
                    category,
                    SUM(amount) AS total,  -- total rupiah per kategori
                    COUNT(*)    AS count   -- jumlah transaksi per kategori
                FROM transactions
                {where_clause}
                GROUP BY type, category
                ORDER BY type ASC, total DESC  -- expense dahulu, income kemudian; keduanya terbesar di atas
                """,
                params if params else None
            )
            rows = cur.fetchall()

    return [
        {
            "type":     r["type"],
            "category": r["category"],
            "total":    float(r["total"]),
            "count":    int(r["count"]),
        }
        for r in rows
    ]


# -----------------------------------------------------------------------------
# GET /transactions/trend — Tren keuangan N bulan terakhir
#
# Query parameters:
#   ?months=6   → ambil data 6 bulan terakhir (default 6, maks 24)
#
# Response: list data per bulan, diurutkan dari terlama ke terbaru
#   [
#     { "month": "2024-08", "label": "Agustus 2024", "income": 5000000, "expense": 3200000, "balance": 1800000 },
#     { "month": "2024-09", "label": "September 2024", ... },
#     ...
#   ]
# -----------------------------------------------------------------------------
@app.get("/transactions/trend")
def get_trend(months: int = Query(6, ge=1, le=24, description="Jumlah bulan ke belakang (1-24)")):
    """Ambil tren pemasukan & pengeluaran N bulan terakhir untuk grafik tren."""

    MONTH_NAMES = [
        "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember"
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    TO_CHAR(DATE_TRUNC('month', date), 'YYYY-MM') AS month,
                    COALESCE(SUM(CASE WHEN type = 'income'  THEN amount ELSE 0 END), 0) AS income,
                    COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS expense
                FROM transactions
                WHERE date >= DATE_TRUNC('month', NOW()) - INTERVAL '%s months'
                  AND date <  DATE_TRUNC('month', NOW()) + INTERVAL '1 month'
                GROUP BY DATE_TRUNC('month', date)
                ORDER BY DATE_TRUNC('month', date) ASC
                """,
                (months - 1,)
                # months - 1 karena bulan ini sudah ikut terhitung:
                # interval '5 months' ke belakang + bulan ini = 6 bulan total
            )
            rows = cur.fetchall()

    result = []
    for r in rows:
        income  = float(r["income"])
        expense = float(r["expense"])
        y, m    = r["month"].split("-")
        label   = f"{MONTH_NAMES[int(m) - 1]} {y}"
        result.append({
            "month":   r["month"],
            "label":   label,
            "income":  income,
            "expense": expense,
            "balance": round(income - expense, 2),
        })

    return result


# -----------------------------------------------------------------------------
# POST /transactions — Tambah transaksi baru
#
# Request body (JSON):
#   {
#     "type": "expense",
#     "amount": 25000,
#     "category": "Makan & Minum",
#     "description": "Makan siang warteg",  ← opsional
#     "date": "2025-01-15"
#   }
#
# Response: data transaksi lengkap termasuk id & created_at yang baru dibuat
# Status  : 201 Created
# -----------------------------------------------------------------------------
@app.post("/transactions", status_code=201)
def create_transaction(tx: TransactionIn):
    """Simpan satu transaksi baru ke database."""
    tx.validate_category_for_type()  # fallback kategori tidak dikenal → "Lainnya"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO transactions (type, amount, category, description, date)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                -- RETURNING * = kembalikan seluruh baris yang baru diinsert
                -- sehingga frontend langsung dapat id dan created_at tanpa query kedua
                """,
                (tx.type, tx.amount, tx.category, tx.description, tx.date)
            )
            row = dict(cur.fetchone())

    # Serialisasi tipe data
    row["amount"]     = float(row["amount"])
    row["date"]       = str(row["date"])
    row["created_at"] = row["created_at"].isoformat()
    return row


# -----------------------------------------------------------------------------
# PATCH /transactions/{tx_id} — Update transaksi yang sudah ada
#
# {tx_id} = UUID transaksi di URL, contoh: /transactions/abc-123-def
# Request body sama dengan POST — semua field dikirim ulang (full replace).
# Response: data transaksi setelah diupdate
# -----------------------------------------------------------------------------
@app.patch("/transactions/{tx_id}")
def update_transaction(tx_id: str, tx: TransactionIn):
    """Update seluruh field transaksi berdasarkan ID."""
    tx.validate_category_for_type()  # fallback kategori tidak dikenal → "Lainnya"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE transactions
                SET
                    type        = %s,
                    amount      = %s,
                    category    = %s,
                    description = %s,
                    date        = %s
                WHERE id = %s
                RETURNING *
                """,
                (tx.type, tx.amount, tx.category, tx.description, tx.date, tx_id)
            )
            row = cur.fetchone()

    # RETURNING tidak mengembalikan apa-apa → id tidak ada di database
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Transaksi dengan id '{tx_id}' tidak ditemukan"
        )

    row = dict(row)
    row["amount"]     = float(row["amount"])
    row["date"]       = str(row["date"])
    row["created_at"] = row["created_at"].isoformat()
    return row


# -----------------------------------------------------------------------------
# DELETE /transactions/{tx_id} — Hapus transaksi
#
# Response: 204 No Content (tidak ada body) jika berhasil
#           404 Not Found jika id tidak ditemukan
# -----------------------------------------------------------------------------
@app.delete("/transactions/{tx_id}", status_code=204)
def delete_transaction(tx_id: str):
    """Hapus transaksi berdasarkan ID."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM transactions WHERE id = %s RETURNING id",
                (tx_id,)
                # PENTING: (tx_id,) dengan trailing comma = tuple 1 elemen
                # Tanpa koma: (tx_id) = string biasa → psycopg2 error
            )
            deleted = cur.fetchone()

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Transaksi dengan id '{tx_id}' tidak ditemukan"
        )

    # Status 204 → FastAPI otomatis tidak kirim body response
