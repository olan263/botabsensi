"""Alur input laporan kegiatan/visit."""
from telegram import (
    Update, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes, ConversationHandler

from .. import db, services
from ..config import logger, DAFTAR_JENIS_KEGIATAN
from ..states import (
    KEG_NAMA_KEGIATAN, KEG_NAMA_USAHA, KEG_HASIL, KEG_STATUS_DEAL, KEG_PAKET,
    KEG_NOHP, KEG_PIC, KEG_JABATAN, KEG_LOKASI, KEG_FOTO,
    KEG_RINGKASAN_AKSI, KEG_PILIH_EDIT,
)
from ..utils.misc import escape_markdown, sensor_nomor_hp, validasi_no_hp, tanggal_hari_ini
from ..utils.geo import buat_link_google_maps, reverse_geocode
from .registrasi import pastikan_terdaftar
from .umum import download_foto_dari_pesan, kirim_notifikasi_grup, MENU_UTAMA_KEYBOARD, MENU_BATAL_KEYBOARD


async def kegiatan_mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    hasil_daftar = await pastikan_terdaftar(update, context)
    if hasil_daftar is None:
        return ConversationHandler.END
    kode, nama = hasil_daftar

    tanggal = tanggal_hari_ini()
    hasil = await db.cek_sudah_absen(tanggal, kode)
    if hasil is None:
        await update.message.reply_text(
            "❌ Anda BELUM melakukan absen masuk HARI INI! Silakan absen terlebih dahulu.\n\n"
            "➡️ Klik tombol 📝 Absen Masuk untuk absen terlebih dahulu, lalu setelah selesai baru klik tombol 🏃 Input Kegiatan lagi."
        )
        return ConversationHandler.END

    _, status = hasil
    if status in ("Sakit", "Izin"):
        await update.message.reply_text(
            f"❌ Anda tercatat *{escape_markdown(status)}* hari ini, sehingga tidak bisa mengisi laporan kegiatan.\n"
            "Jika terdapat keliru, silakan hubungi admin.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["kode"] = kode
    context.user_data["nama"] = nama
    context.user_data["tanggal"] = tanggal

    tombol_kegiatan = InlineKeyboardMarkup(
        [[InlineKeyboardButton(j, callback_data=f"jeniskeg_{i}")] for i, j in enumerate(DAFTAR_JENIS_KEGIATAN)]
    )
    await update.message.reply_text(
        f"Halo, {escape_markdown(nama)}. Pilih Jenis Kegiatan:",
        parse_mode="Markdown",
        reply_markup=tombol_kegiatan,
    )
    return KEG_NAMA_KEGIATAN


async def keg_pilih_jenis_kegiatan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    idx = int(query.data.split("_", 1)[1])
    context.user_data["nama_kegiatan"] = DAFTAR_JENIS_KEGIATAN[idx]

    await query.message.reply_text(f"✅ Anda memilih: *{DAFTAR_JENIS_KEGIATAN[idx]}*", parse_mode="Markdown")

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await query.message.reply_text("🏢 Masukkan Nama Usaha/Toko pelanggan yang dikunjungi:", reply_markup=MENU_BATAL_KEYBOARD)
    return KEG_NAMA_USAHA


async def keg_jenis_kegiatan_belum_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Mohon tap salah satu tombol pilihan Jenis Kegiatan di atas ya, bukan ketik teks."
    )
    return KEG_NAMA_KEGIATAN


async def keg_nama_usaha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nama_usaha"] = update.message.text.strip()

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text("Masukkan Nama PIC Pelanggan:")
    return KEG_PIC


async def keg_hasil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["hasil"] = update.message.text.strip()

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    tombol_lokasi = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Kirim Lokasi Kegiatan Sekarang", request_location=True)], [KeyboardButton("❌ Batal")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "📍 Silakan share lokasi kegiatan (tekan tombol di bawah).\n"
        "Pastikan Anda sedang berada di lokasi kegiatan saat share.",
        reply_markup=tombol_lokasi,
    )
    return KEG_LOKASI


