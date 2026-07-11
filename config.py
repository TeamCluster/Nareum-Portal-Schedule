import os


class Config:
    """Application configuration loaded from environment variables.

    Defaults keep the app runnable out-of-the-box with SQLite, while every
    value can be overridden via environment (see .env.example).
    """

    # SQLite by default; swap to Postgres/MySQL by setting DATABASE_URL.
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///resv.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    MANAGE_PASSWORD = os.environ.get("MANAGE_PASSWORD", "manage123")

    SERVICE_NAME = os.environ.get("SERVICE_NAME", "나름센터 활동실 대관")

    # Comma-separated list of origins allowed to call the API with credentials.
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.environ.get(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if origin.strip()
    ]

    # Cross-site cookies (SPA on a different origin) require SameSite=None+Secure
    # in production behind HTTPS. Defaults are dev-friendly (Lax over http).
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"

    # Booking window (days from today) — business rule shared with the frontend.
    BOOKING_MIN_DAYS = int(os.environ.get("BOOKING_MIN_DAYS", 3))
    BOOKING_MAX_DAYS = int(os.environ.get("BOOKING_MAX_DAYS", 14))
