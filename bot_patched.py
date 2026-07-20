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

try:
    import gspread
    from google.oauth2.service_account import Credentials as GSheetCredentials
    GSHEET_TERSEDIA = True
except ImportError:
    GSHEET_TERSEDIA = False

try:
    from openpyxl import Workbook
    OPENPYXL_TERSEDIA = True
except ImportError:
    OPENPYXL_TERSEDIA = False

try:
    import msal
    MSAL_TERSEDIA = True
except ImportError:
    MSAL_TERSEDIA = False

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
# Kalau ada token/password yang PERNAH nempel di source code atau ke-screenshot,
# ANGGAP BOCOR dan WAJIB diganti:
#   - Token: @BotFather -> /mybots -> pilih bot -> API Token -> Revoke current token
#   - Password DB: ganti user postgres lewat ALTER ROLE / pgAdmin
# Bot ini akan menolak start kalau env var belum diisi (lihat di bawah),
# supaya tidak ada secret yang nempel di source code.
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "Environment variable BOT_TOKEN belum diisi. "
        "Set dulu (token yang BARU, hasil revoke) di file .env sebelum menjalankan bot."
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
        "Set dulu (password yang BARU) di file .env sebelum menjalankan bot."
    )

# Connection pool kecil (1-5 koneksi) supaya tidak buka-tutup koneksi tiap query
_db_pool = psycopg2.pool.SimpleConnectionPool(1, 5, **DB_CONFIG)

# ==========================================
# 0c. KONFIGURASI GOOGLE SHEETS (opsional)
# ==========================================
# Kalau kedua env var ini diisi, tiap absen/kegiatan baru otomatis ditulis
# juga ke Google Sheets (selain ke PostgreSQL). Kalau kosong, sync dilewati
# tanpa mengganggu jalannya bot.
# Cara setup singkat:
#   1. Buat Service Account di Google Cloud Console, aktifkan Google Sheets API
#   2. Download file kunci JSON-nya, taruh di folder bot (JANGAN commit ke Git)
#   3. Share Google Sheet tujuan ke email service account itu (akses Editor)
#   4. Isi path file JSON & ID spreadsheet (dari URL sheet) di .env
GSHEET_CREDENTIALS_FILE = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_FILE", "")
GSHEET_SPREADSHEET_ID = os.environ.get("GOOGLE_SHEETS_ID", "")

FOLDER_EXPORT = "export_excel"
os.makedirs(FOLDER_EXPORT, exist_ok=True)

_gsheet_ws_absensi = None
_gsheet_ws_kegiatan = None


