import os
import re
import math
import logging
import asyncio
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2 import pool

from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

try:
    import easyocr
    OCR_TERSEDIA = True
except ImportError:
    OCR_TERSEDIA = False

try:
    import requests
    REQUESTS_TERSEDIA = True
except ImportError:
    REQUESTS_TERSEDIA = False

from telegram import (
    Update,
    ReplyKeyboardRemove,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)
from telegram.request import HTTPXRequest
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# 0. TOKEN BOT TELEGRAM & KONFIGURASI DATABASE
# ==========================================
# PENTING - KEAMANAN:
# Token & password TIDAK BOLEH punya nilai default/fallback di source code.
# Versi sebelumnya sempat hardcode token+password sebagai fallback -> keduanya
# HARUS dianggap bocor dan WAJIB diganti:
#   - Token: @BotFather -> /mybots -> pilih bot -> API Token -> Revoke current token
#   - Password DB: ganti user postgres lewat ALTER ROLE / pgAdmin
# Bot ini SEKARANG akan menolak start kalau env var belum diisi (lihat di bawah),
# supaya tidak ada lagi secret yang nempel di source code.
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "Environment variable BOT_TOKEN belum diisi. "
        "Set dulu (token yang BARU, hasil revoke) sebelum menjalankan bot."
    )

# ID grup Telegram tujuan notifikasi otomatis (real-time tiap ada absen/kegiatan,
# plus rekap otomatis pagi & malam). Cara dapetin: tambahkan bot ke grup, kirim
# pesan apa saja, lalu jalankan /grupid di grup tsb.
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID", "")
try:
    GROUP_CHAT_ID_INT = int(GROUP_CHAT_ID) if GROUP_CHAT_ID else None
except ValueError:
    GROUP_CHAT_ID_INT = None

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ.get("DB_NAME", "absensi_karyawan"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD"),
}
if not DB_CONFIG["password"]:
    raise RuntimeError(
        "Environment variable DB_PASSWORD belum diisi. "
        "Set dulu (password yang BARU) sebelum menjalankan bot."
    )

# Connection pool kecil (1-5 koneksi) supaya tidak buka-tutup koneksi tiap query
_db_pool = psycopg2.pool.SimpleConnectionPool(1, 5, **DB_CONFIG)


# ==========================================
# 0b. HELPER DATABASE - MASTER KARYAWAN & VERIFIKASI AR
# ==========================================

def _cari_karyawan_by_kode_sync(kode):
    """Return (nama, telegram_id) kalau kode ketemu, atau None."""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT nama, telegram_id FROM karyawan WHERE kode = %s", (kode,))
            return cur.fetchone()
    finally:
        _db_pool.putconn(conn)


async def cari_karyawan_by_kode(kode):
    try:
        return await asyncio.to_thread(_cari_karyawan_by_kode_sync, kode)
    except Exception as e:
        logger.error(f"Gagal query karyawan by kode: {e}")
        return None


def _cari_kode_by_telegram_id_sync(telegram_id):
    """Return (kode, nama) kalau telegram_id ini sudah terikat ke suatu AR, atau None."""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT kode, nama FROM karyawan WHERE telegram_id = %s", (telegram_id,))
            return cur.fetchone()
    finally:
        _db_pool.putconn(conn)


async def cari_kode_by_telegram_id(telegram_id):
    try:
        return await asyncio.to_thread(_cari_kode_by_telegram_id_sync, telegram_id)
    except Exception as e:
        logger.error(f"Gagal query kode by telegram_id: {e}")
        return None


def _bind_telegram_id_sync(kode, telegram_id):
    """Ikat telegram_id ke kode HANYA kalau kode itu belum terikat ke siapa pun
    (telegram_id IS NULL). Return True kalau berhasil diikat."""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE karyawan SET telegram_id = %s WHERE kode = %s AND telegram_id IS NULL",
                (telegram_id, kode),
            )
            berhasil = cur.rowcount > 0
        conn.commit()
        return berhasil
    finally:
        _db_pool.putconn(conn)


async def bind_telegram_id(kode, telegram_id):
    try:
        return await asyncio.to_thread(_bind_telegram_id_sync, kode, telegram_id)
    except Exception as e:
        logger.error(f"Gagal bind telegram_id: {e}")
        return False


async def verifikasi_identitas_ar(kode_input, nama_input, telegram_id):
    """Validasi 2 faktor (kode + nama) sekaligus sinkronisasi dengan telegram_id.
    - Kode/nama tidak cocok di database -> ditolak.
    - Kode belum pernah diikat ke akun manapun -> otomatis diikat ke telegram_id ini
      (pendaftaran otomatis saat pemakaian pertama kali).
    - Kode sudah diikat ke akun telegram lain -> ditolak.
    Return (ok, pesan_error, nama_valid, kode_valid)."""
    kode = kode_input.strip().upper()
    row = await cari_karyawan_by_kode(kode)
    if row is None:
        return False, "kode karyawan tidak ditemukan di database", None, None

    nama_db, telegram_id_terikat = row
    if nama_input.strip().lower() != nama_db.strip().lower():
        return False, "nama tidak cocok dengan kode yang dimasukkan", None, None

    if telegram_id_terikat is None:
        berhasil = await bind_telegram_id(kode, telegram_id)
        if not berhasil:
            return False, "gagal memverifikasi akun, silakan coba lagi", None, None
        return True, None, nama_db, kode

    if telegram_id_terikat != telegram_id:
        return False, "kode ini sudah terdaftar dengan akun Telegram lain, hubungi admin", None, None

    return True, None, nama_db, kode


async def _cek_akses_rekap(update: Update):
    """Rekap manual boleh diakses dari grup notifikasi resmi, atau oleh AR yang
    telegram_id-nya sudah terverifikasi (pernah /absen atau /kegiatan sukses)."""
    if GROUP_CHAT_ID_INT is not None and update.effective_chat.id == GROUP_CHAT_ID_INT:
        return True
    row = await cari_kode_by_telegram_id(update.effective_user.id)
    return row is not None


