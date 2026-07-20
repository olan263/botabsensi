import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', '5432')),
    'dbname': os.environ.get('DB_NAME', 'absensi_karyawan'),
    'user': os.environ.get('DB_USER', 'postgres'),
    'password': os.environ.get('DB_PASSWORD')
}

def main():
    if not DB_CONFIG['password']:
        print("Error: Password DB belum diset di .env")
        return

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        # Cek apakah kolom nama_usaha sudah ada
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='kegiatan' and column_name='nama_usaha';
        """)
        
        if not cur.fetchone():
            print("Menambahkan kolom 'nama_usaha' ke tabel 'kegiatan'...")
            cur.execute("ALTER TABLE kegiatan ADD COLUMN nama_usaha VARCHAR(255);")
            conn.commit()
            print("Kolom berhasil ditambahkan!")
        else:
            print("Kolom 'nama_usaha' sudah ada di tabel 'kegiatan'.")
            
    except Exception as e:
        print("Terjadi kesalahan:", e)
    finally:
        if 'conn' in locals() and conn:
            cur.close()
            conn.close()

if __name__ == "__main__":
    main()