def _init_gsheet():
    """Inisialisasi koneksi Google Sheets sekali di awal (dipanggil dari main()).
    Gagal/tidak dikonfigurasi -> sync dilewati, bot tetap jalan normal."""
    global _gsheet_ws_absensi, _gsheet_ws_kegiatan

    if not GSHEET_TERSEDIA:
        logger.warning("Modul 'gspread'/'google-auth' belum terinstall, sync Google Sheets dilewati.")
        return
    if not GSHEET_CREDENTIALS_FILE or not GSHEET_SPREADSHEET_ID:
        logger.warning(
            "GOOGLE_SHEETS_CREDENTIALS_FILE / GOOGLE_SHEETS_ID belum diisi di .env, "
            "sync Google Sheets dilewati."
        )
        return

    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = GSheetCredentials.from_service_account_file(GSHEET_CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        sh = client.open_by_key(GSHEET_SPREADSHEET_ID)

        try:
            ws_absensi = sh.worksheet("Absensi")
        except gspread.WorksheetNotFound:
            ws_absensi = sh.add_worksheet(title="Absensi", rows=2000, cols=10)
            ws_absensi.append_row(["Tanggal", "Kode", "Nama", "Tag Lokasi", "Rencana/Keterangan", "Jam Absen", "Status"])

        try:
            ws_kegiatan = sh.worksheet("Kegiatan")
        except gspread.WorksheetNotFound:
            ws_kegiatan = sh.add_worksheet(title="Kegiatan", rows=2000, cols=12)
            ws_kegiatan.append_row([
                "Tanggal", "Kode", "Nama Karyawan", "Nama Kegiatan", "Nama Usaha", "Tag Lokasi", "Hasil",
                "No HP PIC Pelanggan", "Nama PIC Pelanggan", "Jabatan PIC Pelanggan", "Status Deal", "Paket",
            ])

        _gsheet_ws_absensi = ws_absensi
        _gsheet_ws_kegiatan = ws_kegiatan
        logger.info("Koneksi Google Sheets berhasil diinisialisasi.")
    except Exception as e:
        logger.error(f"Gagal inisialisasi Google Sheets: {e}")
        _gsheet_ws_absensi = None
        _gsheet_ws_kegiatan = None


def _sync_absensi_ke_sheet_sync(tanggal, kode, nama, tag_lokasi, rencana_atau_keterangan, jam_absen, status):
    if _gsheet_ws_absensi is None:
        return
    try:
        _gsheet_ws_absensi.append_row(
            [tanggal, kode, nama, tag_lokasi or "", rencana_atau_keterangan or "", jam_absen, status]
        )
    except Exception as e:
        logger.error(f"Gagal sync absensi ke Google Sheets: {e}")


async def sync_absensi_ke_sheet(tanggal, kode, nama, tag_lokasi, rencana_atau_keterangan, jam_absen, status):
    await asyncio.to_thread(
        _sync_absensi_ke_sheet_sync, tanggal, kode, nama, tag_lokasi, rencana_atau_keterangan, jam_absen, status
    )


def _sync_kegiatan_ke_sheet_sync(
    tanggal, kode, nama_karyawan, nama_kegiatan, nama_usaha, tag_lokasi, hasil,
    no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
):
    if _gsheet_ws_kegiatan is None:
        return
    try:
        _gsheet_ws_kegiatan.append_row([
            tanggal, kode, nama_karyawan, nama_kegiatan, nama_usaha or "", tag_lokasi or "", hasil,
            no_hp_pic or "", nama_pic or "", jabatan_pic or "", status_deal or "", paket or "",
        ])
    except Exception as e:
        logger.error(f"Gagal sync kegiatan ke Google Sheets: {e}")


async def sync_kegiatan_ke_sheet(
    tanggal, kode, nama_karyawan, nama_kegiatan, nama_usaha, tag_lokasi, hasil,
    no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
):
    await asyncio.to_thread(
        _sync_kegiatan_ke_sheet_sync, tanggal, kode, nama_karyawan, nama_kegiatan, nama_usaha, tag_lokasi, hasil,
        no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
    )


# ==========================================
# 0d. KONFIGURASI EXCEL ONLINE / MICROSOFT 365 (opsional)
# ==========================================
# Sync ke file Excel yang disimpan di OneDrive (akun PERSONAL), lewat Microsoft Graph API.
# Beda dengan export manual (.xlsx lokal) - ini auto-sync real-time seperti Google Sheets,
# tapi datanya masuk ke Excel Table ("TabelAbsensi" & "TabelKegiatan") di file online.
#
# PENTING: akun personal (outlook.com/hotmail/gmail terdaftar sbg Microsoft account)
# TIDAK BISA pakai client secret + Application permission (itu cuma untuk akun
# organisasi/Azure AD). Untuk akun personal, dipakai DELEGATED permission +
# login interaktif SEKALI di awal (device code flow), hasilnya disimpan sebagai
# token cache di file lokal (MS_TOKEN_CACHE_FILE) supaya bot bisa refresh token
# otomatis tanpa login ulang.
#
# Cara setup singkat:
#   1. App Registration di Azure Portal, "Supported account types" harus mencakup
#      akun personal (pilih "Accounts in any organizational directory and personal
#      Microsoft accounts", atau "Personal Microsoft accounts only")
#   2. Authentication -> Advanced settings -> "Allow public client flows" -> Yes -> Save
#   3. API permissions -> Add permission -> Microsoft Graph -> DELEGATED permissions
#      -> cari & tambahkan "Files.ReadWrite" (bukan Application permission, TIDAK
#      butuh admin consent untuk akun personal)
#   4. Buat/siapkan file .xlsx di OneDrive personal - kalau tabel "TabelAbsensi" dan
#      "TabelKegiatan" belum ada di file itu, bot akan otomatis membuatkannya sendiri
#      saat start (worksheet baru + Excel Table + header), mirip seperti Google Sheets.
#   5. Cari Drive ID & Item ID file itu lewat Graph Explorer, isi di .env
#   6. Jalankan SEKALI: python setup_excel_online_auth.py -> login lewat browser
#      -> token cache tersimpan otomatis ke MS_TOKEN_CACHE_FILE
MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID", "")
MS_EXCEL_DRIVE_ID = os.environ.get("MS_EXCEL_DRIVE_ID", "")
MS_EXCEL_ITEM_ID = os.environ.get("MS_EXCEL_ITEM_ID", "")
MS_TOKEN_CACHE_FILE = os.environ.get("MS_TOKEN_CACHE_FILE", "msal_token_cache.bin")

MS_AUTHORITY = "https://login.microsoftonline.com/consumers"  # khusus akun Microsoft personal
MS_SCOPES = ["Files.ReadWrite"]

# Definisi tabel yang WAJIB ada di file Excel Online. Kalau salah satunya belum
# ada saat bot start, akan otomatis dibuatkan (worksheet baru + Excel Table + header),
# persis seperti perilaku _init_gsheet() di atas untuk Google Sheets.
DEFINISI_TABEL_EXCEL_ONLINE = {
    "TabelAbsensi": {
        "worksheet": "Absensi",
        "header": ["Tanggal", "Kode", "Nama", "Tag Lokasi", "Rencana/Keterangan", "Jam Absen", "Status"],
    },
    "TabelKegiatan": {
        "worksheet": "Kegiatan",
        "header": [
            "Tanggal", "Kode", "Nama Karyawan", "Nama Kegiatan", "Nama Usaha", "Tag Lokasi", "Hasil",
            "No HP PIC Pelanggan", "Nama PIC Pelanggan", "Jabatan PIC Pelanggan", "Status Deal", "Paket",
        ],
    },
}


def _excel_online_terkonfigurasi():
    return bool(
        MSAL_TERSEDIA and REQUESTS_TERSEDIA
        and MS_CLIENT_ID and MS_EXCEL_DRIVE_ID and MS_EXCEL_ITEM_ID
    )


def _load_msal_cache():
    cache = msal.SerializableTokenCache()
    if os.path.exists(MS_TOKEN_CACHE_FILE):
        with open(MS_TOKEN_CACHE_FILE, "r") as f:
            cache.deserialize(f.read())
    return cache


def _save_msal_cache(cache):
    if cache.has_state_changed:
        with open(MS_TOKEN_CACHE_FILE, "w") as f:
            f.write(cache.serialize())


def _get_ms_access_token():
    """Ambil access token Microsoft Graph secara silent (tanpa login ulang), pakai
    refresh token yang sudah tersimpan di token cache dari setup_excel_online_auth.py.
    Kalau belum pernah setup/login sama sekali, raise error yang jelas isinya."""
    cache = _load_msal_cache()
    app_msal = msal.PublicClientApplication(MS_CLIENT_ID, authority=MS_AUTHORITY, token_cache=cache)

    accounts = app_msal.get_accounts()
    result = None
    if accounts:
        result = app_msal.acquire_token_silent(MS_SCOPES, account=accounts[0])

    if not result:
        raise RuntimeError(
            "Belum ada sesi login Microsoft yang tersimpan. Jalankan 'python setup_excel_online_auth.py' "
            "satu kali dulu untuk login, sebelum bot bisa sync ke Excel Online."
        )

    _save_msal_cache(cache)

    if "access_token" not in result:
        raise RuntimeError(f"Gagal ambil token Microsoft Graph: {result.get('error_description', result)}")

    return result["access_token"]


def _kolom_excel(jumlah_kolom):
    """Ubah jumlah kolom (1, 2, 3, ...) jadi nama kolom Excel (A, B, C, ..., Z, AA, AB, ...)."""
    huruf = ""
    n = jumlah_kolom
    while n > 0:
        n, sisa = divmod(n - 1, 26)
        huruf = chr(65 + sisa) + huruf
    return huruf


def _url_workbook():
    return f"https://graph.microsoft.com/v1.0/drives/{MS_EXCEL_DRIVE_ID}/items/{MS_EXCEL_ITEM_ID}/workbook"


def _buat_worksheet_dan_tabel_excel_online(token, nama_tabel, nama_worksheet, header):
    """Bikin worksheet (kalau belum ada) + tulis header + buat Excel Table di atasnya,
    lalu rename table itu jadi `nama_tabel`. Dipanggil otomatis dari _init_excel_online()
    saat tabel yang dibutuhkan belum ditemukan di file Excel Online."""
    base = _url_workbook()
    headers_req = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 1. Pastikan worksheet-nya ada, kalau belum -> bikin baru
    resp = requests.get(f"{base}/worksheets", headers=headers_req, timeout=15)
    resp.raise_for_status()
    daftar_worksheet = [w["name"] for w in resp.json().get("value", [])]

    if nama_worksheet not in daftar_worksheet:
        resp = requests.post(
            f"{base}/worksheets/add",
            headers=headers_req,
            json={"name": nama_worksheet},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(f"Worksheet '{nama_worksheet}' berhasil dibuat di Excel Online.")

    # 2. Tulis baris header ke range A1:...1
    kolom_akhir = _kolom_excel(len(header))
    alamat_range = f"A1:{kolom_akhir}1"
    resp = requests.patch(
        f"{base}/worksheets/{nama_worksheet}/range(address='{alamat_range}')",
        headers=headers_req,
        json={"values": [header]},
        timeout=15,
    )
    resp.raise_for_status()

    # 3. Ubah range itu jadi Excel Table
    resp = requests.post(
        f"{base}/worksheets/{nama_worksheet}/tables/add",
        headers=headers_req,
        json={"address": f"{nama_worksheet}!{alamat_range}", "hasHeaders": True},
        timeout=15,
    )
    resp.raise_for_status()
    tabel_id = resp.json()["id"]

    # 4. Rename table ke nama yang dipakai bot (TabelAbsensi / TabelKegiatan)
    resp = requests.patch(
        f"{base}/tables/{tabel_id}",
        headers=headers_req,
        json={"name": nama_tabel},
        timeout=15,
    )
    resp.raise_for_status()

    logger.info(f"Tabel '{nama_tabel}' berhasil dibuat otomatis di worksheet '{nama_worksheet}'.")


def _init_excel_online():
    """Cek koneksi Excel Online sekali di awal (dipanggil dari main()).
    Kalau tabel 'TabelAbsensi'/'TabelKegiatan' belum ada, otomatis dibuatkan
    (worksheet baru + Excel Table + header) - sama seperti perilaku _init_gsheet().
    Gagal/tidak dikonfigurasi -> sync dilewati, bot tetap jalan normal."""
    if not MSAL_TERSEDIA:
        logger.warning("Modul 'msal' belum terinstall, sync Excel Online dilewati.")
        return
    if not _excel_online_terkonfigurasi():
        logger.warning(
            "Konfigurasi Excel Online (MS_CLIENT_ID/MS_EXCEL_DRIVE_ID/MS_EXCEL_ITEM_ID) "
            "belum lengkap di .env, sync Excel Online dilewati."
        )
        return
    try:
        token = _get_ms_access_token()
        url = f"{_url_workbook()}/tables"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        resp.raise_for_status()
        nama_tabel_ada = [t["name"] for t in resp.json().get("value", [])]

        for nama_tabel, info in DEFINISI_TABEL_EXCEL_ONLINE.items():
            if nama_tabel in nama_tabel_ada:
                continue
            logger.info(f"Tabel '{nama_tabel}' belum ditemukan di Excel Online, membuat otomatis...")
            try:
                _buat_worksheet_dan_tabel_excel_online(
                    token, nama_tabel, info["worksheet"], info["header"]
                )
            except Exception as e:
                logger.error(
                    f"Gagal membuat tabel '{nama_tabel}' otomatis di Excel Online: {e}. "
                    "Sync ke tabel ini akan gagal sampai dibuat manual atau bot di-restart."
                )

        logger.info("Koneksi Excel Online berhasil diinisialisasi.")
    except Exception as e:
        logger.error(f"Gagal inisialisasi Excel Online: {e}")


def _tambah_baris_excel_online_sync(nama_tabel, values):
    if not _excel_online_terkonfigurasi():
        return
    try:
        token = _get_ms_access_token()
        url = f"{_url_workbook()}/tables('{nama_tabel}')/rows/add"
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"values": [values]},
            timeout=15,
        )
        if resp.status_code >= 300:
            logger.error(f"Gagal tambah baris ke Excel Online ({nama_tabel}): {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Gagal sync ke Excel Online ({nama_tabel}): {e}")


