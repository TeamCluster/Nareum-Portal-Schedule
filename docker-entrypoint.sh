#!/bin/sh
# 컨테이너 시작 훅 — 마운트된 폴더를 앱이 기대하는 상태로 맞춘 뒤 CMD 를 실행.
set -e

# /app/static 에 NAS 폴더가 마운트되면 이미지에 들어 있던 기본 시설 이미지가
# 가려진다. 마운트된 쪽에 img 폴더가 아예 없을 때만(= 첫 배포) 채워 넣는다.
# 폴더 단위로 판단하므로, 관리자가 나중에 지운 파일을 재시작마다 되살리지 않는다.
if [ -d /app/static-seed/img ] && [ ! -d /app/static/img ]; then
    echo "[entrypoint] 기본 시설 이미지를 static/img 에 복사합니다."
    mkdir -p /app/static
    cp -r /app/static-seed/img /app/static/img
fi

exec "$@"
