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

# 확장자는 클라이언트가 마음대로 붙일 수 있으므로 실제 파일 내용(매직 바이트)으로
# 이미지 형식을 판별하고, 저장 확장자도 판별 결과를 따른다.
_SIGNATURES = (
    ("png", lambda d: d.startswith(b"\x89PNG\r\n\x1a\n")),
    ("jpg", lambda d: d.startswith(b"\xff\xd8\xff")),
    ("gif", lambda d: d.startswith((b"GIF87a", b"GIF89a"))),
    ("webp", lambda d: d.startswith(b"RIFF") and d[8:12] == b"WEBP"),
)


def _sniff_ext(data):
    """내용으로 이미지 형식을 판별해 확장자 반환. 이미지가 아니면 None."""
    for ext, matches in _SIGNATURES:
        if matches(data):
            return ext
    return None


def _facility_dir(slug):
    return os.path.join(config.STATIC_ROOT, slug, "facility")


def _url_to_path(url):
    """/static/... URL 을 실제 파일 경로로. 로컬 static 경로가 아니면 None.

    image_url 은 관리자가 자유 문자열로 넣을 수 있으므로 '..' 이 섞이면
    STATIC_ROOT 밖의 파일을 가리킬 수 있다. 정규화 후 STATIC_ROOT 안에
    있는지 반드시 확인한다.
    """
    if not url or not url.startswith("/static/"):
        return None
    rel = url[len("/static/"):].split("?", 1)[0]  # 캐시버스트 쿼리 제거
    root = os.path.realpath(config.STATIC_ROOT)
    path = os.path.realpath(os.path.join(root, *rel.split("/")))
    if path != root and not path.startswith(root + os.sep):
        return None  # STATIC_ROOT 밖 -> 취급하지 않음
    return path


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
    """확장자·용량·실제 내용 검증 후 (bytes, ext) 반환.

    반환하는 ext 는 파일명이 아니라 **내용에서 판별한** 형식이다. 확장자만
    이미지인 파일(예: 스크립트를 .png 로 올리기)을 걸러낸다.
    """
    filename = secure_filename(file_storage.filename or "")
    name_ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if name_ext not in ALLOWED_EXT:
        raise ApiError("이미지 파일(png, jpg, jpeg, gif, webp)만 업로드할 수 있습니다.")

    # MAX_BYTES 를 1바이트 넘겨 읽어 초과 여부를 판단(전체를 메모리에 올리지 않음).
    data = file_storage.read(MAX_BYTES + 1)
    if not data:
        raise ApiError("빈 파일입니다.")
    if len(data) > MAX_BYTES:
        raise ApiError("이미지 용량은 5MB 이하여야 합니다.")

    ext = _sniff_ext(data)
    if ext is None:
        raise ApiError("이미지 파일이 아니거나 지원하지 않는 형식입니다. (png, jpg, gif, webp)")
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
