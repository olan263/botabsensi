"""Bikin file Excel (.xlsx) lokal berisi rekap absensi & kegiatan, untuk /exportexcel."""
import os
from datetime import datetime

from ..config import FOLDER_EXPORT
from .. import db

try:
    from openpyxl import Workbook
    OPENPYXL_TERSEDIA = True
except ImportError:
    OPENPYXL_TERSEDIA = False


def build_excel_export_sync(tanggal_mulai=None, tanggal_selesai=None):
    """Bangun 1 file Excel (.xlsx) berisi 2 sheet (Absensi & Kegiatan) dari
    data yang ada di database saat ini. Kalau tanggal_mulai/tanggal_selesai
    diisi, hanya data di rentang tanggal itu (inklusif) yang diambil.
    Return path file yang dibuat.

    Catatan: fungsi ini SYNC (dipanggil lewat asyncio.to_thread dari
    handler), karena openpyxl sendiri sync."""
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "Absensi"
    ws1.append(["Tanggal", "Kode", "Nama", "Jam Absen", "Status", "Lokasi"])
    for row in db._ambil_rekap_absensi_sync(tanggal_mulai, tanggal_selesai):
        ws1.append(list(row))

    ws2 = wb.create_sheet("Kegiatan")
    ws2.append([
        "Tanggal", "Kode", "Nama Karyawan", "Nama Kegiatan", "Nama Usaha", "Nama PIC Pelanggan",
        "Jabatan PIC Pelanggan", "No HP PIC Pelanggan", "Status Deal", "Paket",
    ])
    for row in db._ambil_rekap_kegiatan_sync(tanggal_mulai, tanggal_selesai):
        ws2.append(list(row))

    if tanggal_mulai and tanggal_selesai:
        akhiran = f"_{tanggal_mulai}_sd_{tanggal_selesai}"
    else:
        akhiran = ""
    nama_file = f"export_rekap{akhiran}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    path_file = os.path.join(FOLDER_EXPORT, nama_file)
    wb.save(path_file)
    return path_file
