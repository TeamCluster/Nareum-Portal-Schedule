"""로그인 시도 제한 — 무차별 대입 방어.

슈퍼/기관 관리자 로그인은 로그인 없이 두드릴 수 있는 유일한 문이고, 슈퍼를
뚫리면 전 기관이 넘어간다. 시도 횟수를 (범위 + 클라이언트 IP) 별로 세어
임계치를 넘으면 일정 시간 잠근다.

저장 위치는 super.sqlite3 의 login_attempts — gunicorn 워커가 여러 개여도
카운터가 공유되도록 프로세스 메모리가 아닌 DB 에 둔다.

주의: 프록시(nginx/Cloudflare) 뒤에서는 config.TRUSTED_PROXY_HOPS 를 반드시
1 이상으로 두어야 remote_addr 이 실제 클라이언트 IP 가 된다. 0 이면 모든
요청이 프록시 IP 하나로 집계되어 전체가 함께 잠긴다.
"""
from datetime import datetime, timedelta

import config
from db import get_super_db
from .errors import ApiError

_TS = "%Y-%m-%d %H:%M:%S"


def _now():
    return datetime.now()


def _parse(ts):
    return datetime.strptime(ts, _TS) if ts else None


def client_key(scope: str, remote_addr) -> str:
    """시도 카운터의 키. 같은 IP 라도 슈퍼/기관별로 따로 센다."""
    return f"{scope}|{remote_addr or 'unknown'}"


def guard(key: str) -> None:
    """잠겨 있으면 429 로 거절. 로그인 검증 **전에** 호출한다."""
    if config.LOGIN_MAX_ATTEMPTS <= 0:
        return
    row = get_super_db().execute(
        "SELECT locked_until FROM login_attempts WHERE key = ?", (key,)
    ).fetchone()
    if not row or not row["locked_until"]:
        return
    until = _parse(row["locked_until"])
    if until and until > _now():
        minutes = max(1, int((until - _now()).total_seconds() // 60) + 1)
        raise ApiError(
            f"로그인 시도가 너무 많습니다. 약 {minutes}분 후에 다시 시도해주세요.", 429
        )


def record_failure(key: str) -> None:
    """실패 1회 기록. 관측 창 안에서 임계치를 넘기면 잠근다."""
    if config.LOGIN_MAX_ATTEMPTS <= 0:
        return
    db = get_super_db()
    now = _now()
    row = db.execute(
        "SELECT fail_count, first_fail FROM login_attempts WHERE key = ?", (key,)
    ).fetchone()

    first = _parse(row["first_fail"]) if row else None
    within_window = bool(
        first and (now - first) <= timedelta(seconds=config.LOGIN_WINDOW_SECONDS)
    )
    count = (row["fail_count"] + 1) if (row and within_window) else 1
    first_fail = (first if within_window else now).strftime(_TS)

    locked_until = None
    if count >= config.LOGIN_MAX_ATTEMPTS:
        locked_until = (
            now + timedelta(seconds=config.LOGIN_LOCKOUT_SECONDS)
        ).strftime(_TS)

    db.execute(
        "INSERT INTO login_attempts (key, fail_count, first_fail, locked_until)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET fail_count = excluded.fail_count,"
        " first_fail = excluded.first_fail, locked_until = excluded.locked_until",
        (key, count, first_fail, locked_until),
    )
    db.commit()


def record_success(key: str) -> None:
    """로그인 성공 시 카운터 제거. 만료된 다른 기록도 함께 정리한다."""
    db = get_super_db()
    db.execute("DELETE FROM login_attempts WHERE key = ?", (key,))
    cutoff = (
        _now() - timedelta(seconds=max(config.LOGIN_WINDOW_SECONDS,
                                       config.LOGIN_LOCKOUT_SECONDS))
    ).strftime(_TS)
    db.execute(
        "DELETE FROM login_attempts WHERE first_fail < ?"
        " AND (locked_until IS NULL OR locked_until < ?)",
        (cutoff, _now().strftime(_TS)),
    )
    db.commit()
