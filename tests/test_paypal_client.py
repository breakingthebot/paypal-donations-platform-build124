"""Unit tests for PayPalClient."""

from decimal import Decimal
import pytest

from paypal_donations.models import CreateOrderRequest, CurrencyCode
from paypal_donations.paypal_client import PayPalClient, PayPalClientError


def test_paypal_client_defaults_to_mock():
    client = PayPalClient(client_id="", client_secret="", mode="mock")
    assert client.is_mock is True
    assert client.get_access_token() == "mock-access-token-authenticated"


def test_paypal_client_create_mock_order():
    client = PayPalClient(mode="mock")
    req = CreateOrderRequest(
        amount=Decimal("50.00"),
        currency=CurrencyCode.USD,
        donor_name="Jane Supporter",
        donor_email="jane@example.org",
        dedication="In memory of John",
        is_anonymous=False,
    )
    result = client.create_order(req)
    assert result.order_id.startswith("ORDER-MOCK-")
    assert result.status == "CREATED"
    assert result.amount == Decimal("50.00")
    assert result.currency == CurrencyCode.USD
    assert "paypal.com" in result.approval_url


def test_paypal_client_capture_mock_order():
    client = PayPalClient(mode="mock")
    req = CreateOrderRequest(
        amount=Decimal("75.50"),
        currency=CurrencyCode.USD,
        donor_name="Bob Builder",
        donor_email="bob@example.org",
    )
    order = client.create_order(req)
    capture = client.capture_order(order.order_id)

    assert capture["status"] == "COMPLETED"
    assert capture["id"] == order.order_id
    assert capture["capture_id"].startswith("CAP-MOCK-")
    assert capture["amount"] == "75.50"


def test_paypal_client_duplicate_capture_raises_error():
    client = PayPalClient(mode="mock")
    req = CreateOrderRequest(
        amount=Decimal("15.00"),
        currency=CurrencyCode.USD,
        donor_name="Double Donator",
        donor_email="double@example.org",
    )
    order = client.create_order(req)
    client.capture_order(order.order_id)

    with pytest.raises(PayPalClientError) as exc_info:
        client.capture_order(order.order_id)
    assert "already been captured" in str(exc_info.value)


def test_paypal_client_nonexistent_order_capture():
    client = PayPalClient(mode="mock")
    with pytest.raises(PayPalClientError) as exc_info:
        client.capture_order("NON-EXISTENT-ORDER")
    assert exc_info.value.status_code == 404
