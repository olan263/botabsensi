"""Command umum: /start, /help, /batal, /grupid, /rekapabsen, /rekapkegiatan,
/exportexcel, plus helper kirim notifikasi grup & download foto yang dipakai
handler absen & kegiatan."""
import os
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .. import db
from ..config import logger, GROUP_CHAT_ID, GROUP_CHAT_ID_INT, FOLDER_FOTO
from ..integrations.export_excel import OPENPYXL_TERSEDIA, build_excel_export_sync
from ..utils.misc import sensor_nomor_hp
import asyncio
from telegram import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

MENU_UTAMA_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📝 Absen Masuk"), KeyboardButton("🏃 Input Kegiatan")],
        [KeyboardButton("⚙️ Registrasi"), KeyboardButton("❌ Batal")]
    ],
    resize_keyboard=True,
    is_persistent=False
)

MENU_BATAL_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ Batal")]],
    resize_keyboard=True,
    is_persistent=False
)

async def download_foto_dari_pesan(update: Update, prefix: str):
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


async def kirim_notifikasi_grup(context: ContextTypes.DEFAULT_TYPE, caption: str, path_foto: str):
    """Kirim notifikasi otomatis ke grup (dengan foto asli), tidak menghentikan
    alur bot kalau gagal."""
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
    if not GROUP_CHAT_ID:
        logger.warning("GROUP_CHAT_ID belum diisi, notifikasi ke grup dilewati.")
        return
    try:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=teks)
    except Exception as e:
        logger.error(f"Gagal kirim notifikasi teks ke grup: {e}")


async def _cek_akses_rekap(update: Update):
    """Rekap manual (/rekapabsen, /rekapkegiatan) HANYA boleh diakses dari
    dalam grup notifikasi resmi."""
    return GROUP_CHAT_ID_INT is not None and update.effective_chat.id == GROUP_CHAT_ID_INT


async def batal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    
    tipe_chat = update.effective_chat.type
    if tipe_chat in ("group", "supergroup"):
        await update.message.reply_text("Proses dibatalkan.", reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text("Proses dibatalkan. Silakan pilih menu lain atau mulai dari awal.", reply_markup=MENU_UTAMA_KEYBOARD)
        
    return ConversationHandler.END


async def grup_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Jalankan ini DI DALAM GRUP tujuan notifikasi, bot akan balas ID grup itu."""
    await update.message.reply_text(f"ID chat ini: `{update.effective_chat.id}`", parse_mode="Markdown")


async def mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tipe_chat = update.effective_chat.type
    
    # 1. SMART UI: Jika di Grup Chat (Hanya menu Admin/Rekap)
    if tipe_chat in ("group", "supergroup"):
        await update.message.reply_text(
            "🤖 *Bot Absensi & Kegiatan (Grup Mode)*\n\n"
            "Gunakan perintah berikut:\n"
            "/rekapabsen - Rekap absensi hari ini\n"
            "/rekapkegiatan - Rekap kegiatan\n"
            "/exportexcel - Download data ke Excel",
            parse_mode="Markdown"
        )
        return
        
    # 2. SMART UI: Jika di Chat Pribadi (Japri) -> Munculkan Reply Keyboard Permanen
    pesan_welcome = (
        "🤖 *Selamat Datang di Bot Absensi & Kegiatan AR!*\n\n"
        "Silakan gunakan **Menu di bawah** 👇 untuk memulai bot.\n"
        "_(Jika menu tombol tidak muncul, klik ikon kotak bergaris empat di sebelah tombol 📎 atau mikrofon)._\n\n"
        "💡 *Catatan:* Untuk AR yang baru pertama kali menggunakan bot (belum daftar), silakan klik tombol **⚙️ Registrasi** terlebih dahulu sebelum absen."
    )
    
    await update.message.reply_text(
        pesan_welcome,
        reply_markup=MENU_UTAMA_KEYBOARD,
        parse_mode="Markdown",
    )


async def rekap_absen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _cek_akses_rekap(update):
        await update.message.reply_text(
            "❌ Command ini hanya bisa dijalankan di dalam grup notifikasi resmi."
        )
        return

    try:
        baris_absensi = await db.ambil_rekap_absensi()
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
        baris_kegiatan = await db.ambil_rekap_kegiatan()
    except Exception as e:
        logger.error(f"Gagal ambil rekap kegiatan dari database: {e}")
        await update.message.reply_text("⚠️ Gagal mengambil data rekap kegiatan dari database. Coba lagi nanti.")
        return

    teks = "=== REKAP KEGIATAN ===\n"
    if not baris_kegiatan:
        teks += "\nBelum ada data kegiatan.\n"

    tanggal_terakhir = None
    for tanggal, kode, nama_karyawan, nama_kegiatan, nama_usaha, nama_pic, jabatan_pic, no_hp_pic in baris_kegiatan:
        if tanggal != tanggal_terakhir:
            teks += f"\n{tanggal}\n"
            tanggal_terakhir = tanggal
        teks += (
            f"• {kode} | {nama_karyawan} | {nama_kegiatan}\n"
            f"  Usaha: {nama_usaha or '-'}\n"
            f"  PIC: {nama_pic} ({jabatan_pic}) — {sensor_nomor_hp(no_hp_pic)}\n"
        )

    batas = 4000
    for i in range(0, len(teks), batas):
        await update.message.reply_text(teks[i:i + batas])


def _parse_rentang_tanggal(args):
    """Parse argumen command jadi (tanggal_mulai, tanggal_selesai) format 'YYYY-MM-DD',
    atau (None, None) kalau tidak ada argumen. Raise ValueError kalau formatnya salah."""
    if not args:
        return None, None
    if len(args) != 2:
        raise ValueError("Harus 2 tanggal: tanggal_mulai dan tanggal_selesai.")

    tanggal_mulai_str, tanggal_selesai_str = args
    tanggal_mulai = datetime.strptime(tanggal_mulai_str, "%Y-%m-%d").date()
    tanggal_selesai = datetime.strptime(tanggal_selesai_str, "%Y-%m-%d").date()
    if tanggal_mulai > tanggal_selesai:
        tanggal_mulai, tanggal_selesai = tanggal_selesai, tanggal_mulai

    return tanggal_mulai.isoformat(), tanggal_selesai.isoformat()


async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/exportexcel [YYYY-MM-DD YYYY-MM-DD] - khusus dari grup notifikasi resmi."""
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

    try:
        tanggal_mulai, tanggal_selesai = _parse_rentang_tanggal(context.args)
    except ValueError as e:
        await update.message.reply_text(
            f"⚠️ {e}\nContoh pemakaian: /exportexcel 2026-08-17 2026-08-30\n"
            "Atau /exportexcel tanpa argumen untuk export semua data."
        )
        return

    if tanggal_mulai:
        await update.message.reply_text(f"⏳ Sedang menyiapkan file Excel ({tanggal_mulai} s/d {tanggal_selesai})...")
    else:
        await update.message.reply_text("⏳ Sedang menyiapkan file Excel (semua data)...")

    try:
        path_file = await asyncio.to_thread(build_excel_export_sync, tanggal_mulai, tanggal_selesai)
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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Menangkap semua exception yang tidak tertangani di handler manapun."""
    logger.error(f"Terjadi exception saat memproses update: {context.error}", exc_info=context.error)