async def keg_nohp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nomor_valid = validasi_no_hp(update.message.text)
    if nomor_valid is None:
        await update.message.reply_text(
            "❌ Format nomor HP tidak valid. Gunakan format +62xxxxxxxxxx atau 08xxxxxxxxxx "
            "(tanpa spasi berlebih), atau ketik '-' jika tidak ada."
        )
        return KEG_NOHP

    context.user_data["no_hp_pic"] = nomor_valid

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text("Masukkan Jabatan PIC Pelanggan (contoh: Manager Toko, Owner, Staff, dll):")
    return KEG_JABATAN


async def keg_pic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nama_pic"] = update.message.text.strip()

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text(
        "Masukkan No HP PIC Pelanggan (format +62 atau 08..., ketik '-' jika tidak ada):"
    )
    return KEG_NOHP


async def keg_jabatan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["jabatan_pic"] = update.message.text.strip()

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text("Masukkan Hasil dari Kegiatan:")
    return KEG_HASIL


async def keg_lokasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        await update.message.reply_text(
            "Mohon share lokasi lewat tombol 📍 yang tersedia, bukan ketik teks."
        )
        return KEG_LOKASI

    lat = update.message.location.latitude
    lon = update.message.location.longitude
    link_maps = buat_link_google_maps(lat, lon)
    alamat = await reverse_geocode(lat, lon)
    context.user_data["tag_lokasi_kegiatan"] = f"{alamat} ({link_maps})" if alamat else link_maps

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        await update.message.reply_text(
            f"✅ Lokasi kegiatan diperbarui.\n📍 {link_maps}", reply_markup=ReplyKeyboardRemove()
        )
        return await keg_tampilkan_ringkasan(update, context)

    await update.message.reply_text(
        f"✅ Lokasi kegiatan tersimpan.\n📍 {link_maps}\n\n"
        "📸 Sekarang kirim FOTO Kegiatan:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return KEG_FOTO


async def keg_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    path_foto = await download_foto_dari_pesan(update, f"kegiatan_{context.user_data.get('kode', 'x')}")
    if path_foto is None:
        await update.message.reply_text("Mohon kirim dalam bentuk FOTO atau File gambar ya.")
        return KEG_FOTO

    context.user_data["foto_kegiatan"] = path_foto

    if context.user_data.get("mode_edit"):
        context.user_data["mode_edit"] = False
        return await keg_tampilkan_ringkasan(update, context)

    return await keg_tampilkan_ringkasan(update, context)


def _teks_ringkasan_kegiatan(ud, judul):
    return (
        f"{judul}\n\n"
        f"📌 Kegiatan: {ud.get('nama_kegiatan')}\n"
        f"🏢 Nama Usaha: {ud.get('nama_usaha')}\n"
        f"👷 Nama PIC Pelanggan: {ud.get('nama_pic')}\n"
        f"📱 No HP PIC Pelanggan: {sensor_nomor_hp(ud.get('no_hp_pic'))}\n"
        f"💼 Jabatan PIC Pelanggan: {ud.get('jabatan_pic')}\n"
        f"📝 Hasil Kegiatan: {ud.get('hasil')}\n"
        f"📍 Lokasi Usaha: {ud.get('tag_lokasi_kegiatan')}"
    )


async def keg_tampilkan_ringkasan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    ringkasan = _teks_ringkasan_kegiatan(ud, "📋 KONFIRMASI DATA KEGIATAN") + "\n\nMohon cek lagi, apakah data di atas sudah benar?"

    tombol = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Edit", callback_data="keg_aksi_edit")],
            [InlineKeyboardButton("✅ Ya, Simpan & Kirim", callback_data="keg_aksi_submit")],
            [InlineKeyboardButton("❌ Batalkan", callback_data="keg_aksi_batal")],
        ]
    )

    target = update.callback_query.message if update.callback_query else update.message
    foto = ud.get("foto_kegiatan")

    if foto:
        try:
            with open(foto, "rb") as f:
                await target.reply_photo(photo=f, caption=ringkasan, reply_markup=tombol)
            return KEG_RINGKASAN_AKSI
        except Exception as e:
            logger.warning(f"Gagal lampirkan foto di ringkasan, fallback ke teks saja: {e}")

    await target.reply_text(ringkasan, reply_markup=tombol)
    return KEG_RINGKASAN_AKSI


