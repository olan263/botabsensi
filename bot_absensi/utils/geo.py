"""
Deteksi koordinat GPS dari foto (EXIF/OCR), perhitungan jarak, dan reverse
geocoding.
"""
import math
import re
import asyncio

from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

from ..config import logger, TITIK_LOKASI_RESMI

try:
    import easyocr
    OCR_TERSEDIA = True
except ImportError:
    OCR_TERSEDIA = False

try:
    import requests
    REQUESTS_TERSEDIA = True
except ImportError:
    REQUESTS_TERSEDIA = False

_ocr_reader = None


def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        logger.info("Memuat model OCR (sekali di awal)...")
        _ocr_reader = easyocr.Reader(["id", "en"])
    return _ocr_reader


def preload_ocr_reader_background():
    """Panggil ini sekali saat startup (di thread terpisah) supaya model OCR
    sudah ke-load duluan, bukan pas ada user yang butuh fallback OCR (yang
    bikin request itu ngelag lama karena nunggu model dimuat)."""
    if OCR_TERSEDIA:
        try:
            _get_ocr_reader()
        except Exception as e:
            logger.warning(f"Gagal preload model OCR: {e}")


def konversi_ke_desimal(value):
    d = float(value[0])
    m = float(value[1])
    s = float(value[2])
    return d + (m / 60.0) + (s / 3600.0)


def ambil_koordinat_dari_exif(path_foto):
    """METODE 1: baca metadata EXIF GPS foto (hanya ada kalau foto dikirim
    sebagai File/Document, bukan Photo terkompresi biasa)"""
    try:
        image = Image.open(path_foto)
        exif_data = image._getexif()
        if not exif_data:
            return None, None

        geotagging = {}
        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag == "GPSInfo":
                for t in value:
                    sub_tag = GPSTAGS.get(t, t)
                    geotagging[sub_tag] = value[t]

        if "GPSLatitude" in geotagging and "GPSLongitude" in geotagging:
            lat = konversi_ke_desimal(geotagging["GPSLatitude"])
            lon = konversi_ke_desimal(geotagging["GPSLongitude"])
            if geotagging.get("GPSLatitudeRef") == "S":
                lat = -lat
            if geotagging.get("GPSLongitudeRef") == "W":
                lon = -lon
            return lat, lon
    except Exception as e:
        logger.warning(f"Gagal membaca EXIF foto: {e}")

    return None, None


def ambil_koordinat_dari_teks_ocr(path_foto):
    """METODE 2 (fallback): baca watermark teks koordinat di badan foto lewat OCR
    (tetap kebaca walau foto dikompres Telegram, karena bagian dari pixel gambar)"""
    if not OCR_TERSEDIA:
        logger.warning("Modul 'easyocr' belum terinstall.")
        return None, None
    try:
        reader = _get_ocr_reader()
        hasil_ocr = reader.readtext(path_foto)
        teks_penuh = " ".join([blok[1] for blok in hasil_ocr])
        logger.info(f"Teks Terdeteksi di Foto (OCR): {teks_penuh}")

        pola_koordinat = re.findall(r"(-?\d+\.\d+)", teks_penuh)
        if len(pola_koordinat) >= 2:
            lat = float(pola_koordinat[0])
            lon = float(pola_koordinat[1])
            return lat, lon
    except Exception as e:
        logger.warning(f"Gagal memproses OCR foto: {e}")

    return None, None


def deteksi_koordinat_foto(path_foto):
    """Gabungan: coba EXIF dulu, fallback ke OCR watermark teks.
    Return (lat, lon, sumber)"""
    lat, lon = ambil_koordinat_dari_exif(path_foto)
    if lat is not None and lon is not None:
        return lat, lon, "EXIF"

    lat, lon = ambil_koordinat_dari_teks_ocr(path_foto)
    if lat is not None and lon is not None:
        return lat, lon, "OCR"

    return None, None, None


def hitung_jarak_meter(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def cari_kantor_terdekat(lat, lon):
    """Cari titik lokasi resmi TERDEKAT dari koordinat yang diberikan.
    Return (kantor_dict, jarak_meter)."""
    terdekat = None
    jarak_terdekat = None
    for kantor in TITIK_LOKASI_RESMI:
        jarak = hitung_jarak_meter(lat, lon, kantor["lat"], kantor["lon"])
        if jarak_terdekat is None or jarak < jarak_terdekat:
            jarak_terdekat = jarak
            terdekat = kantor
    return terdekat, jarak_terdekat


def buat_link_google_maps(lat, lon):
    return f"https://www.google.com/maps?q={lat},{lon}"


def _reverse_geocode_sync(lat, lon):
    """Ubah koordinat GPS jadi nama alamat (OpenStreetMap Nominatim, gratis
    tanpa API key). Return None kalau gagal/timeout, supaya alur bot tidak macet.
    Timeout dijaga singkat (5 detik) karena Nominatim kadang lambat/rate-limited -
    ini salah satu titik yang bisa bikin bot berasa nge-lag saat share lokasi
    kegiatan."""
    if not REQUESTS_TERSEDIA:
        return None
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "BotAbsensiKegiatan/1.0"},
            timeout=5,
        )
        data = resp.json()
        return data.get("display_name")
    except Exception as e:
        logger.warning(f"Gagal reverse geocode: {e}")
        return None


async def reverse_geocode(lat, lon):
    return await asyncio.to_thread(_reverse_geocode_sync, lat, lon)
