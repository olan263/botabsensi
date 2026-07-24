"""Alur absen masuk (Hadir/Sakit/Izin)."""
from datetime import datetime

from telegram import (
    Update, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes, ConversationHandler

from .. import db, services
from ..config import logger, RADIUS_LOKASI_METER
from ..states import (
    ABSEN_STATUS, ABSEN_RENCANA, ABSEN_LOKASI, ABSEN_FOTO, ABSEN_KONFIRMASI,
    ABSEN_IZIN_KETERANGAN, ABSEN_IZIN_TAMBAH_FOTO, ABSEN_IZIN_FOTO,
)
from ..utils.misc import escape_markdown, tanggal_hari_ini, waktu_sekarang
from ..utils.geo import cari_kantor_terdekat
from .registrasi import pastikan_terdaftar
from .umum import download_foto_dari_pesan, kirim_notifikasi_grup, kirim_notifikasi_grup_teks, pastikan_chat_pribadi


async def absen_mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    if not await pastikan_chat_pribadi(update):
        return ConversationHandler.END

    hasil = await pastikan_terdaftar(update, context)
    if hasil is None:
        return ConversationHandler.END
    kode, nama = hasil

    tanggal = tanggal_hari_ini()
    sudah_absen = await db.cek_sudah_absen(tanggal, kode)
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
        await query.message.reply_text(
            "✅ Status dipilih: *Hadir*\n\nMasukkan Rencana Kegiatan Hari Ini:",
            parse_mode="Markdown",
        )
        return ABSEN_RENCANA

    status_manual = "Sakit" if pilihan == "status_sakit" else "Izin"
    context.user_data["status_manual"] = status_manual
    await query.message.reply_text(
        f"✅ Status dipilih: *{status_manual}*\n\nMasukkan keterangan/alasan {status_manual.lower()} Anda:",
        parse_mode="Markdown",
    )
    return ABSEN_IZIN_KETERANGAN


async def absen_status_belum_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Mohon tap salah satu tombol di atas ya (✅ Hadir / 🤒 Sakit / 📄 Izin), bukan ketik teks."
    )
    return ABSEN_STATUS


async def absen_rencana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["rencana_kegiatan"] = update.message.text.strip()

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await absen_tampilkan_ringkasan(update, context)

    tombol_lokasi = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Kirim Lokasi Saya Sekarang", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        f"📍 Sekarang share lokasi GPS Anda (harus dalam radius {RADIUS_LOKASI_METER} meter "
        "dari salah satu titik kantor resmi):",
        reply_markup=tombol_lokasi,
    )
    return ABSEN_LOKASI


async def absen_izin_keterangan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["keterangan_izin"] = update.message.text.strip()
    tombol_foto = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📸 Ya, lampirkan foto", callback_data="izinfoto_ya")],
            [InlineKeyboardButton("Tidak, lewati", callback_data="izinfoto_tidak")],
        ]
    )
    await update.message.reply_text(
        "Ingin melampirkan foto bukti (opsional)?", reply_markup=tombol_foto
    )
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
    path_foto = await download_foto_dari_pesan(update, f"izin_{context.user_data.get('kode', 'x')}")
    if path_foto is None:
        await update.message.reply_text("Mohon kirim dalam bentuk FOTO atau File gambar ya.")
        return ABSEN_IZIN_FOTO
    return await _simpan_dan_selesai_izin(update.message, context, foto_path=path_foto)


async def _simpan_dan_selesai_izin(target_pesan, context, foto_path):
    """target_pesan: objek dengan .reply_text/.reply_photo -> bisa update.message
    atau query.message tergantung dari mana alurnya masuk."""
    status_final = context.user_data["status_manual"]
    kode = context.user_data["kode"]
    nama = context.user_data["nama"]
    keterangan = context.user_data["keterangan_izin"]
    tanggal = tanggal_hari_ini()

    try:
        await services.simpan_absensi(
            tanggal, kode, nama, None, foto_path, keterangan,
            waktu_sekarang().strftime("%H:%M"), status_final,
        )
    except Exception as e:
        logger.error(f"Gagal simpan absensi (sakit/izin) ke database: {e}")
        await target_pesan.reply_text(
            "⚠️ Terjadi kesalahan saat menyimpan data ke database. Coba lagi atau hubungi admin.",
        )
        return ConversationHandler.END

    emoji_status = "🤒" if status_final == "Sakit" else "📄"
    await target_pesan.reply_text(
        f"✅ Absen tercatat sebagai *{escape_markdown(status_final)}*.\nKeterangan: {escape_markdown(keterangan)}",
        parse_mode="Markdown",
    )

    caption = (
        f"{emoji_status} {status_final.upper()}\n\n"
        f"👤 {nama} ({kode})\n"
        f"🕒 {waktu_sekarang().strftime('%H:%M')}\n"
        f"📝 Keterangan: {keterangan}"
    )
    if foto_path:
        await kirim_notifikasi_grup(context, caption, foto_path)
    else:
        await kirim_notifikasi_grup_teks(context, caption)

    return ConversationHandler.END