async def keg_ringkasan_aksi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    if query.data == "keg_aksi_batal":
        await query.message.reply_text(
            "❌ Laporan kegiatan dibatalkan. Silakan pilih menu lain atau mulai dari awal.",
            reply_markup=MENU_UTAMA_KEYBOARD
        )
        context.user_data.clear()
        return ConversationHandler.END

    if query.data == "keg_aksi_edit":
        daftar_tombol = [
            [InlineKeyboardButton("📌 Nama Kegiatan", callback_data="editf_nama_kegiatan")],
            [InlineKeyboardButton("🏢 Nama Usaha", callback_data="editf_nama_usaha")],
            [InlineKeyboardButton("👷 Nama PIC Pelanggan", callback_data="editf_pic")],
            [InlineKeyboardButton("📱 No HP PIC Pelanggan", callback_data="editf_nohp")],
            [InlineKeyboardButton("💼 Jabatan PIC Pelanggan", callback_data="editf_jabatan")],
            [InlineKeyboardButton("📝 Hasil Kegiatan", callback_data="editf_hasil")],
            [InlineKeyboardButton("📍 Lokasi Usaha", callback_data="editf_lokasi")],
            [InlineKeyboardButton("📸 Foto Kegiatan", callback_data="editf_foto")],
        ]
        await query.message.reply_text(
            "Pilih data yang ingin diedit:", reply_markup=InlineKeyboardMarkup(daftar_tombol)
        )
        return KEG_PILIH_EDIT

    # query.data == "keg_aksi_submit"
    ud = context.user_data
    kode = ud["kode"]
    tanggal = ud["tanggal"]

    try:
        await services.simpan_kegiatan(
            tanggal, kode,
            ud["nama_kegiatan"], ud["nama_usaha"], ud["tag_lokasi_kegiatan"], ud["foto_kegiatan"], ud["hasil"],
            ud["no_hp_pic"], ud["nama_pic"], ud["jabatan_pic"],
            None, None,
        )
    except Exception as e:
        logger.error(f"Gagal simpan kegiatan ke database: {e}")
        await query.message.reply_text(
            "⚠️ Terjadi kesalahan saat menyimpan kegiatan ke database. Coba lagi atau hubungi admin."
        )
        return ConversationHandler.END

    ringkasan_final = (
        f"👤 {ud['nama']} ({kode})\n" + _teks_ringkasan_kegiatan(ud, "✅ LAPORAN KEGIATAN TERSIMPAN")
    )
    await query.message.reply_text(ringkasan_final, reply_markup=MENU_UTAMA_KEYBOARD)
    await kirim_notifikasi_grup(context, ringkasan_final, ud.get("foto_kegiatan"))

    context.user_data.clear()
    return ConversationHandler.END


async def keg_pilih_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    context.user_data["mode_edit"] = True
    field = query.data.split("_", 1)[1]

    if field == "nama_kegiatan":
        tombol_kegiatan = InlineKeyboardMarkup(
            [[InlineKeyboardButton(j, callback_data=f"jeniskeg_{i}")] for i, j in enumerate(DAFTAR_JENIS_KEGIATAN)]
        )
        await query.message.reply_text("Pilih Jenis Kegiatan yang baru:", reply_markup=tombol_kegiatan)
        return KEG_NAMA_KEGIATAN

    if field == "nama_usaha":
        await query.message.reply_text("Masukkan Nama Usaha/Toko yang baru:")
        return KEG_NAMA_USAHA

    if field == "lokasi":
        tombol_lokasi = ReplyKeyboardMarkup(
            [[KeyboardButton("📍 Kirim Lokasi Kegiatan Sekarang", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await query.message.reply_text("Share lokasi kegiatan yang baru:", reply_markup=tombol_lokasi)
        return KEG_LOKASI

    if field == "foto":
        await query.message.reply_text("Kirim FOTO kegiatan yang baru:")
        return KEG_FOTO

    if field == "hasil":
        await query.message.reply_text("Masukkan Hasil kegiatan yang baru:")
        return KEG_HASIL

    if field == "nohp":
        await query.message.reply_text(
            "Masukkan No HP PIC yang baru (format +62 atau 08..., ketik '-' jika tidak ada):"
        )
        return KEG_NOHP

    if field == "pic":
        await query.message.reply_text("Masukkan Nama PIC yang baru:")
        return KEG_PIC

    if field == "jabatan":
        await query.message.reply_text("Masukkan Jabatan PIC yang baru:")
        return KEG_JABATAN

    return await keg_tampilkan_ringkasan(update, context)
