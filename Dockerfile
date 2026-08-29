# 나름 대관 백엔드 — 컨테이너 이미지 (시놀로지 Container Manager 용).
#
# 운영 진입점은 gunicorn(wsgi:app) 이다. `python app.py` 는 개발 전용이며
# 운영 설정이 켜져 있으면 스스로 중단하므로 여기서는 쓰지 않는다.

FROM python:3.12-slim

# PYTHONUNBUFFERED: print 를 버퍼링하지 않아야 부팅 로그(초기 비밀번호,
#   [storage] 경로)가 Container Manager 로그에 바로 보인다.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

WORKDIR /app

# ⚠️ tzdata 는 반드시 필요하다. 이 앱은 datetime.now() 로 예약 날짜와 로그인
#    잠금 시각을 계산하는데, slim 이미지에는 시간대 DB 가 없어서 TZ 를 줘도
#    UTC 로 동작한다. 그러면 예약 날짜가 9시간 어긋난다.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

# 의존성을 먼저 설치해 코드만 바뀐 재빌드에서 이 레이어가 캐시되게 한다.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 기본 시설 이미지(static/img/*.jpg) 원본 보관.
#   compose 가 /app/static 에 NAS 폴더를 마운트하면 이미지 안의 static/img 가
#   통째로 가려진다. 첫 배포에는 그 폴더가 비어 있으므로 기본 시설 사진이
#   전부 404 가 된다. 엔트리포인트가 이 원본에서 한 번 채워 넣는다.
RUN cp -r /app/static /app/static-seed \
 && chmod +x /app/docker-entrypoint.sh /app/scripts/backup.py

EXPOSE 5025

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
