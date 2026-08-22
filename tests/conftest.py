"""Pytest 공용 픽스처 (멀티테넌트).

격리: DB_FOLDER 환경변수를 임시 폴더로 지정한 뒤 app 을 임포트한다. 각 테스트
전에 임시 폴더의 SQLite 를 모두 지우고 db.init_super_db() 로 다시 부트스트랩하여
슈퍼/기본 기관(nareum, 시설 5종)을 새로 만든다.

예약 규칙은 넓은 예약창(MIN=2, MAX=60)으로 설정해 날짜 의존 테스트를 쉽게 한다.
"""
import os
import tempfile
from datetime import date, timedelta

import pytest

# --- app 임포트 전에 환경을 먼저 세팅 (config 가 import 시 읽음) ------------
# 로컬 .env 는 무시한다. 개발자 머신의 .env 에는 운영값(FORCE_HTTPS 등)이 들어
# 있을 수 있고, 그러면 테스트 결과가 머신마다 달라진다.
os.environ["LOAD_DOTENV"] = "0"

_TMP_DB = tempfile.mkdtemp(prefix="spacelog-test-")
os.environ["DB_FOLDER"] = _TMP_DB
os.environ["STATIC_ROOT"] = tempfile.mkdtemp(prefix="spacelog-static-")
os.environ["SUPER_PASSWORD"] = "super-test"
os.environ["DEFAULT_PLACE_PASSWORD"] = "place-test"
os.environ["BOOKING_MIN_DAYS"] = "2"
os.environ["BOOKING_MAX_DAYS"] = "60"
os.environ["QUIET_BOOTSTRAP"] = "1"

import config  # noqa: E402
import db as db_module  # noqa: E402
from app import app as flask_app  # noqa: E402

SLUG = config.DEFAULT_PLACE_SLUG  # "nareum"
SUPER_PW = "super-test"
PLACE_PW = "place-test"


def _wipe_and_bootstrap():
    for f in os.listdir(_TMP_DB):
        try:
            os.remove(os.path.join(_TMP_DB, f))
        except OSError:
            pass
    db_module.init_super_db()


@pytest.fixture
def app():
    _wipe_and_bootstrap()
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def super_client(app):
    c = app.test_client()
    assert c.post("/api/super/login", json={"password": SUPER_PW}).status_code == 200
    return c


@pytest.fixture
def admin_client(app):
    """기본 기관(nareum) 관리자로 로그인된 클라이언트."""
    c = app.test_client()
    assert c.post(f"/api/{SLUG}/admin/login", json={"password": PLACE_PW}).status_code == 200
    return c


# ── 날짜 헬퍼 (예약창 내부) ────────────────────────────────────────────────
def valid_date():
    return (date.today() + timedelta(days=3)).isoformat()


def too_soon_date():
    return (date.today() + timedelta(days=1)).isoformat()


def too_far_date():
    return (date.today() + timedelta(days=61)).isoformat()


def week_days():
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