def _simpan_absensi_sync(tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status):
    """Simpan/replace 1 baris absen untuk (tanggal, kode) tertentu."""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO absensi (tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tanggal, kode) DO UPDATE SET
                    nama = EXCLUDED.nama,
                    tag_lokasi = EXCLUDED.tag_lokasi,
                    foto = EXCLUDED.foto,
                    rencana_kegiatan = EXCLUDED.rencana_kegiatan,
                    jam_absen = EXCLUDED.jam_absen,
                    status = EXCLUDED.status
                """,
                (tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status),
            )
        conn.commit()
    finally:
        _db_pool.putconn(conn)


async def simpan_absensi(tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status):
    await asyncio.to_thread(
        _simpan_absensi_sync, tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status
    )


def _cek_sudah_absen_sync(tanggal, kode):
    """Return (nama, status) kalau kode ini sudah absen di tanggal tsb, atau None kalau belum."""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT nama, status FROM absensi WHERE tanggal = %s AND kode = %s", (tanggal, kode))
            return cur.fetchone()
    finally:
        _db_pool.putconn(conn)


async def cek_sudah_absen(tanggal, kode):
    return await asyncio.to_thread(_cek_sudah_absen_sync, tanggal, kode)


def _simpan_kegiatan_sync(
    tanggal, kode, nama_kegiatan, tag_lokasi, foto_kegiatan, hasil,
    no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kegiatan
                    (tanggal, kode, nama_kegiatan, tag_lokasi, foto_kegiatan, hasil,
                     no_hp_pic, nama_pic, jabatan_pic, status_deal, paket)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (tanggal, kode, nama_kegiatan, tag_lokasi, foto_kegiatan, hasil,
                 no_hp_pic, nama_pic, jabatan_pic, status_deal, paket),
            )
        conn.commit()
    finally:
        _db_pool.putconn(conn)


async def simpan_kegiatan(
    tanggal, kode, nama_kegiatan, tag_lokasi, foto_kegiatan, hasil,
    no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
):
    await asyncio.to_thread(
        _simpan_kegiatan_sync, tanggal, kode, nama_kegiatan, tag_lokasi, foto_kegiatan, hasil,
        no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
    )


def _ambil_rekap_absensi_sync():
    """Ambil semua riwayat absensi dari database untuk /rekapabsen"""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tanggal, kode, nama, jam_absen, status, tag_lokasi
                FROM absensi
                ORDER BY tanggal DESC, kode
                """
            )
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)


async def ambil_rekap_absensi():
    return await asyncio.to_thread(_ambil_rekap_absensi_sync)


def _ambil_rekap_kegiatan_sync():
    """Ambil semua riwayat kegiatan dari database untuk /rekapkegiatan"""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT k.tanggal, k.kode, ky.nama, k.nama_kegiatan, k.nama_pic, k.jabatan_pic,
                       k.no_hp_pic, k.status_deal, k.paket
                FROM kegiatan k
                JOIN karyawan ky ON k.kode = ky.kode
                ORDER BY k.tanggal DESC, k.kode
                """
            )
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)


async def ambil_rekap_kegiatan():
    return await asyncio.to_thread(_ambil_rekap_kegiatan_sync)


def _ambil_absensi_tanggal_sync(tanggal):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT kode, nama, jam_absen, status, tag_lokasi FROM absensi "
                "WHERE tanggal = %s ORDER BY kode",
                (tanggal,),
            )
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)


async def ambil_absensi_tanggal(tanggal):
    return await asyncio.to_thread(_ambil_absensi_tanggal_sync, tanggal)


def _ambil_kegiatan_tanggal_sync(tanggal):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT k.kode, ky.nama, k.nama_kegiatan, k.status_deal, k.paket
                FROM kegiatan k
                JOIN karyawan ky ON k.kode = ky.kode
                WHERE k.tanggal = %s
                ORDER BY k.kode
                """,
                (tanggal,),
            )
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)


async def ambil_kegiatan_tanggal(tanggal):
    return await asyncio.to_thread(_ambil_kegiatan_tanggal_sync, tanggal)


