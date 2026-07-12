"""시설 이미지 업로드/삭제 — static/<slug>/facility/<파일명>.

수정 시 기존 이미지를 삭제하고 새 이미지만 남겨 용량을 관리한다.
저장 파일명은 예측 불가한 토큰을 붙여 캐시/충돌 문제를 피한다.
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
    rel = url[len("/static/"):]
    return os.path.join(config.STATIC_ROOT, *rel.split("/"))


def delete_image_file(url):
    """업로드된 시설 이미지(static/<slug>/facility/...)만 삭제(멱등).

    공유 기본 이미지(static/img/...)나 외부 URL 은 절대 삭제하지 않는다.
    """
    if not url or "/facility/" not in url:
        return
    path = _url_to_path(url)
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def save_facility_image(slug, facility_id, file_storage, old_url=None):
    """업로드 파일을 저장하고 공개 URL(/static/...)을 반환. 기존 이미지는 삭제."""
    filename = secure_filename(file_storage.filename or "")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXT:
        raise ApiError("이미지 파일(png, jpg, jpeg, gif, webp)만 업로드할 수 있습니다.")

    data = file_storage.read()
    if not data:
        raise ApiError("빈 파일입니다.")
    if len(data) > MAX_BYTES:
        raise ApiError("이미지 용량은 5MB 이하여야 합니다.")

    folder = _facility_dir(slug)
    os.makedirs(folder, exist_ok=True)

    new_name = f"{facility_id}_{secrets.token_hex(6)}.{ext}"
    with open(os.path.join(folder, new_name), "wb") as f:
        f.write(data)

    # 기존 이미지 삭제(용량 관리)
    if old_url:
        delete_image_file(old_url)

    return f"/static/{slug}/facility/{new_name}"