async def absen_lokasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text(
            "Mohon share lokasi lewat tombol 📍 yang tersedia, bukan ketik teks."
        )
        return ABSEN_LOKASI

    lat = update.message.location.latitude
    lon = update.message.location.longitude
    kantor, jarak = cari_kantor_terdekat(lat, lon)

    if jarak > RADIUS_LOKASI_METER:
        tombol_lokasi = ReplyKeyboardMarkup(
            [[KeyboardButton("📍 Kirim Lokasi Saya Sekarang", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text(
            f"❌ Lokasi Anda berjarak {jarak:.1f} meter dari titik kantor terdekat "
            f"(*{escape_markdown(kantor['nama'])}*), di luar radius {RADIUS_LOKASI_METER} meter.\n\n"
            "📍 Silakan pindah ke lokasi yang benar, lalu kirim ulang lokasi lewat tombol di bawah.",
            parse_mode="Markdown",
            reply_markup=tombol_lokasi,
        )
        return ABSEN_LOKASI

    context.user_data["tag_lokasi"] = f"{kantor['nama']} ({jarak:.1f} meter)"

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        await update.message.reply_text(
            f"✅ Lokasi diperbarui: *{escape_markdown(kantor['nama'])}* ({jarak:.1f} m)",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return await absen_tampilkan_ringkasan(update, context)

    await update.message.reply_text(
        f"✅ Lokasi tervalidasi: *{escape_markdown(kantor['nama'])}* ({jarak:.1f} m dari titik resmi)\n\n"
        "📸 Sekarang kirim FOTO sebagai bukti kehadiran Anda:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ABSEN_FOTO


async def absen_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    path_foto = await download_foto_dari_pesan(update, f"absen_{context.user_data.get('kode', 'x')}")
    if path_foto is None:
        await update.message.reply_text("Mohon kirim dalam bentuk FOTO atau File gambar ya.")
        return ABSEN_FOTO

    context.user_data["foto"] = path_foto

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await absen_tampilkan_ringkasan(update, context)

    return await absen_tampilkan_ringkasan(update, context)


def _teks_ringkasan_absen(ud, judul):
    jam_sekarang = waktu_sekarang().time()
    batas_telat = datetime.strptime("11:00", "%H:%M").time()
    status_preview = "Tepat Waktu" if jam_sekarang <= batas_telat else "Telat (Lanjut untuk kegiatan lain)"
    return (
        f"{judul}\n\n"
        f"👤 Nama: {ud.get('nama')} ({ud.get('kode')})\n"
        f"📝 Rencana: {ud.get('rencana_kegiatan')}\n"
        f"📍 Lokasi: {ud.get('tag_lokasi')}\n"
        f"🕒 Status: {status_preview}"
    )


async def absen_tampilkan_ringkasan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    ringkasan = _teks_ringkasan_absen(ud, "📋 KONFIRMASI DATA ABSEN") + "\n\nMohon cek lagi, apakah data di atas sudah benar?"

    tombol = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Edit Rencana", callback_data="absenaksi_edit_rencana")],
            [InlineKeyboardButton("✅ Ya, Simpan & Kirim", callback_data="absenaksi_submit")],
            [InlineKeyboardButton("❌ Batalkan", callback_data="absenaksi_batal")],
        ]
    )

    target = update.callback_query.message if update.callback_query else update.message
    foto = ud.get("foto")

    if foto:
        try:
            with open(foto, "rb") as f:
                await target.reply_photo(photo=f, caption=ringkasan, reply_markup=tombol)
            return ABSEN_KONFIRMASI
        except Exception as e:
            logger.warning(f"Gagal lampirkan foto di ringkasan absen, fallback ke teks saja: {e}")

    await target.reply_text(ringkasan, reply_markup=tombol)
    return ABSEN_KONFIRMASI


async def absen_konfirmasi_aksi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    if query.data == "absenaksi_batal":
        await query.message.reply_text(
            "❌ Absen dibatalkan. Ketik /absen untuk mulai ulang dari awal."
        )
        context.user_data.clear()
        return ConversationHandler.END

    if query.data == "absenaksi_edit_rencana":
        context.user_data["mode_edit"] = True
        await query.message.reply_text("Masukkan Rencana Kegiatan yang baru:")
        return ABSEN_RENCANA

    # query.data == "absenaksi_submit"
    ud = context.user_data
    kode = ud["kode"]

    jam_sekarang = waktu_sekarang().time()
    batas_telat = datetime.strptime("11:00", "%H:%M").time()
    status_absen = "Tepat Waktu" if jam_sekarang <= batas_telat else "Telat (Lanjut untuk kegiatan lain)"
    tanggal = tanggal_hari_ini()

    try:
        await services.simpan_absensi(
            tanggal, kode, ud["nama"], ud["tag_lokasi"], ud["foto"], ud["rencana_kegiatan"],
            waktu_sekarang().strftime("%H:%M"), status_absen,
        )
    except Exception as e:
        logger.error(f"Gagal simpan absensi ke database: {e}")
        await query.message.reply_text(
            "⚠️ Terjadi kesalahan saat menyimpan absen ke database. Coba lagi atau hubungi admin.",
        )
        return ConversationHandler.END

    caption = (
        "✅ ABSEN MASUK\n\n"
        f"👤 {ud['nama']} ({kode})\n"
        f"🕒 {waktu_sekarang().strftime('%H:%M')} — {status_absen}\n"
        f"📍 {ud['tag_lokasi']}\n"
        f"📝 Rencana: {ud['rencana_kegiatan']}"
    )

    await query.message.reply_text(caption)
    await kirim_notifikasi_grup(context, caption, ud["foto"])

    context.user_data.clear()
    return ConversationHandler.END
