"""동아리 목록 조회 — 외부 ClubLog API 프록시.

관리자가 동아리 단기대관을 직접 추가할 때 동아리명을 매번 타이핑하지 않도록
같은 slug 의 동아리 목록을 가져온다. 프론트에서 외부 도메인을 직접 부르면
CORS 에 막히고 인증 경계도 흐려지므로 백엔드가 대신 호출한다.

외부 서비스이므로 **실패해도 화면이 죽지 않는 것**이 원칙이다. 조회에 실패하면
available=False 와 사유를 담아 200 으로 돌려주고, 관리자는 직접 입력으로 진행한다.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config

# slug -> (만료시각, payload). 동아리 목록은 자주 바뀌지 않아 짧게 캐싱한다.
_cache = {}


# 기본 urllib User-Agent 는 일부 WAF 가 차단하므로 서비스명을 밝혀 보낸다.
_USER_AGENT = "SpaceLog-Reservation/1.0 (+clublog-proxy)"


def _request(url):
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=config.CLUBLOG_TIMEOUT) as res:
        raw = res.read(config.CLUBLOG_MAX_BYTES + 1)
    if len(raw) > config.CLUBLOG_MAX_BYTES:
        raise ValueError("응답이 너무 큽니다.")
    return json.loads(raw.decode("utf-8"))


def _normalize(payload):
    """ClubLog 응답 → [{name, category}] (이름순). clubs / club_dict 둘 다 지원."""
    items = []
    if isinstance(payload, dict):
        raw = payload.get("clubs")
        if isinstance(raw, list):
            for c in raw:
                if isinstance(c, dict):
                    items.append((c.get("name"), c.get("category")))
                elif isinstance(c, str):
                    items.append((c, ""))
        elif isinstance(payload.get("club_dict"), dict):
            items = list(payload["club_dict"].items())
    elif isinstance(payload, list):
        items = [(c.get("name"), c.get("category")) if isinstance(c, dict) else (c, "")
                 for c in payload]

    clubs, seen = [], set()
    for name, category in items:
        name = (name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        clubs.append({"name": name, "category": (category or "").strip()})
    clubs.sort(key=lambda c: (c["category"], c["name"]))
    return clubs


def list_clubs(slug, refresh=False):
    """기관의 동아리 목록.

    반환: {clubs, available, cached, error}
      available=False 면 외부 조회 실패 — 관리자는 직접 입력으로 진행하면 된다.
    """
    now = time.time()
    if not refresh:
        hit = _cache.get(slug)
        if hit and hit[0] > now:
            return {**hit[1], "cached": True}

    url = f"{config.CLUBLOG_API_BASE}/api/{urllib.parse.quote(slug, safe='')}/clubs"
    try:
        result = {"clubs": _normalize(_request(url)), "available": True, "error": None}
        _cache[slug] = (now + config.CLUBLOG_CACHE_TTL, result)
    except urllib.error.HTTPError as e:
        reason = ("이 기관은 동아리 서비스에 등록되어 있지 않습니다."
                  if e.code == 404 else f"동아리 목록 조회 실패 (HTTP {e.code})")
        result = {"clubs": [], "available": False, "error": reason}
    except (urllib.error.URLError, TimeoutError, OSError):
        result = {"clubs": [], "available": False,
                  "error": "동아리 서비스에 연결하지 못했습니다."}
    except (ValueError, TypeError):
        result = {"clubs": [], "available": False,
                  "error": "동아리 목록 응답을 해석하지 못했습니다."}
    return {**result, "cached": False}


def clear_cache():
    """테스트용 — 캐시 비우기."""
    _cache.clear()