def _ambil_karyawan_belum_absen_sync(tanggal):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ky.kode, ky.nama
                FROM karyawan ky
                LEFT JOIN absensi a ON a.kode = ky.kode AND a.tanggal = %s
                WHERE a.kode IS NULL
                ORDER BY ky.kode
                """,
                (tanggal,),
            )
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)


async def ambil_karyawan_belum_absen(tanggal):
    return await asyncio.to_thread(_ambil_karyawan_belum_absen_sync, tanggal)


# ==========================================
# 1. KONFIGURASI DATA UTAMA
# ==========================================

TITIK_LOKASI_RESMI = [
    {"nama": "Abadijaya", "lat": -6.392621, "lon": 106.843638},
    {"nama": "Sukmajaya", "lat": -6.392450026373989, "lon": 106.84337345582173},
    {"nama": "Depok Nusantara", "lat": -6.389825906722636, "lon": 106.81416618650695},
    {"nama": "Cisalak", "lat": -6.3861459349696785, "lon": 106.87139071534261},
    {"nama": "Pancoran Mas", "lat": -6.395130753797186, "lon": 106.77637276321262},
    {"nama": "Cinere", "lat": -6.344098479526588, "lon": 106.7783042674652},
    {"nama": "Ex Plasa Grapari", "lat": -6.3886237759186395, "lon": 106.81892645767132},
    {"nama": "Cendana", "lat": -6.477439, "lon": 106.839196},
]

FOLDER_FOTO = "foto_absen"
os.makedirs(FOLDER_FOTO, exist_ok=True)


def _tanggal_hari_ini():
    return datetime.now().strftime("%Y-%m-%d")

_ocr_reader = None


def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        logger.info("Memuat model OCR (sekali di awal)...")
        _ocr_reader = easyocr.Reader(["id", "en"])
    return _ocr_reader


# ==========================================
# 2. FUNGSI UTILITAS
# ==========================================

def escape_markdown(teks):
    """Escape karakter spesial Markdown (legacy) dari teks bebas input user,
    supaya tidak bikin error 'Can't parse entities' di Telegram saat teks
    tsb disisipkan ke pesan yang pakai parse_mode='Markdown'."""
    if teks is None:
        return ""
    teks = str(teks)
    karakter_spesial = ["_", "*", "`", "["]
    for k in karakter_spesial:
        teks = teks.replace(k, f"\\{k}")
    return teks


def konversi_ke_desimal(value):
    d = float(value[0])
    m = float(value[1])
    s = float(value[2])
    return d + (m / 60.0) + (s / 3600.0)


def ambil_koordinat_dari_exif(path_foto):
    """METODE 1: baca metadata EXIF GPS foto (hanya ada kalau foto dikirim
    sebagai File/Document, bukan Photo terkompresi biasa)"""
    try:
        image = Image.open(path_foto)
        exif_data = image._getexif()
        if not exif_data:
            return None, None

        geotagging = {}
        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag == "GPSInfo":
                for t in value:
                    sub_tag = GPSTAGS.get(t, t)
                    geotagging[sub_tag] = value[t]

        if "GPSLatitude" in geotagging and "GPSLongitude" in geotagging:
            lat = konversi_ke_desimal(geotagging["GPSLatitude"])
            lon = konversi_ke_desimal(geotagging["GPSLongitude"])
            if geotagging.get("GPSLatitudeRef") == "S":
                lat = -lat
            if geotagging.get("GPSLongitudeRef") == "W":
                lon = -lon
            return lat, lon
    except Exception as e:
        logger.warning(f"Gagal membaca EXIF foto: {e}")

    return None, None


def ambil_koordinat_dari_teks_ocr(path_foto):
    """METODE 2 (fallback): baca watermark teks koordinat di badan foto lewat OCR
    (tetap kebaca walau foto dikompres Telegram, karena bagian dari pixel gambar)"""
    if not OCR_TERSEDIA:
        logger.warning("Modul 'easyocr' belum terinstall.")
        return None, None
    try:
        reader = _get_ocr_reader()
        hasil_ocr = reader.readtext(path_foto)
        teks_penuh = " ".join([blok[1] for blok in hasil_ocr])
        logger.info(f"Teks Terdeteksi di Foto (OCR): {teks_penuh}")

        pola_koordinat = re.findall(r"(-?\d+\.\d+)", teks_penuh)
        if len(pola_koordinat) >= 2:
            lat = float(pola_koordinat[0])
            lon = float(pola_koordinat[1])
            return lat, lon
    except Exception as e:
        logger.warning(f"Gagal memproses OCR foto: {e}")

    return None, None


def deteksi_koordinat_foto(path_foto):
    """Gabungan: coba EXIF dulu, fallback ke OCR watermark teks.
    Return (lat, lon, sumber)"""
    lat, lon = ambil_koordinat_dari_exif(path_foto)
    if lat is not None and lon is not None:
        return lat, lon, "EXIF"

    lat, lon = ambil_koordinat_dari_teks_ocr(path_foto)
    if lat is not None and lon is not None:
        return lat, lon, "OCR"

    return None, None, None


def hitung_jarak_meter(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def sensor_nomor_hp(nomor):
    """Menyensor nomor HP, hanya menampilkan 3 digit awal, contoh: 081234567890 -> 081*********"""
    nomor_bersih = re.sub(r"\s+", "", nomor or "")
    if len(nomor_bersih) <= 3:
        return "*" * len(nomor_bersih)
    depan = nomor_bersih[:3]
    tengah = "*" * (len(nomor_bersih) - 3)
    return f"{depan}{tengah}"


def buat_link_google_maps(lat, lon):
    """Bikin link Google Maps yang bisa langsung diklik & diarahkan ke titik GPS."""
    return f"https://www.google.com/maps?q={lat},{lon}"


def _reverse_geocode_sync(lat, lon):
    """Ubah koordinat GPS jadi nama alamat (pakai OpenStreetMap Nominatim, gratis tanpa API key).
    Return None kalau gagal/tidak tersedia, supaya alur bot tidak macet."""
    if not REQUESTS_TERSEDIA:
        return None
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "BotAbsensiKegiatan/1.0"},
            timeout=5,
        )
        data = resp.json()
        return data.get("display_name")
    except Exception as e:
        logger.warning(f"Gagal reverse geocode: {e}")
        return None


async def reverse_geocode(lat, lon):
    return await asyncio.to_thread(_reverse_geocode_sync, lat, lon)


async def kirim_notifikasi_grup(context: ContextTypes.DEFAULT_TYPE, caption: str, path_foto: str):
    """Kirim notifikasi otomatis ke grup (dengan foto asli), tidak menghentikan
    alur bot kalau gagal (misal GROUP_CHAT_ID belum diisi atau bot belum jadi admin grup)."""
    if not GROUP_CHAT_ID:
        logger.warning("GROUP_CHAT_ID belum diisi, notifikasi ke grup dilewati.")
        return
    if not path_foto:
        await kirim_notifikasi_grup_teks(context, caption)
        return
    try:
        with open(path_foto, "rb") as f:
            await context.bot.send_photo(
                chat_id=GROUP_CHAT_ID,
                photo=f,
                caption=caption,
            )
    except Exception as e:
        logger.error(f"Gagal kirim notifikasi ke grup: {e}")


async def kirim_notifikasi_grup_teks(context: ContextTypes.DEFAULT_TYPE, teks: str):
    """Versi teks (tanpa foto) untuk notifikasi grup, dipakai untuk kasus sakit/izin
    yang tidak ada foto/lokasi."""
    if not GROUP_CHAT_ID:
        logger.warning("GROUP_CHAT_ID belum diisi, notifikasi ke grup dilewati.")
        return
    try:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=teks)
    except Exception as e:
        logger.error(f"Gagal kirim notifikasi teks ke grup: {e}")


async def _download_foto_dari_pesan(update: Update, prefix: str):
    """Menerima foto baik dikirim sebagai Photo maupun Document (file gambar).
    Return path foto lokal, atau None kalau pesan bukan foto/gambar."""
    pesan = update.message
    tg_file = None
    ext = "jpg"

    if pesan.photo:
        tg_file = await pesan.photo[-1].get_file()
    elif pesan.document and pesan.document.mime_type and pesan.document.mime_type.startswith("image/"):
        tg_file = await pesan.document.get_file()
        if pesan.document.file_name and "." in pesan.document.file_name:
            ext = pesan.document.file_name.rsplit(".", 1)[-1]

    if tg_file is None:
        return None

    path_foto = os.path.join(FOLDER_FOTO, f"{prefix}_{int(datetime.now().timestamp())}.{ext}")
    await tg_file.download_to_drive(path_foto)
    return path_foto


# ==========================================
# 3. STATE CONVERSATION HANDLER
# ==========================================
(
    ABSEN_KODE, ABSEN_NAMA, ABSEN_STATUS, ABSEN_PILIH_KANTOR, ABSEN_LOKASI, ABSEN_FOTO,
    ABSEN_RENCANA, ABSEN_IZIN_KETERANGAN, ABSEN_IZIN_TAMBAH_FOTO, ABSEN_IZIN_FOTO,
) = range(10)

(
    KEG_KODE, KEG_NAMA_VERIFIKASI, KEG_NAMA_KEGIATAN, KEG_LOKASI, KEG_FOTO, KEG_HASIL,
    KEG_STATUS_DEAL, KEG_PAKET, KEG_NOHP, KEG_PIC, KEG_JABATAN,
    KEG_RINGKASAN_AKSI, KEG_PILIH_EDIT,
) = range(13)


# ---------- ALUR ABSEN MASUK ----------

async def absen_mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "📋 *ABSEN MASUK*\nMasukkan Kode Karyawan (AR) Anda:", parse_mode="Markdown"
    )
    return ABSEN_KODE


async def absen_kode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["kode_input"] = update.message.text.strip()
    await update.message.reply_text("Masukkan Nama Lengkap Anda (sesuai data karyawan):")
    return ABSEN_NAMA


async def absen_nama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nama_input = update.message.text.strip()
    kode_input = context.user_data.get("kode_input", "")
    telegram_id = update.effective_user.id

    ok, pesan_error, nama_valid, kode_valid = await verifikasi_identitas_ar(kode_input, nama_input, telegram_id)
    if not ok:
        await update.message.reply_text(
            f"❌ Verifikasi gagal: {pesan_error}.\n\n➡️ Ketik /absen untuk mencoba lagi."
        )
        return ConversationHandler.END

    tanggal = _tanggal_hari_ini()
    sudah_absen = await cek_sudah_absen(tanggal, kode_valid)
    if sudah_absen is not None:
        _, status_lama = sudah_absen
        await update.message.reply_text(
            f"⚠️ Anda SUDAH absen hari ini dengan status: *{escape_markdown(status_lama)}*.\n"
            "Absen hanya bisa dilakukan 1x per hari, jadi tidak bisa absen ulang.\n\n"
            "Kalau ini keliru, hubungi admin.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["kode"] = kode_valid
    context.user_data["nama"] = nama_valid

    tombol_status = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Hadir", callback_data="status_hadir")],
            [
                InlineKeyboardButton("🤒 Sakit", callback_data="status_sakit"),
                InlineKeyboardButton("📄 Izin", callback_data="status_izin"),
            ],
        ]
    )
    await update.message.reply_text(
        f"Halo, *{escape_markdown(nama_valid)}*! 👋\n\n"
        "Silakan pilih status kehadiran Anda hari ini (tap tombol di bawah):",
        parse_mode="Markdown",
        reply_markup=tombol_status,
    )
    return ABSEN_STATUS


async def absen_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dipanggil saat user TAP salah satu tombol inline (Hadir/Sakit/Izin)."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)  # hilangkan tombol biar ga bisa di-tap ulang

    pilihan = query.data  # "status_hadir" / "status_sakit" / "status_izin"

    if pilihan == "status_hadir":
        tombol_kantor = InlineKeyboardMarkup(
            [[InlineKeyboardButton(lok["nama"], callback_data=f"kantor_{i}")] for i, lok in enumerate(TITIK_LOKASI_RESMI)]
        )
        await query.message.reply_text(
            "🏢 Pilih lokasi kantor Anda hari ini:", reply_markup=tombol_kantor
        )
        return ABSEN_PILIH_KANTOR

    status_manual = "Sakit" if pilihan == "status_sakit" else "Izin"
    context.user_data["status_manual"] = status_manual
    await query.message.reply_text(
        f"Masukkan keterangan/alasan {status_manual.lower()} Anda:",
    )
    return ABSEN_IZIN_KETERANGAN


async def absen_status_belum_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback kalau user ngetik teks biasa alih-alih tap tombol."""
    await update.message.reply_text(
        "Mohon tap salah satu tombol di atas ya (✅ Hadir / 🤒 Sakit / 📄 Izin), bukan ketik teks."
    )
    return ABSEN_STATUS


