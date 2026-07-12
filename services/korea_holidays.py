"""한국 공휴일 자동 조회 — `holidays` 라이브러리(오프라인, 키 불필요).

대체공휴일(주말/중복 시 다음 평일로 이동)은 법령상 결정적이라 라이브러리가
자동 계산한다. 매년 공휴일 제정/폐지 등 규칙 변경은 라이브러리 업데이트로 반영되며,
슈퍼 페이지에서 연도별로 다시 동기화하면 최신 상태가 적용된다.

(선거일 등 급박한 임시공휴일은 라이브러리에 없을 수 있으므로, 그 경우 슈퍼가
수동으로 추가하면 된다.)
"""
import holidays


def fetch_korea_holidays(year):
    """해당 연도의 한국 공휴일 [{date:'YYYY-MM-DD', name}] (대체공휴일 포함, 날짜순)."""
    y = int(year)
    try:
        kr = holidays.SouthKorea(years=y, language="ko")
    except TypeError:  # 구버전 holidays: language 미지원
        kr = holidays.SouthKorea(years=y)
    return [{"date": d.isoformat(), "name": kr[d]} for d in sorted(kr)]
