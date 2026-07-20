"""Alur registrasi awal (ikat Kode Karyawan/AR ke Telegram ID lewat /daftar)."""
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .. import db
from ..config import logger
from ..states import REGISTRASI_KODE
from ..utils.misc import escape_markdown


async def registrasi_mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    sudah = await db.cari_kode_by_telegram_id(telegram_id)
    if sudah is not None:
        kode, nama = sudah
        await update.message.reply_text(
            f"✅ Akun Telegram Anda sudah terdaftar sebagai *{escape_markdown(nama)}* "
            f"(Kode: {escape_markdown(kode)}).\n\n"
            "Kalau ini keliru atau Anda ganti HP, hubungi admin untuk reset.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📋 *REGISTRASI AWAL*\n"
        "Masukkan Kode Karyawan (AR) Anda untuk mengikat akun Telegram ini ke kode tsb:",
        parse_mode="Markdown",
    )
    return REGISTRASI_KODE


async def registrasi_kode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kode = update.message.text.strip().upper()
    telegram_id = update.effective_user.id

    try:
        status = await db.daftar_telegram_id(kode, telegram_id)
    except Exception as e:
        logger.error(f"Gagal proses registrasi: {e}")
        await update.message.reply_text("⚠️ Terjadi kesalahan saat registrasi. Coba lagi atau hubungi admin.")
        return ConversationHandler.END

    if status == "kode_tidak_ada":
        await update.message.reply_text(
            f"❌ Kode karyawan '{escape_markdown(kode)}' tidak ditemukan di database.\n"
            "Mohon cek kembali kode Anda, atau hubungi admin.\n\n"
            "➡️ Ketik /daftar lagi untuk mencoba dari awal."
        )
        return ConversationHandler.END

    if status == "kode_sudah_dipakai":
        await update.message.reply_text(
            f"❌ Kode '{escape_markdown(kode)}' sudah terdaftar ke akun Telegram lain.\n"
            "Kalau ini keliru, hubungi admin."
        )
        return ConversationHandler.END

    if status == "sudah_terdaftar":
        await update.message.reply_text(
            "❌ Akun Telegram Anda sudah terdaftar dengan kode lain sebelumnya.\n"
            "Hubungi admin kalau perlu diganti."
        )
        return ConversationHandler.END

    nama = await db.cari_nama_karyawan(kode)
    await update.message.reply_text(
        f"✅ Registrasi berhasil! Akun Telegram ini terikat ke *{escape_markdown(nama)}* "
        f"(Kode: {escape_markdown(kode)}).\n\n"
        "Sekarang Anda bisa langsung pakai /absen dan /kegiatan tanpa ketik kode lagi.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def pastikan_terdaftar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Helper dipakai di entry point /absen & /kegiatan: cari kode+nama dari
    telegram_id pengirim. Kalau belum terdaftar, kasih tau untuk /daftar dulu
    dan return None. Kalau sudah, return (kode, nama)."""
    telegram_id = update.effective_user.id
    hasil = await db.cari_kode_by_telegram_id(telegram_id)
    if hasil is None:
        await update.message.reply_text(
            "❌ Akun Telegram Anda belum terdaftar.\n"
            "Silakan jalankan /daftar terlebih dahulu (masukkan Kode Karyawan/AR Anda),"
            " baru bisa pakai /absen dan /kegiatan."
        )
        return None
    return hasil
