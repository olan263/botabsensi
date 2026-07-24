"""Script sekali-pakai: ganti isi fungsi rekap_absen() dan rekap_kegiatan()
di bot_absensi/handlers/umum.py jadi format snapshot harian per-karyawan,
tanpa menyentuh bagian lain di file itu.

Jalankan dari folder induk (yang ada folder bot_absensi/ di dalamnya):
    python3 patch_rekap.py
"""
import re

PATH = "bot_absensi/handlers/umum.py"

FUNGSI_REKAP_ABSEN_BARU = (
    'async def rekap_absen(update: Update, context: ContextTypes.DEFAULT_TYPE):\n'
    '    if not await _cek_akses_rekap(update):\n'
    '        await update.message.reply_text(\n'
    '            "\u274c Command ini hanya bisa dijalankan di dalam grup notifikasi resmi."\n'
    '        )\n'
    '        return\n'
    '\n'
    '    tanggal = tanggal_hari_ini()\n'
    '    try:\n'
    '        semua_karyawan = await db.ambil_semua_karyawan()\n'
    '        absensi_hari_ini = await db.ambil_absensi_tanggal(tanggal)\n'
    '    except Exception as e:\n'
    '        logger.error(f"Gagal ambil rekap absensi dari database: {e}")\n'
    '        await update.message.reply_text("\u26a0\ufe0f Gagal mengambil data rekap absensi dari database. Coba lagi nanti.")\n'
    '        return\n'
    '\n'
    '    status_by_kode = {baris[0]: baris[3] for baris in absensi_hari_ini}\n'
    '\n'
    '    def format_status(status_db):\n'
    '        if status_db is None:\n'
    '            return "BELUM"\n'
    '        if status_db in ("Sakit", "Izin"):\n'
    '            return status_db.upper()\n'
    '        return "SUDAH"\n'
    '\n'
    '    tanggal_tampil = datetime.strptime(tanggal, "%Y-%m-%d").strftime("%d/%m/%Y")\n'
    '    jam_tampil = waktu_sekarang().strftime("%H:%M")\n'
    '\n'
    '    teks = (\n'
    '        f"Rekap Absen Pagi" + chr(10) +\n'
    '        f"Tgl : {tanggal_tampil}" + chr(10) +\n'
    '        f"Jam : {jam_tampil} WIB" + chr(10) + chr(10) +\n'
    '        f"Kode / Nama / Absen Pagi" + chr(10)\n'
    '    )\n'
    '    for kode, nama in semua_karyawan:\n'
    '        teks += f"{kode} / {nama} / {format_status(status_by_kode.get(kode))}" + chr(10)\n'
    '\n'
    '    batas = 4000\n'
    '    for i in range(0, len(teks), batas):\n'
    '        await update.message.reply_text(teks[i:i + batas])\n'
)

FUNGSI_REKAP_KEGIATAN_BARU = (
    'async def rekap_kegiatan(update: Update, context: ContextTypes.DEFAULT_TYPE):\n'
    '    if not await _cek_akses_rekap(update):\n'
    '        await update.message.reply_text(\n'
    '            "\u274c Command ini hanya bisa dijalankan di dalam grup notifikasi resmi."\n'
    '        )\n'
    '        return\n'
    '\n'
    '    tanggal = tanggal_hari_ini()\n'
    '    try:\n'
    '        semua_karyawan = await db.ambil_semua_karyawan()\n'
    '        absensi_hari_ini = await db.ambil_absensi_tanggal(tanggal)\n'
    '        agregat_kegiatan = await db.ambil_agregat_kegiatan_tanggal(tanggal)\n'
    '    except Exception as e:\n'
    '        logger.error(f"Gagal ambil rekap kegiatan dari database: {e}")\n'
    '        await update.message.reply_text("\u26a0\ufe0f Gagal mengambil data rekap kegiatan dari database. Coba lagi nanti.")\n'
    '        return\n'
    '\n'
    '    status_absen_by_kode = {baris[0]: baris[3] for baris in absensi_hari_ini}\n'
    '    agregat_by_kode = {baris[0]: (baris[1], baris[2]) for baris in agregat_kegiatan}\n'
    '\n'
    '    def format_absen(status_db):\n'
    '        if status_db is None:\n'
    '            return "TIDAK MASUK"\n'
    '        if status_db in ("Sakit", "Izin"):\n'
    '            return status_db.upper()\n'
    '        return "MASUK"\n'
    '\n'
    '    tanggal_tampil = datetime.strptime(tanggal, "%Y-%m-%d").strftime("%d/%m/%Y")\n'
    '    jam_tampil = waktu_sekarang().strftime("%H:%M")\n'
    '\n'
    '    teks = (\n'
    '        f"Rekap Aktivitas" + chr(10) +\n'
    '        f"Tgl : {tanggal_tampil}" + chr(10) +\n'
    '        f"Jam : {jam_tampil} WIB" + chr(10) + chr(10) +\n'
    '        f"Kode / Nama / Absen / Jumlah Visit / Deal" + chr(10)\n'
    '    )\n'
    '    for kode, nama in semua_karyawan:\n'
    '        absen_tampil = format_absen(status_absen_by_kode.get(kode))\n'
    '        visit, deal = agregat_by_kode.get(kode, (0, 0))\n'
    '        teks += f"{kode} / {nama} / {absen_tampil} / {visit} / {deal}" + chr(10)\n'
    '\n'
    '    batas = 4000\n'
    '    for i in range(0, len(teks), batas):\n'
    '        await update.message.reply_text(teks[i:i + batas])\n'
)


def ganti_fungsi(isi, nama_fungsi, fungsi_baru):
    pola = re.compile(r"^async def " + nama_fungsi + r"\(.*?(?=^async def |\Z)", re.DOTALL | re.MULTILINE)
    if not pola.search(isi):
        print(f"WARNING: Fungsi '{nama_fungsi}' tidak ketemu, dilewati (mungkin nama beda).")
        return isi
    return pola.sub(lambda m: fungsi_baru, isi, count=1)


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        isi = f.read()

    m = re.search(r"from \.\.utils\.misc import (.+)", isi)
    if m:
        baris_import = m.group(0)
        kebutuhan = ["tanggal_hari_ini", "waktu_sekarang"]
        tambahan = [k for k in kebutuhan if k not in baris_import]
        if tambahan:
            baris_baru = baris_import.rstrip() + ", " + ", ".join(tambahan)
            isi = isi.replace(baris_import, baris_baru, 1)
            print("OK: Menambahkan import: " + ", ".join(tambahan))
    else:
        print("WARNING: Baris 'from ..utils.misc import ...' tidak ketemu - tambahkan manual:")
        print("    from ..utils.misc import tanggal_hari_ini, waktu_sekarang")

    if "from datetime import datetime" not in isi:
        isi = "from datetime import datetime\n" + isi
        print("OK: Menambahkan import datetime")

    isi = ganti_fungsi(isi, "rekap_absen", FUNGSI_REKAP_ABSEN_BARU)
    isi = ganti_fungsi(isi, "rekap_kegiatan", FUNGSI_REKAP_KEGIATAN_BARU)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(isi)

    print("SELESAI: patch bot_absensi/handlers/umum.py")


if __name__ == "__main__":
    main()