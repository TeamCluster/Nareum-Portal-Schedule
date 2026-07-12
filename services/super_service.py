"""슈퍼 관리자 본인 관리 — 비밀번호 검증/변경.

DB:  db/super.sqlite3 의 super_admin 테이블 (단일 row, id = 1)
해시: werkzeug.security 의 pbkdf2 기반.

초기 비밀번호는 db.init_super_db() 가 첫 실행 시 자동 생성해 콘솔에 1회 출력.
"""
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from db import get_super_db

MIN_PASSWORD_LENGTH = 6


def verify_super_password(password: str) -> bool:
    """슈퍼 관리자 비밀번호가 맞는지 검증. 로그인 라우트에서 호출."""
    if not isinstance(password, str) or not password:
        return False
    row = get_super_db().execute(
        "SELECT password_hash FROM super_admin WHERE id = 1"
    ).fetchone()
    if not row:
        return False
    return check_password_hash(row["password_hash"], password)


def update_super_password(new_password: str):
    """슈퍼 관리자 비밀번호 변경. (성공여부, 메시지) 반환."""
    if not isinstance(new_password, str):
        return False, "비밀번호 형식이 올바르지 않습니다."
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return False, f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다."

    db = get_super_db()
    db.execute(
        "UPDATE super_admin SET password_hash = ?, updated_at = ? WHERE id = 1",
        (generate_password_hash(new_password),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    db.commit()
    return True, "슈퍼 관리자 비밀번호가 변경되었습니다."
