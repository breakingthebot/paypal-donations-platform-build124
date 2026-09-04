"""Automated confirmation receipt generator and email dispatch service."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional
import jinja2

from paypal_donations.models import EmailReceiptPayload, RefundReceiptPayload

logger = logging.getLogger(__name__)

HTML_RECEIPT_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Donation Receipt - {{ org_name }}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b; }
    .card { max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
    .header { background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%); color: #ffffff; padding: 32px 24px; text-align: center; }
    .header h1 { margin: 0 0 8px 0; font-size: 24px; font-weight: 700; letter-spacing: -0.025em; }
    .header p { margin: 0; opacity: 0.9; font-size: 14px; }
    .body { padding: 32px 24px; }
    .amount-box { background: #f0f9ff; border: 1px solid #bae6fd; border-radius: 8px; text-align: center; padding: 20px; margin-bottom: 24px; }
    .amount-val { font-size: 36px; font-weight: 800; color: #0369a1; }
    .amount-lbl { font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; margin-top: 4px; }
    .table-details { width: 100%; border-collapse: collapse; margin-bottom: 24px; }
    .table-details td { padding: 12px 0; border-bottom: 1px solid #f1f5f9; font-size: 14px; }
    .table-details td.label { color: #64748b; width: 40%; font-weight: 500; }
    .table-details td.val { color: #0f172a; text-align: right; font-weight: 600; }
    .dedication { background: #fdf4ff; border-left: 4px solid #c084fc; padding: 12px 16px; border-radius: 4px; margin-bottom: 24px; font-style: italic; color: #581c87; font-size: 14px; }
    .tax-notice { background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 6px; padding: 14px; font-size: 12px; color: #64748b; line-height: 1.5; text-align: center; }
    .footer { text-align: center; padding: 24px; font-size: 12px; color: #94a3b8; border-top: 1px solid #f1f5f9; }
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1>Thank You for Your Generosity!</h1>
      <p>Official Tax & Donation Receipt</p>
    </div>
    <div class="body">
      <p>Dear <strong>{{ donor_name }}</strong>,</p>
      <p>We gratefully acknowledge receipt of your gift supporting <strong>{{ org_name }}</strong>. Your contribution directly fuels our mission and community impact.</p>

      <div class="amount-box">
        <div class="amount-val">${{ "%.2f"|format(amount|float) }} {{ currency }}</div>
        <div class="amount-lbl">Total Contribution Received</div>
      </div>

      {% if dedication %}
      <div class="dedication">
        "{{ dedication }}"
      </div>
      {% endif %}

      <table class="table-details">
        <tr>
          <td class="label">Receipt Number</td>
          <td class="val">{{ receipt_id }}</td>
        </tr>
        <tr>
          <td class="label">PayPal Order ID</td>
          <td class="val">{{ order_id }}</td>
        </tr>
        <tr>
          <td class="label">Capture Transaction ID</td>
          <td class="val">{{ capture_id }}</td>
        </tr>
        <tr>
          <td class="label">Date & Time (UTC)</td>
          <td class="val">{{ date_formatted }}</td>
        </tr>
        <tr>
          <td class="label">Payment Status</td>
          <td class="val" style="color: #16a34a;">COMPLETED</td>
        </tr>
      </table>

      <div class="tax-notice">
        <strong>Tax Exemption & Deduction Disclosure</strong><br>
        {{ org_name }} is a registered non-profit organization (EIN / Tax ID: {{ org_tax_id }}). No goods or services were provided in exchange for this contribution. Please retain this receipt for your tax records.
      </div>
    </div>
    <div class="footer">
      {{ org_name }} &bull; {{ org_email }} &bull; <a href="{{ org_website }}" style="color: #0284c7; text-decoration: none;">{{ org_website }}</a>
    </div>
  </div>
</body>
</html>
"""