async def sync_absensi_ke_excel_online(tanggal, kode, nama, tag_lokasi, rencana_atau_keterangan, jam_absen, status):
    await asyncio.to_thread(
        _tambah_baris_excel_online_sync,
        "TabelAbsensi",
        [tanggal, kode, nama, tag_lokasi or "", rencana_atau_keterangan or "", jam_absen, status],
    )


async def sync_kegiatan_ke_excel_online(
    tanggal, kode, nama_karyawan, nama_kegiatan, nama_usaha, tag_lokasi, hasil,
    no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
):
    await asyncio.to_thread(
        _tambah_baris_excel_online_sync,
        "TabelKegiatan",
        [
            tanggal, kode, nama_karyawan, nama_kegiatan, nama_usaha or "", tag_lokasi or "", hasil,
            no_hp_pic or "", nama_pic or "", jabatan_pic or "", status_deal or "", paket or "",
        ],
    )


def _build_excel_export_sync(start_date=None, end_date=None):
    """Bangun 1 file Excel (.xlsx) berisi 2 sheet (Absensi & Kegiatan).
    Jika start_date & end_date diisi, export hanya untuk rentang tersebut.
    Return path file yang dibuat."""
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "Absensi"
    ws1.append(["Tanggal", "Kode", "Nama", "Jam Absen", "Status", "Lokasi"])
    for row in _ambil_rekap_absensi_sync(start_date, end_date):
        ws1.append(list(row))

    ws2 = wb.create_sheet("Kegiatan")
    ws2.append([
        "Tanggal", "Kode", "Nama Karyawan", "Nama Kegiatan", "Nama Usaha", "Nama PIC Pelanggan",
        "Jabatan PIC Pelanggan", "No HP PIC Pelanggan", "Status Deal", "Paket",
    ])
    for row in _ambil_rekap_kegiatan_sync(start_date, end_date):
        ws2.append(list(row))

    nama_file = f"export_rekap_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    path_file = os.path.join(FOLDER_EXPORT, nama_file)
    wb.save(path_file)
    return path_file


