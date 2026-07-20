"""
Entry point bot. Merangkai semua ConversationHandler & command handler,
lalu jalankan polling.

Jalankan dengan: python -m bot_absensi.main
(dari folder di atas bot_absensi/, dengan file .env di folder yang sama)
"""
import threading
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
)
from telegram.request import HTTPXRequest

from . import db
from .config import BOT_TOKEN, logger
from .integrations.gsheet import init_gsheet
from .integrations.excel_online import init_excel_online
from .integrations.gcs import init_gcs
from .utils.geo import preload_ocr_reader_background
from .states import (
    ABSEN_STATUS, ABSEN_RENCANA, ABSEN_LOKASI, ABSEN_FOTO, ABSEN_KONFIRMASI,
    ABSEN_IZIN_KETERANGAN, ABSEN_IZIN_TAMBAH_FOTO, ABSEN_IZIN_FOTO,
    KEG_NAMA_KEGIATAN, KEG_NAMA_USAHA, KEG_HASIL, KEG_STATUS_DEAL, KEG_PAKET,
    KEG_NOHP, KEG_PIC, KEG_JABATAN, KEG_LOKASI, KEG_FOTO,
    KEG_RINGKASAN_AKSI, KEG_PILIH_EDIT, REGISTRASI_KODE,
)
from .handlers import registrasi, absen, kegiatan, umum
from . import scheduler


def main():
    # Timeout HTTP dinaikkan supaya lebih tahan terhadap koneksi yang lambat
    # (mengurangi kemungkinan telegram.error.TimedOut / httpx.ReadTimeout).
    request = HTTPXRequest(connect_timeout=20, read_timeout=20)
    app = ApplicationBuilder().token(BOT_TOKEN).request(request).build()

    conv_registrasi = ConversationHandler(
        entry_points=[CommandHandler("daftar", registrasi.registrasi_mulai)],
        states={
            REGISTRASI_KODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, registrasi.registrasi_kode)],
        },
        fallbacks=[CommandHandler("batal", umum.batal)],
    )

    conv_absen = ConversationHandler(
        entry_points=[CommandHandler("absen", absen.absen_mulai)],
        states={
            ABSEN_STATUS: [
                CallbackQueryHandler(absen.absen_status, pattern="^status_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, absen.absen_status_belum_tap),
            ],
            ABSEN_RENCANA: [MessageHandler(filters.TEXT & ~filters.COMMAND, absen.absen_rencana)],
            ABSEN_LOKASI: [MessageHandler(filters.LOCATION, absen.absen_lokasi)],
            ABSEN_FOTO: [MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, absen.absen_foto)],
            ABSEN_KONFIRMASI: [CallbackQueryHandler(absen.absen_konfirmasi_aksi, pattern="^absenaksi_")],
            ABSEN_IZIN_KETERANGAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, absen.absen_izin_keterangan)],
            ABSEN_IZIN_TAMBAH_FOTO: [CallbackQueryHandler(absen.absen_izin_tambah_foto, pattern="^izinfoto_")],
            ABSEN_IZIN_FOTO: [MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, absen.absen_izin_foto)],
        },
        fallbacks=[CommandHandler("batal", umum.batal)],
    )

    conv_kegiatan = ConversationHandler(
        entry_points=[CommandHandler("kegiatan", kegiatan.kegiatan_mulai)],
        states={
            KEG_NAMA_KEGIATAN: [
                CallbackQueryHandler(kegiatan.keg_pilih_jenis_kegiatan, pattern="^jeniskeg_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, kegiatan.keg_jenis_kegiatan_belum_tap),
            ],
            KEG_NAMA_USAHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, kegiatan.keg_nama_usaha)],
            KEG_HASIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, kegiatan.keg_hasil)],
            KEG_STATUS_DEAL: [CallbackQueryHandler(kegiatan.keg_status_deal, pattern="^deal_")],
            KEG_PAKET: [MessageHandler(filters.TEXT & ~filters.COMMAND, kegiatan.keg_paket)],
            KEG_NOHP: [MessageHandler(filters.TEXT & ~filters.COMMAND, kegiatan.keg_nohp)],
            KEG_PIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, kegiatan.keg_pic)],
            KEG_JABATAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, kegiatan.keg_jabatan)],
            KEG_LOKASI: [MessageHandler(filters.LOCATION, kegiatan.keg_lokasi)],
            KEG_FOTO: [MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, kegiatan.keg_foto)],
            KEG_RINGKASAN_AKSI: [CallbackQueryHandler(kegiatan.keg_ringkasan_aksi, pattern="^keg_aksi_")],
            KEG_PILIH_EDIT: [CallbackQueryHandler(kegiatan.keg_pilih_edit, pattern="^editf_")],
        },
        fallbacks=[CommandHandler("batal", umum.batal)],
    )

    app.add_handler(CommandHandler("start", umum.mulai))
    app.add_handler(CommandHandler("help", umum.mulai))
    app.add_handler(CommandHandler("rekapabsen", umum.rekap_absen))
    app.add_handler(CommandHandler("rekapkegiatan", umum.rekap_kegiatan))
    app.add_handler(CommandHandler("exportexcel", umum.export_excel))
    app.add_handler(CommandHandler("grupid", umum.grup_id))
    app.add_handler(conv_registrasi)
    app.add_handler(conv_absen)
    app.add_handler(conv_kegiatan)
    app.add_error_handler(umum.error_handler)

    db.migrasi_schema()
    init_gsheet()
    init_excel_online()
    init_gcs()

    # Preload model OCR di thread terpisah saat startup, supaya bot tidak
    # nge-lag lama pas user PERTAMA kali butuh fallback OCR (EXIF gagal
    # dibaca). Dijalankan di background, tidak menahan proses start bot.
    threading.Thread(target=preload_ocr_reader_background, daemon=True).start()

    if app.job_queue is not None:
        wib = ZoneInfo("Asia/Jakarta")
        app.job_queue.run_daily(scheduler.job_rekap_pagi, time=dt_time(11, 0, tzinfo=wib), name="rekap_pagi_otomatis")
        app.job_queue.run_daily(scheduler.job_rekap_malam, time=dt_time(20, 0, tzinfo=wib), name="rekap_malam_otomatis")
        logger.info("Scheduler rekap otomatis aktif: 11:00 & 20:00 WIB.")
    else:
        logger.warning(
            "JobQueue tidak aktif (ekstra APScheduler belum terpasang). Rekap otomatis TIDAK berjalan. "
            "Install dengan: pip install \"python-telegram-bot[job-queue]\""
        )

    logger.info("Bot berjalan...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
