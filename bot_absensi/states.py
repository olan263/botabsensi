"""Semua konstanta state ConversationHandler, dikumpulkan di 1 tempat supaya
handlers/ dan main.py sama-sama pakai angka yang konsisten."""

(
    ABSEN_STATUS, ABSEN_RENCANA, ABSEN_LOKASI, ABSEN_FOTO, ABSEN_KONFIRMASI,
    ABSEN_IZIN_KETERANGAN, ABSEN_IZIN_TAMBAH_FOTO, ABSEN_IZIN_FOTO,
) = range(8)

(
    KEG_NAMA_KEGIATAN, KEG_NAMA_USAHA, KEG_HASIL, KEG_STATUS_DEAL, KEG_PAKET,
    KEG_NOHP, KEG_PIC, KEG_JABATAN, KEG_LOKASI, KEG_FOTO,
    KEG_RINGKASAN_AKSI, KEG_PILIH_EDIT,
) = range(8, 20)

(REGISTRASI_KODE,) = range(20, 21)