# ==========================================
# 0b. HELPER DATABASE - MASTER KARYAWAN
# ==========================================
# CATATAN: verifikasi identitas di bot ini HANYA berdasarkan Kode Karyawan
# (tidak ada konfirmasi nama, tidak ada binding ke akun Telegram tertentu).
# Konsekuensinya: siapa pun yang tahu/menebak sebuah kode bisa absen/lapor
# kegiatan atas nama kode itu. Kalau butuh proteksi lebih ketat lagi nanti,
# bisa ditambahkan verifikasi 2 faktor (kode + nama + ikat ke 1 akun Telegram).

def _cari_nama_karyawan_sync(kode):
    """Return nama (str) kalau kode ketemu di tabel karyawan, atau None."""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT nama FROM karyawan WHERE kode = %s", (kode,))
            hasil = cur.fetchone()
            return hasil[0] if hasil else None
    finally:
        _db_pool.putconn(conn)


async def cari_nama_karyawan(kode):
    try:
        return await asyncio.to_thread(_cari_nama_karyawan_sync, kode)
    except Exception as e:
        logger.error(f"Gagal query karyawan by kode: {e}")
        return None


async def _cek_akses_rekap(update: Update):
    """Rekap manual (/rekapabsen, /rekapkegiatan) HANYA boleh diakses dari
    dalam grup notifikasi resmi. Ini karena tanpa binding akun per-AR, tidak
    ada cara membedakan AR terverifikasi dari orang random di chat pribadi."""
    return GROUP_CHAT_ID_INT is not None and update.effective_chat.id == GROUP_CHAT_ID_INT


def _simpan_absensi_sync(tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status):
    """Simpan/replace 1 baris absen untuk (tanggal, kode) tertentu."""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO absensi (tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    # Sync ke Google Sheets / Excel Online tidak boleh menggagalkan alur absen kalau
    # errornya di sisi mereka, jadi errornya sudah ditangani & di-log di dalam
    # masing-masing fungsi sync itu sendiri.
    await sync_absensi_ke_sheet(tanggal, kode, nama, tag_lokasi, rencana_kegiatan, jam_absen, status)
    await sync_absensi_ke_excel_online(tanggal, kode, nama, tag_lokasi, rencana_kegiatan, jam_absen, status)


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
                    (tanggal, kode, nama_kegiatan, nama_usaha, tag_lokasi, foto_kegiatan, hasil,
                     no_hp_pic, nama_pic, jabatan_pic, status_deal, paket)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (tanggal, kode, nama_kegiatan, nama_usaha, tag_lokasi, foto_kegiatan, hasil,
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
    # nama_karyawan diambil dari context.user_data["nama"] oleh pemanggil lewat kode di bawah;
    # di sini kita sync pakai data yang sama seperti yang barusan disimpan ke DB.
    nama_karyawan = await cari_nama_karyawan(kode)
    await sync_kegiatan_ke_sheet(
        tanggal, kode, nama_karyawan, nama_kegiatan, nama_usaha, tag_lokasi, hasil,
        no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
    )
    await sync_kegiatan_ke_excel_online(
        tanggal, kode, nama_karyawan, nama_kegiatan, nama_usaha, tag_lokasi, hasil,
        no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
    )


def _ambil_rekap_absensi_sync(start_date=None, end_date=None):
    """Ambil riwayat absensi dari database untuk /rekapabsen atau /exportexcel"""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            query = "SELECT tanggal, kode, nama, jam_absen, status, tag_lokasi FROM absensi"
            params = ()
            if start_date and end_date:
                query += " WHERE tanggal >= %s AND tanggal <= %s"
                params = (start_date, end_date)
            query += " ORDER BY tanggal DESC, kode"
            cur.execute(query, params)
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)



def _simpan_karyawan_sync(kode, nama):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO karyawan (kode, nama) VALUES (%s, %s) ON CONFLICT (kode) DO UPDATE SET nama = EXCLUDED.nama",
                (kode, nama)
            )
        conn.commit()
    finally:
        _db_pool.putconn(conn)

async def simpan_karyawan(kode, nama):
    await asyncio.to_thread(_simpan_karyawan_sync, kode, nama)

async def ambil_rekap_absensi(start_date=None, end_date=None):

    return await asyncio.to_thread(_ambil_rekap_absensi_sync)


