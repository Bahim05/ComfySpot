"""Pengaturan dibaca dari file .env (lihat .env.example). Isi .env tidak boleh masuk Git."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv tidak wajib; variabel lingkungan biasa tetap terbaca
    pass


def _flag(value):
    return str(value).strip().lower() in ("1", "true", "yes", "ya")


BROKER_HOST = os.getenv("BROKER_HOST", "broker.hivemq.com")
BROKER_PORT = int(os.getenv("BROKER_PORT", "8883"))
BROKER_TLS = _flag(os.getenv("BROKER_TLS", "true"))
BROKER_USERNAME = os.getenv("BROKER_USERNAME") or None
BROKER_PASSWORD = os.getenv("BROKER_PASSWORD") or None
PROJECT_ID = os.getenv("PROJECT_ID", "").strip()


def require_project_id():
    """PROJECT_ID wajib unik per kelompok karena broker publik dipakai bersama."""
    if not PROJECT_ID or PROJECT_ID.startswith("isi_"):
        raise SystemExit("PROJECT_ID belum diisi. Salin .env.example menjadi .env lalu isi PROJECT_ID yang unik.")
    return PROJECT_ID
