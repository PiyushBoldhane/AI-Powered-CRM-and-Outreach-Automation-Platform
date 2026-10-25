"""
Unit tests for POST /leads/{id}/claim (BE-17).
Mocks the DB layer entirely — no real Postgres connection required.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from backend.app.main import app
from backend.app.core.security import get_current_user

RESEARCHER = {"id": "u-researcher", "email": "r@example.com", "full_name": "Rita", "role": "researcher"}
ADMIN = {"id": "u-admin", "email": "a@example.com", "full_name": "Ada", "role": "admin"}
OTHER_USER_ID = "u-other"
LEAD_ID = "b0000001-0000-0000-0000-000000000001"


def _override_user(user):
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _mock_transaction(claim_result, existing_assigned_to=None):
    """
    Builds a fake `transaction()` context manager whose cursor:
    - returns `claim_result` from the UPDATE...RETURNING fetchone()
    - if claim_result is None, returns (existing_assigned_to,) from the follow-up SELECT
    """
    mock_cur = MagicMock()
    mock_cur.description = [("id",), ("assigned_to",), ("claimed_at",), ("status",)]

    call_count = {"n": 0}

    def fetchone_side_effect():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return claim_result
        return (existing_assigned_to,) if existing_assigned_to is not None else None

    mock_cur.fetchone.side_effect = fetchone_side_effect
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    @contextmanager
    def fake_transaction():
        yield mock_conn

    return fake_transaction, mock_cur


def test_researcher_claims_for_self_returns_200(client):
    _override_user(RESEARCHER)
    import datetime
    claimed_at = datetime.datetime(2026, 10, 25, 12, 0, 0)
    fake_tx, mock_cur = _mock_transaction((LEAD_ID, RESEARCHER["id"], claimed_at, "verified"))

    with patch("backend.app.api.v1.leads.transaction", fake_tx):
        resp = client.post(f"/api/v1/leads/{LEAD_ID}/claim", json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body["assigned_to"] == RESEARCHER["id"]
    # audit_logs INSERT was executed
    insert_calls = [c for c in mock_cur.execute.call_args_list if "INSERT INTO audit_logs" in c.args[0]]
    assert len(insert_calls) == 1


def test_second_concurrent_claim_returns_409(client):
    _override_user(RESEARCHER)
    fake_tx, mock_cur = _mock_transaction(claim_result=None, existing_assigned_to="someone-else")

    with patch("backend.app.api.v1.leads.transaction", fake_tx):
        resp = client.post(f"/api/v1/leads/{LEAD_ID}/claim", json={})

    assert resp.status_code == 409
    insert_calls = [c for c in mock_cur.execute.call_args_list if "INSERT INTO audit_logs" in c.args[0]]
    assert len(insert_calls) == 0  # no audit row on a losing claim


def test_claim_nonexistent_lead_returns_404(client):
    _override_user(RESEARCHER)
    fake_tx, _ = _mock_transaction(claim_result=None, existing_assigned_to=None)

    with patch("backend.app.api.v1.leads.transaction", fake_tx):
        resp = client.post(f"/api/v1/leads/{LEAD_ID}/claim", json={})

    assert resp.status_code == 404


def test_researcher_cannot_assign_to_other_user_returns_403(client):
    _override_user(RESEARCHER)
    fake_tx, mock_cur = _mock_transaction(claim_result=None)

    with patch("backend.app.api.v1.leads.transaction", fake_tx):
        resp = client.post(f"/api/v1/leads/{LEAD_ID}/claim", json={"user_id": OTHER_USER_ID})

    assert resp.status_code == 403
    mock_cur.execute.assert_not_called()  # must reject before touching the DB


def test_admin_can_assign_to_other_user_returns_200(client):
    _override_user(ADMIN)
    import datetime
    claimed_at = datetime.datetime(2026, 10, 25, 12, 0, 0)
    fake_tx, _ = _mock_transaction((LEAD_ID, OTHER_USER_ID, claimed_at, "verified"))

    with patch("backend.app.api.v1.leads.transaction", fake_tx):
        resp = client.post(f"/api/v1/leads/{LEAD_ID}/claim", json={"user_id": OTHER_USER_ID})

    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == OTHER_USER_ID


def test_campaign_manager_can_assign_to_other_user_returns_200(client):
    campaign_manager = {"id": "u-cm", "email": "cm@example.com", "full_name": "Carlos", "role": "campaign_manager"}
    _override_user(campaign_manager)
    import datetime
    claimed_at = datetime.datetime(2026, 10, 25, 12, 0, 0)
    fake_tx, _ = _mock_transaction((LEAD_ID, OTHER_USER_ID, claimed_at, "verified"))

    with patch("backend.app.api.v1.leads.transaction", fake_tx):
        resp = client.post(f"/api/v1/leads/{LEAD_ID}/claim", json={"user_id": OTHER_USER_ID})

    assert resp.status_code == 200
