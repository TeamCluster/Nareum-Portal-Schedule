"""Integration tests for the admin API (api/admin.py)."""

from conftest import reservation_payload, valid_date


class TestAuth:
    def test_login_wrong_password(self, client):
        res = client.post("/api/admin/login", json={"password": "nope"})
        assert res.status_code == 401

    def test_login_correct(self, client):
        res = client.post("/api/admin/login", json={"password": "test-pass"})
        assert res.status_code == 200
        assert res.get_json()["ok"] is True

    def test_me_reflects_session(self, admin_client):
        assert admin_client.get("/api/admin/me").get_json()["logged_in"] is True

    def test_logout(self, admin_client):
        admin_client.post("/api/admin/logout")
        assert admin_client.get("/api/admin/me").get_json()["logged_in"] is False

    def test_guard_blocks_anonymous(self, client):
        assert client.get("/api/admin/dashboard").status_code == 401
        assert client.get("/api/admin/requests").status_code == 401
        assert client.get("/api/admin/reservations").status_code == 401


class TestAdminCreate:
    def test_create_confirmed_reservation(self, admin_client):
        res = admin_client.post("/api/admin/reservations", json=reservation_payload())
        assert res.status_code == 201
        res_id = res.get_json()["id"]
        detail = admin_client.get(f"/api/admin/reservations/{res_id}").get_json()
        assert detail["status"] == "confirmed"

    def test_zero_participants_allowed_for_admin(self, admin_client):
        # Public rejects 0 participants; admin may create with none.
        res = admin_client.post("/api/admin/reservations", json=reservation_payload(participants={}))
        assert res.status_code == 201

    def test_over_two_hours_allowed_for_admin(self, admin_client):
        res = admin_client.post("/api/admin/reservations", json=reservation_payload(hours=[10, 11, 12, 13]))
        assert res.status_code == 201

    def test_overlap_still_blocked(self, admin_client):
        admin_client.post("/api/admin/reservations", json=reservation_payload(hours=[10, 11]))
        res = admin_client.post("/api/admin/reservations", json=reservation_payload(hours=[11]))
        assert res.status_code == 400


class TestApproveReject:
    def _create_pending(self, client):
        client.post("/api/reservations", json=reservation_payload())
        return client  # pending created by public flow

    def test_approve_moves_pending_to_confirmed(self, client, admin_client):
        client.post("/api/reservations", json=reservation_payload())
        pending = admin_client.get("/api/admin/requests").get_json()
        assert len(pending) == 1
        res_id = pending[0]["id"]

        res = admin_client.post(f"/api/admin/reservations/{res_id}/approve")
        assert res.status_code == 200

        detail = admin_client.get(f"/api/admin/reservations/{res_id}").get_json()
        assert detail["status"] == "confirmed"
        # No longer pending.
        assert admin_client.get("/api/admin/requests").get_json() == []

    def test_reject_soft_deletes_with_reason(self, client, admin_client):
        client.post("/api/reservations", json=reservation_payload())
        res_id = admin_client.get("/api/admin/requests").get_json()[0]["id"]

        res = admin_client.post(
            f"/api/admin/reservations/{res_id}/reject",
            json={"reject_reason": "시설 점검"},
        )
        assert res.status_code == 200

        detail = admin_client.get(f"/api/admin/reservations/{res_id}").get_json()
        assert detail["status"] == "rejected"
        assert detail["is_deleted"] is True
        assert detail["reject_reason"] == "시설 점검"

        # Rejected reservation frees its slot.
        avail = client.get(f"/api/availability?date={valid_date()}").get_json()
        assert avail["facilities"][0]["hours"]["10"] == "available"


class TestUpdate:
    def _pending_id(self, client, admin_client):
        client.post("/api/reservations", json=reservation_payload())
        return admin_client.get("/api/admin/requests").get_json()[0]["id"]

    def test_update_status_to_confirmed(self, client, admin_client):
        res_id = self._pending_id(client, admin_client)
        payload = reservation_payload(status="confirmed")
        res = admin_client.put(f"/api/admin/reservations/{res_id}", json=payload)
        assert res.status_code == 200
        assert admin_client.get(f"/api/admin/reservations/{res_id}").get_json()["status"] == "confirmed"

    def test_update_invalid_status_rejected(self, client, admin_client):
        res_id = self._pending_id(client, admin_client)
        res = admin_client.put(
            f"/api/admin/reservations/{res_id}",
            json=reservation_payload(status="bogus"),
        )
        assert res.status_code == 400

    def test_dashboard_counts_pending(self, client, admin_client):
        client.post("/api/reservations", json=reservation_payload())
        data = admin_client.get("/api/admin/dashboard").get_json()
        assert data["pending_count"] == 1

    def test_calendar_events_color_by_status(self, client, admin_client):
        client.post("/api/reservations", json=reservation_payload())
        events = admin_client.get("/api/admin/calendar-events").get_json()
        assert len(events) == 1
        assert events[0]["color"] == "#ff9800"  # pending = orange
