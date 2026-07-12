"""시설/헤더 이미지 업로드·삭제.

  시설: static/<slug>/facility/<파일명>  (교체 시 토큰명으로 새로 저장)
  헤더: static/<slug>/header.<ext>       (고정 파일명, 캐시버스트 쿼리 부여)

수정 시 기존 이미지를 삭제하고 새 이미지만 남겨 용량을 관리한다.
"""
import os
import secrets

from werkzeug.utils import secure_filename

import config
from .errors import ApiError

ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_BYTES = 5 * 1024 * 1024  # 5MB


def _facility_dir(slug):
    return os.path.join(config.STATIC_ROOT, slug, "facility")


def _url_to_path(url):
    """/static/... URL 을 실제 파일 경로로. 로컬 static 경로가 아니면 None."""
    if not url or not url.startswith("/static/"):
        return None
    rel = url[len("/static/"):].split("?", 1)[0]  # 캐시버스트 쿼리 제거
    return os.path.join(config.STATIC_ROOT, *rel.split("/"))


def delete_image_file(url):
    """업로드 이미지 파일 삭제(멱등).

    공유 기본 이미지(static/img/...)나 외부 URL 은 절대 삭제하지 않는다.
    그 외 static/<slug>/... 하위(시설·헤더 업로드)는 삭제 대상.
    """
    if not url or not url.startswith("/static/"):
        return
    if url.startswith("/static/img/"):
        return
    path = _url_to_path(url)
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _read_validated(file_storage):
    """확장자·용량 검증 후 (bytes, ext) 반환."""
    filename = secure_filename(file_storage.filename or "")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXT:
        raise ApiError("이미지 파일(png, jpg, jpeg, gif, webp)만 업로드할 수 있습니다.")
    data = file_storage.read()
    if not data:
        raise ApiError("빈 파일입니다.")
    if len(data) > MAX_BYTES:
        raise ApiError("이미지 용량은 5MB 이하여야 합니다.")
    return data, ext


def save_header_image(slug, file_storage, old_url=None):
    """헤더 로고를 static/<slug>/header.<ext> 에 저장하고 URL(캐시버스트 쿼리 포함) 반환."""
    data, ext = _read_validated(file_storage)
    folder = os.path.join(config.STATIC_ROOT, slug)
    os.makedirs(folder, exist_ok=True)
    if old_url:
        delete_image_file(old_url)  # 확장자가 바뀌어도 기존 파일 정리
    with open(os.path.join(folder, f"header.{ext}"), "wb") as f:
        f.write(data)
    return f"/static/{slug}/header.{ext}?v={secrets.token_hex(4)}"


def save_facility_image(slug, facility_id, file_storage, old_url=None):
    """업로드 파일을 저장하고 공개 URL(/static/...)을 반환. 기존 이미지는 삭제."""
    data, ext = _read_validated(file_storage)

    folder = _facility_dir(slug)
    os.makedirs(folder, exist_ok=True)

    new_name = f"{facility_id}_{secrets.token_hex(6)}.{ext}"
    with open(os.path.join(folder, new_name), "wb") as f:
        f.write(data)

    # 기존 이미지 삭제(용량 관리)
    if old_url:
        delete_image_file(old_url)

    return f"/static/{slug}/facility/{new_name}"
