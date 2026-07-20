"""
Konfigurasi & konstanta global.
Semua env var dibaca di sini, jadi kalau nanti butuh tambah/ubah konfigurasi
cukup edit file ini saja.
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("bot_absensi")

# ==========================================
# TOKEN BOT TELEGRAM & KONFIGURASI DATABASE
# ==========================================
# PENTING - KEAMANAN:
# Token & password TIDAK BOLEH punya nilai default/fallback di source code.
# Kalau ada token/password yang PERNAH nempel di source code atau ke-screenshot,
# ANGGAP BOCOR dan WAJIB diganti:
#   - Token: @BotFather -> /mybots -> pilih bot -> API Token -> Revoke current token
#   - Password DB: ganti user postgres lewat ALTER ROLE / pgAdmin
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "Environment variable BOT_TOKEN belum diisi. "
        "Set dulu (token yang BARU, hasil revoke) di file .env sebelum menjalankan bot."
    )

# ID grup Telegram tujuan notifikasi otomatis (real-time tiap ada absen/kegiatan,
# plus rekap otomatis pagi & malam).
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

# ==========================================
# KONFIGURASI GOOGLE SHEETS (opsional)
# ==========================================
GSHEET_CREDENTIALS_FILE = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_FILE", "")
GSHEET_SPREADSHEET_ID = os.environ.get("GOOGLE_SHEETS_ID", "")

# ==========================================
# KONFIGURASI EXCEL ONLINE / MICROSOFT 365 (opsional)
# ==========================================
MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID", "")
MS_EXCEL_DRIVE_ID = os.environ.get("MS_EXCEL_DRIVE_ID", "")
MS_EXCEL_ITEM_ID = os.environ.get("MS_EXCEL_ITEM_ID", "")
MS_TOKEN_CACHE_FILE = os.environ.get("MS_TOKEN_CACHE_FILE", "msal_token_cache.bin")
MS_AUTHORITY = "https://login.microsoftonline.com/consumers"  # khusus akun Microsoft personal
MS_SCOPES = ["Files.ReadWrite"]

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

# ==========================================
# FOLDER & DATA UTAMA
# ==========================================
FOLDER_EXPORT = "export_excel"
FOLDER_FOTO = "foto_absen"
os.makedirs(FOLDER_EXPORT, exist_ok=True)
os.makedirs(FOLDER_FOTO, exist_ok=True)

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

RADIUS_LOKASI_METER = 100

DAFTAR_JENIS_KEGIATAN = [
    "Visit",
    "Negosiasi",
    "Deal",
    "Follow Up",
    "Gangguan",
    "Collection - CTB",
]
