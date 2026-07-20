"""Rekap otomatis terjadwal (11:00 & 20:00 WIB)."""
from telegram.ext import ContextTypes

from . import db
from .config import logger, GROUP_CHAT_ID
from .utils.misc import tanggal_hari_ini


async def bangun_rekap_pagi(tanggal=None):
    tanggal = tanggal or tanggal_hari_ini()
    hadir = await db.ambil_absensi_tanggal(tanggal)
    belum = await db.ambil_karyawan_belum_absen(tanggal)

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
    tanggal = tanggal or tanggal_hari_ini()
    absensi = await db.ambil_absensi_tanggal(tanggal)
    kegiatan = await db.ambil_kegiatan_tanggal(tanggal)
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
