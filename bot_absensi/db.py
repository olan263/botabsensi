"""
Semua akses database (PostgreSQL) ada di sini: connection pool + query
untuk tabel karyawan, absensi, dan kegiatan. Disesuaikan dengan Skema Supabase terbaru.
"""
import asyncio
import psycopg2
from psycopg2 import pool
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import DB_CONFIG, logger

_db_pool = psycopg2.pool.SimpleConnectionPool(1, 5, **DB_CONFIG)

def migrasi_schema():
    # Migrasi dinonaktifkan karena skema diatur manual via Supabase
    logger.info("Migrasi schema dilewati (menggunakan skema manual Supabase).")

# ---------- MASTER KARYAWAN ----------

def _cari_nama_karyawan_sync(kode):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT nama_ar FROM karyawan WHERE kode_ar = %s", (kode,))
            hasil = cur.fetchone()
            return hasil[0] if hasil else None
    finally:
        _db_pool.putconn(conn)

async def cari_nama_karyawan(kode):
    try:
        return await asyncio.to_thread(_cari_nama_karyawan_sync, kode)
    except Exception as e:
        logger.error(f"Gagal query karyawan by kode: {e}")
        return None

def _cari_kode_by_telegram_id_sync(telegram_id):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT kode_ar, nama_ar FROM karyawan WHERE id_telegram = %s", (telegram_id,))
            return cur.fetchone()
    finally:
        _db_pool.putconn(conn)

async def cari_kode_by_telegram_id(telegram_id):
    try:
        return await asyncio.to_thread(_cari_kode_by_telegram_id_sync, telegram_id)
    except Exception as e:
        logger.error(f"Gagal query karyawan by telegram_id: {e}")
        return None

def _daftar_telegram_id_sync(kode, telegram_id):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id_telegram FROM karyawan WHERE kode_ar = %s", (kode,))
            hasil = cur.fetchone()
            if hasil is None:
                return "kode_tidak_ada"
            if hasil[0] is not None and hasil[0] != telegram_id:
                return "kode_sudah_dipakai"

            cur.execute("SELECT kode_ar FROM karyawan WHERE id_telegram = %s", (telegram_id,))
            hasil_lain = cur.fetchone()
            if hasil_lain is not None and hasil_lain[0] != kode:
                return "sudah_terdaftar"

            cur.execute("UPDATE karyawan SET id_telegram = %s WHERE kode_ar = %s", (telegram_id, kode))
        conn.commit()
        return "ok"
    except Exception:
        conn.rollback()
        raise
    finally:
        _db_pool.putconn(conn)

async def daftar_telegram_id(kode, telegram_id):
    return await asyncio.to_thread(_daftar_telegram_id_sync, kode, telegram_id)

# ---------- ABSENSI ----------

