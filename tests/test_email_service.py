"""Unit tests for EmailService."""

from decimal import Decimal
from pathlib import Path
import pytest

from paypal_donations.email_service import EmailService
from paypal_donations.models import CurrencyCode, EmailReceiptPayload


@pytest.fixture
def sample_payload() -> EmailReceiptPayload:
    return EmailReceiptPayload(
        receipt_id="REC-20260904-ABCDEF",
        order_id="ORDER-12345",
        capture_id="CAP-67890",
        donor_name="Jordan Bell",
        donor_email="jordan@example.com",
        amount=Decimal("125.00"),
        currency=CurrencyCode.USD,
        date_formatted="September 04, 2026 at 16:30 UTC",
        dedication="In honor of global educators",
        org_name="Open Impact Foundation",
        org_email="donations@openimpact.org",
        org_tax_id="501(c)(3)-999888",
        org_website="https://openimpact.org",
    )


def test_render_html_receipt(sample_payload: EmailReceiptPayload):
    service = EmailService(mode="mock")
    html = service.render_html(sample_payload)

    assert "Jordan Bell" in html
    assert "$125.00 USD" in html
    assert "REC-20260904-ABCDEF" in html
    assert "In honor of global educators" in html
    assert "501(c)(3)-999888" in html


def test_render_plaintext_receipt(sample_payload: EmailReceiptPayload):
    service = EmailService(mode="mock")
    text = service.render_plaintext(sample_payload)

    assert "OFFICIAL DONATION CONFIRMATION RECEIPT" in text
    assert "Jordan Bell" in text
    assert "$125.00 USD" in text
    assert "501(c)(3)-999888" in text


def test_send_receipt_mock_mode(sample_payload: EmailReceiptPayload, tmp_path: Path):
    receipts_dir = tmp_path / "mock_receipts"
    service = EmailService(mode="mock", receipts_dir=str(receipts_dir))

    result = service.send_receipt(sample_payload)
    assert result is True
    assert len(service.dispatched_receipts) == 1

    saved_file = receipts_dir / f"{sample_payload.receipt_id}.html"
    assert saved_file.exists()
    assert "Jordan Bell" in saved_file.read_text(encoding="utf-8")