def _ambil_rekap_kegiatan_sync(start_date=None, end_date=None):
    """Ambil riwayat kegiatan dari database untuk /rekapkegiatan atau /exportexcel"""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT k.tanggal, k.kode, ky.nama, k.nama_kegiatan, k.nama_usaha, k.nama_pic, k.jabatan_pic,
                       k.no_hp_pic, k.status_deal, k.paket
                FROM kegiatan k
                JOIN karyawan ky ON k.kode = ky.kode
            """
            params = ()
            if start_date and end_date:
                query += " WHERE k.tanggal >= %s AND k.tanggal <= %s"
                params = (start_date, end_date)
            query += " ORDER BY k.tanggal DESC, k.kode"
            cur.execute(query, params)
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)


async def ambil_rekap_kegiatan(start_date=None, end_date=None):
    return await asyncio.to_thread(_ambil_rekap_kegiatan_sync, start_date, end_date)


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
                SELECT k.kode, ky.nama, k.nama_kegiatan, k.nama_usaha, k.status_deal, k.paket
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

DAFTAR_JENIS_KEGIATAN = [
    "Visit",
    "Negosiasi",
    "Deal",
    "Follow Up",
    "Gangguan",
    "Collection - CTB",
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
    ABSEN_KODE, ABSEN_STATUS, ABSEN_LOKASI, ABSEN_FOTO,
    ABSEN_RENCANA, ABSEN_IZIN_KETERANGAN, ABSEN_IZIN_TAMBAH_FOTO, ABSEN_IZIN_FOTO,
    ABSEN_RINGKASAN_AKSI, ABSEN_PILIH_EDIT,
) = range(10)

(
    KEG_KODE, KEG_NAMA_KEGIATAN, KEG_NAMA_USAHA, KEG_LOKASI, KEG_FOTO, KEG_HASIL,
    KEG_STATUS_DEAL, KEG_PAKET, KEG_NOHP, KEG_PIC, KEG_JABATAN,
    KEG_RINGKASAN_AKSI, KEG_PILIH_EDIT,
) = range(10, 23)

(REG_KODE, REG_NAMA) = range(23, 25)



# ---------- ALUR ABSEN MASUK ----------

async def absen_mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "📋 *ABSEN MASUK*\nMasukkan Kode Karyawan (AR) Anda:", parse_mode="Markdown"
    )
    return ABSEN_KODE

async def absen_kode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kode = update.message.text.strip().upper()

    nama = await cari_nama_karyawan(kode)
    if nama is None:
        await update.message.reply_text(
            f"❌ Kode karyawan '{escape_markdown(kode)}' tidak ditemukan di database.\n"
            "Mohon cek kembali kode Anda, atau hubungi admin.\n\n"
            "Atau gunakan /register untuk mendaftar jika Anda karyawan baru.\n"
            "➡️ Ketik /absen lagi untuk mencoba dari awal.",
            parse_mode="Markdown",
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
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    pilihan = query.data

    if pilihan == "status_hadir":
        jam_sekarang = datetime.now().time()
        batas_telat = datetime.strptime("11:00", "%H:%M").time()
        status_absen = "Tepat Waktu" if jam_sekarang <= batas_telat else "Telat (Lanjut untuk kegiatan lain)"
        context.user_data["status_manual"] = status_absen
        
        await query.message.reply_text("Masukkan Rencana Kegiatan Hari Ini:")
        return ABSEN_RENCANA

    status_manual = "Sakit" if pilihan == "status_sakit" else "Izin"
    context.user_data["status_manual"] = status_manual
    await query.message.reply_text(f"Masukkan keterangan/alasan {status_manual.lower()} Anda:")
    return ABSEN_IZIN_KETERANGAN

async def absen_status_belum_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Mohon tap salah satu tombol di atas ya (✅ Hadir / 🤒 Sakit / 📄 Izin), bukan ketik teks.")
    return ABSEN_STATUS

async def absen_rencana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["rencana_kegiatan"] = update.message.text.strip()
    
    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await absen_tampilkan_ringkasan(update, context)

    await update.message.reply_text("📸 Silakan kirim FOTO sebagai bukti kehadiran Anda:")
    return ABSEN_FOTO

async def absen_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    path_foto = await _download_foto_dari_pesan(update, f"absen_{context.user_data.get('kode', 'x')}")
    if path_foto is None:
        await update.message.reply_text("Mohon kirim dalam bentuk FOTO atau File gambar ya.")
        return ABSEN_FOTO

    context.user_data["foto"] = path_foto

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await absen_tampilkan_ringkasan(update, context)

    tombol_lokasi = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Kirim Lokasi Saya Sekarang", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "📍 Sekarang share lokasi GPS Anda:",
        parse_mode="Markdown",
        reply_markup=tombol_lokasi,
    )
    return ABSEN_LOKASI

async def absen_lokasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text("Mohon share lokasi lewat tombol 📍 yang tersedia, bukan ketik teks.")
        return ABSEN_LOKASI

    lat = update.message.location.latitude
    lon = update.message.location.longitude
    link_maps = buat_link_google_maps(lat, lon)
    alamat = await reverse_geocode(lat, lon)
    context.user_data["tag_lokasi"] = f"{alamat} ({link_maps})" if alamat else link_maps

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        await update.message.reply_text(f"✅ Lokasi diperbarui.\n📍 {link_maps}", reply_markup=ReplyKeyboardRemove())
        return await absen_tampilkan_ringkasan(update, context)

    await update.message.reply_text(f"✅ Lokasi tersimpan.\n📍 {link_maps}", reply_markup=ReplyKeyboardRemove())
    return await absen_tampilkan_ringkasan(update, context)

def _teks_ringkasan_absen(ud, judul):
    return (
        f"{judul}\n\n"
        f"👤 {ud.get('nama')} ({ud.get('kode')})\n"
        f"📝 Status: {ud.get('status_manual')}\n"
        f"📋 Rencana: {ud.get('rencana_kegiatan')}\n"
        f"📍 Lokasi: {ud.get('tag_lokasi')}"
    )

async def absen_tampilkan_ringkasan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    ringkasan = _teks_ringkasan_absen(ud, "📋 KONFIRMASI DATA ABSENSI") + "\n\nMohon cek lagi, apakah data di atas sudah benar?"
    
    tombol = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Edit", callback_data="abs_aksi_edit")],
            [InlineKeyboardButton("✅ Ya, Submit", callback_data="abs_aksi_submit")],
            [InlineKeyboardButton("❌ Batalkan", callback_data="abs_aksi_batal")],
        ]
    )
    
    target = update.callback_query.message if update.callback_query else update.message
    foto = ud.get("foto")

    if foto:
        try:
            with open(foto, "rb") as f:
                await target.reply_photo(photo=f, caption=ringkasan, reply_markup=tombol)
            return ABSEN_RINGKASAN_AKSI
        except Exception as e:
            logger.warning(f"Gagal lampirkan foto di ringkasan absen: {e}")

    await target.reply_text(ringkasan, reply_markup=tombol)
    return ABSEN_RINGKASAN_AKSI

async def absen_ringkasan_aksi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    if query.data == "abs_aksi_batal":
        await query.message.reply_text("❌ Absen dibatalkan. Ketik /absen untuk mulai ulang.")
        context.user_data.clear()
        return ConversationHandler.END

    if query.data == "abs_aksi_edit":
        daftar_tombol = [
            [InlineKeyboardButton("📋 Rencana", callback_data="edita_rencana")],
            [InlineKeyboardButton("📸 Foto", callback_data="edita_foto")],
            [InlineKeyboardButton("📍 Lokasi", callback_data="edita_lokasi")],
        ]
        await query.message.reply_text("Pilih data yang ingin diedit:", reply_markup=InlineKeyboardMarkup(daftar_tombol))
        return ABSEN_PILIH_EDIT

    # Submit
    ud = context.user_data
    kode = ud["kode"]
    tanggal = _tanggal_hari_ini()
    jam_absen = datetime.now().strftime("%H:%M")

    try:
        await simpan_absensi(
            tanggal, kode, ud["nama"], ud["tag_lokasi"], ud["foto"],
            ud["rencana_kegiatan"], jam_absen, ud["status_manual"]
        )
    except Exception as e:
        logger.error(f"Gagal simpan absensi ke database: {e}")
        await query.message.reply_text("⚠️ Terjadi kesalahan saat menyimpan absen ke database.")
        return ConversationHandler.END

    ringkasan_final = _teks_ringkasan_absen(ud, "✅ ABSEN MASUK BERHASIL") + f"\n🕒 {jam_absen}"
    await query.message.reply_text(ringkasan_final)
    await kirim_notifikasi_grup(context, ringkasan_final, ud["foto"])
    context.user_data.clear()
    return ConversationHandler.END

async def absen_pilih_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    context.user_data["mode_edit"] = True
    field = query.data.split("_", 1)[1]

    if field == "rencana":
        await query.message.reply_text("Masukkan Rencana Kegiatan yang baru:")
        return ABSEN_RENCANA
    if field == "foto":
        await query.message.reply_text("Kirim FOTO yang baru:")
        return ABSEN_FOTO
    if field == "lokasi":
        tombol_lokasi = ReplyKeyboardMarkup([[KeyboardButton("📍 Kirim Lokasi", request_location=True)]], resize_keyboard=True, one_time_keyboard=True)
        await query.message.reply_text("Share lokasi yang baru:", reply_markup=tombol_lokasi)
        return ABSEN_LOKASI

    return await absen_tampilkan_ringkasan(update, context)

# SAkIT / IZIN LOGIC (Unchanged mostly)
async def absen_izin_keterangan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["keterangan_izin"] = update.message.text.strip()
    tombol_foto = InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Ya, lampirkan foto", callback_data="izinfoto_ya")],
        [InlineKeyboardButton("Tidak, lewati", callback_data="izinfoto_tidak")],
    ])
    await update.message.reply_text("Ingin melampirkan foto bukti (opsional)?", reply_markup=tombol_foto)
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
    status_final = context.user_data["status_manual"]
    kode = context.user_data["kode"]
    nama = context.user_data["nama"]
    keterangan = context.user_data["keterangan_izin"]
    tanggal = _tanggal_hari_ini()

    try:
        await simpan_absensi(tanggal, kode, nama, None, foto_path, keterangan, datetime.now().strftime("%H:%M"), status_final)
    except Exception as e:
        logger.error(f"Gagal simpan absensi (sakit/izin): {e}")
        await target_pesan.reply_text("⚠️ Terjadi kesalahan. Coba lagi.")
        return ConversationHandler.END

    emoji_status = "🤒" if status_final == "Sakit" else "📄"
    await target_pesan.reply_text(f"✅ Absen tercatat sebagai *{escape_markdown(status_final)}*.\nKeterangan: {escape_markdown(keterangan)}", parse_mode="Markdown")

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

# ---------- ALUR INPUT KEGIATAN / VISIT ----------

async def kegiatan_mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("📋 *INPUT KEGIATAN / VISIT*\nMasukkan Kode Karyawan (AR) Anda:", parse_mode="Markdown")
    return KEG_KODE

async def keg_kode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kode = update.message.text.strip().upper()
    tanggal = _tanggal_hari_ini()

    hasil = await cek_sudah_absen(tanggal, kode)
    if hasil is None:
        await update.message.reply_text(
            "❌ Anda BELUM melakukan absen masuk HARI INI! Silakan /absen terlebih dahulu."
        )
        return ConversationHandler.END

    nama, status = hasil
    if status in ("Sakit", "Izin"):
        await update.message.reply_text(f"❌ Anda tercatat *{escape_markdown(status)}* hari ini.", parse_mode="Markdown")
        return ConversationHandler.END

    context.user_data["kode"] = kode
    context.user_data["nama"] = nama
    context.user_data["tanggal"] = tanggal

    tombol_kegiatan = InlineKeyboardMarkup([[InlineKeyboardButton(j, callback_data=f"jeniskeg_{i}")] for i, j in enumerate(DAFTAR_JENIS_KEGIATAN)])
    await update.message.reply_text(f"Halo, {escape_markdown(nama)}. Pilih Jenis Kegiatan:", parse_mode="Markdown", reply_markup=tombol_kegiatan)
    return KEG_NAMA_KEGIATAN

async def keg_pilih_jenis_kegiatan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    idx = int(query.data.split("_", 1)[1])
    nama_kegiatan = DAFTAR_JENIS_KEGIATAN[idx]
    context.user_data["nama_kegiatan"] = nama_kegiatan

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await query.message.reply_text(f"✅ Anda memilih kegiatan: *{escape_markdown(nama_kegiatan)}*\n\n🏢 Masukkan Nama Usaha / Klien:", parse_mode="Markdown")
    return KEG_NAMA_USAHA

async def keg_jenis_kegiatan_belum_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Mohon tap salah satu tombol pilihan.")
    return KEG_NAMA_KEGIATAN

async def keg_nama_usaha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nama_usaha"] = update.message.text.strip()
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

    tombol_deal = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Deal", callback_data="deal_ya")],
        [InlineKeyboardButton("❌ Belum Deal", callback_data="deal_tidak")],
    ])
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

    await query.message.reply_text("Masukkan No HP PIC Pelanggan:\n*(Awali dengan +62 atau 08. Ketik 0 jika tidak ada)*")
    return KEG_NOHP

async def keg_paket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["paket"] = update.message.text.strip()

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text("Masukkan No HP PIC Pelanggan:\n*(Awali dengan +62 atau 08. Ketik 0 jika tidak ada)*")
    return KEG_NOHP

async def keg_nohp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nohp = update.message.text.strip()
    if nohp != "0":
        if not re.match(r"^(?:\+62|08)\d+$", nohp):
            await update.message.reply_text("❌ Format Nomor HP tidak valid!\nHanya gunakan angka yang diawali dengan +62 atau 08.\n(Ketik 0 jika tidak ada)\n\nSilakan masukkan ulang:")
            return KEG_NOHP

    context.user_data["no_hp_pic"] = nohp

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text("Masukkan Nama PIC Pelanggan:")
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
    
    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)
        
    await update.message.reply_text("📸 Sekarang kirim FOTO Kegiatan:")
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

    tombol_lokasi = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Kirim Lokasi Kegiatan", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text("📍 Silakan share lokasi kegiatan:", reply_markup=tombol_lokasi)
    return KEG_LOKASI

async def keg_lokasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text("Mohon share lokasi lewat tombol 📍 yang tersedia.")
        return KEG_LOKASI

    lat = update.message.location.latitude
    lon = update.message.location.longitude
    link_maps = buat_link_google_maps(lat, lon)
    alamat = await reverse_geocode(lat, lon)
    context.user_data["tag_lokasi_kegiatan"] = f"{alamat} ({link_maps})" if alamat else link_maps

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        await update.message.reply_text(f"✅ Lokasi diperbarui.\n📍 {link_maps}", reply_markup=ReplyKeyboardRemove())
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text(f"✅ Lokasi tersimpan.\n📍 {link_maps}", reply_markup=ReplyKeyboardRemove())
    return await keg_tampilkan_ringkasan(update, context)

def _teks_ringkasan_kegiatan(ud, judul):
    status_deal = ud.get("status_deal", "-")
    baris_paket = f"📦 Paket: {ud.get('paket')}\n" if status_deal == "Deal" and ud.get("paket") else ""
    return (
        f"{judul}\n\n"
        f"📌 Kegiatan: {ud.get('nama_kegiatan')}\n"
        f"🏢 Usaha: {ud.get('nama_usaha')}\n"
        f"📝 Hasil: {ud.get('hasil')}\n"
        f"🤝 Status: {status_deal}\n"
        f"{baris_paket}"
        f"👷 PIC: {ud.get('nama_pic')} ({ud.get('jabatan_pic')})\n"
        f"📱 No HP: {sensor_nomor_hp(ud.get('no_hp_pic'))}\n"
        f"📍 Lokasi: {ud.get('tag_lokasi_kegiatan')}"
    )

async def keg_tampilkan_ringkasan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    ringkasan = _teks_ringkasan_kegiatan(ud, "📋 KONFIRMASI DATA KEGIATAN") + "\n\nMohon cek lagi, apakah data di atas sudah benar?"

    tombol = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit", callback_data="keg_aksi_edit")],
        [InlineKeyboardButton("✅ Ya, Simpan & Kirim", callback_data="keg_aksi_submit")],
        [InlineKeyboardButton("❌ Batalkan", callback_data="keg_aksi_batal")],
    ])

    target = update.callback_query.message if update.callback_query else update.message
    foto = ud.get("foto_kegiatan")

    if foto:
        try:
            with open(foto, "rb") as f:
                await target.reply_photo(photo=f, caption=ringkasan, reply_markup=tombol)
            return KEG_RINGKASAN_AKSI
        except Exception as e:
            logger.warning(f"Gagal lampirkan foto: {e}")

    await target.reply_text(ringkasan, reply_markup=tombol)
    return KEG_RINGKASAN_AKSI

async def keg_ringkasan_aksi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    if query.data == "keg_aksi_batal":
        await query.message.reply_text("❌ Laporan kegiatan dibatalkan. Ketik /kegiatan untuk mulai ulang.")
        context.user_data.clear()
        return ConversationHandler.END

    if query.data == "keg_aksi_edit":
        daftar_tombol = [
            [InlineKeyboardButton("📌 Kegiatan", callback_data="editf_kegiatan"), InlineKeyboardButton("🏢 Usaha", callback_data="editf_usaha")],
            [InlineKeyboardButton("📝 Hasil", callback_data="editf_hasil"), InlineKeyboardButton("🤝 Status Deal", callback_data="editf_status")],
            [InlineKeyboardButton("📱 No HP", callback_data="editf_nohp"), InlineKeyboardButton("👷 PIC", callback_data="editf_pic")],
            [InlineKeyboardButton("💼 Jabatan", callback_data="editf_jabatan"), InlineKeyboardButton("📸 Foto", callback_data="editf_foto")],
            [InlineKeyboardButton("📍 Lokasi", callback_data="editf_lokasi")],
        ]
        await query.message.reply_text("Pilih data yang ingin diedit:", reply_markup=InlineKeyboardMarkup(daftar_tombol))
        return KEG_PILIH_EDIT

    # Submit
    ud = context.user_data
    try:
        await simpan_kegiatan(
            ud["tanggal"], ud["kode"], ud["nama_kegiatan"], ud["nama_usaha"],
            ud["tag_lokasi_kegiatan"], ud["foto_kegiatan"], ud["hasil"],
            ud["no_hp_pic"], ud["nama_pic"], ud["jabatan_pic"],
            ud.get("status_deal"), ud.get("paket")
        )
    except Exception as e:
        logger.error(f"Gagal simpan kegiatan: {e}")
        await query.message.reply_text("⚠️ Terjadi kesalahan saat menyimpan.")
        return ConversationHandler.END

    ringkasan_final = f"👤 {ud['nama']} ({ud['kode']})\n" + _teks_ringkasan_kegiatan(ud, "✅ LAPORAN KEGIATAN TERSIMPAN")
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

    if field == "kegiatan":
        tombol = InlineKeyboardMarkup([[InlineKeyboardButton(j, callback_data=f"jeniskeg_{i}")] for i, j in enumerate(DAFTAR_JENIS_KEGIATAN)])
        await query.message.reply_text("Pilih Jenis Kegiatan yang baru:", reply_markup=tombol)
        return KEG_NAMA_KEGIATAN
    if field == "usaha":
        await query.message.reply_text("Masukkan Nama Usaha yang baru:")
        return KEG_NAMA_USAHA
    if field == "hasil":
        await query.message.reply_text("Masukkan Hasil yang baru:")
        return KEG_HASIL
    if field == "status":
        tombol = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Deal", callback_data="deal_ya")],
            [InlineKeyboardButton("❌ Belum Deal", callback_data="deal_tidak")],
        ])
        await query.message.reply_text("Pilih status hasil visit yang baru:", reply_markup=tombol)
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
    if field == "foto":
        await query.message.reply_text("Kirim FOTO yang baru:")
        return KEG_FOTO
    if field == "lokasi":
        tombol = ReplyKeyboardMarkup([[KeyboardButton("📍 Kirim Lokasi", request_location=True)]], resize_keyboard=True, one_time_keyboard=True)
        await query.message.reply_text("Share lokasi yang baru:", reply_markup=tombol)
        return KEG_LOKASI

    return await keg_tampilkan_ringkasan(update, context)



# ---------- ALUR REGISTRASI AWAL ----------
async def register_mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🔑 *REGISTRASI AWAL*\nMasukkan Kode Karyawan (AR) Anda (contoh: AR001):", 
        parse_mode="Markdown"
    )
    return REG_KODE

async def reg_kode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reg_kode"] = update.message.text.strip().upper()
    await update.message.reply_text("👤 Masukkan Nama Lengkap Anda:")
    return REG_NAMA

async def reg_nama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kode = context.user_data["reg_kode"]
    nama = update.message.text.strip()
    
    try:
        await simpan_karyawan(kode, nama)
        await update.message.reply_text(f"✅ Registrasi Berhasil!\n\nKode AR: {kode}\nNama: {nama}\n\nSekarang Anda bisa menggunakan /absen atau /kegiatan.")
    except Exception as e:
        logger.error(f"Gagal registrasi: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat registrasi ke database. Hubungi admin.")
    
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
        "/register - Pendaftaran karyawan baru\n/absen - Absen masuk (Hadir/Sakit/Izin), cukup masukkan Kode Karyawan\n"
        "/kegiatan - Input laporan kegiatan/visit (wajib absen Hadir dulu)\n"
        "/rekapabsen - Lihat rekap riwayat absensi (khusus di grup notifikasi)\n"
        "/rekapkegiatan - Lihat rekap riwayat kegiatan (khusus di grup notifikasi)\n"
        "/exportexcel - Download semua data sebagai file Excel (khusus di grup notifikasi)\n"
        "/grupid - (setup admin) Lihat ID chat grup ini\n"
        "/batal - Batalkan proses yang sedang berjalan",
        parse_mode="Markdown",
    )


async def rekap_absen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _cek_akses_rekap(update):
        await update.message.reply_text(
            "❌ Command ini hanya bisa dijalankan di dalam grup notifikasi resmi."
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
            "❌ Command ini hanya bisa dijalankan di dalam grup notifikasi resmi."
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


async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export data absensi & kegiatan ke file Excel (.xlsx). Bisa pakai rentang tanggal."""
    if not await _cek_akses_rekap(update):
        await update.message.reply_text(
            "❌ Command ini hanya bisa dijalankan di dalam grup notifikasi resmi."
        )
        return

    if not OPENPYXL_TERSEDIA:
        await update.message.reply_text(
            "⚠️ Modul 'openpyxl' belum terinstall di server.\nInstall dengan: pip install openpyxl"
        )
        return

    args = update.message.text.split()[1:]
    start_date = None
    end_date = None
    if len(args) == 2:
        start_date = args[0]
        end_date = args[1]
        await update.message.reply_text(f"⏳ Sedang menyiapkan file Excel untuk tanggal {start_date} s/d {end_date}...")
    else:
        await update.message.reply_text("⏳ Sedang menyiapkan file Excel untuk SELURUH data...")

    try:
        path_file = await asyncio.to_thread(_build_excel_export_sync, start_date, end_date)
    except Exception as e:
        logger.error(f"Gagal membuat file Excel: {e}")
        await update.message.reply_text("⚠️ Gagal membuat file Excel dari database. Coba lagi nanti.")
        return

    try:
        with open(path_file, "rb") as f:
            await update.message.reply_document(document=f, filename=os.path.basename(path_file))
    except Exception as e:
        logger.error(f"Gagal mengirim file Excel: {e}")
        await update.message.reply_text("⚠️ File berhasil dibuat tapi gagal dikirim. Coba lagi.")


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
        for kode, nama, nama_kegiatan, nama_usaha, status_deal, paket in kegiatan:
            baris_paket = f" — Paket: {paket}" if status_deal == "Deal" and paket else ""
            teks += f"• {nama} ({kode}) — {nama_kegiatan} di {nama_usaha} [{status_deal or '-'}]{baris_paket}\n"
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

    conv_register = ConversationHandler(
        entry_points=[CommandHandler("register", register_mulai)],
        states={
            REG_KODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_kode)],
            REG_NAMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_nama)],
        },
        fallbacks=[CommandHandler("batal", batal)],
    )

    conv_kegiatan = ConversationHandler(
        entry_points=[CommandHandler("kegiatan", kegiatan_mulai)],
        states={
            KEG_KODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, keg_kode)],
            KEG_NAMA_KEGIATAN: [
                CallbackQueryHandler(keg_pilih_jenis_kegiatan, pattern="^jeniskeg_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, keg_jenis_kegiatan_belum_tap),
            ],
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
    app.add_handler(CommandHandler("exportexcel", export_excel))
    app.add_handler(CommandHandler("grupid", grup_id))
    app.add_handler(conv_absen)
    app.add_handler(conv_kegiatan)
    app.add_handler(conv_register)
    app.add_error_handler(error_handler)

    _init_gsheet()
    _init_excel_online()

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