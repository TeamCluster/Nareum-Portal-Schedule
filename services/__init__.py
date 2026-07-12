"""도메인 서비스 계층.

각 서비스는 raw sqlite3 커넥션(db.get_super_db / db.get_place_db)을 받아
비즈니스 로직을 수행하고 (성공여부, 메시지[, 결과]) 또는 dict/list 를 반환.
라우트(app.py)는 얇게 유지하고 검증/규칙은 여기에 모읍니다.
"""
