"""
Script setup SEKALI JALAN untuk login ke akun Microsoft personal, supaya bot
bisa sync ke Excel Online tanpa perlu login ulang tiap kali (pakai refresh token
yang disimpan otomatis di file token cache).

Cara pakai:
    1. Pastikan .env sudah diisi MS_CLIENT_ID (dan MS_TOKEN_CACHE_FILE kalau mau ganti nama file)
    2. Jalankan: python setup_excel_online_auth.py
    3. Script akan menampilkan sebuah URL + kode singkat
    4. Buka URL itu di browser (di device manapun), masukkan kodenya, lalu login
       pakai akun Microsoft personal yang punya file Excel-nya
    5. Setelah berhasil, token cache otomatis tersimpan -> bot.py bisa langsung dipakai

Jalankan ulang script ini kalau token cache-nya rusak/hilang, atau kalau ganti akun.
"""

import os
import sys

import msal
from dotenv import load_dotenv

load_dotenv()

MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID", "")
MS_TOKEN_CACHE_FILE = os.environ.get("MS_TOKEN_CACHE_FILE", "msal_token_cache.bin")
MS_AUTHORITY = "https://login.microsoftonline.com/consumers"
MS_SCOPES = ["Files.ReadWrite"]

if not MS_CLIENT_ID:
    print("❌ MS_CLIENT_ID belum diisi di .env. Isi dulu Application (client) ID dari Azure Portal.")
    sys.exit(1)

cache = msal.SerializableTokenCache()
if os.path.exists(MS_TOKEN_CACHE_FILE):
    with open(MS_TOKEN_CACHE_FILE, "r") as f:
        cache.deserialize(f.read())

app = msal.PublicClientApplication(MS_CLIENT_ID, authority=MS_AUTHORITY, token_cache=cache)

# Cek dulu apakah sudah ada sesi login tersimpan
accounts = app.get_accounts()
if accounts:
    result = app.acquire_token_silent(MS_SCOPES, account=accounts[0])
    if result and "access_token" in result:
        print(f"✅ Sudah ada sesi login aktif untuk akun: {accounts[0].get('username')}")
        print("Tidak perlu login ulang. Kalau mau ganti akun, hapus dulu file token cache-nya:")
        print(f"  {MS_TOKEN_CACHE_FILE}")
        sys.exit(0)

# Belum ada sesi -> mulai device code flow (login interaktif)
flow = app.initiate_device_flow(scopes=MS_SCOPES)
if "user_code" not in flow:
    print("❌ Gagal memulai proses login:", flow.get("error_description", flow))
    sys.exit(1)

print(flow["message"])
print("\nMenunggu kamu login di browser...")

result = app.acquire_token_by_device_flow(flow)  # blocking, menunggu sampai login selesai

if "access_token" in result:
    with open(MS_TOKEN_CACHE_FILE, "w") as f:
        f.write(cache.serialize())
    print(f"\n✅ Login berhasil! Token tersimpan di '{MS_TOKEN_CACHE_FILE}'.")
    print("Sekarang bot.py bisa sync ke Excel Online tanpa perlu login ulang.")
else:
    print("\n❌ Login gagal:", result.get("error_description", result))
    sys.exit(1)