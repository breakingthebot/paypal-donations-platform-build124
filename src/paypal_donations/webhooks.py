"""PayPal Webhooks verification engine, replay attack prevention, and event processor."""

from __future__ import annotations

import json
import logging
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
import requests

from paypal_donations.email_service import EmailService
from paypal_donations.models import (
    CurrencyCode,
    DonationRecord,
    DonationStatus,
    EmailReceiptPayload,
    RefundReceiptPayload,
    WebhookEventRecord,
)
from paypal_donations.paypal_client import PayPalClient
from paypal_donations.repository import DonorRepository

logger = logging.getLogger(__name__)


class WebhookVerificationError(Exception):
    """Raised when PayPal webhook signature verification fails."""
    pass


class WebhookManager:
    """Manages PayPal webhook verification, idempotent processing, and event dispatch."""

    TRUSTED_CERT_HOSTS = ("api.paypal.com", "api-m.paypal.com", "api-m.sandbox.paypal.com")

    def __init__(
        self,
        paypal_client: PayPalClient,
        repo: DonorRepository,
        email_service: EmailService,
        webhook_id: Optional[str] = None,
        org_metadata: Optional[Dict[str, str]] = None,
    ):
        self.paypal_client = paypal_client
        self.repo = repo
        self.email_service = email_service
        self.webhook_id = webhook_id or "MOCK-WEBHOOK-ID"
        self.org_metadata = org_metadata or {
            "org_name": "Open Impact Foundation",
            "org_email": "donations@openimpact.org",
            "org_tax_id": "501(c)(3)-849201",
            "org_website": "https://openimpact.org",
        }

    def verify_headers_security(self, headers: Dict[str, str]) -> None:
        """Validate presence of required PayPal transmission headers and cert domain safety."""
        cert_url = headers.get("paypal-cert-url") or headers.get("PAYPAL-CERT-URL")
        if not cert_url:
            raise WebhookVerificationError("Missing required PAYPAL-CERT-URL header")

        parsed = urllib.parse.urlparse(cert_url)
        if parsed.scheme != "https":
            raise WebhookVerificationError("PAYPAL-CERT-URL must use HTTPS protocol")

        hostname = (parsed.hostname or "").lower()
        if not (hostname.endswith(".paypal.com") or hostname in self.TRUSTED_CERT_HOSTS):
            raise WebhookVerificationError(f"Untrusted cert host: {hostname}")

    def verify_signature(self, headers: Dict[str, str], raw_body: str) -> bool:
        """Verify the cryptographic authenticity of a PayPal webhook event."""
        if self.paypal_client.is_mock:
            # In mock mode, check that transmission headers exist
            sig = headers.get("paypal-transmission-sig") or headers.get("PAYPAL-TRANSMISSION-SIG")
            if not sig or sig == "INVALID_SIGNATURE":
                return False
            return True

        self.verify_headers_security(headers)

        token = self.paypal_client.get_access_token()
        url = f"{self.paypal_client.base_url}/v1/notifications/verify-webhook-signature"
        auth_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        verification_payload = {
            "auth_algo": headers.get("paypal-auth-algo") or headers.get("PAYPAL-AUTH-ALGO"),
            "cert_url": headers.get("paypal-cert-url") or headers.get("PAYPAL-CERT-URL"),
            "transmission_id": headers.get("paypal-transmission-id") or headers.get("PAYPAL-TRANSMISSION-ID"),
            "transmission_sig": headers.get("paypal-transmission-sig") or headers.get("PAYPAL-TRANSMISSION-SIG"),
            "transmission_time": headers.get("paypal-transmission-time") or headers.get("PAYPAL-TRANSMISSION-TIME"),
            "webhook_id": self.webhook_id,
            "webhook_event": json.loads(raw_body),
        }

        try:
            resp = requests.post(url, headers=auth_headers, json=verification_payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("verification_status") == "SUCCESS"
            return False
        except Exception as e:
            logger.error("Error contacting PayPal verify-webhook-signature API: %s", e)
            return False

    def process_webhook_event(self, headers: Dict[str, str], raw_body: str) -> Dict[str, Any]:
        """Verify, deduplicate, and process a PayPal webhook event payload."""
        # 1. Verify authenticity
        if not self.verify_signature(headers, raw_body):
            raise WebhookVerificationError("Webhook signature validation failed.")

        event = json.loads(raw_body)
        event_id = event.get("id") or f"WH-{datetime.now(timezone.utc).timestamp()}"
        event_type = event.get("event_type", "UNKNOWN")
        resource = event.get("resource", {})
        resource_id = resource.get("id", "UNKNOWN")

        # 2. Idempotency check: avoid duplicate event execution
        if self.repo.is_webhook_event_processed(event_id):
            logger.info("Ignoring duplicate webhook event: %s (%s)", event_id, event_type)
            return {
                "status": "DUPLICATE",
                "event_id": event_id,
                "event_type": event_type,
                "message": "Event has already been processed.",
            }

        # 3. Route event to corresponding handler
        result_details: Dict[str, Any] = {}

        if event_type == "PAYMENT.CAPTURE.COMPLETED":
            result_details = self._handle_capture_completed(resource)
        elif event_type in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.REFUND.COMPLETED"):
            result_details = self._handle_capture_refunded(resource)
        elif event_type in ("PAYMENT.CAPTURE.DENIED", "CHECKOUT.ORDER.VOIDED"):
            result_details = self._handle_capture_denied(resource)
        elif event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
            sub_id = resource.get("id")
            self.repo.update_subscription_status(sub_id, "ACTIVE")
            result_details = {"action": "subscription_activated", "subscription_id": sub_id}
        elif event_type == "BILLING.SUBSCRIPTION.CANCELLED":
            sub_id = resource.get("id")
            self.repo.update_subscription_status(sub_id, "CANCELLED")
            result_details = {"action": "subscription_cancelled", "subscription_id": sub_id}
        elif event_type == "PAYMENT.SALE.COMPLETED":
            result_details = self._handle_recurring_sale_completed(resource)
        else:
            result_details = {"action": "ignored_unhandled_event_type"}

        # 4. Record event in audit log
        self.repo.record_webhook_event(
            event_id=event_id,
            event_type=event_type,
            resource_id=resource_id,
            status="PROCESSED",
            payload_json=raw_body,
        )

        return {
            "status": "SUCCESS",
            "event_id": event_id,
            "event_type": event_type,
            "resource_id": resource_id,
            "details": result_details,
        }

    def _handle_capture_completed(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        """Handle PAYMENT.CAPTURE.COMPLETED event."""
        capture_id = resource.get("id")
        # Try to find corresponding donation
        donation = self.repo.get_by_capture_id(capture_id)
        if not donation:
            # Check supplementary data for order_id
            order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
            if order_id:
                donation = self.repo.get_by_order_id(order_id)

        if donation and donation.status != DonationStatus.COMPLETED:
            with self.repo._get_connection() as conn:
                conn.execute(
                    "UPDATE donations SET status = 'COMPLETED', capture_id = ? WHERE id = ?",
                    (capture_id, donation.id),
                )
                conn.commit()

            # Ensure receipt email is dispatched if missing
            if not donation.receipt_sent:
                receipt_payload = EmailReceiptPayload(
                    receipt_id=f"REC-{donation.order_id[-6:]}",
                    order_id=donation.order_id,
                    capture_id=capture_id or donation.order_id,
                    donor_name=donation.donor_name,
                    donor_email=donation.donor_email,
                    amount=donation.amount,
                    currency=donation.currency,
                    date_formatted=datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC"),
                    dedication=donation.dedication,
                    org_name=self.org_metadata["org_name"],
                    org_email=self.org_metadata["org_email"],
                    org_tax_id=self.org_metadata["org_tax_id"],
                    org_website=self.org_metadata["org_website"],
                )
                sent = self.email_service.send_receipt(receipt_payload)
                if sent:
                    self.repo.mark_receipt_sent(donation.order_id)

            return {"action": "marked_completed", "donation_id": donation.id}

        return {"action": "already_completed_or_unmatched", "capture_id": capture_id}

    def _handle_capture_refunded(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        """Handle PAYMENT.CAPTURE.REFUNDED event."""
        refund_id = resource.get("id", f"REF-{datetime.now(timezone.utc).timestamp()}")
        capture_id = resource.get("capture_id")
        amount_data = resource.get("amount", {})
        refund_amount = Decimal(str(amount_data.get("value", "0.00")))
        note_to_payer = resource.get("note_to_payer", "Donation refunded upon request")

        donation = None
        if capture_id:
            donation = self.repo.get_by_capture_id(capture_id)

        if not donation:
            # Fallback check by order id if passed in custom id
            custom_id = resource.get("custom_id")
            if custom_id:
                donation = self.repo.get_by_order_id(custom_id)

        if donation:
            self.repo.record_refund(order_id=donation.order_id, reason=note_to_payer)

            # Dispatch official refund notice
            refund_payload = RefundReceiptPayload(
                receipt_id=f"REF-{donation.order_id[-6:]}",
                order_id=donation.order_id,
                capture_id=donation.capture_id or donation.order_id,
                refund_id=refund_id,
                donor_name=donation.donor_name,
                donor_email=donation.donor_email,
                amount=refund_amount if refund_amount > 0 else donation.amount,
                currency=donation.currency,
                date_formatted=datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC"),
                reason=note_to_payer,
                org_name=self.org_metadata["org_name"],
                org_email=self.org_metadata["org_email"],
                org_website=self.org_metadata["org_website"],
            )
            self.email_service.send_refund_receipt(refund_payload)

            return {
                "action": "refund_processed",
                "order_id": donation.order_id,
                "refund_id": refund_id,
                "donor": donation.donor_name,
            }

        return {"action": "unmatched_donation_for_refund", "capture_id": capture_id}

    def _handle_capture_denied(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        """Handle PAYMENT.CAPTURE.DENIED event."""
        capture_id = resource.get("id")
        donation = self.repo.get_by_capture_id(capture_id) if capture_id else None
        if donation:
            with self.repo._get_connection() as conn:
                conn.execute("UPDATE donations SET status = 'FAILED' WHERE id = ?", (donation.id,))
                conn.commit()
            return {"action": "marked_failed", "order_id": donation.order_id}
        return {"action": "unmatched_denied_event", "capture_id": capture_id}

    def _handle_recurring_sale_completed(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        """Handle recurring sale execution under a subscription."""
        sale_id = resource.get("id", f"SALE-{datetime.now(timezone.utc).timestamp()}")
        billing_agreement_id = resource.get("billing_agreement_id")
        amount_info = resource.get("amount", {})
        sale_amount = Decimal(str(amount_info.get("total", "25.00")))
        curr_str = amount_info.get("currency", "USD")

        sub = self.repo.get_subscription(billing_agreement_id) if billing_agreement_id else None
        donor_name = sub.donor_name if sub else "Recurring Sustainer"
        donor_email = sub.donor_email if sub else "donor@example.com"
        dedication = f"Recurring monthly support ({sub.subscription_id})" if sub else "Recurring subscription"
        is_anon = sub.is_anonymous if sub else False

        order_ref = f"REC-ORD-{sale_id[-8:]}"
        record = DonationRecord(
            order_id=order_ref,
            capture_id=sale_id,
            donor_name=donor_name,
            donor_email=donor_email,
            amount=sale_amount,
            currency=CurrencyCode(curr_str),
            status=DonationStatus.COMPLETED,
            dedication=dedication,
            is_anonymous=is_anon,
            receipt_sent=False,
        )
        self.repo.create_donation(record)

        # Dispatch tax receipt for the cycle payment
        receipt_payload = EmailReceiptPayload(
            receipt_id=f"REC-{sale_id[-8:]}",
            order_id=order_ref,
            capture_id=sale_id,
            donor_name=donor_name,
            donor_email=donor_email,
            amount=sale_amount,
            currency=CurrencyCode(curr_str),
            date_formatted=datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC"),
            dedication=dedication,
            org_name=self.org_metadata["org_name"],
            org_email=self.org_metadata["org_email"],
            org_tax_id=self.org_metadata["org_tax_id"],
            org_website=self.org_metadata["org_website"],
        )
        sent = self.email_service.send_receipt(receipt_payload)
        if sent:
            self.repo.mark_receipt_sent(order_ref)

        return {
            "action": "recurring_donation_recorded",
            "sale_id": sale_id,
            "billing_agreement_id": billing_agreement_id,
            "order_id": order_ref,
            "amount": str(sale_amount),
        }
