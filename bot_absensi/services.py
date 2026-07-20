"""
Lapisan "service" yang menggabungkan simpan-ke-DB dengan sync ke Google Sheets
& Excel Online.

INI BAGIAN YANG MEMPERBAIKI LAG:
Dulu simpan_absensi()/simpan_kegiatan() nge-`await` sync ke Google Sheets DAN
Excel Online secara berurutan sebelum bot sempat balas ke user - jadi user
nunggu 2 network call ke API luar (yang bisa lambat/timeout) sebelum dapat
konfirmasi.

Sekarang: data disimpan ke DB dulu (itu WAJIB ditunggu, karena harus sukses
sebelum bot bilang "tersimpan"), lalu sync ke Sheets/Excel dilepas jalan di
background lewat asyncio.create_task() - bot langsung balas ke user tanpa
nunggu itu selesai. Errornya tetap ke-log seperti biasa lewat _tugas_background().
"""
import asyncio

from .config import logger
from . import db
from .integrations import gsheet, excel_online


def _tugas_background(coro, nama_tugas):
    """Jalankan coroutine di background tanpa diblok/di-await oleh caller.
    Exception yang muncul tetap di-log, tidak menghilang diam-diam."""
    async def _runner():
        try:
            await coro
        except Exception as e:
            logger.error(f"Gagal menjalankan tugas background '{nama_tugas}': {e}")

    asyncio.create_task(_runner())


async def simpan_absensi(tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status):
    # 1. WAJIB ditunggu - ini yang menentukan bot boleh bilang "tersimpan" atau tidak
    await db.simpan_absensi_db(tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status)

    # 2. TIDAK ditunggu - sync ke layanan luar jalan di background
    _tugas_background(
        gsheet.sync_absensi_ke_sheet(tanggal, kode, nama, tag_lokasi, rencana_kegiatan, jam_absen, status),
        "sync_absensi_ke_sheet",
    )
    _tugas_background(
        excel_online.sync_absensi_ke_excel_online(tanggal, kode, nama, tag_lokasi, rencana_kegiatan, jam_absen, status),
        "sync_absensi_ke_excel_online",
    )


async def simpan_kegiatan(
    tanggal, kode, nama_kegiatan, nama_usaha, tag_lokasi, foto_kegiatan, hasil,
    no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
):
    await db.simpan_kegiatan_db(
        tanggal, kode, nama_kegiatan, nama_usaha, tag_lokasi, foto_kegiatan, hasil,
        no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
    )

    nama_karyawan = await db.cari_nama_karyawan(kode)
    _tugas_background(
        gsheet.sync_kegiatan_ke_sheet(
            tanggal, kode, nama_karyawan, nama_kegiatan, nama_usaha, tag_lokasi, hasil,
            no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
        ),
        "sync_kegiatan_ke_sheet",
    )
    _tugas_background(
        excel_online.sync_kegiatan_ke_excel_online(
            tanggal, kode, nama_karyawan, nama_kegiatan, nama_usaha, tag_lokasi, hasil,
            no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
        ),
        "sync_kegiatan_ke_excel_online",
    )
