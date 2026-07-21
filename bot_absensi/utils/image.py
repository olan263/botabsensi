"""Kompresi foto sebelum disimpan, supaya ukuran file lebih kecil (hemat
storage lokal, kuota upload ke GCS, dan lebih cepat dikirim ke Telegram)."""
from PIL import Image

from ..config import logger

# Dimensi maksimum (sisi terpanjang foto) - foto lebih besar dari ini akan
# di-resize turun, sambil tetap menjaga rasio aspek.
MAX_DIMENSI = 1280

# Kualitas JPEG hasil kompresi (1-95). 70 sudah cukup jelas dilihat tapi
# ukuran filenya jauh lebih kecil dari foto asli kamera HP.
KUALITAS_JPEG = 70


def kompres_foto(path_foto):
    """Kompres file foto di tempat (in-place). Aman dipanggil untuk semua
    format umum (jpg/png/webp dst) - hasilnya selalu disimpan ulang sebagai
    JPEG (format paling efisien untuk foto kamera), path/nama file TIDAK
    diubah supaya kode lain (kirim ke Telegram, upload GCS, dst) tetap
    jalan tanpa perubahan.
    Kalau gagal (file corrupt dst), dibiarkan apa adanya - tidak menghentikan
    alur bot."""
    try:
        with Image.open(path_foto) as img:
            img = img.convert("RGB")  # buang alpha channel kalau ada (png/webp)

            lebar, tinggi = img.size
            sisi_terpanjang = max(lebar, tinggi)
            if sisi_terpanjang > MAX_DIMENSI:
                skala = MAX_DIMENSI / sisi_terpanjang
                ukuran_baru = (int(lebar * skala), int(tinggi * skala))
                img = img.resize(ukuran_baru, Image.LANCZOS)

            img.save(path_foto, format="JPEG", quality=KUALITAS_JPEG, optimize=True)
    except Exception as e:
        logger.warning(f"Gagal kompres foto {path_foto}, dipakai apa adanya: {e}")