def _simpan_absensi_sync(tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status):
    sekarang_str = datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M:%S")
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO absensi (tanggal_absen, kode_ar, nama_ar, lokasi_absen, foto_absen, rencana_kegiatan, waktu_absen, status_absen)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tanggal_absen, kode_ar) DO UPDATE SET
                    nama_ar = EXCLUDED.nama_ar,
                    lokasi_absen = EXCLUDED.lokasi_absen,
                    foto_absen = EXCLUDED.foto_absen,
                    rencana_kegiatan = EXCLUDED.rencana_kegiatan,
                    waktu_absen = EXCLUDED.waktu_absen,
                    status_absen = EXCLUDED.status_absen
                """,
                (tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, sekarang_str, status),
            )
        conn.commit()
    finally:
        _db_pool.putconn(conn)

async def simpan_absensi_db(tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status):
    await asyncio.to_thread(
        _simpan_absensi_sync, tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status
    )

def _cek_sudah_absen_sync(tanggal, kode):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT nama_ar, status_absen FROM absensi WHERE tanggal_absen = %s AND kode_ar = %s", (tanggal, kode))
            return cur.fetchone()
    finally:
        _db_pool.putconn(conn)

async def cek_sudah_absen(tanggal, kode):
    return await asyncio.to_thread(_cek_sudah_absen_sync, tanggal, kode)

# ---------- KEGIATAN ----------

def _simpan_kegiatan_sync(
    tanggal, kode, nama_kegiatan, nama_usaha, tag_lokasi, foto_kegiatan, hasil,
    no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kegiatan
                    (tanggal_kegiatan, kode_ar, nama_kegiatan, nama_usaha, lokasi_usaha, foto_kegiatan, hasil_kegiatan,
                     nomor_pic, nama_pic, jabatan_pic)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (tanggal, kode, nama_kegiatan, nama_usaha, tag_lokasi, foto_kegiatan, hasil,
                 no_hp_pic, nama_pic, jabatan_pic),
            )
        conn.commit()
    finally:
        _db_pool.putconn(conn)

async def simpan_kegiatan_db(
    tanggal, kode, nama_kegiatan, nama_usaha, tag_lokasi, foto_kegiatan, hasil,
    no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
):
    await asyncio.to_thread(
        _simpan_kegiatan_sync, tanggal, kode, nama_kegiatan, nama_usaha, tag_lokasi, foto_kegiatan, hasil,
        no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
    )

# ---------- REKAP / EXPORT ----------

def _ambil_rekap_absensi_sync(tanggal_mulai=None, tanggal_selesai=None):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            if tanggal_mulai and tanggal_selesai:
                cur.execute(
                    """
                    SELECT tanggal_absen, kode_ar, nama_ar, TO_CHAR(waktu_absen, 'HH24:MI'), status_absen, lokasi_absen
                    FROM absensi
                    WHERE tanggal_absen BETWEEN %s AND %s
                    ORDER BY tanggal_absen DESC, kode_ar
                    """,
                    (tanggal_mulai, tanggal_selesai),
                )
            else:
                cur.execute(
                    """
                    SELECT tanggal_absen, kode_ar, nama_ar, TO_CHAR(waktu_absen, 'HH24:MI'), status_absen, lokasi_absen
                    FROM absensi
                    ORDER BY tanggal_absen DESC, kode_ar
                    """
                )
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)

async def ambil_rekap_absensi(tanggal_mulai=None, tanggal_selesai=None):
    return await asyncio.to_thread(_ambil_rekap_absensi_sync, tanggal_mulai, tanggal_selesai)

def _ambil_rekap_kegiatan_sync(tanggal_mulai=None, tanggal_selesai=None):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            if tanggal_mulai and tanggal_selesai:
                cur.execute(
                    """
                    SELECT k.tanggal_kegiatan, k.kode_ar, ky.nama_ar, k.nama_kegiatan, k.nama_usaha, k.nama_pic, k.jabatan_pic,
                           k.nomor_pic
                    FROM kegiatan k
                    JOIN karyawan ky ON k.kode_ar = ky.kode_ar
                    WHERE k.tanggal_kegiatan BETWEEN %s AND %s
                    ORDER BY k.tanggal_kegiatan DESC, k.kode_ar
                    """,
                    (tanggal_mulai, tanggal_selesai),
                )
            else:
                cur.execute(
                    """
                    SELECT k.tanggal_kegiatan, k.kode_ar, ky.nama_ar, k.nama_kegiatan, k.nama_usaha, k.nama_pic, k.jabatan_pic,
                           k.nomor_pic
                    FROM kegiatan k
                    JOIN karyawan ky ON k.kode_ar = ky.kode_ar
                    ORDER BY k.tanggal_kegiatan DESC, k.kode_ar
                    """
                )
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)

async def ambil_rekap_kegiatan(tanggal_mulai=None, tanggal_selesai=None):
    return await asyncio.to_thread(_ambil_rekap_kegiatan_sync, tanggal_mulai, tanggal_selesai)

def _ambil_absensi_tanggal_sync(tanggal):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT kode_ar, nama_ar, TO_CHAR(waktu_absen, 'HH24:MI'), status_absen, lokasi_absen FROM absensi "
                "WHERE tanggal_absen = %s ORDER BY kode_ar",
                (tanggal,),
            )
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)

async def ambil_absensi_tanggal(tanggal):
    return await asyncio.to_thread(_ambil_absensi_tanggal_sync, tanggal)

def _ambil_kegiatan_tanggal_sync(tanggal):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT k.kode_ar, ky.nama_ar, k.nama_kegiatan
                FROM kegiatan k
                JOIN karyawan ky ON k.kode_ar = ky.kode_ar
                WHERE k.tanggal_kegiatan = %s
                ORDER BY k.kode_ar
                """,
                (tanggal,),
            )
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)

async def ambil_kegiatan_tanggal(tanggal):
    return await asyncio.to_thread(_ambil_kegiatan_tanggal_sync, tanggal)

def _ambil_karyawan_belum_absen_sync(tanggal):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ky.kode_ar, ky.nama_ar
                FROM karyawan ky
                LEFT JOIN absensi a ON a.kode_ar = ky.kode_ar AND a.tanggal_absen = %s
                WHERE a.kode_ar IS NULL
                ORDER BY ky.kode_ar
                """,
                (tanggal,),
            )
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)

async def ambil_karyawan_belum_absen(tanggal):
    return await asyncio.to_thread(_ambil_karyawan_belum_absen_sync, tanggal)
