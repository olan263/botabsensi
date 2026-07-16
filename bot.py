import os
import re
import math
import logging
import asyncio
from datetime import datetime

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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# 0. TOKEN BOT TELEGRAM
# ==========================================
# PENTING: JANGAN hardcode token di sini. Simpan di environment variable:
#   BOT_TOKEN = os.environ["BOT_TOKEN"]
# Token sebelumnya sempat ter-expose di chat/log -> WAJIB di-regenerate lewat
# @BotFather (/mybots -> pilih bot -> API Token -> Revoke current token),
# lalu isi token baru lewat environment variable, JANGAN ditulis di source code.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8826307677:AAGgt9O7eGgrgbcFOGIj3pHhs5p1tOs5a5Y")

# ID grup Telegram tujuan notifikasi otomatis (real-time tiap ada absen/kegiatan).
# Cara dapetin: tambahkan bot ke grup, kirim pesan apa aja di grup, lalu jalankan
# command /grupid di grup tsb — bot akan balas dengan ID grupnya (angka negatif).
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID", "-5591365135")

# ==========================================
# 0b. KONFIGURASI DATABASE POSTGRESQL
# ==========================================
# PENTING: JANGAN hardcode password di sini juga. Password sebelumnya sempat
# ter-expose -> WAJIB diganti di PostgreSQL, lalu isi lewat environment variable.
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "absensi_karyawan",
    "user": "postgres",
    "password": os.environ.get("DB_PASSWORD", "140505"),
}

# Connection pool kecil (1-5 koneksi) supaya tidak buka-tutup koneksi tiap query
_db_pool = psycopg2.pool.SimpleConnectionPool(1, 5, **DB_CONFIG)


def _cari_nama_karyawan_sync(kode):
    """Query sinkron ke tabel master 'karyawan' berdasarkan kode.
    Return nama (str) kalau ketemu, atau None kalau tidak ada."""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT nama FROM karyawan WHERE kode = %s", (kode,))
            hasil = cur.fetchone()
            return hasil[0] if hasil else None
    finally:
        _db_pool.putconn(conn)


async def cari_nama_karyawan(kode):
    """Versi async (dijalankan di thread terpisah supaya tidak memblokir bot)"""
    try:
        return await asyncio.to_thread(_cari_nama_karyawan_sync, kode)
    except Exception as e:
        logger.error(f"Gagal query database: {e}")
        return None


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


def _simpan_kegiatan_sync(tanggal, kode, nama_kegiatan, tag_lokasi, foto_kegiatan, hasil, no_hp_pic, nama_pic, jabatan_pic):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kegiatan (tanggal, kode, nama_kegiatan, tag_lokasi, foto_kegiatan, hasil, no_hp_pic, nama_pic, jabatan_pic)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (tanggal, kode, nama_kegiatan, tag_lokasi, foto_kegiatan, hasil, no_hp_pic, nama_pic, jabatan_pic),
            )
        conn.commit()
    finally:
        _db_pool.putconn(conn)


async def simpan_kegiatan(tanggal, kode, nama_kegiatan, tag_lokasi, foto_kegiatan, hasil, no_hp_pic, nama_pic, jabatan_pic):
    await asyncio.to_thread(
        _simpan_kegiatan_sync, tanggal, kode, nama_kegiatan, tag_lokasi, foto_kegiatan, hasil, no_hp_pic, nama_pic, jabatan_pic
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
                SELECT k.tanggal, k.kode, ky.nama, k.nama_kegiatan, k.nama_pic, k.jabatan_pic, k.no_hp_pic
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
# 2. FUNGSI UTILITAS & DETEKSI KOORDINAT FOTO
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


def validasi_lokasi(user_lat, user_lon):
    for lokasi in TITIK_LOKASI_RESMI:
        jarak = hitung_jarak_meter(user_lat, user_lon, lokasi["lat"], lokasi["lon"])
        if jarak <= 50:
            return True, lokasi["nama"], jarak
    return False, None, None


def sensor_nomor_hp(nomor):
    """Menyensor nomor HP, hanya menampilkan 3 digit awal, contoh: 081234567890 -> 081*********"""
    nomor_bersih = re.sub(r"\s+", "", nomor)
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
ABSEN_KODE, ABSEN_STATUS, ABSEN_LOKASI, ABSEN_FOTO, ABSEN_RENCANA, ABSEN_IZIN_KETERANGAN = range(6)
KEG_KODE, KEG_NAMA, KEG_LOKASI, KEG_FOTO, KEG_HASIL, KEG_NOHP, KEG_PIC, KEG_JABATAN, KEG_KONFIRMASI = range(6, 15)


# ---------- ALUR ABSEN MASUK ----------

async def absen_mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "📋 *ABSEN MASUK*\nMasukkan Kode Karyawan Anda:", parse_mode="Markdown"
    )
    return ABSEN_KODE