PLAINTEXT_RECEIPT_TEMPLATE = """================================================================
OFFICIAL DONATION CONFIRMATION RECEIPT
{{ org_name }} (Tax ID: {{ org_tax_id }})
================================================================

Dear {{ donor_name }},

Thank you for your generous gift to {{ org_name }}. Your contribution
is vital to furthering our ongoing mission.

RECEIPT DETAILS:
----------------------------------------------------------------
Receipt Number:   {{ receipt_id }}
Amount Paid:      ${{ "%.2f"|format(amount|float) }} {{ currency }}
Date (UTC):       {{ date_formatted }}
PayPal Order ID:  {{ order_id }}
Capture ID:       {{ capture_id }}
Status:           COMPLETED
{% if dedication %}
Dedication:       "{{ dedication }}"
{% endif %}
----------------------------------------------------------------

TAX DEDUCTION NOTICE:
{{ org_name }} certifies that no goods or services were provided in
whole or partial exchange for this charitable contribution. Keep this
receipt for your personal tax records.

Questions? Contact {{ org_email }} or visit {{ org_website }}.
================================================================
"""

HTML_REFUND_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Donation Refund Notice - {{ org_name }}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b; }
    .card { max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; }
    .header { background: linear-gradient(135deg, #e11d48 0%, #be123c 100%); color: #ffffff; padding: 28px 24px; text-align: center; }
    .header h1 { margin: 0; font-size: 22px; }
    .body { padding: 28px 24px; }
    .amount-box { background: #fff1f2; border: 1px solid #fecdd3; border-radius: 8px; text-align: center; padding: 18px; margin-bottom: 20px; }
    .amount-val { font-size: 32px; font-weight: 800; color: #be123c; }
    .table-details { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
    .table-details td { padding: 10px 0; border-bottom: 1px solid #f1f5f9; font-size: 14px; }
    .table-details td.label { color: #64748b; width: 40%; }
    .table-details td.val { text-align: right; font-weight: 600; }
    .footer { text-align: center; padding: 20px; font-size: 12px; color: #94a3b8; }
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1>Donation Refund Confirmation</h1>
    </div>
    <div class="body">
      <p>Dear <strong>{{ donor_name }}</strong>,</p>
      <p>This email confirms that your contribution to <strong>{{ org_name }}</strong> has been refunded.</p>
      <div class="amount-box">
        <div class="amount-val">${{ "%.2f"|format(amount|float) }} {{ currency }}</div>
        <div>Total Refund Amount</div>
      </div>
      <table class="table-details">
        <tr><td class="label">Refund ID</td><td class="val">{{ refund_id }}</td></tr>
        <tr><td class="label">Original Order ID</td><td class="val">{{ order_id }}</td></tr>
        <tr><td class="label">Original Capture ID</td><td class="val">{{ capture_id }}</td></tr>
        <tr><td class="label">Date Processed</td><td class="val">{{ date_formatted }}</td></tr>
        {% if reason %}<tr><td class="label">Reason</td><td class="val">{{ reason }}</td></tr>{% endif %}
      </table>
      <p style="font-size: 13px; color: #64748b; text-align: center;">Funds should return to your original payment method in 3-5 business days.</p>
    </div>
    <div class="footer">{{ org_name }} &bull; {{ org_email }} &bull; {{ org_website }}</div>
  </div>
</body>
</html>
"""

PLAINTEXT_REFUND_TEMPLATE = """================================================================
DONATION REFUND CONFIRMATION
{{ org_name }}
================================================================

Dear {{ donor_name }},

This email confirms that your contribution of ${{ "%.2f"|format(amount|float) }} {{ currency }}
to {{ org_name }} has been refunded.

REFUND DETAILS:
----------------------------------------------------------------
Refund Reference: {{ refund_id }}
Original Order:   {{ order_id }}
Capture ID:       {{ capture_id }}
Date Processed:   {{ date_formatted }}
{% if reason %}Reason:           {{ reason }}{% endif %}
----------------------------------------------------------------

Funds typically reflect in your original payment method within
3 to 5 business days.

Questions? Contact {{ org_email }} or visit {{ org_website }}.
================================================================
"""


class EmailService:
    """Service to render and transmit official donation receipts."""

    def __init__(
        self,
        mode: str = "mock",
        smtp_host: str = "localhost",
        smtp_port: int = 587,
        smtp_username: Optional[str] = None,
        smtp_password: Optional[str] = None,
        smtp_from_email: str = "donations@example.org",
        smtp_use_tls: bool = True,
        receipts_dir: str = "storage/receipts",
    ):
        self.mode = mode.lower().strip()
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username or ""
        self.smtp_password = smtp_password or ""
        self.smtp_from_email = smtp_from_email
        self.smtp_use_tls = smtp_use_tls
        self.receipts_dir = Path(receipts_dir)

        if self.mode == "mock":
            self.receipts_dir.mkdir(parents=True, exist_ok=True)

        self._html_template = jinja2.Template(HTML_RECEIPT_TEMPLATE)
        self._text_template = jinja2.Template(PLAINTEXT_RECEIPT_TEMPLATE)
        self._html_refund_template = jinja2.Template(HTML_REFUND_TEMPLATE)
        self._text_refund_template = jinja2.Template(PLAINTEXT_REFUND_TEMPLATE)
        self.dispatched_receipts: List[Dict[str, Any]] = []

    def render_html(self, payload: EmailReceiptPayload) -> str:
        """Render HTML email body."""
        return self._html_template.render(payload.model_dump(mode="json"))

    def render_plaintext(self, payload: EmailReceiptPayload) -> str:
        """Render plaintext email body."""
        return self._text_template.render(payload.model_dump(mode="json"))

    def render_refund_html(self, payload: RefundReceiptPayload) -> str:
        """Render HTML refund notice body."""
        return self._html_refund_template.render(payload.model_dump(mode="json"))

    def render_refund_plaintext(self, payload: RefundReceiptPayload) -> str:
        """Render plaintext refund notice body."""
        return self._text_refund_template.render(payload.model_dump(mode="json"))

    def send_refund_receipt(self, payload: RefundReceiptPayload) -> bool:
        """Deliver the refund confirmation email via configured transport."""
        html_content = self.render_refund_html(payload)
        text_content = self.render_refund_plaintext(payload)
        subject = f"Refund Notice: Your donation to {payload.org_name} has been refunded"

        if self.mode == "mock" or not self.smtp_username:
            file_path = self.receipts_dir / f"REFUND-{payload.receipt_id}.html"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            self.dispatched_receipts.append({
                "recipient": payload.donor_email,
                "subject": subject,
                "receipt_id": f"REFUND-{payload.receipt_id}",
                "file_path": str(file_path),
                "payload": payload.model_dump(),
            })
            logger.info("Mock refund notice dispatched to %s", payload.donor_email)
            return True

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.smtp_from_email
        msg["To"] = payload.donor_email
        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                if self.smtp_use_tls:
                    server.starttls()
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.sendmail(self.smtp_from_email, [payload.donor_email], msg.as_string())
            logger.info("Live refund email delivered to %s via SMTP", payload.donor_email)
            return True
        except Exception as e:
            logger.error("Failed to deliver refund email via SMTP to %s: %e", payload.donor_email, e)
            return False

    def send_receipt(self, payload: EmailReceiptPayload) -> bool:
        """Deliver the receipt email via configured transport (mock disk or SMTP).

        Returns:
            True if dispatched successfully.
        """
        html_content = self.render_html(payload)
        text_content = self.render_plaintext(payload)
        subject = f"Receipt: Thank you for your ${payload.amount:.2f} donation to {payload.org_name}"

        if self.mode == "mock" or not self.smtp_username:
            # Save mock receipt to disk and memory
            file_path = self.receipts_dir / f"{payload.receipt_id}.html"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            self.dispatched_receipts.append({
                "recipient": payload.donor_email,
                "subject": subject,
                "receipt_id": payload.receipt_id,
                "file_path": str(file_path),
                "payload": payload.model_dump(),
            })
            logger.info("Mock receipt dispatched to %s (saved to %s)", payload.donor_email, file_path)
            return True

        # SMTP Dispatch
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.smtp_from_email
        msg["To"] = payload.donor_email
        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                if self.smtp_use_tls:
                    server.starttls()
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.sendmail(self.smtp_from_email, [payload.donor_email], msg.as_string())
            logger.info("Live receipt email delivered to %s via SMTP", payload.donor_email)
            return True
        except Exception as e:
            logger.error("Failed to deliver receipt email via SMTP to %s: %e", payload.donor_email, e)
            return False
