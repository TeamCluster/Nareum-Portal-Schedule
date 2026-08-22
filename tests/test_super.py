"""슈퍼 관리자 API 통합 테스트."""
import io

from conftest import PLACE_PW, SLUG, SUPER_PW
from test_scheduling import _PNG


class TestSuperAuth:
    def test_login_wrong(self, client):
        assert client.post("/api/super/login", json={"password": "x"}).status_code == 401

    def test_bruteforce_locks_out(self, client):
        """임계치를 넘으면 429 로 잠기고, 이후엔 올바른 비밀번호도 통하지 않는다."""
        import config
        for _ in range(config.LOGIN_MAX_ATTEMPTS):
            assert client.post("/api/super/login", json={"password": "x"}).status_code == 401
        assert client.post("/api/super/login", json={"password": "x"}).status_code == 429
        assert client.post("/api/super/login", json={"password": SUPER_PW}).status_code == 429

    def test_success_resets_counter(self, client):
        """성공하면 카운터가 지워져 이전 실패가 누적되지 않는다."""
        import config
        for _ in range(config.LOGIN_MAX_ATTEMPTS - 1):
            client.post("/api/super/login", json={"password": "x"})
        assert client.post("/api/super/login", json={"password": SUPER_PW}).status_code == 200
        assert client.post("/api/super/login", json={"password": "x"}).status_code == 401

    def test_place_lockout_is_independent_of_super(self, client):
        """기관 잠금이 슈퍼 로그인을 막지 않는다(범위별로 따로 센다)."""
        import config
        for _ in range(config.LOGIN_MAX_ATTEMPTS):
            client.post(f"/api/{SLUG}/admin/login", json={"password": "x"})
        assert client.post(f"/api/{SLUG}/admin/login",
                           json={"password": PLACE_PW}).status_code == 429
        assert client.post("/api/super/login", json={"password": SUPER_PW}).status_code == 200

    def test_login_ok(self, client):
        r = client.post("/api/super/login", json={"password": SUPER_PW})
        assert r.status_code == 200 and r.get_json()["ok"] is True

    def test_session(self, super_client):
        assert super_client.get("/api/super/session").get_json()["logged_in"] is True

    def test_logout(self, super_client):
        super_client.post("/api/super/logout")
        assert super_client.get("/api/super/session").get_json()["logged_in"] is False

    def test_guard(self, client):
        assert client.get("/api/super/places").status_code == 401


class TestSuperPassword:
    def test_change_and_relogin(self, super_client, client):
        r = super_client.post("/api/super/password", json={"new_password": "newsuper1"})
        assert r.status_code == 200
        assert client.post("/api/super/login", json={"password": "newsuper1"}).status_code == 200
        assert client.post("/api/super/login", json={"password": SUPER_PW}).status_code == 401

    def test_too_short(self, super_client):
        assert super_client.post("/api/super/password", json={"new_password": "123"}).status_code == 400


class TestPlacesCrud:
    def test_default_place_listed(self, super_client):
        places = super_client.get("/api/super/places").get_json()["places"]
        assert any(p["slug"] == SLUG for p in places)

    def test_add_place(self, super_client):
        r = super_client.post("/api/super/places", json={
            "slug": "didim", "full_name": "디딤센터", "short_name": "디딤",
            "password": "didim123", "phone": "031-111-2222",
        })
        assert r.status_code == 200 and r.get_json()["ok"] is True
        slugs = [p["slug"] for p in super_client.get("/api/super/places").get_json()["places"]]
        assert "didim" in slugs

    def test_add_invalid_slug(self, super_client):
        r = super_client.post("/api/super/places", json={
            "slug": "Super", "full_name": "x", "short_name": "x", "password": "abcdef",
        })
        assert r.status_code == 400

    def test_add_reserved_slug(self, super_client):
        r = super_client.post("/api/super/places", json={
            "slug": "admin", "full_name": "x", "short_name": "x", "password": "abcdef",
        })
        assert r.status_code == 400

    def test_add_short_password(self, super_client):
        r = super_client.post("/api/super/places", json={
            "slug": "abc", "full_name": "x", "short_name": "x", "password": "12",
        })
        assert r.status_code == 400

    def test_duplicate_slug(self, super_client):
        body = {"slug": "dup", "full_name": "x", "short_name": "x", "password": "abcdef"}
        assert super_client.post("/api/super/places", json=body).status_code == 200
        assert super_client.post("/api/super/places", json=body).status_code == 400

    def test_update_info(self, super_client):
        super_client.post("/api/super/places", json={
            "slug": "edit", "full_name": "옛이름", "short_name": "옛", "password": "abcdef"})
        r = super_client.put("/api/super/places/edit", json={
            "full_name": "새이름", "phone": "010-0000-0000"})
        assert r.status_code == 200
        place = next(p for p in super_client.get("/api/super/places").get_json()["places"]
                     if p["slug"] == "edit")
        assert place["full_name"] == "새이름" and place["phone"] == "010-0000-0000"

    def test_delete(self, super_client):
        super_client.post("/api/super/places", json={
            "slug": "temp", "full_name": "x", "short_name": "x", "password": "abcdef"})
        assert super_client.delete("/api/super/places/temp").status_code == 200
        slugs = [p["slug"] for p in super_client.get("/api/super/places").get_json()["places"]]
        assert "temp" not in slugs

    def test_change_place_password_enables_login(self, super_client, client):
        super_client.post("/api/super/places", json={
            "slug": "pw", "full_name": "x", "short_name": "x", "password": "abcdef"})
        super_client.post("/api/super/places/pw/password", json={"new_password": "zzzzzz"})
        assert client.post("/api/pw/admin/login", json={"password": "zzzzzz"}).status_code == 200
        assert client.post("/api/pw/admin/login", json={"password": "abcdef"}).status_code == 401


class TestPlaceHeader:
    def _upload(self, super_client, name="logo.png", data=_PNG):
        return super_client.post(
            f"/api/super/places/{SLUG}/header",
            data={"image": (io.BytesIO(data), name)},
            content_type="multipart/form-data",
        )

    def test_upload_reflected_in_info(self, super_client, client):
        r = self._upload(super_client)
        assert r.status_code == 200
        url = r.get_json()["header_image"]
        assert url.startswith(f"/static/{SLUG}/header.png")
        info = client.get(f"/api/{SLUG}/info").get_json()
        assert info["header_image"] == url
        assert len(info["operating_hours"]) == 7  # /info 에 운영시간 포함

    def test_delete(self, super_client, client):
        self._upload(super_client)
        assert super_client.delete(f"/api/super/places/{SLUG}/header").status_code == 200
        assert client.get(f"/api/{SLUG}/info").get_json()["header_image"] == ""

    def test_reject_non_image(self, super_client):
        assert self._upload(super_client, "note.txt", b"hello").status_code == 400

    def test_guard(self, client):
        assert client.post(f"/api/super/places/{SLUG}/header").status_code == 401