async def absen_pilih_lokasi_kantor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    idx = int(query.data.split("_", 1)[1])
    kantor = TITIK_LOKASI_RESMI[idx]
    context.user_data["kantor_pilihan"] = kantor

    tombol_lokasi = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Kirim Lokasi Saya Sekarang", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await query.message.reply_text(
        f"🏢 Kantor dipilih: *{escape_markdown(kantor['nama'])}*\n\n"
        "📍 Sekarang share lokasi GPS Anda (harus dalam radius 50 meter dari kantor ini):",
        parse_mode="Markdown",
        reply_markup=tombol_lokasi,
    )
    return ABSEN_LOKASI


async def absen_izin_keterangan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["keterangan_izin"] = update.message.text.strip()
    tombol_foto = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📸 Ya, lampirkan foto", callback_data="izinfoto_ya")],
            [InlineKeyboardButton("Tidak, lewati", callback_data="izinfoto_tidak")],
        ]
    )
    await update.message.reply_text(
        "Ingin melampirkan foto bukti (opsional)?", reply_markup=tombol_foto
    )
    return ABSEN_IZIN_TAMBAH_FOTO


async def absen_izin_tambah_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    if query.data == "izinfoto_tidak":
        return await _simpan_dan_selesai_izin(query.message, context, foto_path=None)

    await query.message.reply_text("Silakan kirim FOTO bukti:")
    return ABSEN_IZIN_FOTO


async def absen_izin_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    path_foto = await _download_foto_dari_pesan(update, f"izin_{context.user_data.get('kode', 'x')}")
    if path_foto is None:
        await update.message.reply_text("Mohon kirim dalam bentuk FOTO atau File gambar ya.")
        return ABSEN_IZIN_FOTO
    return await _simpan_dan_selesai_izin(update.message, context, foto_path=path_foto)


async def _simpan_dan_selesai_izin(target_pesan, context, foto_path):
    """target_pesan: objek dengan .reply_text/.reply_photo -> bisa update.message
    atau query.message tergantung dari mana alurnya masuk."""
    status_final = context.user_data["status_manual"]
    kode = context.user_data["kode"]
    nama = context.user_data["nama"]
    keterangan = context.user_data["keterangan_izin"]
    tanggal = _tanggal_hari_ini()

    try:
        await simpan_absensi(
            tanggal, kode, nama, None, foto_path, keterangan,
            datetime.now().strftime("%H:%M"), status_final,
        )
    except Exception as e:
        logger.error(f"Gagal simpan absensi (sakit/izin) ke database: {e}")
        await target_pesan.reply_text(
            "⚠️ Terjadi kesalahan saat menyimpan data ke database. Coba lagi atau hubungi admin.",
        )
        return ConversationHandler.END

    emoji_status = "🤒" if status_final == "Sakit" else "📄"
    await target_pesan.reply_text(
        f"✅ Absen tercatat sebagai *{escape_markdown(status_final)}*.\nKeterangan: {escape_markdown(keterangan)}",
        parse_mode="Markdown",
    )

    caption = (
        f"{emoji_status} {status_final.upper()}\n\n"
        f"👤 {nama} ({kode})\n"
        f"🕒 {datetime.now().strftime('%H:%M')}\n"
        f"📝 Keterangan: {keterangan}"
    )
    if foto_path:
        await kirim_notifikasi_grup(context, caption, foto_path)
    else:
        await kirim_notifikasi_grup_teks(context, caption)

    return ConversationHandler.END


