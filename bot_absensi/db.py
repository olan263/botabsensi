"""
Semua akses database (PostgreSQL) ada di sini: connection pool + query
untuk tabel karyawan, absensi, dan kegiatan.
"""
import asyncio

import psycopg2
from psycopg2 import pool

from .config import DB_CONFIG, logger

# Connection pool kecil (1-5 koneksi) supaya tidak buka-tutup koneksi tiap query
_db_pool = psycopg2.pool.SimpleConnectionPool(1, 5, **DB_CONFIG)


def _migrasi_schema_sync():
    """Nambahin kolom yang dibutuhkan fitur baru kalau belum ada, supaya
    deployment lama tidak perlu migrasi manual. Aman dipanggil berkali-kali."""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE karyawan ADD COLUMN IF NOT EXISTS telegram_id BIGINT")
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS karyawan_telegram_id_key "
                "ON karyawan (telegram_id) WHERE telegram_id IS NOT NULL"
            )
            cur.execute("ALTER TABLE kegiatan ADD COLUMN IF NOT EXISTS nama_usaha TEXT")
        conn.commit()
        logger.info("Migrasi schema database selesai dicek/dijalankan.")
    except Exception as e:
        conn.rollback()
        logger.error(f"Gagal migrasi schema database: {e}")
    finally:
        _db_pool.putconn(conn)


def migrasi_schema():
    _migrasi_schema_sync()


# ---------- MASTER KARYAWAN ----------

def _cari_nama_karyawan_sync(kode):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT nama FROM karyawan WHERE kode = %s", (kode,))
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
            cur.execute("SELECT kode, nama FROM karyawan WHERE telegram_id = %s", (telegram_id,))
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
    """Ikat telegram_id ke kode karyawan. Return salah satu:
    "ok", "kode_tidak_ada", "kode_sudah_dipakai", "sudah_terdaftar"."""
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM karyawan WHERE kode = %s", (kode,))
            hasil = cur.fetchone()
            if hasil is None:
                return "kode_tidak_ada"
            if hasil[0] is not None and hasil[0] != telegram_id:
                return "kode_sudah_dipakai"

            cur.execute("SELECT kode FROM karyawan WHERE telegram_id = %s", (telegram_id,))
            hasil_lain = cur.fetchone()
            if hasil_lain is not None and hasil_lain[0] != kode:
                return "sudah_terdaftar"

            cur.execute("UPDATE karyawan SET telegram_id = %s WHERE kode = %s", (telegram_id, kode))
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
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO absensi (tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tanggal, kode) DO UPDATE SET
                    nama = EXCLUDED.nama,
                    tag_lokasi = EXCLUDED.tag_lokasi,
                    foto = EXCLUDED.foto,
                    rencana_kegiatan = EXCLUDED.rencana_kegiatan,
                    jam_absen = EXCLUDED.jam_absen,
                    status = EXCLUDED.status
                """,
                (tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status),
            )
        conn.commit()
    finally:
        _db_pool.putconn(conn)


async def simpan_absensi_db(tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status):
    """HANYA nulis ke database. Sync ke Google Sheets/Excel Online dilakukan
    terpisah (lihat services.py) supaya tidak memperlambat balasan ke user."""
    await asyncio.to_thread(
        _simpan_absensi_sync, tanggal, kode, nama, tag_lokasi, foto, rencana_kegiatan, jam_absen, status
    )


def _cek_sudah_absen_sync(tanggal, kode):
    conn = _db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT nama, status FROM absensi WHERE tanggal = %s AND kode = %s", (tanggal, kode))
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
                    (tanggal, kode, nama_kegiatan, nama_usaha, tag_lokasi, foto_kegiatan, hasil,
                     no_hp_pic, nama_pic, jabatan_pic, status_deal, paket)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (tanggal, kode, nama_kegiatan, nama_usaha, tag_lokasi, foto_kegiatan, hasil,
                 no_hp_pic, nama_pic, jabatan_pic, status_deal, paket),
            )
        conn.commit()
    finally:
        _db_pool.putconn(conn)


async def simpan_kegiatan_db(
    tanggal, kode, nama_kegiatan, nama_usaha, tag_lokasi, foto_kegiatan, hasil,
    no_hp_pic, nama_pic, jabatan_pic, status_deal, paket,
):
    """HANYA nulis ke database. Sync ke Google Sheets/Excel Online dilakukan
    terpisah (lihat services.py) supaya tidak memperlambat balasan ke user."""
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
                    SELECT tanggal, kode, nama, jam_absen, status, tag_lokasi
                    FROM absensi
                    WHERE tanggal BETWEEN %s AND %s
                    ORDER BY tanggal DESC, kode
                    """,
                    (tanggal_mulai, tanggal_selesai),
                )
            else:
                cur.execute(
                    """
                    SELECT tanggal, kode, nama, jam_absen, status, tag_lokasi
                    FROM absensi
                    ORDER BY tanggal DESC, kode
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
                    SELECT k.tanggal, k.kode, ky.nama, k.nama_kegiatan, k.nama_usaha, k.nama_pic, k.jabatan_pic,
                           k.no_hp_pic, k.status_deal, k.paket
                    FROM kegiatan k
                    JOIN karyawan ky ON k.kode = ky.kode
                    WHERE k.tanggal BETWEEN %s AND %s
                    ORDER BY k.tanggal DESC, k.kode
                    """,
                    (tanggal_mulai, tanggal_selesai),
                )
            else:
                cur.execute(
                    """
                    SELECT k.tanggal, k.kode, ky.nama, k.nama_kegiatan, k.nama_usaha, k.nama_pic, k.jabatan_pic,
                           k.no_hp_pic, k.status_deal, k.paket
                    FROM kegiatan k
                    JOIN karyawan ky ON k.kode = ky.kode
                    ORDER BY k.tanggal DESC, k.kode
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
                "SELECT kode, nama, jam_absen, status, tag_lokasi FROM absensi "
                "WHERE tanggal = %s ORDER BY kode",
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
                SELECT k.kode, ky.nama, k.nama_kegiatan, k.status_deal, k.paket
                FROM kegiatan k
                JOIN karyawan ky ON k.kode = ky.kode
                WHERE k.tanggal = %s
                ORDER BY k.kode
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
                SELECT ky.kode, ky.nama
                FROM karyawan ky
                LEFT JOIN absensi a ON a.kode = ky.kode AND a.tanggal = %s
                WHERE a.kode IS NULL
                ORDER BY ky.kode
                """,
                (tanggal,),
            )
            return cur.fetchall()
    finally:
        _db_pool.putconn(conn)


async def ambil_karyawan_belum_absen(tanggal):
    return await asyncio.to_thread(_ambil_karyawan_belum_absen_sync, tanggal)
