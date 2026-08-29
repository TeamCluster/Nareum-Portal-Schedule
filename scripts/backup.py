#!/usr/bin/env python3
"""SQLite DB + 업로드 이미지 백업.

  python scripts/backup.py [백업폴더]

SQLite 의 온라인 백업 API 를 쓰므로 앱이 켜져 있어도 안전하다(파일 복사는
WAL 이 섞여 깨질 수 있어 쓰지 않는다). 업로드 이미지는 tar.gz 로 묶는다.

크론 예시 — 매일 새벽 4시:
  0 4 * * * cd /srv/nareum/BackEnd && .venv/bin/python scripts/backup.py >> /var/log/nareum-backup.log 2>&1

환경변수:
  BACKUP_DIR      백업 저장 위치 (기본: <DB_FOLDER>/../backups)
  BACKUP_KEEP     보관할 백업 개수 (기본: 14)
"""
import os
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402


def backup_sqlite(src_path, dest_path):
    """온라인 백업 API 로 일관된 스냅샷을 만든다."""
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dest = sqlite3.connect(dest_path)
    try:
        with dest:
            src.backup(dest)
    finally:
        src.close()
        dest.close()


def prune(root, keep):
    """오래된 백업 폴더 정리 — 최신 keep 개만 남긴다."""
    entries = sorted(
        (e for e in os.scandir(root) if e.is_dir() and e.name[0].isdigit()),
        key=lambda e: e.name,
        reverse=True,
    )
    for stale in entries[keep:]:
        shutil.rmtree(stale.path, ignore_errors=True)
        print(f"  삭제(보관기간 초과): {stale.name}")


def main():
    default_root = os.path.join(os.path.dirname(os.path.abspath(config.DB_FOLDER)), "backups")
    root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BACKUP_DIR", default_root)
    keep = int(os.environ.get("BACKUP_KEEP", 14))

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = os.path.join(root, stamp)
    os.makedirs(out, exist_ok=True)
    print(f"[backup] {stamp} -> {out}")

    dbs = sorted(f for f in os.listdir(config.DB_FOLDER) if f.endswith(".sqlite3"))
    if not dbs:
        print("  경고: DB 파일이 없습니다. DB_FOLDER 설정을 확인하세요:", config.DB_FOLDER)
    for name in dbs:
        backup_sqlite(os.path.join(config.DB_FOLDER, name), os.path.join(out, name))
        print(f"  DB  {name}")

    # 업로드 이미지(static/<slug>/...). 공유 기본 이미지 static/img 는 저장소에 있어 제외.
    uploads = [
        d for d in sorted(os.listdir(config.STATIC_ROOT))
        if d != "img" and os.path.isdir(os.path.join(config.STATIC_ROOT, d))
    ] if os.path.isdir(config.STATIC_ROOT) else []
    if uploads:
        tar_path = os.path.join(out, "uploads.tar.gz")
        with tarfile.open(tar_path, "w:gz") as tar:
            for d in uploads:
                tar.add(os.path.join(config.STATIC_ROOT, d), arcname=d)
        size = os.path.getsize(tar_path) // 1024
        print(f"  이미지 uploads.tar.gz ({len(uploads)}개 기관, {size}KB)")

    prune(root, keep)
    print("[backup] 완료")


if __name__ == "__main__":
    main()