async def absen_lokasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text(
            "Mohon share lokasi lewat tombol 📍 yang tersedia, bukan ketik teks."
        )
        return ABSEN_LOKASI

    lat = update.message.location.latitude
    lon = update.message.location.longitude
    kantor = context.user_data["kantor_pilihan"]
    jarak = hitung_jarak_meter(lat, lon, kantor["lat"], kantor["lon"])

    if jarak > 50:
        tombol_lokasi = ReplyKeyboardMarkup(
            [[KeyboardButton("📍 Kirim Lokasi Saya Sekarang", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text(
            f"❌ Lokasi Anda berjarak {jarak:.1f} meter dari *{escape_markdown(kantor['nama'])}* "
            "(di luar radius 50 meter).\n\n"
            "📍 Silakan pindah ke lokasi yang benar, lalu kirim ulang lokasi lewat tombol di bawah "
            "(tidak perlu ketik /absen dari awal lagi).",
            parse_mode="Markdown",
            reply_markup=tombol_lokasi,
        )
        return ABSEN_LOKASI

    context.user_data["tag_lokasi"] = f"{kantor['nama']} ({jarak:.1f} meter)"

    await update.message.reply_text(
        f"✅ Lokasi tervalidasi: *{escape_markdown(kantor['nama'])}* ({jarak:.1f} m dari titik resmi)\n\n"
        "📸 Sekarang kirim FOTO sebagai bukti kehadiran Anda:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ABSEN_FOTO


async def absen_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    path_foto = await _download_foto_dari_pesan(update, f"absen_{context.user_data.get('kode', 'x')}")
    if path_foto is None:
        await update.message.reply_text("Mohon kirim dalam bentuk FOTO atau File gambar ya.")
        return ABSEN_FOTO

    context.user_data["foto"] = path_foto

    await update.message.reply_text("Masukkan Rencana Kegiatan Hari Ini:")
    return ABSEN_RENCANA


async def absen_rencana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["rencana_kegiatan"] = update.message.text.strip()

    jam_sekarang = datetime.now().time()
    batas_telat = datetime.strptime("11:00", "%H:%M").time()
    status_absen = "Tepat Waktu" if jam_sekarang <= batas_telat else "Telat (Lanjut untuk kegiatan lain)"

    kode = context.user_data["kode"]
    tanggal = _tanggal_hari_ini()

    try:
        await simpan_absensi(
            tanggal,
            kode,
            context.user_data["nama"],
            context.user_data["tag_lokasi"],
            context.user_data["foto"],
            context.user_data["rencana_kegiatan"],
            datetime.now().strftime("%H:%M"),
            status_absen,
        )
    except Exception as e:
        logger.error(f"Gagal simpan absensi ke database: {e}")
        await update.message.reply_text(
            "⚠️ Terjadi kesalahan saat menyimpan absen ke database. Coba lagi atau hubungi admin.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    caption = (
        "✅ ABSEN MASUK\n\n"
        f"👤 {context.user_data['nama']} ({kode})\n"
        f"🕒 {datetime.now().strftime('%H:%M')} — {status_absen}\n"
        f"📍 {context.user_data['tag_lokasi']}\n"
        f"📝 Rencana: {context.user_data['rencana_kegiatan']}"
    )

    # Ringkasan absen ditampilkan juga di chat, bukan cuma dikirim ke grup.
    await update.message.reply_text(caption, reply_markup=ReplyKeyboardRemove())
    await kirim_notifikasi_grup(context, caption, context.user_data["foto"])

    return ConversationHandler.END


# ---------- ALUR INPUT KEGIATAN / VISIT ----------

async def kegiatan_mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "📋 *INPUT KEGIATAN / VISIT*\nMasukkan Kode Karyawan (AR) Anda:", parse_mode="Markdown"
    )
    return KEG_KODE


async def keg_kode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["kode_input"] = update.message.text.strip()
    await update.message.reply_text("Masukkan Nama Lengkap Anda (sesuai data karyawan):")
    return KEG_NAMA_VERIFIKASI


async def keg_nama_verifikasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nama_input = update.message.text.strip()
    kode_input = context.user_data.get("kode_input", "")
    telegram_id = update.effective_user.id

    ok, pesan_error, nama_valid, kode_valid = await verifikasi_identitas_ar(kode_input, nama_input, telegram_id)
    if not ok:
        await update.message.reply_text(
            f"❌ Verifikasi gagal: {pesan_error}.\n\n➡️ Ketik /kegiatan untuk mencoba lagi."
        )
        return ConversationHandler.END

    tanggal = _tanggal_hari_ini()
    hasil = await cek_sudah_absen(tanggal, kode_valid)
    if hasil is None:
        await update.message.reply_text(
            "❌ Anda BELUM melakukan absen masuk HARI INI! Silakan /absen terlebih dahulu.\n\n"
            "➡️ Ketik /absen, lalu setelah selesai baru ketik /kegiatan lagi."
        )
        return ConversationHandler.END

    _, status = hasil
    if status in ("Sakit", "Izin"):
        await update.message.reply_text(
            f"❌ Anda tercatat *{escape_markdown(status)}* hari ini, sehingga tidak bisa mengisi laporan kegiatan.\n"
            "Kalau ini keliru, hubungi admin.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["kode"] = kode_valid
    context.user_data["nama"] = nama_valid
    context.user_data["tanggal"] = tanggal
    await update.message.reply_text(f"Halo, {nama_valid}. Masukkan Nama Kegiatan:")
    return KEG_NAMA_KEGIATAN


async def keg_nama_kegiatan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nama_kegiatan"] = update.message.text.strip()

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    tombol_lokasi = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Kirim Lokasi Kegiatan Sekarang", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "📍 Silakan share lokasi kegiatan (tekan tombol di bawah).\n"
        "Pastikan Anda sedang berada di lokasi kegiatan saat share.",
        reply_markup=tombol_lokasi,
    )
    return KEG_LOKASI


async def keg_lokasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text(
            "Mohon share lokasi lewat tombol 📍 yang tersedia, bukan ketik teks."
        )
        return KEG_LOKASI

    lat = update.message.location.latitude
    lon = update.message.location.longitude
    link_maps = buat_link_google_maps(lat, lon)
    alamat = await reverse_geocode(lat, lon)
    context.user_data["tag_lokasi_kegiatan"] = f"{alamat} ({link_maps})" if alamat else link_maps

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        await update.message.reply_text(
            f"✅ Lokasi kegiatan diperbarui.\n📍 {link_maps}", reply_markup=ReplyKeyboardRemove()
        )
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text(
        f"✅ Lokasi kegiatan tersimpan.\n📍 {link_maps}\n\n"
        "📸 Sekarang kirim FOTO Kegiatan:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return KEG_FOTO


async def keg_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    path_foto = await _download_foto_dari_pesan(update, f"kegiatan_{context.user_data.get('kode', 'x')}")
    if path_foto is None:
        await update.message.reply_text("Mohon kirim dalam bentuk FOTO atau File gambar ya.")
        return KEG_FOTO

    context.user_data["foto_kegiatan"] = path_foto

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text("Masukkan Hasil dari Kegiatan:")
    return KEG_HASIL


async def keg_hasil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["hasil"] = update.message.text.strip()

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    tombol_deal = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Deal", callback_data="deal_ya")],
            [InlineKeyboardButton("❌ Belum Deal", callback_data="deal_tidak")],
        ]
    )
    await update.message.reply_text("Bagaimana hasil visit ini?", reply_markup=tombol_deal)
    return KEG_STATUS_DEAL


async def keg_status_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    if query.data == "deal_ya":
        context.user_data["status_deal"] = "Deal"
        await query.message.reply_text("🎉 Masukkan Nama Paket yang deal dipilih:")
        return KEG_PAKET

    context.user_data["status_deal"] = "Belum Deal"
    context.user_data["paket"] = None

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await query.message.reply_text("Masukkan No HP PIC Lapangan:")
    return KEG_NOHP


async def keg_paket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["paket"] = update.message.text.strip()

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text("Masukkan No HP PIC Lapangan:")
    return KEG_NOHP


async def keg_nohp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["no_hp_pic"] = update.message.text.strip()

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text("Masukkan Nama PIC Lapangan:")
    return KEG_PIC


async def keg_pic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nama_pic"] = update.message.text.strip()

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text("Masukkan Jabatan PIC Pelanggan (contoh: Manager Toko, Owner, Staff, dll):")
    return KEG_JABATAN


async def keg_jabatan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["jabatan_pic"] = update.message.text.strip()
    context.user_data["mode_edit"] = False
    return await keg_tampilkan_ringkasan(update, context)


def _teks_ringkasan_kegiatan(ud, judul):
    status_deal = ud.get("status_deal", "-")
    baris_paket = f"📦 Paket: {ud.get('paket')}\n" if status_deal == "Deal" and ud.get("paket") else ""
    return (
        f"{judul}\n\n"
        f"📌 Kegiatan: {ud.get('nama_kegiatan')}\n"
        f"📍 Lokasi: {ud.get('tag_lokasi_kegiatan')}\n"
        f"📝 Hasil: {ud.get('hasil')}\n"
        f"🤝 Status: {status_deal}\n"
        f"{baris_paket}"
        f"👷 PIC Pelanggan: {ud.get('nama_pic')}\n"
        f"💼 Jabatan: {ud.get('jabatan_pic')}\n"
        f"📱 No HP: {sensor_nomor_hp(ud.get('no_hp_pic'))}"
    )


async def keg_tampilkan_ringkasan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    ringkasan = _teks_ringkasan_kegiatan(ud, "📋 KONFIRMASI DATA KEGIATAN") + "\n\nMohon cek lagi, apakah data di atas sudah benar?"

    tombol = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Edit", callback_data="keg_aksi_edit")],
            [InlineKeyboardButton("✅ Ya, Simpan & Kirim", callback_data="keg_aksi_submit")],
            [InlineKeyboardButton("❌ Batalkan", callback_data="keg_aksi_batal")],
        ]
    )

    target = update.callback_query.message if update.callback_query else update.message
    foto = ud.get("foto_kegiatan")

    if foto:
        try:
            with open(foto, "rb") as f:
                await target.reply_photo(photo=f, caption=ringkasan, reply_markup=tombol)
            return KEG_RINGKASAN_AKSI
        except Exception as e:
            logger.warning(f"Gagal lampirkan foto di ringkasan, fallback ke teks saja: {e}")

    await target.reply_text(ringkasan, reply_markup=tombol)
    return KEG_RINGKASAN_AKSI


async def keg_ringkasan_aksi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    if query.data == "keg_aksi_batal":
        await query.message.reply_text(
            "❌ Laporan kegiatan dibatalkan. Ketik /kegiatan untuk mulai ulang dari awal."
        )
        context.user_data.clear()
        return ConversationHandler.END

    if query.data == "keg_aksi_edit":
        ud = context.user_data
        daftar_tombol = [
            [InlineKeyboardButton("📌 Nama Kegiatan", callback_data="editf_nama_kegiatan")],
            [InlineKeyboardButton("📍 Lokasi", callback_data="editf_lokasi")],
            [InlineKeyboardButton("📸 Foto", callback_data="editf_foto")],
            [InlineKeyboardButton("📝 Hasil", callback_data="editf_hasil")],
            [InlineKeyboardButton("🤝 Status Deal", callback_data="editf_status")],
            [InlineKeyboardButton("📱 No HP PIC", callback_data="editf_nohp")],
            [InlineKeyboardButton("👷 Nama PIC", callback_data="editf_pic")],
            [InlineKeyboardButton("💼 Jabatan PIC", callback_data="editf_jabatan")],
        ]
        await query.message.reply_text(
            "Pilih data yang ingin diedit:", reply_markup=InlineKeyboardMarkup(daftar_tombol)
        )
        return KEG_PILIH_EDIT

    # query.data == "keg_aksi_submit"
    ud = context.user_data
    kode = ud["kode"]
    tanggal = ud["tanggal"]

    try:
        await simpan_kegiatan(
            tanggal, kode,
            ud["nama_kegiatan"], ud["tag_lokasi_kegiatan"], ud["foto_kegiatan"], ud["hasil"],
            ud["no_hp_pic"], ud["nama_pic"], ud["jabatan_pic"],
            ud.get("status_deal"), ud.get("paket"),
        )
    except Exception as e:
        logger.error(f"Gagal simpan kegiatan ke database: {e}")
        await query.message.reply_text(
            "⚠️ Terjadi kesalahan saat menyimpan kegiatan ke database. Coba lagi atau hubungi admin."
        )
        return ConversationHandler.END

    ringkasan_final = (
        f"👤 {ud['nama']} ({kode})\n" + _teks_ringkasan_kegiatan(ud, "✅ LAPORAN KEGIATAN TERSIMPAN")
    )
    await query.message.reply_text(ringkasan_final)
    await kirim_notifikasi_grup(context, ringkasan_final, ud.get("foto_kegiatan"))

    context.user_data.clear()
    return ConversationHandler.END


async def keg_pilih_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    context.user_data["mode_edit"] = True
    field = query.data.split("_", 1)[1]

    if field == "nama_kegiatan":
        await query.message.reply_text("Masukkan Nama Kegiatan baru:")
        return KEG_NAMA_KEGIATAN

    if field == "lokasi":
        tombol_lokasi = ReplyKeyboardMarkup(
            [[KeyboardButton("📍 Kirim Lokasi Kegiatan Sekarang", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await query.message.reply_text("Share lokasi kegiatan yang baru:", reply_markup=tombol_lokasi)
        return KEG_LOKASI

    if field == "foto":
        await query.message.reply_text("Kirim FOTO kegiatan yang baru:")
        return KEG_FOTO

    if field == "hasil":
        await query.message.reply_text("Masukkan Hasil kegiatan yang baru:")
        return KEG_HASIL

    if field == "status":
        tombol_deal = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ Deal", callback_data="deal_ya")],
                [InlineKeyboardButton("❌ Belum Deal", callback_data="deal_tidak")],
            ]
        )
        await query.message.reply_text("Pilih status hasil visit yang baru:", reply_markup=tombol_deal)
        return KEG_STATUS_DEAL

    if field == "nohp":
        await query.message.reply_text("Masukkan No HP PIC yang baru:")
        return KEG_NOHP

    if field == "pic":
        await query.message.reply_text("Masukkan Nama PIC yang baru:")
        return KEG_PIC

    if field == "jabatan":
        await query.message.reply_text("Masukkan Jabatan PIC yang baru:")
        return KEG_JABATAN

    # fallback tidak dikenal
    return await keg_tampilkan_ringkasan(update, context)


# ---------- UMUM ----------

async def batal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Dibatalkan.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def grup_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command bantu setup: jalankan ini DI DALAM GRUP tujuan notifikasi,
    bot akan balas ID grup itu untuk diisi ke GROUP_CHAT_ID."""
    await update.message.reply_text(f"ID chat ini: `{update.effective_chat.id}`", parse_mode="Markdown")


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lihat ID Telegram pribadi (berguna kalau admin perlu bind manual lewat SQL)."""
    await update.message.reply_text(f"ID Telegram Anda: `{update.effective_user.id}`", parse_mode="Markdown")


async def mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Bot Absensi & Kegiatan (khusus AR terdaftar)*\n\n"
        "/absen - Absen masuk (Hadir/Sakit/Izin)\n"
        "/kegiatan - Input laporan kegiatan/visit (wajib absen Hadir dulu)\n"
        "/rekapabsen - Lihat rekap riwayat absensi\n"
        "/rekapkegiatan - Lihat rekap riwayat kegiatan\n"
        "/myid - Lihat ID Telegram Anda\n"
        "/grupid - (setup admin) Lihat ID chat grup ini\n"
        "/batal - Batalkan proses yang sedang berjalan\n\n"
        "ℹ️ Saat pertama kali /absen atau /kegiatan, Anda akan diminta memasukkan "
        "Kode & Nama sesuai data karyawan — akun Telegram Anda otomatis terikat ke kode "
        "tersebut dan tidak bisa dipakai kode lain setelahnya.",
        parse_mode="Markdown",
    )


async def rekap_absen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _cek_akses_rekap(update):
        await update.message.reply_text(
            "❌ Anda belum terdaftar sebagai AR di sistem ini.\n"
            "Silakan /absen atau /kegiatan terlebih dahulu untuk verifikasi kode & nama Anda."
        )
        return

    try:
        baris_absensi = await ambil_rekap_absensi()
    except Exception as e:
        logger.error(f"Gagal ambil rekap absensi dari database: {e}")
        await update.message.reply_text("⚠️ Gagal mengambil data rekap absensi dari database. Coba lagi nanti.")
        return

    teks = "=== REKAP ABSENSI ===\n"
    if not baris_absensi:
        teks += "\nBelum ada data absensi.\n"

    tanggal_terakhir = None
    for tanggal, kode, nama, jam_absen, status, tag_lokasi in baris_absensi:
        if tanggal != tanggal_terakhir:
            teks += f"\n{tanggal}\n"
            tanggal_terakhir = tanggal
        teks += f"• {kode} | {nama} | Jam {jam_absen} | {status}\n"
        if tag_lokasi:
            teks += f"  Lokasi: {tag_lokasi}\n"

    # Telegram batasi 1 pesan maksimal 4096 karakter, jadi dipecah kalau kepanjangan
    batas = 4000
    for i in range(0, len(teks), batas):
        await update.message.reply_text(teks[i:i + batas])


async def rekap_kegiatan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _cek_akses_rekap(update):
        await update.message.reply_text(
            "❌ Anda belum terdaftar sebagai AR di sistem ini.\n"
            "Silakan /absen atau /kegiatan terlebih dahulu untuk verifikasi kode & nama Anda."
        )
        return

    try:
        baris_kegiatan = await ambil_rekap_kegiatan()
    except Exception as e:
        logger.error(f"Gagal ambil rekap kegiatan dari database: {e}")
        await update.message.reply_text("⚠️ Gagal mengambil data rekap kegiatan dari database. Coba lagi nanti.")
        return

    teks = "=== REKAP KEGIATAN ===\n"
    if not baris_kegiatan:
        teks += "\nBelum ada data kegiatan.\n"

    tanggal_terakhir = None
    for tanggal, kode, nama_karyawan, nama_kegiatan, nama_pic, jabatan_pic, no_hp_pic, status_deal, paket in baris_kegiatan:
        if tanggal != tanggal_terakhir:
            teks += f"\n{tanggal}\n"
            tanggal_terakhir = tanggal
        baris_paket = f", Paket: {paket}" if status_deal == "Deal" and paket else ""
        teks += (
            f"• {kode} | {nama_karyawan} | {nama_kegiatan} [{status_deal or '-'}{baris_paket}]\n"
            f"  PIC: {nama_pic} ({jabatan_pic}) — {sensor_nomor_hp(no_hp_pic)}\n"
        )

    batas = 4000
    for i in range(0, len(teks), batas):
        await update.message.reply_text(teks[i:i + batas])


# ==========================================
# 3b. REKAP OTOMATIS TERJADWAL (11:00 & 20:00 WIB)
# ==========================================

async def bangun_rekap_pagi(tanggal=None):
    tanggal = tanggal or _tanggal_hari_ini()
    hadir = await ambil_absensi_tanggal(tanggal)
    belum = await ambil_karyawan_belum_absen(tanggal)

    teks = f"☀️ REKAP ABSEN PAGI — {tanggal}\n\n"
    if hadir:
        teks += "Sudah Lapor:\n"
        for kode, nama, jam, status, _lokasi in hadir:
            teks += f"• {nama} ({kode}) — {status}, jam {jam}\n"
    else:
        teks += "Belum ada yang lapor absen.\n"

    teks += "\n"
    if belum:
        teks += f"⚠️ Belum Absen ({len(belum)} orang):\n"
        for kode, nama in belum:
            teks += f"• {nama} ({kode})\n"
    else:
        teks += "✅ Semua karyawan sudah absen.\n"

    return teks


async def bangun_rekap_malam(tanggal=None):
    tanggal = tanggal or _tanggal_hari_ini()
    absensi = await ambil_absensi_tanggal(tanggal)
    kegiatan = await ambil_kegiatan_tanggal(tanggal)
    jumlah_deal = sum(1 for baris in kegiatan if baris[3] == "Deal")

    teks = f"🌙 REKAP HARIAN — {tanggal}\n\n"
    teks += "== ABSENSI ==\n"
    if absensi:
        for kode, nama, jam, status, _lokasi in absensi:
            teks += f"• {nama} ({kode}) — {status}, jam {jam}\n"
    else:
        teks += "Tidak ada data absensi.\n"

    teks += f"\n== KEGIATAN ({len(kegiatan)} laporan, {jumlah_deal} Deal) ==\n"
    if kegiatan:
        for kode, nama, nama_kegiatan, status_deal, paket in kegiatan:
            baris_paket = f" — Paket: {paket}" if status_deal == "Deal" and paket else ""
            teks += f"• {nama} ({kode}) — {nama_kegiatan} [{status_deal or '-'}]{baris_paket}\n"
    else:
        teks += "Tidak ada laporan kegiatan.\n"

    return teks


async def job_rekap_pagi(context: ContextTypes.DEFAULT_TYPE):
    if not GROUP_CHAT_ID:
        return
    try:
        teks = await bangun_rekap_pagi()
    except Exception as e:
        logger.error(f"Gagal bangun rekap pagi: {e}")
        return
    for i in range(0, len(teks), 4000):
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=teks[i:i + 4000])


async def job_rekap_malam(context: ContextTypes.DEFAULT_TYPE):
    if not GROUP_CHAT_ID:
        return
    try:
        teks = await bangun_rekap_malam()
    except Exception as e:
        logger.error(f"Gagal bangun rekap malam: {e}")
        return
    for i in range(0, len(teks), 4000):
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=teks[i:i + 4000])


