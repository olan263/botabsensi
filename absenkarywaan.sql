-- ==========================================================
-- SCRIPT SQL LENGKAP: Database Bot Absensi & Kegiatan
-- Urutan: 1) Master Karyawan  2) Riwayat Absensi  3) Riwayat Kegiatan
-- ==========================================================

-- ==========================================================
-- 1. TABEL MASTER KARYAWAN
-- ==========================================================
CREATE TABLE IF NOT EXISTS karyawan (
    kode  VARCHAR(20) PRIMARY KEY,
    nama  VARCHAR(100) NOT NULL
);

INSERT INTO karyawan (kode, nama) VALUES
    ('MN01258', 'Tri Indrawati'),
    ('MN02155', 'Anton Wisnu'),
    ('MN02262', 'Apriansyah'),
    ('MN02304', 'Kanda Rohman'),
    ('MN02350', 'Sulastri'),
    ('MN02404', 'Fitria Wati'),
    ('MN02466', 'Solihin Santoso'),
    ('MN02706', 'Elis Amalia')
ON CONFLICT (kode) DO UPDATE SET nama = EXCLUDED.nama;


-- ==========================================================
-- 2. TABEL RIWAYAT ABSENSI
-- ==========================================================
-- Kombinasi (tanggal, kode) dibuat UNIK supaya 1 karyawan hanya
-- punya 1 baris absen per hari. Kalau absen ulang di hari yang
-- sama, datanya akan di-UPDATE (sesuai ON CONFLICT di kode bot).
CREATE TABLE IF NOT EXISTS absensi (
    id                SERIAL PRIMARY KEY,
    tanggal           DATE NOT NULL,
    kode              VARCHAR(20) NOT NULL REFERENCES karyawan(kode),
    nama              VARCHAR(100) NOT NULL,
    tag_lokasi        VARCHAR(150),
    foto              TEXT,                 -- path/nama file foto bukti absen
    rencana_kegiatan  TEXT,
    jam_absen         VARCHAR(5),           -- format "HH:MM"
    status            VARCHAR(50),          -- "Tepat Waktu" / "Telat (...)"
    dibuat_pada       TIMESTAMP NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_absensi_tanggal_kode UNIQUE (tanggal, kode)
);

CREATE INDEX IF NOT EXISTS idx_absensi_tanggal ON absensi (tanggal DESC);
CREATE INDEX IF NOT EXISTS idx_absensi_kode ON absensi (kode);


-- ==========================================================
-- 3. TABEL RIWAYAT KEGIATAN
-- ==========================================================
-- Tidak ada UNIQUE (tanggal, kode) di sini karena 1 karyawan bisa
-- input BEBERAPA laporan kegiatan dalam 1 hari yang sama.
CREATE TABLE IF NOT EXISTS kegiatan (
    id              SERIAL PRIMARY KEY,
    tanggal         DATE NOT NULL,
    kode            VARCHAR(20) NOT NULL REFERENCES karyawan(kode),
    nama_kegiatan   VARCHAR(150) NOT NULL,
    tag_lokasi      VARCHAR(150),
    foto_kegiatan   TEXT,                 -- path/nama file foto bukti kegiatan
    hasil           TEXT,
    no_hp_pic       VARCHAR(20),
    nama_pic        VARCHAR(100),
    dibuat_pada     TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kegiatan_tanggal ON kegiatan (tanggal DESC);
CREATE INDEX IF NOT EXISTS idx_kegiatan_kode ON kegiatan (kode);


-- ==========================================================
-- 4. CEK HASIL
-- ==========================================================
SELECT * FROM karyawan ORDER BY kode;
SELECT * FROM absensi ORDER BY tanggal DESC, kode;
SELECT * FROM kegiatan ORDER BY tanggal DESC, kode;