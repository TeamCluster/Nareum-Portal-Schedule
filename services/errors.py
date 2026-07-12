"""도메인 예외 — 라우트의 전역 에러핸들러가 {"error": message} + status 로 변환."""


class ApiError(Exception):
    """사용자에게 노출할 메시지와 HTTP 상태코드를 가진 도메인 에러."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status
