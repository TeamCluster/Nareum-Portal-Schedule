"""Flask REST API for the Nareum center facility reservation system.

Serves JSON to the React (Vite) frontend. Run with `python app.py` for local
development, or point a WSGI server at `create_app()`.
"""

import io
import os
import sys
import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_cors import CORS
from sqlalchemy import text

from config import Config
from models import db, Facility, Reservation
from api.helpers import ApiError
from api.public import public_bp
from api.admin import admin_bp

# Ensure UTF-8 output on Windows consoles (Korean text in logs).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ["PYTHONIOENCODING"] = "utf-8"

load_dotenv()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)

    CORS(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}},
        supports_credentials=True,
    )

    app.register_blueprint(public_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")

    @app.errorhandler(ApiError)
    def handle_api_error(err):
        return jsonify({"error": err.message}), err.status

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": app.config["SERVICE_NAME"]})

    @app.get("/api/config")
    def public_config():
        # Business rules the frontend needs to mirror.
        return jsonify(
            {
                "service_name": app.config["SERVICE_NAME"],
                "booking_min_days": app.config["BOOKING_MIN_DAYS"],
                "booking_max_days": app.config["BOOKING_MAX_DAYS"],
            }
        )

    return app


def init_db(app):
    """Create tables, run lightweight migrations, and seed default facilities."""
    with app.app_context():
        db.create_all()

        # Idempotent column additions for pre-existing SQLite databases.
        for statement in (
            "ALTER TABLE reservations ADD COLUMN access_id VARCHAR(36);",
            "ALTER TABLE reservations ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0;",
            "ALTER TABLE reservations ADD COLUMN reject_reason VARCHAR(255);",
        ):
            try:
                db.session.execute(text(statement))
                db.session.commit()
            except Exception:
                db.session.rollback()

        # Backfill access_id for legacy rows.
        legacy = Reservation.query.filter(Reservation.access_id.is_(None)).all()
        for row in legacy:
            row.access_id = str(uuid.uuid4())
        if legacy:
            db.session.commit()

        if not Facility.query.first():
            db.session.add_all(
                [
                    Facility(name="활력충전터", type="연습실", image_url="/static/img/room001.jpg", description="밴드/음악 연습 공간"),
                    Facility(name="창의키움터", type="활동실", image_url="/static/img/room002.jpg", description="과학 활동 공간"),
                    Facility(name="탐구개발터", type="활동실", image_url="/static/img/room003.jpg", description="3D 프린터가 있는 오픈LAB실"),
                    Facility(name="상상이룸터", type="연습실", image_url="/static/img/room004.jpg", description="댄스 연습 특화 공간"),
                    Facility(name="생각나눔터", type="회의실", image_url="/static/img/room005.jpg", description="회의 공간"),
                ]
            )
            db.session.commit()


app = create_app()

if __name__ == "__main__":
    init_db(app)
    app.run(debug=True, host="127.0.0.1", port=5000)
