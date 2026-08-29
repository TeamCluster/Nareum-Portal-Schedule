"""gunicorn 설정 — `gunicorn -c gunicorn.conf.py wsgi:app`.

워커 수에 대해:
  데이터 저장소가 SQLite(파일 하나)라 쓰기는 어차피 직렬화된다. 워커를 CPU 수만큼
  늘려도 쓰기 처리량은 늘지 않고 잠금 경합만 커진다. 읽기 동시성은 WAL + 스레드로
  충분하므로 프로세스는 적게, 스레드를 넉넉히 두는 편이 이 앱에 맞는다.
  (db._connect 가 WAL 과 busy_timeout 을 켠다.)

  ⚠️ 이 앱은 단일 인스턴스 전용이다. SQLite 파일과 업로드 이미지를 로컬 디스크에
  두므로 여러 대로 수평 확장할 수 없다. 확장이 필요해지면 Postgres + 오브젝트
  스토리지로 옮겨야 한다.
"""
import os

bind = os.environ.get("BIND", "127.0.0.1:8000")
workers = int(os.environ.get("WEB_WORKERS", 2))
worker_class = "gthread"
threads = int(os.environ.get("WEB_THREADS", 4))

timeout = 60
graceful_timeout = 30
keepalive = 5

# 앱 임포트를 fork 전에 1회만 — 부팅(스키마 생성/시딩)이 워커마다 중복되지 않는다.
preload_app = True

accesslog = os.environ.get("ACCESS_LOG", "-")   # '-' = stdout
errorlog = os.environ.get("ERROR_LOG", "-")
loglevel = os.environ.get("LOG_LEVEL", "info")
# 프록시 뒤에서 실제 클라이언트 IP 를 로그에 남긴다(집계·차단 추적용).
access_log_format = '%({X-Forwarded-For}i)s %(h)s "%(r)s" %(s)s %(b)s %(D)sus'
