"""Unit and integration tests for PayPal webhook verification and event routing."""

import json
from decimal import Decimal
from pathlib import Path
import pytest

from paypal_donations.email_service import EmailService
from paypal_donations.models import CurrencyCode, DonationRecord, DonationStatus
from paypal_donations.paypal_client import PayPalClient
from paypal_donations.repository import DonorRepository
from paypal_donations.webhooks import WebhookManager, WebhookVerificationError


@pytest.fixture
def webhook_env(tmp_path: Path):
    db_file = tmp_path / "test_webhooks.sqlite3"
    repo = DonorRepository(db_path=str(db_file))
    email_dir = tmp_path / "webhook_receipts"
    email_service = EmailService(mode="mock", receipts_dir=str(email_dir))
    paypal_client = PayPalClient(mode="mock")
    manager = WebhookManager(paypal_client=paypal_client, repo=repo, email_service=email_service)
    return manager, repo, email_service


def test_verify_headers_missing_or_insecure(webhook_env):
    manager, _, _ = webhook_env

    with pytest.raises(WebhookVerificationError) as exc:
        manager.verify_headers_security({})
    assert "Missing required PAYPAL-CERT-URL" in str(exc.value)

    with pytest.raises(WebhookVerificationError) as exc:
        manager.verify_headers_security({"paypal-cert-url": "http://api.paypal.com/cert.pem"})
    assert "must use HTTPS" in str(exc.value)

    with pytest.raises(WebhookVerificationError) as exc:
        manager.verify_headers_security({"paypal-cert-url": "https://malicious-site.com/cert.pem"})
    assert "Untrusted cert host" in str(exc.value)


def test_verify_signature_mock_mode(webhook_env):
    manager, _, _ = webhook_env
    valid_headers = {"paypal-transmission-sig": "mock-sig-abc"}
    assert manager.verify_signature(valid_headers, "{}") is True

    invalid_headers = {"paypal-transmission-sig": "INVALID_SIGNATURE"}
    assert manager.verify_signature(invalid_headers, "{}") is False


def test_webhook_capture_completed_event(webhook_env):
    manager, repo, email_service = webhook_env

    # Seed pending donation
    donation = DonationRecord(
        order_id="ORD-WH-001",
        donor_name="Bruce Wayne",
        donor_email="bruce@waynecorp.com",
        amount=Decimal("1000.00"),
        currency=CurrencyCode.USD,
        status=DonationStatus.PENDING,
    )
    repo.create_donation(donation)

    payload = {
        "id": "WH-EVT-001",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAP-WH-001",
            "amount": {"value": "1000.00", "currency_code": "USD"},
            "supplementary_data": {"related_ids": {"order_id": "ORD-WH-001"}},
        },
    }
    headers = {"paypal-transmission-sig": "valid-sig"}
    res = manager.process_webhook_event(headers, json.dumps(payload))

    assert res["status"] == "SUCCESS"
    assert res["event_type"] == "PAYMENT.CAPTURE.COMPLETED"

    updated = repo.get_by_order_id("ORD-WH-001")
    assert updated.status == DonationStatus.COMPLETED
    assert updated.capture_id == "CAP-WH-001"
    assert updated.receipt_sent is True


def test_webhook_idempotency_duplicate_event(webhook_env):
    manager, repo, _ = webhook_env

    payload = {
        "id": "WH-EVT-DUP-01",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": "CAP-DUP-01"},
    }
    headers = {"paypal-transmission-sig": "valid-sig"}

    res1 = manager.process_webhook_event(headers, json.dumps(payload))
    assert res1["status"] == "SUCCESS"

    # Second dispatch with identical event_id
    res2 = manager.process_webhook_event(headers, json.dumps(payload))
    assert res2["status"] == "DUPLICATE"
    assert res2["message"] == "Event has already been processed."


def test_webhook_capture_refunded_event(webhook_env):
    manager, repo, email_service = webhook_env

    # Seed completed donation
    donation = DonationRecord(
        order_id="ORD-REF-001",
        capture_id="CAP-REF-001",
        donor_name="Clark Kent",
        donor_email="clark@dailyplanet.com",
        amount=Decimal("150.00"),
        currency=CurrencyCode.USD,
        status=DonationStatus.COMPLETED,
    )
    repo.create_donation(donation)

    payload = {
        "id": "WH-EVT-REF-01",
        "event_type": "PAYMENT.CAPTURE.REFUNDED",
        "resource": {
            "id": "REFUND-TX-001",
            "capture_id": "CAP-REF-001",
            "amount": {"value": "150.00", "currency_code": "USD"},
            "note_to_payer": "Accidental duplicate donation refunded",
        },
    }
    headers = {"paypal-transmission-sig": "valid-sig"}
    res = manager.process_webhook_event(headers, json.dumps(payload))

    assert res["status"] == "SUCCESS"
    assert res["details"]["action"] == "refund_processed"

    updated = repo.get_by_order_id("ORD-REF-001")
    assert updated.status == DonationStatus.REFUNDED
    assert "Refunded" in updated.dedication

    # Verify refund receipt was dispatched
    assert any("REFUND" in r["receipt_id"] for r in email_service.dispatched_receipts)
