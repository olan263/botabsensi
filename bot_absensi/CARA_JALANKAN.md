# Cara menjalankan

1. Taruh folder `bot_absensi/` ini di dalam project Anda, sejajar dengan file `.env` yang sudah ada.
2. Install dependency: `pip install -r bot_absensi/requirements.txt`
3. Jalankan dari folder **di luar** `bot_absensi/`:

   ```
   python -m bot_absensi.main
   ```

   (Jangan `python bot_absensi/main.py` langsung, karena ini pakai relative import antar modul.)

## Yang berubah dari versi 1-file

- Kode dipecah per tanggung jawab: `config.py`, `db.py`, `services.py`,
  `states.py`, `scheduler.py`, `integrations/` (Google Sheets, Excel Online,
  export lokal), `utils/` (geo, misc), `handlers/` (registrasi, absen,
  kegiatan, umum), `main.py` (wiring + entry point).
- **Fix lag**: `services.py` sekarang menyimpan ke database dulu (ditunggu),
  baru melepas sync ke Google Sheets & Excel Online sebagai *background task*
  (`asyncio.create_task`, tidak ditunggu). Sebelumnya bot menunggu KEDUA sync
  itu selesai dulu sebelum membalas ke user — itu penyebab utama lag saat
  submit absen/kegiatan.
- Model OCR (`easyocr`) sekarang di-preload di thread terpisah saat bot
  start, bukan dimuat pas ada user pertama yang butuh fallback OCR (yang
  bikin request itu nge-freeze lama).
- Logika, teks pesan, dan alur percakapan **tidak diubah sama sekali** —
  cuma penataan ulang file + perbaikan cara nge-sync ke layanan luar.