async def absen_kode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kode = update.message.text.strip()

    nama = await cari_nama_karyawan(kode)
    if nama is None:
        await update.message.reply_text(
            f"❌ Kode karyawan '{kode}' tidak ditemukan di database.\n"
            "Mohon cek kembali kode Anda, atau hubungi admin kalau kode ini seharusnya terdaftar.\n\n"
            "➡️ Ketik /absen lagi untuk mencoba dari awal."
        )
        return ConversationHandler.END

    tanggal = _tanggal_hari_ini()
    sudah_absen = await cek_sudah_absen(tanggal, kode)
    if sudah_absen is not None:
        _, status_lama = sudah_absen
        await update.message.reply_text(
            f"⚠️ Anda SUDAH absen hari ini dengan status: *{escape_markdown(status_lama)}*.\n"
            "Absen hanya bisa dilakukan 1x per hari, jadi tidak bisa absen ulang.\n\n"
            "Kalau ini keliru, hubungi admin.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["kode"] = kode
    context.user_data["nama"] = nama

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
        f"Halo, *{escape_markdown(nama)}*! 👋\n\n"
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
        tombol_lokasi = ReplyKeyboardMarkup(
            [[KeyboardButton("📍 Kirim Lokasi Saya Sekarang", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await query.message.reply_text(
            "📍 Silakan share lokasi Anda dulu (tekan tombol di bawah).\n"
            "Pastikan Anda sedang berada di lokasi yang benar saat share.",
            reply_markup=tombol_lokasi,
        )
        return ABSEN_LOKASI

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


async def absen_izin_keterangan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keterangan = update.message.text.strip()
    status_final = context.user_data["status_manual"]
    kode = context.user_data["kode"]
    nama = context.user_data["nama"]
    tanggal = _tanggal_hari_ini()

    try:
        await simpan_absensi(
            tanggal,
            kode,
            nama,
            None,          # tag_lokasi: tidak relevan untuk sakit/izin
            None,          # foto: tidak relevan untuk sakit/izin
            keterangan,    # dipakai untuk menyimpan keterangan/alasan
            datetime.now().strftime("%H:%M"),
            status_final,
        )
    except Exception as e:
        logger.error(f"Gagal simpan absensi (sakit/izin) ke database: {e}")
        await update.message.reply_text(
            "⚠️ Terjadi kesalahan saat menyimpan data ke database. Coba lagi atau hubungi admin.",
        )
        return ConversationHandler.END

    emoji_status = "🤒" if status_final == "Sakit" else "📄"
    await update.message.reply_text(
        f"✅ Absen tercatat sebagai *{escape_markdown(status_final)}*.\nKeterangan: {escape_markdown(keterangan)}",
        parse_mode="Markdown",
    )

    caption = (
        f"{emoji_status} {status_final.upper()}\n\n"
        f"👤 {nama} ({kode})\n"
        f"🕒 {datetime.now().strftime('%H:%M')}\n"
        f"📝 Keterangan: {keterangan}"
    )
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

    tervalidasi, nama_lokasi, jarak = validasi_lokasi(lat, lon)
    if not tervalidasi:
        tombol_lokasi = ReplyKeyboardMarkup(
            [[KeyboardButton("📍 Kirim Lokasi Saya Sekarang", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text(
            "❌ Lokasi Anda berada di luar radius 50 meter dari titik resmi.\n"
            f"(Koordinat Anda: {lat:.6f}, {lon:.6f})\n\n"
            "📍 Silakan pindah ke lokasi yang benar, lalu kirim ulang lokasi lewat tombol di bawah "
            "(tidak perlu ketik /absen dari awal lagi).",
            reply_markup=tombol_lokasi,
        )
        return ABSEN_LOKASI

    context.user_data["tag_lokasi"] = f"{nama_lokasi} ({jarak:.1f} meter)"

    await update.message.reply_text(
        f"✅ Lokasi tervalidasi: *{escape_markdown(nama_lokasi)}* ({jarak:.1f} m dari titik resmi)\n\n"
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

    await update.message.reply_text(
        f"✅ Absen berhasil tersimpan!\nStatus: *{escape_markdown(status_absen)}*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )

    caption = (
        "✅ ABSEN MASUK\n\n"
        f"👤 {context.user_data['nama']} ({kode})\n"
        f"🕒 {datetime.now().strftime('%H:%M')} — {status_absen}\n"
        f"📍 {context.user_data['tag_lokasi']}\n"
        f"📝 Rencana: {context.user_data['rencana_kegiatan']}"
    )
    await kirim_notifikasi_grup(context, caption, context.user_data["foto"])

    return ConversationHandler.END


# ---------- ALUR INPUT KEGIATAN ----------

async def kegiatan_mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "📋 *INPUT KEGIATAN*\nMasukkan Kode Karyawan Anda:", parse_mode="Markdown"
    )
    return KEG_KODE


async def keg_kode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kode = update.message.text.strip()
    tanggal = _tanggal_hari_ini()

    hasil = await cek_sudah_absen(tanggal, kode)
    if hasil is None:
        await update.message.reply_text(
            "❌ Anda BELUM melakukan absen masuk HARI INI! Silakan /absen terlebih dahulu.\n\n"
            "➡️ Ketik /absen, lalu setelah selesai baru ketik /kegiatan lagi."
        )
        return ConversationHandler.END

    nama, status = hasil
    if status in ("Sakit", "Izin"):
        await update.message.reply_text(
            f"❌ Anda tercatat *{escape_markdown(status)}* hari ini, sehingga tidak bisa mengisi laporan kegiatan.\n"
            "Kalau ini keliru, hubungi admin.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["kode"] = kode
    context.user_data["nama"] = nama
    context.user_data["tanggal"] = tanggal
    await update.message.reply_text(f"Halo, {nama}. Masukkan Nama Kegiatan:")
    return KEG_NAMA


async def keg_nama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nama_kegiatan"] = update.message.text.strip()

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

    if alamat:
        context.user_data["tag_lokasi_kegiatan"] = f"{alamat} ({link_maps})"
    else:
        context.user_data["tag_lokasi_kegiatan"] = link_maps

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
    await update.message.reply_text("Masukkan Hasil dari Kegiatan:")
    return KEG_HASIL


async def keg_hasil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["hasil"] = update.message.text.strip()
    await update.message.reply_text("Masukkan No HP PIC Lapangan:")
    return KEG_NOHP


async def keg_nohp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["no_hp_pic"] = update.message.text.strip()
    await update.message.reply_text("Masukkan Nama PIC Lapangan:")
    return KEG_PIC


async def keg_pic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nama_pic"] = update.message.text.strip()
    await update.message.reply_text("Masukkan Jabatan PIC Pelanggan (contoh: Manager Toko, Owner, Staff, dll):")
    return KEG_JABATAN


async def keg_jabatan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["jabatan_pic"] = update.message.text.strip()

    # PENTING: pesan ini SENGAJA tidak pakai parse_mode="Markdown".
    # sensor_nomor_hp() menghasilkan banyak karakter '*' berurutan (mis.
    # "081**********"), dan Telegram membaca '*' sebagai penanda bold —
    # banyak '*' yang tidak berpasangan bikin parser Markdown gagal
    # ("Can't parse entities"). Karena field lain di sini juga bebas diisi
    # user (bisa mengandung '_', '*', '`', '[' dst.), cara paling aman
    # adalah kirim sebagai teks polos tanpa parse_mode sama sekali.
    ringkasan = (
        "📋 KONFIRMASI DATA KEGIATAN\n\n"
        f"📌 Kegiatan: {context.user_data['nama_kegiatan']}\n"
        f"📍 Lokasi: {context.user_data['tag_lokasi_kegiatan']}\n"
        f"📝 Hasil: {context.user_data['hasil']}\n\n"
        f"👷 Nama PIC Pelanggan: {context.user_data['nama_pic']}\n"
        f"💼 Jabatan: {context.user_data['jabatan_pic']}\n"
        f"📱 No HP: {sensor_nomor_hp(context.user_data['no_hp_pic'])}\n\n"
        "Mohon cek lagi, apakah data di atas sudah benar?"
    )
    tombol_konfirmasi = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Ya, Simpan & Kirim", callback_data="keg_konfirmasi_ya")],
            [InlineKeyboardButton("❌ Batalkan", callback_data="keg_konfirmasi_batal")],
        ]
    )

    try:
        with open(context.user_data["foto_kegiatan"], "rb") as f:
            await update.message.reply_photo(
                photo=f,
                caption=ringkasan,
                reply_markup=tombol_konfirmasi,
            )
    except Exception as e:
        logger.warning(f"Gagal lampirkan foto di konfirmasi, fallback ke teks saja: {e}")
        await update.message.reply_text(
            ringkasan, reply_markup=tombol_konfirmasi
        )

    return KEG_KONFIRMASI


async def keg_konfirmasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    if query.data == "keg_konfirmasi_batal":
        await query.message.reply_text(
            "❌ Laporan kegiatan dibatalkan. Ketik /kegiatan untuk mulai ulang dari awal."
        )
        context.user_data.clear()
        return ConversationHandler.END

    kode = context.user_data["kode"]
    tanggal = context.user_data["tanggal"]

    try:
        await simpan_kegiatan(
            tanggal,
            kode,
            context.user_data["nama_kegiatan"],
            context.user_data["tag_lokasi_kegiatan"],
            context.user_data["foto_kegiatan"],
            context.user_data["hasil"],
            context.user_data["no_hp_pic"],
            context.user_data["nama_pic"],
            context.user_data["jabatan_pic"],
        )
    except Exception as e:
        logger.error(f"Gagal simpan kegiatan ke database: {e}")
        await query.message.reply_text(
            "⚠️ Terjadi kesalahan saat menyimpan kegiatan ke database. Coba lagi atau hubungi admin."
        )
        return ConversationHandler.END

    await query.message.reply_text("✅ Laporan kegiatan berhasil disimpan!")

    caption = (
        "📋 LAPORAN KEGIATAN\n\n"
        f"👤 {context.user_data['nama']} ({kode})\n"
        f"📌 Kegiatan: {context.user_data['nama_kegiatan']}\n"
        f"📍 Lokasi: {context.user_data['tag_lokasi_kegiatan']}\n"
        f"📝 Hasil: {context.user_data['hasil']}\n"
        f"👷 PIC Pelanggan: {context.user_data['nama_pic']} ({context.user_data['jabatan_pic']})\n"
        f"📱 No HP: {sensor_nomor_hp(context.user_data['no_hp_pic'])}"
    )
    await kirim_notifikasi_grup(context, caption, context.user_data["foto_kegiatan"])

    return ConversationHandler.END


# ---------- UMUM ----------

async def batal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Dibatalkan.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def grup_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command bantu setup: jalankan ini DI DALAM GRUP tujuan notifikasi,
    bot akan balas ID grup itu untuk diisi ke GROUP_CHAT_ID."""
    await update.message.reply_text(f"ID chat ini: `{update.effective_chat.id}`", parse_mode="Markdown")


async def mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Bot Absensi & Kegiatan*\n\n"
        "/absen - Absen masuk (Hadir/Sakit/Izin)\n"
        "/kegiatan - Input laporan kegiatan (wajib absen Hadir dulu)\n"
        "/rekapabsen - Lihat rekap riwayat absensi\n"
        "/rekapkegiatan - Lihat rekap riwayat kegiatan\n"
        "/grupid - (setup admin) Lihat ID chat grup ini\n"
        "/batal - Batalkan proses yang sedang berjalan",
        parse_mode="Markdown",
    )


async def rekap_absen(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    for tanggal, kode, nama_karyawan, nama_kegiatan, nama_pic, jabatan_pic, no_hp_pic in baris_kegiatan:
        if tanggal != tanggal_terakhir:
            teks += f"\n{tanggal}\n"
            tanggal_terakhir = tanggal
        teks += (
            f"• {kode} | {nama_karyawan} | {nama_kegiatan}\n"
            f"  PIC: {nama_pic} ({jabatan_pic}) — {sensor_nomor_hp(no_hp_pic)}\n"
        )

    # Telegram batasi 1 pesan maksimal 4096 karakter, jadi dipecah kalau kepanjangan
    batas = 4000
    for i in range(0, len(teks), batas):
        await update.message.reply_text(teks[i:i + batas])


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
            ABSEN_STATUS: [
                CallbackQueryHandler(absen_status, pattern="^status_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, absen_status_belum_tap),
            ],
            ABSEN_LOKASI: [MessageHandler(filters.LOCATION, absen_lokasi)],
            ABSEN_FOTO: [MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, absen_foto)],
            ABSEN_RENCANA: [MessageHandler(filters.TEXT & ~filters.COMMAND, absen_rencana)],
            ABSEN_IZIN_KETERANGAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, absen_izin_keterangan)],
        },
        fallbacks=[CommandHandler("batal", batal)],
    )

    conv_kegiatan = ConversationHandler(
        entry_points=[CommandHandler("kegiatan", kegiatan_mulai)],
        states={
            KEG_KODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_kode)],
            KEG_NAMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_nama)],
            KEG_LOKASI: [MessageHandler(filters.LOCATION, keg_lokasi)],
            KEG_FOTO: [MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, keg_foto)],
            KEG_HASIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_hasil)],
            KEG_NOHP: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_nohp)],
            KEG_PIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_pic)],
            KEG_JABATAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_jabatan)],
            KEG_KONFIRMASI: [CallbackQueryHandler(keg_konfirmasi, pattern="^keg_konfirmasi_")],
        },
        fallbacks=[CommandHandler("batal", batal)],
    )

    app.add_handler(CommandHandler("start", mulai))
    app.add_handler(CommandHandler("help", mulai))
    app.add_handler(CommandHandler("rekapabsen", rekap_absen))
    app.add_handler(CommandHandler("rekapkegiatan", rekap_kegiatan))
    app.add_handler(CommandHandler("grupid", grup_id))
    app.add_handler(conv_absen)
    app.add_handler(conv_kegiatan)
    app.add_error_handler(error_handler)

    logger.info("Bot berjalan...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()