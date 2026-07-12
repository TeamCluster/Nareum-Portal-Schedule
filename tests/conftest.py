"""Shared pytest fixtures.

Uses an in-memory SQLite database (StaticPool so every connection shares the
same in-memory DB) and a widened booking window so date-based rules are easy to
exercise without depending on the current calendar date.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy.pool import StaticPool

from app import create_app
from config import Config
from models import db, Facility


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    MANAGE_PASSWORD = "test-pass"
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # in-memory
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    # Wide window so "next Monday" style dates always fall inside it.
    BOOKING_MIN_DAYS = 2
    BOOKING_MAX_DAYS = 60


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        db.session.add_all(
            [
                Facility(id=1, name="연습터", type="연습실", description="연습 공간"),
                Facility(id=2, name="활동터", type="활동실", description="활동 공간"),
            ]
        )
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_client(app):
    """A test client with an authenticated admin session."""
    c = app.test_client()
    res = c.post("/api/admin/login", json={"password": "test-pass"})
    assert res.status_code == 200
    return c


# ── Date helpers (relative to today, inside the test booking window) ──────────

def valid_date():
    """A date safely inside the booking window."""
    return (date.today() + timedelta(days=3)).isoformat()


def too_soon_date():
    return (date.today() + timedelta(days=1)).isoformat()


def too_far_date():
    return (date.today() + timedelta(days=61)).isoformat()


def week_days():
    """Three distinct dates (Mon/Tue/Wed) within one Mon–Sun week, inside the window."""
    base = date.today() + timedelta(days=2)
    monday = base + timedelta(days=(7 - base.weekday()) % 7)
    return [
        monday.isoformat(),
        (monday + timedelta(days=1)).isoformat(),
        (monday + timedelta(days=2)).isoformat(),
    ]


def reservation_payload(**overrides):
    payload = {
        "facility_id": 1,
        "date": valid_date(),
        "hours": [10, 11],
        "name": "홍길동",
        "contact": "010-1234-5678",
        "school": "나름중",
        "club": "밴드부",
        "participants": {"middle": 3},
        "equipment": [],
    }
    payload.update(overrides)
    return payload
