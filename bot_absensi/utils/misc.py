"""Helper kecil: escape markdown, sensor nomor HP, validasi nomor HP, tanggal hari ini."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo


def tanggal_hari_ini():
    return datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d")


def escape_markdown(teks):
    """Escape karakter spesial Markdown (legacy) dari teks bebas input user,
    supaya tidak bikin error 'Can't parse entities' di Telegram."""
    if teks is None:
        return ""
    teks = str(teks)
    karakter_spesial = ["_", "*", "`", "["]
    for k in karakter_spesial:
        teks = teks.replace(k, f"\\{k}")
    return teks


def sensor_nomor_hp(nomor):
    """Menyensor nomor HP, hanya menampilkan 3 digit awal, contoh: 081234567890 -> 081*********"""
    nomor_bersih = re.sub(r"\s+", "", nomor or "")
    if len(nomor_bersih) <= 3:
        return "*" * len(nomor_bersih)
    depan = nomor_bersih[:3]
    tengah = "*" * (len(nomor_bersih) - 3)
    return f"{depan}{tengah}"


def validasi_no_hp(teks):
    """Validasi & normalisasi nomor HP PIC pelanggan.
    - Kosong / '-' / 'tidak ada' dsb -> dianggap tidak diisi, hasilnya "0".
    - Harus berformat +62xxxxxxxxx atau 08xxxxxxxxx (8-13 digit setelah prefix),
      kalau tidak sesuai -> return None (artinya tidak valid, minta input ulang).
    - Hasil valid dinormalisasi ke format 08xxxxxxxxx.
    """
    if teks is None:
        return "0"
    bersih = teks.strip()
    if bersih == "" or bersih.lower() in ("-", "0", "tidak ada", "tdk ada", "kosong", "belum ada"):
        return "0"

    bersih = re.sub(r"[\s\-]", "", bersih)

    if re.fullmatch(r"\+62\d{8,13}", bersih):
        return "0" + bersih[3:]
    if re.fullmatch(r"62\d{8,13}", bersih):
        return "0" + bersih[2:]
    if re.fullmatch(r"08\d{7,12}", bersih):
        return bersih

    return None
