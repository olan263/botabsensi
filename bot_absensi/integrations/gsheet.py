"""
Sync opsional ke Google Sheets. Kalau tidak dikonfigurasi, semua fungsi di
sini jadi no-op dan tidak mengganggu jalannya bot.
"""
import asyncio

from ..config import GSHEET_CREDENTIALS_FILE, GSHEET_SPREADSHEET_ID, logger

try:
    import gspread
    from google.oauth2.service_account import Credentials as GSheetCredentials
    GSHEET_TERSEDIA = True
except ImportError:
    GSHEET_TERSEDIA = False

_gsheet_ws_absensi = None
_gsheet_ws_kegiatan = None


def init_gsheet():
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
