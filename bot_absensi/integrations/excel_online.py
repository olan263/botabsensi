"""
Sync opsional ke file Excel di OneDrive (akun personal) lewat Microsoft Graph API.
Kalau tidak dikonfigurasi, semua fungsi di sini jadi no-op.
"""
import asyncio
import os

from ..config import (
    MS_CLIENT_ID, MS_EXCEL_DRIVE_ID, MS_EXCEL_ITEM_ID, MS_TOKEN_CACHE_FILE,
    MS_AUTHORITY, MS_SCOPES, DEFINISI_TABEL_EXCEL_ONLINE, logger,
)

try:
    import requests
    REQUESTS_TERSEDIA = True
except ImportError:
    REQUESTS_TERSEDIA = False

try:
    import msal
    MSAL_TERSEDIA = True
except ImportError:
    MSAL_TERSEDIA = False


def excel_online_terkonfigurasi():
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
    refresh token yang sudah tersimpan di token cache dari setup_excel_online_auth.py."""
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


def _hitung_kolom_tabel_excel_online(token, nama_tabel):
    """Return jumlah kolom tabel yang SUDAH ADA di Excel Online, atau None
    kalau tabelnya belum ada / gagal dicek."""
    base = _url_workbook()
    headers_req = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(f"{base}/tables('{nama_tabel}')/columns", headers=headers_req, timeout=15)
        resp.raise_for_status()
        return len(resp.json().get("value", []))
    except Exception as e:
        logger.warning(f"Gagal cek jumlah kolom tabel '{nama_tabel}': {e}")
        return None


def _hapus_tabel_excel_online(token, nama_tabel):
    base = _url_workbook()
    headers_req = {"Authorization": f"Bearer {token}"}
    resp = requests.delete(f"{base}/tables('{nama_tabel}')", headers=headers_req, timeout=15)
    resp.raise_for_status()
    logger.info(f"Tabel '{nama_tabel}' (skema lama/tidak cocok) berhasil dihapus, akan dibuat ulang.")


def _buat_worksheet_dan_tabel_excel_online(token, nama_tabel, nama_worksheet, header):
    base = _url_workbook()
    headers_req = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

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

    kolom_akhir = _kolom_excel(len(header))
    alamat_range = f"A1:{kolom_akhir}1"
    resp = requests.patch(
        f"{base}/worksheets/{nama_worksheet}/range(address='{alamat_range}')",
        headers=headers_req,
        json={"values": [header]},
        timeout=15,
    )
    resp.raise_for_status()

    resp = requests.post(
        f"{base}/worksheets/{nama_worksheet}/tables/add",
        headers=headers_req,
        json={"address": f"{nama_worksheet}!{alamat_range}", "hasHeaders": True},
        timeout=15,
    )
    resp.raise_for_status()
    tabel_id = resp.json()["id"]

    resp = requests.patch(
        f"{base}/tables/{tabel_id}",
        headers=headers_req,
        json={"name": nama_tabel},
        timeout=15,
    )
    resp.raise_for_status()

    logger.info(f"Tabel '{nama_tabel}' berhasil dibuat otomatis di worksheet '{nama_worksheet}'.")


def init_excel_online():
    """Cek koneksi Excel Online sekali di awal (dipanggil dari main()).
    Gagal/tidak dikonfigurasi -> sync dilewati, bot tetap jalan normal."""
    if not MSAL_TERSEDIA:
        logger.warning("Modul 'msal' belum terinstall, sync Excel Online dilewati.")
        return
    if not excel_online_terkonfigurasi():
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
            jumlah_kolom_diharapkan = len(info["header"])

            if nama_tabel in nama_tabel_ada:
                jumlah_kolom_sekarang = _hitung_kolom_tabel_excel_online(token, nama_tabel)
                if jumlah_kolom_sekarang == jumlah_kolom_diharapkan:
                    continue  # tabel sudah ada dan skemanya sudah cocok, tidak perlu apa-apa

                logger.warning(
                    f"Tabel '{nama_tabel}' skemanya tidak cocok lagi "
                    f"(sekarang {jumlah_kolom_sekarang} kolom, seharusnya {jumlah_kolom_diharapkan}). "
                    "Menghapus & membuat ulang otomatis..."
                )
                try:
                    _hapus_tabel_excel_online(token, nama_tabel)
                except Exception as e:
                    logger.error(
                        f"Gagal menghapus tabel lama '{nama_tabel}': {e}. "
                        "Sync ke tabel ini akan terus gagal sampai dibenerin manual."
                    )
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
    if not excel_online_terkonfigurasi():
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