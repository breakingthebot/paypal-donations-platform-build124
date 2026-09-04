"""Integration tests for FastAPI endpoints."""

from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from paypal_donations.api import app, repo, email_service, paypal_client
from paypal_donations.repository import DonorRepository


@pytest.fixture(autouse=True)
def clean_test_repo(tmp_path):
    test_db = tmp_path / "test_api_donations.sqlite3"
    test_repo = DonorRepository(db_path=str(test_db))
    # Override global repo in api module
    import paypal_donations.api as api_mod
    api_mod.repo = test_repo
    api_mod.webhook_manager.repo = test_repo
    api_mod.email_service.receipts_dir = tmp_path / "api_receipts"
    api_mod.email_service.receipts_dir.mkdir(parents=True, exist_ok=True)
    api_mod.webhook_manager.email_service = api_mod.email_service
    yield test_repo


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data


def test_config_endpoint(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "org_name" in data
    assert "paypal_mode" in data


def test_portal_html_serves(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Make a Donation" in response.text


def test_create_and_capture_donation_lifecycle(client):
    # Step 1: Create Order
    create_payload = {
        "amount": 35.00,
        "currency": "USD",
        "donor_name": "Taylor Swift",
        "donor_email": "taylor@example.org",
        "dedication": "Music education fund",
        "is_anonymous": False,
    }
    create_resp = client.post("/api/donations/create-order", json=create_payload)
    assert create_resp.status_code == 200
    order_data = create_resp.json()
    order_id = order_data["order_id"]
    assert order_id.startswith("ORDER-MOCK-")

    # Step 2: Capture Order
    capture_payload = {
        "order_id": order_id,
        "donor_name": "Taylor Swift",
        "donor_email": "taylor@example.org",
    }
    capture_resp = client.post("/api/donations/capture-order", json=capture_payload)
    assert capture_resp.status_code == 200
    capture_data = capture_resp.json()
    assert capture_data["status"] == "COMPLETED"
    assert capture_data["receipt_sent"] is True
    assert capture_data["order_id"] == order_id

    # Step 3: Verify Donor Wall
    wall_resp = client.get("/api/donations/donors")
    assert wall_resp.status_code == 200
    wall = wall_resp.json()
    assert len(wall) == 1
    assert wall[0]["donor_name"] == "Taylor Swift"
    assert Decimal(str(wall[0]["amount"])) == Decimal("35.00")

    # Step 4: Verify Stats
    stats_resp = client.get("/api/donations/stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["total_donations"] == 1
    assert Decimal(str(stats["total_amount_by_currency"]["USD"])) == Decimal("35.00")


def test_admin_export_csv_and_json(client):
    # Seed a donation
    create_resp = client.post(
        "/api/donations/create-order",
        json={
            "amount": 10.00,
            "currency": "USD",
            "donor_name": "Admin Tester",
            "donor_email": "admin@example.org",
            "is_anonymous": True,
        },
    )
    order_id = create_resp.json()["order_id"]
    client.post("/api/donations/capture-order", json={"order_id": order_id})

    # CSV Export
    csv_resp = client.get("/api/admin/export?format=csv")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "Admin Tester" in csv_resp.text

    # JSON Export
    json_resp = client.get("/api/admin/export?format=json")
    assert json_resp.status_code == 200
    assert "application/json" in json_resp.headers["content-type"]
    assert order_id in json_resp.text


def test_webhook_endpoint_processing(client):
    import uuid
    evt_id = f"WH-API-{uuid.uuid4().hex[:8]}"
    webhook_payload = {
        "id": evt_id,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": f"CAP-{uuid.uuid4().hex[:8]}"},
    }
    headers = {
        "paypal-transmission-sig": "valid-sig-token",
        "content-type": "application/json",
    }
    response = client.post("/api/webhooks/paypal", json=webhook_payload, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert data["event_id"] == evt_id


def test_admin_refund_donation_endpoint(client):
    # Create & capture donation
    create_resp = client.post(
        "/api/donations/create-order",
        json={
            "amount": 75.00,
            "currency": "USD",
            "donor_name": "Refund Candidate",
            "donor_email": "candidate@example.org",
        },
    )
    order_id = create_resp.json()["order_id"]
    client.post("/api/donations/capture-order", json={"order_id": order_id})

    # Execute refund
    refund_resp = client.post(
        f"/api/admin/donations/{order_id}/refund",
        json={"reason": "User requested cancellation within 24 hours"},
    )
    assert refund_resp.status_code == 200
    ref_data = refund_resp.json()
    assert ref_data["status"] == "REFUNDED"
    assert ref_data["receipt_sent"] is True

    # Duplicate refund attempt should fail
    dup_resp = client.post(
        f"/api/admin/donations/{order_id}/refund",
        json={"reason": "Repeat request"},
    )
    assert dup_resp.status_code == 400

    # Verify admin webhooks endpoint
    wh_resp = client.get("/api/admin/webhooks")
    assert wh_resp.status_code == 200
    assert isinstance(wh_resp.json(), list)
