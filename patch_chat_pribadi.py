"""Script sekali-pakai: tambahin pengecekan supaya /absen, /kegiatan, /daftar
CUMA bisa dipakai di chat pribadi dengan bot, ditolak kalau dipanggil di grup.

Jalankan dari folder induk (yang ada folder bot_absensi/ di dalamnya):
    python3 patch_chat_pribadi.py
"""
import re

PATH_UMUM = "bot_absensi/handlers/umum.py"
PATH_ABSEN = "bot_absensi/handlers/absen.py"
PATH_KEGIATAN = "bot_absensi/handlers/kegiatan.py"
PATH_REGISTRASI = "bot_absensi/handlers/registrasi.py"

FUNGSI_CEK_CHAT_PRIBADI = (
    "\n\n"
    "async def pastikan_chat_pribadi(update: Update):\n"
    "    \"\"\"Return True kalau chat ini private (DM ke bot). Kalau dipanggil di\n"
    "    grup, otomatis balas penolakan dan return False.\"\"\"\n"
    "    if update.effective_chat.type != \"private\":\n"
    "        await update.message.reply_text(\n"
    "            \"\\u274c Command ini cuma bisa dipakai lewat chat pribadi dengan bot, bukan di grup.\\n\"\n"
    "            \"Silakan klik nama bot ini, buka chat pribadi (DM), baru jalankan lagi.\"\n"
    "        )\n"
    "        return False\n"
    "    return True\n"
)


def tambah_fungsi_ke_umum():
    with open(PATH_UMUM, "r", encoding="utf-8") as f:
        isi = f.read()
    if "def pastikan_chat_pribadi" in isi:
        print("SKIP: pastikan_chat_pribadi sudah ada di umum.py")
        return
    isi = isi.rstrip("\n") + FUNGSI_CEK_CHAT_PRIBADI
    with open(PATH_UMUM, "w", encoding="utf-8") as f:
        f.write(isi)
    print("OK: fungsi pastikan_chat_pribadi ditambahkan ke umum.py")


def tambah_import(path, nama_fungsi_baru):
    with open(path, "r", encoding="utf-8") as f:
        isi = f.read()
    if nama_fungsi_baru in isi.split("\n")[0:40].__str__() and "from .umum import" in isi:
        pass
    m = re.search(r"from \.umum import (.+)", isi)
    if m:
        baris_lama = m.group(0)
        if nama_fungsi_baru in baris_lama:
            print(f"SKIP: import {nama_fungsi_baru} sudah ada di {path}")
            return isi
        baris_baru = baris_lama.rstrip() + ", " + nama_fungsi_baru
        isi = isi.replace(baris_lama, baris_baru, 1)
        print(f"OK: menambahkan import {nama_fungsi_baru} di {path}")
    else:
        # belum ada baris import dari .umum sama sekali -> tambahkan baris baru
        # taruh setelah baris import terakhir yang mulai dengan "from ."
        baris_list = isi.split("\n")
        idx_terakhir = 0
        for i, baris in enumerate(baris_list):
            if baris.startswith("from .") or baris.startswith("from .."):
                idx_terakhir = i
        baris_list.insert(idx_terakhir + 1, f"from .umum import {nama_fungsi_baru}")
        isi = "\n".join(baris_list)
        print(f"OK: menambahkan baris baru 'from .umum import {nama_fungsi_baru}' di {path}")
    return isi


def sisip_pengecekan(isi, anchor, path):
    """Sisipkan pengecekan chat pribadi tepat SEBELUM baris anchor.
    Anchor TIDAK termasuk whitespace di depannya (indentasi asli tetap dipakai
    apa adanya sebagai indentasi baris pertama pengecekan)."""
    if "pastikan_chat_pribadi(update)" in isi:
        print(f"SKIP: pengecekan chat pribadi sudah ada di {path}")
        return isi
    if anchor not in isi:
        print(f"WARNING: anchor tidak ketemu di {path}, dilewati: {anchor!r}")
        return isi
    pengecekan = (
        "if not await pastikan_chat_pribadi(update):\n"
        "        return ConversationHandler.END\n"
        "\n"
        "    "
    )
    isi = isi.replace(anchor, pengecekan + anchor, 1)
    print(f"OK: pengecekan chat pribadi disisipkan di {path}")
    return isi


