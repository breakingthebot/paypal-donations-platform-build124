"""Unit and integration tests for recurring pledges and monthly subscriptions."""

import json
from decimal import Decimal
from pathlib import Path
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from paypal_donations.models import (
    CurrencyCode,
    DonationFrequency,
    CreateSubscriptionRequest,
    SubscriptionRecord,
)
from paypal_donations.paypal_client import PayPalClient
from paypal_donations.repository import DonorRepository
from paypal_donations.webhooks import WebhookManager
from paypal_donations.email_service import EmailService
from paypal_donations.cli import main
import paypal_donations.api as api_mod


@pytest.fixture
def repo(tmp_path: Path) -> DonorRepository:
    db_file = tmp_path / "test_subs.sqlite3"
    return DonorRepository(db_path=str(db_file))


@pytest.fixture
def paypal_client() -> PayPalClient:
    return PayPalClient(
        client_id="mock_client_id",
        client_secret="mock_secret",
        mode="mock",
    )


# =========================================================================
# 1. PayPalClient Subscriptions
# =========================================================================

def test_paypal_client_create_subscription(paypal_client: PayPalClient):
    req = CreateSubscriptionRequest(
        donor_name="Rachel Green",
        donor_email="rachel@example.com",
        amount=Decimal("35.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
        dedication="Clean Water Initiative",
    )
    result = paypal_client.create_subscription(req)

    assert "SUB" in result.subscription_id
    assert result.status == "ACTIVE"
    assert "paypal.com" in result.approval_url
    assert result.amount == Decimal("35.00")

    # Fetch from client
    sub_data = paypal_client.get_subscription(result.subscription_id)
    assert sub_data["id"] == result.subscription_id
    assert sub_data["status"] == "ACTIVE"


def test_paypal_client_cancel_subscription(paypal_client: PayPalClient):
    req = CreateSubscriptionRequest(
        donor_name="Chandler Bing",
        donor_email="chandler@example.com",
        amount=Decimal("50.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
    )
    result = paypal_client.create_subscription(req)

    cancel_res = paypal_client.cancel_subscription(result.subscription_id, reason="Donor moved")
    assert cancel_res["status"] == "CANCELLED"

    sub_data = paypal_client.get_subscription(result.subscription_id)
    assert sub_data["status"] == "CANCELLED"


# =========================================================================
# 2. DonorRepository Subscriptions & MRR Metrics
# =========================================================================

def test_repo_create_and_get_subscription(repo: DonorRepository):
    sub = SubscriptionRecord(
        subscription_id="I-SUB-TEST-001",
        donor_name="Monica Geller",
        donor_email="monica@example.com",
        amount=Decimal("25.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
        status="ACTIVE",
        dedication="Kitchen fund",
        is_anonymous=False,
    )
    saved = repo.create_subscription(sub)
    assert saved.id is not None

    retrieved = repo.get_subscription("I-SUB-TEST-001")
    assert retrieved is not None
    assert retrieved.donor_name == "Monica Geller"
    assert retrieved.amount == Decimal("25.00")
    assert retrieved.status == "ACTIVE"
    assert retrieved.frequency == DonationFrequency.MONTHLY


def test_repo_update_subscription_status(repo: DonorRepository):
    sub = SubscriptionRecord(
        subscription_id="I-SUB-TEST-002",
        donor_name="Joey Tribbiani",
        donor_email="joey@example.com",
        amount=Decimal("15.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
        status="ACTIVE",
    )
    repo.create_subscription(sub)

    updated = repo.update_subscription_status("I-SUB-TEST-002", "CANCELLED")
    assert updated is True

    retrieved = repo.get_subscription("I-SUB-TEST-002")
    assert retrieved is not None
    assert retrieved.status == "CANCELLED"


def test_repo_recurring_metrics(repo: DonorRepository):
    # Active USD subscription
    repo.create_subscription(SubscriptionRecord(
        subscription_id="I-SUB-USD1",
        donor_name="User 1",
        donor_email="u1@example.com",
        amount=Decimal("20.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
        status="ACTIVE",
    ))
    # Another active USD subscription
    repo.create_subscription(SubscriptionRecord(
        subscription_id="I-SUB-USD2",
        donor_name="User 2",
        donor_email="u2@example.com",
        amount=Decimal("30.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
        status="ACTIVE",
    ))
    # Active EUR subscription
    repo.create_subscription(SubscriptionRecord(
        subscription_id="I-SUB-EUR1",
        donor_name="User 3",
        donor_email="u3@example.com",
        amount=Decimal("25.00"),
        currency=CurrencyCode.EUR,
        frequency=DonationFrequency.MONTHLY,
        status="ACTIVE",
    ))
    # Cancelled subscription (should not be counted in MRR)
    repo.create_subscription(SubscriptionRecord(
        subscription_id="I-SUB-CAN1",
        donor_name="User 4",
        donor_email="u4@example.com",
        amount=Decimal("100.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
        status="CANCELLED",
    ))

    metrics = repo.get_recurring_metrics()
    assert metrics.active_subscribers == 3
    assert metrics.mrr_by_currency["USD"] == Decimal("50.00")
    assert metrics.mrr_by_currency["EUR"] == Decimal("25.00")
    assert metrics.total_active_pledged_monthly == Decimal("50.00")


def test_repo_list_subscriptions_filter(repo: DonorRepository):
    repo.create_subscription(SubscriptionRecord(
        subscription_id="I-SUB-ACTIVE-1",
        donor_name="Active Supporter",
        donor_email="active@example.com",
        amount=Decimal("10.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
        status="ACTIVE",
    ))
    repo.create_subscription(SubscriptionRecord(
        subscription_id="I-SUB-CANCEL-1",
        donor_name="Past Supporter",
        donor_email="past@example.com",
        amount=Decimal("15.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
        status="CANCELLED",
    ))

    all_subs = repo.list_subscriptions()
    assert len(all_subs) == 2

    active_subs = repo.list_subscriptions(status="ACTIVE")
    assert len(active_subs) == 1
    assert active_subs[0].subscription_id == "I-SUB-ACTIVE-1"


# =========================================================================
# 3. Webhook Handling for Subscriptions
# =========================================================================

def test_webhook_subscription_activated_and_cancelled(repo: DonorRepository, paypal_client: PayPalClient, tmp_path: Path):
    email_service = EmailService(mode="mock", receipts_dir=str(tmp_path / "receipts"))
    manager = WebhookManager(paypal_client, repo, email_service)

    # Pre-register subscription
    repo.create_subscription(SubscriptionRecord(
        subscription_id="I-SUB-HOOK-1",
        donor_name="Phoebe Buffay",
        donor_email="phoebe@example.com",
        amount=Decimal("40.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
        status="PENDING",
    ))

    headers = {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": "https://api.sandbox.paypal.com/cert.pem",
        "paypal-transmission-id": "tx-wh-act-1",
        "paypal-transmission-sig": "mock_sig_123",
        "paypal-transmission-time": "2026-09-04T12:00:00Z",
    }

    # 1. BILLING.SUBSCRIPTION.ACTIVATED
    activated_event = {
        "id": "WH-ACT-001",
        "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
        "resource": {"id": "I-SUB-HOOK-1", "status": "ACTIVE"},
    }
    res = manager.process_webhook_event(headers, json.dumps(activated_event))
    assert res["status"] == "SUCCESS"
    assert repo.get_subscription("I-SUB-HOOK-1").status == "ACTIVE"

    # 2. BILLING.SUBSCRIPTION.CANCELLED
    cancelled_event = {
        "id": "WH-CAN-001",
        "event_type": "BILLING.SUBSCRIPTION.CANCELLED",
        "resource": {"id": "I-SUB-HOOK-1", "status": "CANCELLED"},
    }
    res_cancel = manager.process_webhook_event(headers, json.dumps(cancelled_event))
    assert res_cancel["status"] == "SUCCESS"
    assert repo.get_subscription("I-SUB-HOOK-1").status == "CANCELLED"


def test_webhook_recurring_sale_completed(repo: DonorRepository, paypal_client: PayPalClient, tmp_path: Path):
    email_service = EmailService(mode="mock", receipts_dir=str(tmp_path / "receipts"))
    manager = WebhookManager(paypal_client, repo, email_service)

    # Store active subscription
    repo.create_subscription(SubscriptionRecord(
        subscription_id="I-SUB-SALE-99",
        donor_name="Ross Geller",
        donor_email="ross@example.com",
        amount=Decimal("75.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
        status="ACTIVE",
    ))

    headers = {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": "https://api.sandbox.paypal.com/cert.pem",
        "paypal-transmission-id": "tx-sale-99",
        "paypal-transmission-sig": "mock_sig_99",
        "paypal-transmission-time": "2026-09-04T12:00:00Z",
    }

    sale_event = {
        "id": "WH-SALE-001",
        "event_type": "PAYMENT.SALE.COMPLETED",
        "resource": {
            "id": "SALE-REC-001",
            "billing_agreement_id": "I-SUB-SALE-99",
            "amount": {"total": "75.00", "currency": "USD"},
        },
    }

    res = manager.process_webhook_event(headers, json.dumps(sale_event))
    assert res["status"] == "SUCCESS"
    assert res["details"]["action"] == "recurring_donation_recorded"

    # Verify a new completed donation was inserted into the database
    donations = repo.list_donations()
    matching = [d for d in donations if d.capture_id == "SALE-REC-001"]
    assert len(matching) == 1
    assert matching[0].donor_name == "Ross Geller"
    assert matching[0].amount == Decimal("75.00")
    assert matching[0].receipt_sent is True


# =========================================================================
# 4. API Endpoints for Subscriptions
# =========================================================================

@pytest.fixture
def client_with_db(tmp_path: Path):
    db_file = tmp_path / "api_subs_test.sqlite3"
    test_repo = DonorRepository(db_path=str(db_file))
    api_mod.repo = test_repo
    api_mod.webhook_manager.repo = test_repo
    api_mod.webhook_manager.email_service = api_mod.email_service
    client = TestClient(api_mod.app)
    return client, test_repo


def test_api_create_subscription(client_with_db):
    client, repo = client_with_db
    payload = {
        "donor_name": "Carol Willick",
        "donor_email": "carol@example.com",
        "amount": 50.0,
        "currency": "USD",
        "frequency": "MONTHLY",
        "dedication": "Monthly literacy drive",
        "is_anonymous": False,
    }
    response = client.post("/api/donations/create-subscription", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "subscription_id" in data
    assert data["status"] == "ACTIVE"

    # Verify in DB
    sub = repo.get_subscription(data["subscription_id"])
    assert sub is not None
    assert sub.donor_name == "Carol Willick"
    assert sub.amount == Decimal("50.00")


def test_api_recurring_stats(client_with_db):
    client, repo = client_with_db
    # Pre-populate subscription
    repo.create_subscription(SubscriptionRecord(
        subscription_id="I-SUB-API-STAT",
        donor_name="Stat Donor",
        donor_email="stat@example.com",
        amount=Decimal("45.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
        status="ACTIVE",
    ))

    response = client.get("/api/donations/recurring-stats")
    assert response.status_code == 200
    data = response.json()
    assert data["active_subscribers"] >= 1
    assert Decimal(str(data["total_active_pledged_monthly"])) >= Decimal("45.00")


def test_api_admin_list_and_cancel_subscription(client_with_db):
    client, repo = client_with_db
    create_resp = client.post("/api/donations/create-subscription", json={
        "donor_name": "Cancel Donor",
        "donor_email": "cancel@example.com",
        "amount": 12.0,
        "currency": "USD",
        "frequency": "MONTHLY",
        "dedication": "Will cancel soon",
        "is_anonymous": False,
    })
    assert create_resp.status_code == 200
    sub_id = create_resp.json()["subscription_id"]

    # List admin subscriptions
    list_resp = client.get("/api/admin/subscriptions")
    assert list_resp.status_code == 200
    assert any(s["subscription_id"] == sub_id for s in list_resp.json())

    # Cancel subscription
    cancel_resp = client.post(f"/api/admin/subscriptions/{sub_id}/cancel?reason=Test+cancellation")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"

    # Verify DB update
    updated_sub = repo.get_subscription(sub_id)
    assert updated_sub.status == "CANCELLED"


# =========================================================================
# 5. CLI Subscription Commands
# =========================================================================

def test_cli_subscriptions_commands(tmp_path: Path):
    db_file = str(tmp_path / "cli_subs.sqlite3")
    runner = CliRunner()

    # Pre-populate subscription
    repo = DonorRepository(db_path=db_file)
    repo.create_subscription(SubscriptionRecord(
        subscription_id="I-SUB-CLI-01",
        donor_name="Emma Geller",
        donor_email="emma@example.com",
        amount=Decimal("22.00"),
        currency=CurrencyCode.USD,
        frequency=DonationFrequency.MONTHLY,
        status="ACTIVE",
    ))

    # 1. List subscriptions
    res = runner.invoke(main, ["subscriptions", "list", "--db", db_file])
    assert res.exit_code == 0
    assert "Emma Geller" in res.output
    assert "I-SUB-CLI-01" in res.output

    # 2. Subscriptions stats
    res_stats = runner.invoke(main, ["subscriptions", "stats", "--db", db_file])
    assert res_stats.exit_code == 0
    assert "Recurring Pledge Metrics" in res_stats.output
    assert "22.00" in res_stats.output

    # 3. Cancel subscription
    res_cancel = runner.invoke(main, ["subscriptions", "cancel", "--id", "I-SUB-CLI-01", "--db", db_file])
    assert res_cancel.exit_code == 0
    assert "has been cancelled" in res_cancel.output
    assert repo.get_subscription("I-SUB-CLI-01").status == "CANCELLED"