# ==========================================
# 4. ERROR HANDLER GLOBAL
# ==========================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Menangkap semua exception yang tidak tertangani di handler manapun,
    supaya selalu ter-log dengan rapi dan bot tidak diam-diam gagal."""
    logger.error(f"Terjadi exception saat memproses update: {context.error}", exc_info=context.error)


# ==========================================
# 5. SETUP APLIKASI BOT
# ==========================================

def main():
    # Timeout HTTP dinaikkan supaya lebih tahan terhadap koneksi yang lambat
    # (mengurangi kemungkinan telegram.error.TimedOut / httpx.ReadTimeout).
    request = HTTPXRequest(connect_timeout=20, read_timeout=20)
    app = ApplicationBuilder().token(BOT_TOKEN).request(request).build()

    conv_absen = ConversationHandler(
        entry_points=[CommandHandler("absen", absen_mulai)],
        states={
            ABSEN_KODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, absen_kode)],
            ABSEN_NAMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, absen_nama)],
            ABSEN_STATUS: [
                CallbackQueryHandler(absen_status, pattern="^status_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, absen_status_belum_tap),
            ],
            ABSEN_PILIH_KANTOR: [CallbackQueryHandler(absen_pilih_lokasi_kantor, pattern="^kantor_")],
            ABSEN_LOKASI: [MessageHandler(filters.LOCATION, absen_lokasi)],
            ABSEN_FOTO: [MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, absen_foto)],
            ABSEN_RENCANA: [MessageHandler(filters.TEXT & ~filters.COMMAND, absen_rencana)],
            ABSEN_IZIN_KETERANGAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, absen_izin_keterangan)],
            ABSEN_IZIN_TAMBAH_FOTO: [CallbackQueryHandler(absen_izin_tambah_foto, pattern="^izinfoto_")],
            ABSEN_IZIN_FOTO: [MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, absen_izin_foto)],
        },
        fallbacks=[CommandHandler("batal", batal)],
    )

    conv_kegiatan = ConversationHandler(
        entry_points=[CommandHandler("kegiatan", kegiatan_mulai)],
        states={
            KEG_KODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_kode)],
            KEG_NAMA_VERIFIKASI: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_nama_verifikasi)],
            KEG_NAMA_KEGIATAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_nama_kegiatan)],
            KEG_LOKASI: [MessageHandler(filters.LOCATION, keg_lokasi)],
            KEG_FOTO: [MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, keg_foto)],
            KEG_HASIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_hasil)],
            KEG_STATUS_DEAL: [CallbackQueryHandler(keg_status_deal, pattern="^deal_")],
            KEG_PAKET: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_paket)],
            KEG_NOHP: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_nohp)],
            KEG_PIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_pic)],
            KEG_JABATAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_jabatan)],
            KEG_RINGKASAN_AKSI: [CallbackQueryHandler(keg_ringkasan_aksi, pattern="^keg_aksi_")],
            KEG_PILIH_EDIT: [CallbackQueryHandler(keg_pilih_edit, pattern="^editf_")],
        },
        fallbacks=[CommandHandler("batal", batal)],
    )

    app.add_handler(CommandHandler("start", mulai))
    app.add_handler(CommandHandler("help", mulai))
    app.add_handler(CommandHandler("rekapabsen", rekap_absen))
    app.add_handler(CommandHandler("rekapkegiatan", rekap_kegiatan))
    app.add_handler(CommandHandler("grupid", grup_id))
    app.add_handler(CommandHandler("myid", my_id))
    app.add_handler(conv_absen)
    app.add_handler(conv_kegiatan)
    app.add_error_handler(error_handler)

    if app.job_queue is not None:
        wib = ZoneInfo("Asia/Jakarta")
        app.job_queue.run_daily(job_rekap_pagi, time=dt_time(11, 0, tzinfo=wib), name="rekap_pagi_otomatis")
        app.job_queue.run_daily(job_rekap_malam, time=dt_time(20, 0, tzinfo=wib), name="rekap_malam_otomatis")
        logger.info("Scheduler rekap otomatis aktif: 11:00 & 20:00 WIB.")
    else:
        logger.warning(
            "JobQueue tidak aktif (ekstra APScheduler belum terpasang). Rekap otomatis TIDAK berjalan. "
            "Install dengan: pip install \"python-telegram-bot[job-queue]\""
        )

    logger.info("Bot berjalan...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()