def patch_absen():
    with open(PATH_ABSEN, "r", encoding="utf-8") as f:
        isi = f.read()
    isi = tambah_import(PATH_ABSEN, "pastikan_chat_pribadi") if False else isi
    # tambah import manual karena tambah_import butuh re-read file; lakukan langsung
    m = re.search(r"from \.umum import (.+)", isi)
    if m and "pastikan_chat_pribadi" not in m.group(0):
        isi = isi.replace(m.group(0), m.group(0).rstrip() + ", pastikan_chat_pribadi", 1)
        print("OK: import pastikan_chat_pribadi ditambahkan di absen.py")
    elif not m:
        print("WARNING: baris 'from .umum import ...' tidak ketemu di absen.py")

    isi = sisip_pengecekan(isi, "hasil = await pastikan_terdaftar(update, context)", PATH_ABSEN)

    with open(PATH_ABSEN, "w", encoding="utf-8") as f:
        f.write(isi)


def patch_kegiatan():
    with open(PATH_KEGIATAN, "r", encoding="utf-8") as f:
        isi = f.read()
    m = re.search(r"from \.umum import (.+)", isi)
    if m and "pastikan_chat_pribadi" not in m.group(0):
        isi = isi.replace(m.group(0), m.group(0).rstrip() + ", pastikan_chat_pribadi", 1)
        print("OK: import pastikan_chat_pribadi ditambahkan di kegiatan.py")
    elif not m:
        print("WARNING: baris 'from .umum import ...' tidak ketemu di kegiatan.py")

    isi = sisip_pengecekan(isi, "hasil_daftar = await pastikan_terdaftar(update, context)", PATH_KEGIATAN)

    with open(PATH_KEGIATAN, "w", encoding="utf-8") as f:
        f.write(isi)


def patch_registrasi():
    with open(PATH_REGISTRASI, "r", encoding="utf-8") as f:
        isi = f.read()

    if "from .umum import" not in isi:
        baris_list = isi.split("\n")
        idx_terakhir = 0
        for i, baris in enumerate(baris_list):
            if baris.startswith("from .") or baris.startswith("from .."):
                idx_terakhir = i
        baris_list.insert(idx_terakhir + 1, "from .umum import pastikan_chat_pribadi")
        isi = "\n".join(baris_list)
        print("OK: menambahkan 'from .umum import pastikan_chat_pribadi' di registrasi.py")
    else:
        m = re.search(r"from \.umum import (.+)", isi)
        if m and "pastikan_chat_pribadi" not in m.group(0):
            isi = isi.replace(m.group(0), m.group(0).rstrip() + ", pastikan_chat_pribadi", 1)
            print("OK: import pastikan_chat_pribadi ditambahkan di registrasi.py")

    anchor = "telegram_id = update.effective_user.id\n    sudah = await db.cari_kode_by_telegram_id(telegram_id)"
    if "pastikan_chat_pribadi(update)" in isi:
        print("SKIP: pengecekan chat pribadi sudah ada di registrasi.py")
    elif anchor in isi:
        pengecekan = (
            "if not await pastikan_chat_pribadi(update):\n"
            "        return ConversationHandler.END\n"
            "\n"
            "    "
        )
        isi = isi.replace(anchor, pengecekan + anchor, 1)
        print("OK: pengecekan chat pribadi disisipkan di registrasi.py")
    else:
        print("WARNING: anchor tidak ketemu di registrasi.py, sisip manual dibutuhkan.")

    with open(PATH_REGISTRASI, "w", encoding="utf-8") as f:
        f.write(isi)


def main():
    tambah_fungsi_ke_umum()
    patch_absen()
    patch_kegiatan()
    patch_registrasi()
    print("SELESAI semua patch.")


if __name__ == "__main__":
    main()
