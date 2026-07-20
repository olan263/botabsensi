"""
Upload foto absen/kegiatan ke Google Cloud Storage (opsional, backup cloud).
Foto tetap disimpan lokal di FOLDER_FOTO seperti biasa (dipakai buat kirim ke
Telegram) - ini cuma nambahin salinan ke GCS di background, tidak ditunggu.
Kalau tidak dikonfigurasi, fungsi di sini jadi no-op.
"""
import asyncio
import os

from ..config import GCS_BUCKET_NAME, GCS_CREDENTIALS_FILE, logger

try:
    from google.cloud import storage
    GCS_TERSEDIA = True
except ImportError:
    GCS_TERSEDIA = False

_gcs_bucket = None


def gcs_terkonfigurasi():
    return bool(GCS_TERSEDIA and GCS_BUCKET_NAME and GCS_CREDENTIALS_FILE)


def init_gcs():
    """Inisialisasi koneksi GCS sekali di awal (dipanggil dari main()).
    Gagal/tidak dikonfigurasi -> upload dilewati, bot tetap jalan normal."""
    global _gcs_bucket

    if not GCS_TERSEDIA:
        logger.warning("Modul 'google-cloud-storage' belum terinstall, upload foto ke GCS dilewati.")
        return
    if not gcs_terkonfigurasi():
        logger.warning(
            "GCS_BUCKET_NAME/GCS_CREDENTIALS_FILE belum diisi di .env, upload foto ke GCS dilewati."
        )
        return
    try:
        client = storage.Client.from_service_account_json(GCS_CREDENTIALS_FILE)
        _gcs_bucket = client.bucket(GCS_BUCKET_NAME)
        logger.info(f"Koneksi Google Cloud Storage berhasil diinisialisasi (bucket: {GCS_BUCKET_NAME}).")
    except Exception as e:
        logger.error(f"Gagal inisialisasi Google Cloud Storage: {e}")
        _gcs_bucket = None


def _upload_foto_sync(path_lokal, nama_objek):
    if _gcs_bucket is None:
        return None
    try:
        blob = _gcs_bucket.blob(nama_objek)
        blob.upload_from_filename(path_lokal)
        logger.info(f"Foto ter-upload ke GCS: {nama_objek}")
        return f"gs://{GCS_BUCKET_NAME}/{nama_objek}"
    except Exception as e:
        logger.error(f"Gagal upload foto ke GCS ({nama_objek}): {e}")
        return None


async def upload_foto(path_lokal):
    """Upload 1 file foto lokal ke GCS, di dalam sub-folder foto_absen/ di
    bucket (supaya nama filenya tidak bentrok dengan objek lain).
    Return path gs:// kalau berhasil, None kalau gagal/tidak dikonfigurasi."""
    if not path_lokal or not gcs_terkonfigurasi():
        return None
    nama_objek = f"foto_absen/{os.path.basename(path_lokal)}"
    return await asyncio.to_thread(_upload_foto_sync, path_lokal, nama_objek)
