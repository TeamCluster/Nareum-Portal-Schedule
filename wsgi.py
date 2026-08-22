"""운영 WSGI 진입점.

    gunicorn -c gunicorn.conf.py wsgi:app

app.py 를 직접 실행(`python app.py`)하는 것은 **개발 전용**이다. 그쪽은 Werkzeug
개발 서버 + 디버거라서 운영에 노출되면 원격 코드 실행이 된다.
"""
from app import app

__all__ = ["app"]
