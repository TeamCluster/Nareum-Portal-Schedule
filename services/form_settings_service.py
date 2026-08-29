"""신청서 설정 — 필요 물품 목록 / 공지 / 대관규정 (기관 DB 의 place_settings).

종이 「시설대관이용신청서」의 '필요 물품' 항목과 앞·뒷면 안내문은 기관마다
다르므로 코드 상수가 아니라 기관 DB 에 JSON 으로 저장하고 기관 관리자가
수정한다. 기본값은 db._seed_form_settings() 가 config 의 DEFAULT_* 로 시딩.

저장 형태 (place_settings):
  equipment_catalog  [{title, facility_types[], allow_other, items:[{name, qty}]}]
  form_notice        ["공지 문구", ...]
  form_rules         ["대관규정 문구", ...]
"""
import json

import config
from .errors import ApiError

# 설정 키 ↔ 기본값. 값이 없거나 깨졌으면 기본값으로 폴백한다.
_DEFAULTS = {
    "equipment_catalog": config.DEFAULT_EQUIPMENT_CATALOG,
    "form_notice": config.DEFAULT_FORM_NOTICE,
    "form_rules": config.DEFAULT_FORM_RULES,
}

MAX_GROUPS = 10
MAX_ITEMS_PER_GROUP = 60
MAX_LINES = 100
MAX_TEXT = 300


def _read(conn, key):
    row = conn.execute(
        "SELECT value FROM place_settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return json.loads(json.dumps(_DEFAULTS[key]))  # 깊은 복사
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return json.loads(json.dumps(_DEFAULTS[key]))


def _write(conn, key, value):
    conn.execute(
        "INSERT INTO place_settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )


# ----------------------------------------------------------------------
# 정규화 (관리자 입력 → 저장 형태)
# ----------------------------------------------------------------------
def _clean_text(value, field):
    text = (value if isinstance(value, str) else "").strip()
    if len(text) > MAX_TEXT:
        raise ApiError(f"{field} 는 {MAX_TEXT}자를 넘을 수 없습니다.")
    return text


def normalize_catalog(raw):
    """관리자가 보낸 물품 목록을 검증/정규화. 빈 이름·중복은 제거."""
    if not isinstance(raw, list):
        raise ApiError("필요 물품 목록 형식이 올바르지 않습니다.")
    if len(raw) > MAX_GROUPS:
        raise ApiError(f"물품 분류는 최대 {MAX_GROUPS}개까지 등록할 수 있습니다.")

    groups = []
    for group in raw:
        if not isinstance(group, dict):
            raise ApiError("필요 물품 목록 형식이 올바르지 않습니다.")
        title = _clean_text(group.get("title"), "물품 분류명")
        if not title:
            continue

        types = group.get("facility_types") or []
        if not isinstance(types, list):
            raise ApiError("적용 시설 유형 형식이 올바르지 않습니다.")
        types = [t for t in (_clean_text(t, "시설 유형") for t in types) if t]

        raw_items = group.get("items") or []
        if not isinstance(raw_items, list):
            raise ApiError("물품 항목 형식이 올바르지 않습니다.")
        if len(raw_items) > MAX_ITEMS_PER_GROUP:
            raise ApiError(f"한 분류에는 물품을 최대 {MAX_ITEMS_PER_GROUP}개까지 등록할 수 있습니다.")

        items, seen = [], set()
        for item in raw_items:
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict):
                raise ApiError("물품 항목 형식이 올바르지 않습니다.")
            name = _clean_text(item.get("name"), "물품명")
            if not name or name in seen:
                continue
            seen.add(name)
            items.append({"name": name, "qty": bool(item.get("qty"))})

        if items:
            groups.append({
                "title": title,
                "facility_types": types,
                "allow_other": bool(group.get("allow_other", True)),
                "items": items,
            })
    return groups


def normalize_lines(raw, field):
    """공지/규정 문구 목록을 정규화. 문자열이면 줄 단위로 분해."""
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list):
        raise ApiError(f"{field} 형식이 올바르지 않습니다.")
    lines = [t for t in (_clean_text(x, field) for x in raw) if t]
    if len(lines) > MAX_LINES:
        raise ApiError(f"{field} 는 최대 {MAX_LINES}줄까지 등록할 수 있습니다.")
    return lines


# ----------------------------------------------------------------------
# 조회 / 저장
# ----------------------------------------------------------------------
def get_form_config(conn, facility_type=None):
    """신청 화면이 쓰는 설정 묶음.

    facility_type 을 주면 해당 유형에 적용되는 물품 분류만 남긴다
    (분류의 facility_types 가 비어 있으면 모든 시설에 적용).
    """
    catalog = _read(conn, "equipment_catalog")
    if facility_type:
        catalog = [
            g for g in catalog
            if not g.get("facility_types") or facility_type in g["facility_types"]
        ]
    return {
        "equipment_catalog": catalog,
        "notice": _read(conn, "form_notice"),
        "rules": _read(conn, "form_rules"),
    }


def update_form_config(conn, data):
    """관리자 저장. 보낸 키만 갱신한다(부분 수정 허용)."""
    data = data or {}
    if "equipment_catalog" in data:
        _write(conn, "equipment_catalog", normalize_catalog(data["equipment_catalog"]))
    if "notice" in data:
        _write(conn, "form_notice", normalize_lines(data["notice"], "공지 및 준수사항"))
    if "rules" in data:
        _write(conn, "form_rules", normalize_lines(data["rules"], "대관 규정 및 유의사항"))
    conn.commit()
    return get_form_config(conn)
