"""PayPal REST API Client supporting OAuth2 authentication, v2 Orders API, and offline mock sandbox."""

from __future__ import annotations

import base64
import logging
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
import requests

from paypal_donations.models import (
    CreateOrderRequest,
    CurrencyCode,
    DonationStatus,
    OrderCreationResult,
)

logger = logging.getLogger(__name__)


class PayPalClientError(Exception):
    """Raised when a PayPal API call fails."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}


class PayPalClient:
    """Client for PayPal v2 Checkout Orders API with automatic sandbox mock capability."""

    SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
    LIVE_BASE_URL = "https://api-m.paypal.com"

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        mode: str = "mock",
        base_url: Optional[str] = None,
    ):
        """Initialize PayPal client.

        Args:
            client_id: PayPal application client ID.
            client_secret: PayPal application secret.
            mode: 'mock' (offline deterministic engine), 'sandbox', or 'live'.
            base_url: Override base URL if needed.
        """
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.mode = mode.lower().strip()

        # If credentials are not provided or set to placeholders, default to mock mode
        if not self.client_id or not self.client_secret or "your_paypal" in self.client_id:
            self.mode = "mock"

        if base_url:
            self.base_url = base_url
        elif self.mode == "live":
            self.base_url = self.LIVE_BASE_URL
        else:
            self.base_url = self.SANDBOX_BASE_URL

        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

        # In-memory mock store for mock mode
        self._mock_orders: Dict[str, Dict[str, Any]] = {}

    @property
    def is_mock(self) -> bool:
        """Returns True if the client is running in offline mock mode."""
        return self.mode == "mock"

    def get_access_token(self) -> str:
        """Retrieve a valid OAuth2 Bearer token from PayPal or cache."""
        if self.is_mock:
            return "mock-access-token-authenticated"

        now = time.time()
        if self._token and now < self._token_expires_at - 60:
            return self._token

        auth_header = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("utf-8")
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Accept": "application/json",
            "Accept-Language": "en_US",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "client_credentials"}

        try:
            resp = requests.post(
                f"{self.base_url}/v1/oauth2/token",
                headers=headers,
                data=data,
                timeout=15,
            )
            if resp.status_code != 200:
                raise PayPalClientError(
                    f"OAuth authentication failed: {resp.text}",
                    status_code=resp.status_code,
                    response_data=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {},
                )
            payload = resp.json()
            self._token = payload["access_token"]
            expires_in = payload.get("expires_in", 3600)
            self._token_expires_at = now + expires_in
            return self._token
        except requests.RequestException as e:
            raise PayPalClientError(f"Network error during PayPal OAuth authentication: {e}") from e

    def create_order(self, request: CreateOrderRequest, return_url: Optional[str] = None, cancel_url: Optional[str] = None) -> OrderCreationResult:
        """Create a PayPal v2 Checkout Order for a one-time donation.

        Args:
            request: Donation order request specifications.
            return_url: URL to return to on donor checkout approval.
            cancel_url: URL to return to if donor cancels.

        Returns:
            OrderCreationResult with order_id and approval metadata.
        """
        if self.is_mock:
            mock_id = f"ORDER-MOCK-{uuid.uuid4().hex[:12].upper()}"
            self._mock_orders[mock_id] = {
                "order_id": mock_id,
                "status": "CREATED",
                "amount": request.amount,
                "currency": request.currency,
                "donor_name": request.donor_name,
                "donor_email": request.donor_email,
                "dedication": request.dedication,
                "is_anonymous": request.is_anonymous,
                "created_at": time.time(),
            }
            return OrderCreationResult(
                order_id=mock_id,
                status="CREATED",
                amount=request.amount,
                currency=request.currency,
                approval_url=f"https://www.sandbox.paypal.com/checkoutnow?token={mock_id}",
                mode="mock",
            )

        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

        order_payload: Dict[str, Any] = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": f"donation-{uuid.uuid4().hex[:8]}",
                    "description": f"Donation from {request.donor_name}",
                    "custom_id": f"{request.donor_email}|anon={request.is_anonymous}",
                    "amount": {
                        "currency_code": request.currency.value,
                        "value": f"{request.amount:.2f}",
                    },
                }
            ],
            "application_context": {
                "brand_name": "Donation Support",
                "landing_page": "NO_PREFERENCE",
                "user_action": "PAY_NOW",
                "return_url": return_url or "https://example.com/donation-complete",
                "cancel_url": cancel_url or "https://example.com/donation-cancel",
            },
        }

        try:
            resp = requests.post(
                f"{self.base_url}/v2/checkout/orders",
                headers=headers,
                json=order_payload,
                timeout=15,
            )
            if resp.status_code not in (200, 201):
                raise PayPalClientError(
                    f"PayPal order creation failed: {resp.text}",
                    status_code=resp.status_code,
                    response_data=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {},
                )

            data = resp.json()
            order_id = data["id"]
            approval_url = None
            for link in data.get("links", []):
                if link.get("rel") == "approve":
                    approval_url = link.get("href")
                    break

            return OrderCreationResult(
                order_id=order_id,
                status=data.get("status", "CREATED"),
                amount=request.amount,
                currency=request.currency,
                approval_url=approval_url,
                mode=self.mode,
            )
        except requests.RequestException as e:
            raise PayPalClientError(f"Network failure calling PayPal create_order: {e}") from e

    def capture_order(self, order_id: str) -> Dict[str, Any]:
        """Capture payment for an approved PayPal order.

        Args:
            order_id: The order ID returned from create_order.

        Returns:
            Dictionary containing capture details, capture_id, and status.
        """
        if self.is_mock:
            if order_id not in self._mock_orders:
                raise PayPalClientError(f"Mock order {order_id} not found", status_code=404)

            mock_order = self._mock_orders[order_id]
            if mock_order.get("status") == "COMPLETED":
                raise PayPalClientError(f"Mock order {order_id} has already been captured", status_code=422)

            capture_id = f"CAP-MOCK-{uuid.uuid4().hex[:12].upper()}"
            mock_order["status"] = "COMPLETED"
            mock_order["capture_id"] = capture_id

            return {
                "id": order_id,
                "status": "COMPLETED",
                "capture_id": capture_id,
                "amount": f"{mock_order['amount']:.2f}",
                "currency": mock_order["currency"].value if hasattr(mock_order["currency"], "value") else str(mock_order["currency"]),
                "payer": {
                    "email_address": mock_order.get("donor_email"),
                    "name": {"given_name": mock_order.get("donor_name", "Supporter")},
                },
                "order_metadata": mock_order,
            }

        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

        try:
            resp = requests.post(
                f"{self.base_url}/v2/checkout/orders/{order_id}/capture",
                headers=headers,
                json={},
                timeout=15,
            )
            if resp.status_code not in (200, 201):
                raise PayPalClientError(
                    f"PayPal order capture failed: {resp.text}",
                    status_code=resp.status_code,
                    response_data=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {},
                )

            data = resp.json()
            capture_id = None
            amount_val = "0.00"
            curr_code = "USD"

            purchase_units = data.get("purchase_units", [])
            if purchase_units:
                payments = purchase_units[0].get("payments", {})
                captures = payments.get("captures", [])
                if captures:
                    capture_id = captures[0].get("id")
                    amt = captures[0].get("amount", {})
                    amount_val = amt.get("value", "0.00")
                    curr_code = amt.get("currency_code", "USD")

            payer = data.get("payer", {})
            return {
                "id": data.get("id", order_id),
                "status": data.get("status", "COMPLETED"),
                "capture_id": capture_id or f"CAP-{order_id}",
                "amount": amount_val,
                "currency": curr_code,
                "payer": payer,
            }
        except requests.RequestException as e:
            raise PayPalClientError(f"Network failure capturing PayPal order: {e}") from e
