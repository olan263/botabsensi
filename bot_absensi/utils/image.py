"""Kompresi foto sebelum disimpan, supaya ukuran file lebih kecil (hemat
storage lokal, kuota upload ke GCS, dan lebih cepat dikirim ke Telegram)."""
import os

from PIL import Image
from ..config import logger

# Dimensi maksimum (sisi terpanjang foto) - foto lebih besar dari ini akan
# di-resize turun, sambil tetap menjaga rasio aspek.
MAX_DIMENSI = 1280
# Kualitas JPEG hasil kompresi (1-95). 70 sudah cukup jelas dilihat tapi
# ukuran filenya jauh lebih kecil dari foto asli kamera HP.
KUALITAS_JPEG = 70

# Target ukuran file akhir, dalam KB. Kalau hasil kompresi awal (quality 70)
# masih di atas ini, quality akan diturunkan bertahap sampai di bawah target.
TARGET_KB = 100
KUALITAS_MINIMUM = 25

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

            # Kalau hasil kompresi awal masih di atas TARGET_KB, turunkan
            # quality bertahap sampai di bawah target (atau sampai KUALITAS_MINIMUM).
            kualitas = KUALITAS_JPEG
            ukuran_kb = os.path.getsize(path_foto) / 1024
            while ukuran_kb > TARGET_KB and kualitas > KUALITAS_MINIMUM:
                kualitas -= 10
                img.save(path_foto, format="JPEG", quality=kualitas, optimize=True)
                ukuran_kb = os.path.getsize(path_foto) / 1024

            logger.info(f"Foto {path_foto} dikompres: {ukuran_kb:.1f} KB (quality={kualitas})")
    except Exception as e:
        logger.warning(f"Gagal kompres foto {path_foto}, dipakai apa adanya: {e}")
