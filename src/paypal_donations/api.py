"""FastAPI REST API and interactive Donation Portal web application."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from paypal_donations import __version__
from paypal_donations.email_service import EmailService
from paypal_donations.models import (
    CaptureOrderRequest,
    CreateOrderRequest,
    CurrencyCode,
    DonationRecord,
    DonationStats,
    DonationStatus,
    EmailReceiptPayload,
    OrderCreationResult,
    PublicDonorEntry,
    RefundReceiptPayload,
    RefundRequest,
    RefundResult,
    WebhookEventRecord,
)
from paypal_donations.paypal_client import PayPalClient, PayPalClientError
from paypal_donations.repository import DonorRepository
from paypal_donations.webhooks import WebhookManager, WebhookVerificationError

# Initialize FastAPI application
app = FastAPI(
    title="PayPal Donation Platform API",
    version=__version__,
    description="REST API for one-time PayPal donations, automated email receipts, and persistent donor logs.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared platform state
_config = {
    "paypal_mode": os.getenv("PAYPAL_MODE", "mock"),
    "paypal_client_id": os.getenv("PAYPAL_CLIENT_ID", ""),
    "paypal_client_secret": os.getenv("PAYPAL_CLIENT_SECRET", ""),
    "paypal_currency": os.getenv("PAYPAL_CURRENCY", "USD"),
    "org_name": os.getenv("ORG_NAME", "Open Impact Foundation"),
    "org_email": os.getenv("ORG_EMAIL", "donations@openimpact.org"),
    "org_tax_id": os.getenv("ORG_TAX_ID", "501(c)(3)-849201"),
    "org_website": os.getenv("ORG_WEBSITE", "https://openimpact.org"),
    "database_path": os.getenv("DATABASE_PATH", "storage/donations.sqlite3"),
    "email_mode": os.getenv("EMAIL_MODE", "mock"),
    "smtp_host": os.getenv("SMTP_HOST", "localhost"),
    "smtp_port": int(os.getenv("SMTP_PORT", "587")),
    "smtp_username": os.getenv("SMTP_USERNAME", ""),
    "smtp_password": os.getenv("SMTP_PASSWORD", ""),
    "smtp_from_email": os.getenv("SMTP_FROM_EMAIL", "donations@openimpact.org"),
    "smtp_use_tls": os.getenv("SMTP_USE_TLS", "true").lower() == "true",
}

repo = DonorRepository(db_path=_config["database_path"])
paypal_client = PayPalClient(
    client_id=_config["paypal_client_id"],
    client_secret=_config["paypal_client_secret"],
    mode=_config["paypal_mode"],
)
email_service = EmailService(
    mode=_config["email_mode"],
    smtp_host=_config["smtp_host"],
    smtp_port=_config["smtp_port"],
    smtp_username=_config["smtp_username"],
    smtp_password=_config["smtp_password"],
    smtp_from_email=_config["smtp_from_email"],
    smtp_use_tls=_config["smtp_use_tls"],
)
webhook_manager = WebhookManager(
    paypal_client=paypal_client,
    repo=repo,
    email_service=email_service,
    webhook_id=os.getenv("PAYPAL_WEBHOOK_ID", "MOCK-WEBHOOK-ID"),
    org_metadata={
        "org_name": _config["org_name"],
        "org_email": _config["org_email"],
        "org_tax_id": _config["org_tax_id"],
        "org_website": _config["org_website"],
    },
)


@app.get("/health")
def health_check() -> Dict[str, Any]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": __version__,
        "paypal_mode": paypal_client.mode,
        "email_mode": email_service.mode,
        "database": str(repo.db_path),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/config")
def get_public_config() -> Dict[str, Any]:
    """Expose non-sensitive public configuration for frontend checkout rendering."""
    return {
        "paypal_mode": paypal_client.mode,
        "paypal_client_id": paypal_client.client_id if not paypal_client.is_mock else "",
        "default_currency": _config["paypal_currency"],
        "org_name": _config["org_name"],
        "org_email": _config["org_email"],
        "org_tax_id": _config["org_tax_id"],
        "org_website": _config["org_website"],
    }


@app.post("/api/donations/create-order", response_model=OrderCreationResult)
def create_donation_order(req: CreateOrderRequest) -> OrderCreationResult:
    """Initiate a PayPal donation checkout order."""
    try:
        result = paypal_client.create_order(req)
        # Store pending record in SQLite
        pending_record = DonationRecord(
            order_id=result.order_id,
            donor_name=req.donor_name,
            donor_email=str(req.donor_email),
            amount=req.amount,
            currency=req.currency,
            status=DonationStatus.PENDING,
            dedication=req.dedication,
            is_anonymous=req.is_anonymous,
            receipt_sent=False,
        )
        repo.create_donation(pending_record)
        return result
    except PayPalClientError as e:
        raise HTTPException(status_code=e.status_code or 500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create donation order: {e}")


@app.post("/api/donations/capture-order")
def capture_donation_order(req: CaptureOrderRequest) -> Dict[str, Any]:
    """Capture an authorized donation order and dispatch the confirmation receipt."""
    try:
        capture_res = paypal_client.capture_order(req.order_id)
        capture_id = capture_res.get("capture_id", f"CAP-{req.order_id}")

        existing = repo.get_by_order_id(req.order_id)
        donor_name = req.donor_name or (existing.donor_name if existing else "Generous Supporter")
        donor_email = req.donor_email or (existing.donor_email if existing else "donor@example.com")
        amount = Decimal(str(capture_res.get("amount", existing.amount if existing else "25.00")))
        curr_str = capture_res.get("currency", existing.currency.value if existing else "USD")
        currency = CurrencyCode(curr_str)
        dedication = req.dedication if req.dedication is not None else (existing.dedication if existing else None)
        is_anonymous = req.is_anonymous if req.is_anonymous is not None else (existing.is_anonymous if existing else False)

        if existing:
            # Update SQLite entry to COMPLETED
            with repo._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE donations
                    SET status = ?, capture_id = ?, amount = ?, currency = ?
                    WHERE order_id = ?
                    """,
                    (DonationStatus.COMPLETED.value, capture_id, str(amount), currency.value, req.order_id)
                )
                conn.commit()
            record = repo.get_by_order_id(req.order_id)
        else:
            new_record = DonationRecord(
                order_id=req.order_id,
                capture_id=capture_id,
                donor_name=donor_name,
                donor_email=donor_email,
                amount=amount,
                currency=currency,
                status=DonationStatus.COMPLETED,
                dedication=dedication,
                is_anonymous=is_anonymous,
                receipt_sent=False,
            )
            record = repo.create_donation(new_record)

        # Generate and dispatch confirmation email
        receipt_id = f"REC-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{req.order_id[-6:]}"
        receipt_payload = EmailReceiptPayload(
            receipt_id=receipt_id,
            order_id=req.order_id,
            capture_id=capture_id,
            donor_name=donor_name,
            donor_email=donor_email,
            amount=amount,
            currency=currency,
            date_formatted=datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC"),
            dedication=dedication,
            org_name=_config["org_name"],
            org_email=_config["org_email"],
            org_tax_id=_config["org_tax_id"],
            org_website=_config["org_website"],
        )
        sent = email_service.send_receipt(receipt_payload)
        if sent:
            repo.mark_receipt_sent(req.order_id)

        return {
            "status": "COMPLETED",
            "order_id": req.order_id,
            "capture_id": capture_id,
            "receipt_id": receipt_id,
            "amount": f"{amount:.2f}",
            "currency": currency.value,
            "receipt_sent": sent,
            "donor_name": "Anonymous" if is_anonymous else donor_name,
            "message": "Thank you! Your donation has been captured and a confirmation receipt was sent.",
        }
    except PayPalClientError as e:
        raise HTTPException(status_code=e.status_code or 400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to capture order: {e}")


@app.get("/api/donations/donors", response_model=List[PublicDonorEntry])
def get_donor_wall(limit: int = Query(20, ge=1, le=100)) -> List[PublicDonorEntry]:
    """Retrieve public donor wall entries with privacy protection."""
    return repo.get_public_wall(limit=limit)


@app.get("/api/donations/stats", response_model=DonationStats)
def get_donation_statistics() -> DonationStats:
    """Retrieve aggregated donation financial metrics and totals."""
    return repo.get_statistics()


@app.get("/api/admin/donations", response_model=List[DonationRecord])
def get_admin_donations(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
) -> List[DonationRecord]:
    """Admin endpoint to retrieve raw donation records."""
    return repo.list_donations(limit=limit, offset=offset, status=status)


@app.post("/api/webhooks/paypal")
async def handle_paypal_webhook(request: Request) -> Dict[str, Any]:
    """Asynchronous PayPal webhook receiver with cryptographic signature verification."""
    headers = dict(request.headers)
    body_bytes = await request.body()
    raw_body = body_bytes.decode("utf-8")

    try:
        result = webhook_manager.process_webhook_event(headers, raw_body)
        return result
    except WebhookVerificationError as e:
        logger.warning("PayPal webhook verification rejected: %s", e)
        raise HTTPException(status_code=400, detail=f"Webhook verification failed: {e}")
    except Exception as e:
        logger.error("Error processing PayPal webhook: %s", e)
        raise HTTPException(status_code=500, detail="Internal webhook processing error.")


@app.post("/api/admin/donations/{order_id}/refund", response_model=RefundResult)
def refund_donation(order_id: str, req: RefundRequest) -> RefundResult:
    """Admin endpoint to process and record a donation refund."""
    donation = repo.get_by_order_id(order_id)
    if not donation:
        raise HTTPException(status_code=404, detail=f"Donation with Order ID {order_id} not found.")

    if donation.status == DonationStatus.REFUNDED:
        raise HTTPException(status_code=400, detail="This donation has already been refunded.")

    refund_id = f"REF-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    updated = repo.record_refund(order_id=order_id, reason=req.reason)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update donation status to REFUNDED.")

    # Dispatch refund receipt
    refund_payload = RefundReceiptPayload(
        receipt_id=f"REF-{order_id[-6:]}",
        order_id=order_id,
        capture_id=donation.capture_id or order_id,
        refund_id=refund_id,
        donor_name=donation.donor_name,
        donor_email=donation.donor_email,
        amount=donation.amount,
        currency=donation.currency,
        date_formatted=datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC"),
        reason=req.reason,
        org_name=_config["org_name"],
        org_email=_config["org_email"],
        org_website=_config["org_website"],
    )
    sent = email_service.send_refund_receipt(refund_payload)

    return RefundResult(
        order_id=order_id,
        capture_id=donation.capture_id,
        refund_id=refund_id,
        amount=donation.amount,
        currency=donation.currency,
        status="REFUNDED",
        receipt_sent=sent,
        message=f"Donation {order_id} has been refunded and notice sent to {donation.donor_email}.",
    )


@app.get("/api/admin/webhooks", response_model=List[WebhookEventRecord])
def get_admin_webhooks(limit: int = Query(50, ge=1, le=200)) -> List[WebhookEventRecord]:
    """Retrieve audit log of received PayPal webhook notifications."""
    return repo.list_webhook_events(limit=limit)


@app.get("/api/admin/export")
def export_donations(format: str = Query("csv", pattern="^(csv|json)$")):
    """Export complete donation history to CSV or JSON."""
    if format == "csv":
        path = "storage/exports/donations.csv"
        repo.export_csv(path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=donations_export.csv"},
        )
    else:
        path = "storage/exports/donations.json"
        repo.export_json(path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=donations_export.json"},
        )


@app.get("/", response_class=HTMLResponse)
def serve_donation_portal() -> str:
    """Render the responsive PayPal Donation Portal interface."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_config["org_name"]} &mdash; Donate Today</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Plus Jakarta Sans', sans-serif; }}
    .glass-card {{
      background: rgba(255, 255, 255, 0.95);
      backdrop-filter: blur(12px);
      border: 1px solid rgba(226, 232, 240, 0.8);
    }}
    .glow-primary {{
      box-shadow: 0 10px 25px -5px rgba(2, 132, 199, 0.25);
    }}
  </style>
</head>
<body class="bg-slate-50 text-slate-900 min-h-screen flex flex-col antialiased selection:bg-sky-500 selection:text-white">

  <!-- Navigation Header -->
  <header class="w-full bg-white/80 backdrop-blur border-b border-slate-200 sticky top-0 z-40">
    <div class="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-sky-600 to-indigo-600 flex items-center justify-center text-white font-black text-xl shadow-md">
          &#10084;
        </div>
        <div>
          <span class="font-extrabold text-lg tracking-tight text-slate-900">{_config["org_name"]}</span>
          <span class="block text-xs text-sky-600 font-semibold tracking-wide uppercase">Donation Portal</span>
        </div>
      </div>
      <div class="flex items-center space-x-4">
        <div id="mode-badge" class="px-3 py-1 text-xs font-semibold rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
          PayPal API Active ({paypal_client.mode.upper()})
        </div>
        <a href="/docs" target="_blank" class="text-sm font-medium text-slate-600 hover:text-sky-600 transition-colors">
          REST API
        </a>
      </div>
    </div>
  </header>

  <!-- Main Hero & Donation Container -->
  <main class="flex-grow max-w-6xl mx-auto px-4 sm:px-6 py-10 w-full">
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-10 items-start">

      <!-- Left Column: Mission, Transparency & Donor Wall -->
      <div class="lg:col-span-6 space-y-8">
        <div>
          <div class="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-semibold bg-sky-50 text-sky-700 border border-sky-200 mb-4">
            <span class="w-2 h-2 rounded-full bg-sky-500 animate-pulse"></span>
            Verified Non-Profit Support
          </div>
          <h1 class="text-3xl sm:text-4xl font-extrabold tracking-tight text-slate-900 leading-tight">
            Empower communities through direct, transparent support.
          </h1>
          <p class="mt-4 text-base text-slate-600 leading-relaxed">
            Every contribution enables critical educational initiatives, open-source technology development, and relief projects worldwide. All donations are tax-deductible under Tax ID: <span class="font-semibold text-slate-800">{_config["org_tax_id"]}</span>.
          </p>
        </div>

        <!-- Live Impact Metrics -->
        <div class="grid grid-cols-3 gap-4 bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
          <div>
            <div class="text-xs font-medium text-slate-500 uppercase tracking-wider">Total Raised</div>
            <div id="stat-total-amount" class="text-2xl font-black text-slate-900 mt-1">$0.00</div>
          </div>
          <div>
            <div class="text-xs font-medium text-slate-500 uppercase tracking-wider">Donations</div>
            <div id="stat-donations-count" class="text-2xl font-black text-sky-600 mt-1">0</div>
          </div>
          <div>
            <div class="text-xs font-medium text-slate-500 uppercase tracking-wider">Supporters</div>
            <div id="stat-unique-donors" class="text-2xl font-black text-indigo-600 mt-1">0</div>
          </div>
        </div>

        <!-- Recent Donor Roll -->
        <div class="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">
          <div class="flex items-center justify-between mb-4">
            <h3 class="font-bold text-slate-900 text-base">Recent Supporters Wall</h3>
            <span class="text-xs text-slate-500 font-medium">Real-time log</span>
          </div>
          <div id="donor-wall-container" class="space-y-3 max-h-72 overflow-y-auto pr-1">
            <div class="text-center py-6 text-slate-400 text-sm">Loading verified donations...</div>
          </div>
        </div>
      </div>

      <!-- Right Column: Interactive PayPal Donation Box -->
      <div class="lg:col-span-6">
        <div class="glass-card rounded-3xl p-6 sm:p-8 shadow-xl border border-slate-200 relative overflow-hidden">
          <div class="absolute top-0 left-0 right-0 h-2 bg-gradient-to-r from-sky-500 via-indigo-500 to-amber-400"></div>

          <div class="flex items-center justify-between pb-6 border-b border-slate-100 mb-6">
            <div>
              <h2 class="text-xl font-bold text-slate-900">Make a Donation</h2>
              <p class="text-xs text-slate-500 mt-0.5">Secure, 1-click PayPal checkout</p>
            </div>
            <!-- Currency selector -->
            <select id="currency-select" class="text-sm font-semibold bg-slate-100 border border-slate-200 text-slate-700 rounded-lg px-3 py-1.5 focus:ring-2 focus:ring-sky-500 outline-none">
              <option value="USD" selected>USD ($)</option>
              <option value="EUR">EUR (&euro;)</option>
              <option value="GBP">GBP (&pound;)</option>
              <option value="CAD">CAD ($)</option>
              <option value="AUD">AUD ($)</option>
            </select>
          </div>

          <!-- Preset Amount Chips -->
          <div class="mb-6">
            <label class="block text-xs font-bold text-slate-700 uppercase tracking-wider mb-2">Select Donation Amount</label>
            <div class="grid grid-cols-4 gap-2.5">
              <button type="button" data-amount="10" class="amount-pill py-3 rounded-xl border-2 border-slate-200 text-slate-700 font-bold text-base hover:border-sky-500 hover:text-sky-600 transition-all text-center">$10</button>
              <button type="button" data-amount="25" class="amount-pill py-3 rounded-xl border-2 border-sky-500 bg-sky-50 text-sky-700 font-bold text-base hover:border-sky-600 transition-all text-center glow-primary">$25</button>
              <button type="button" data-amount="50" class="amount-pill py-3 rounded-xl border-2 border-slate-200 text-slate-700 font-bold text-base hover:border-sky-500 hover:text-sky-600 transition-all text-center">$50</button>
              <button type="button" data-amount="100" class="amount-pill py-3 rounded-xl border-2 border-slate-200 text-slate-700 font-bold text-base hover:border-sky-500 hover:text-sky-600 transition-all text-center">$100</button>
            </div>

            <!-- Custom Amount Input -->
            <div class="mt-3 relative rounded-xl">
              <div class="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400 font-bold text-sm" id="curr-symbol">$</div>
              <input type="number" id="custom-amount" min="1" step="0.50" placeholder="Other custom amount"
                class="block w-full pl-8 pr-12 py-3 bg-slate-50 border border-slate-200 rounded-xl text-slate-900 font-semibold focus:bg-white focus:border-sky-500 focus:ring-2 focus:ring-sky-200 outline-none transition-all text-sm">
              <div class="absolute inset-y-0 right-0 pr-3.5 flex items-center pointer-events-none text-xs font-semibold text-slate-400" id="curr-label">USD</div>
            </div>
          </div>

          <!-- Donor Details Form -->
          <div class="space-y-4 mb-6">
            <div>
              <label for="donor-name" class="block text-xs font-bold text-slate-700 uppercase tracking-wider mb-1.5">Full Name</label>
              <input type="text" id="donor-name" placeholder="Alex Morgan" required
                class="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm font-medium focus:bg-white focus:border-sky-500 focus:ring-2 focus:ring-sky-200 outline-none transition-all">
            </div>

            <div>
              <label for="donor-email" class="block text-xs font-bold text-slate-700 uppercase tracking-wider mb-1.5">Email Address (For Tax Receipt)</label>
              <input type="email" id="donor-email" placeholder="alex.morgan@example.com" required
                class="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm font-medium focus:bg-white focus:border-sky-500 focus:ring-2 focus:ring-sky-200 outline-none transition-all">
            </div>

            <div>
              <label for="donor-dedication" class="block text-xs font-bold text-slate-700 uppercase tracking-wider mb-1.5">Dedication / Note (Optional)</label>
              <input type="text" id="donor-dedication" placeholder="In honor of global open education..."
                class="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm font-medium focus:bg-white focus:border-sky-500 focus:ring-2 focus:ring-sky-200 outline-none transition-all">
            </div>

            <div class="flex items-center space-x-3 pt-1">
              <input type="checkbox" id="is-anonymous" class="w-4 h-4 text-sky-600 rounded border-slate-300 focus:ring-sky-500">
              <label for="is-anonymous" class="text-xs text-slate-600 font-medium">Keep my name anonymous on the public supporters wall</label>
            </div>
          </div>

          <!-- PayPal Checkout Action Section -->
          <div class="space-y-3">
            <button id="paypal-donate-btn" type="button"
              class="w-full py-4 px-6 rounded-2xl bg-[#ffc439] hover:bg-[#f6b92a] active:scale-[0.99] text-[#003087] font-black text-base shadow-md flex items-center justify-center space-x-2 transition-all cursor-pointer">
              <span>Donate with</span>
              <span class="italic font-extrabold text-lg tracking-tight text-[#003087]">Pay<span class="text-[#0079C1]">Pal</span></span>
            </button>

            <div id="checkout-feedback" class="hidden rounded-xl p-3.5 text-xs text-center"></div>
          </div>

          <div class="mt-5 flex items-center justify-center space-x-4 text-slate-400 text-xs font-medium">
            <span class="flex items-center gap-1">&#128274; 256-Bit SSL Encrypted</span>
            <span>&bull;</span>
            <span>Instant Tax Receipt</span>
            <span>&bull;</span>
            <span>PayPal Verified</span>
          </div>
        </div>
      </div>
    </div>
  </main>

  <!-- Success Modal Confirmation -->
  <div id="receipt-modal" class="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
    <div class="bg-white rounded-3xl max-w-md w-full p-8 shadow-2xl border border-slate-100 text-center transform transition-all animate-in fade-in zoom-in-95">
      <div class="w-16 h-16 mx-auto bg-emerald-100 text-emerald-600 rounded-2xl flex items-center justify-center text-3xl mb-4 font-bold">
        &#10003;
      </div>
      <h3 class="text-2xl font-black text-slate-900">Donation Successful!</h3>
      <p class="text-sm text-slate-600 mt-2">
        Thank you for your generous gift. Your transaction has been recorded and an official confirmation receipt was dispatched to your inbox.
      </p>

      <div class="bg-slate-50 border border-slate-200 rounded-xl p-4 my-6 text-left text-xs space-y-2 font-mono">
        <div class="flex justify-between"><span class="text-slate-500">Order ID:</span><span id="modal-order-id" class="font-bold text-slate-900"></span></div>
        <div class="flex justify-between"><span class="text-slate-500">Capture ID:</span><span id="modal-capture-id" class="font-bold text-slate-900"></span></div>
        <div class="flex justify-between"><span class="text-slate-500">Receipt No:</span><span id="modal-receipt-id" class="font-bold text-sky-600"></span></div>
        <div class="flex justify-between"><span class="text-slate-500">Amount:</span><span id="modal-amount" class="font-bold text-slate-900"></span></div>
      </div>

      <button id="modal-close-btn" class="w-full py-3 px-4 rounded-xl bg-slate-900 hover:bg-slate-800 text-white font-bold text-sm transition-all">
        Done
      </button>
    </div>
  </div>

  <!-- Footer -->
  <footer class="bg-white border-t border-slate-200 py-6 mt-12">
    <div class="max-w-6xl mx-auto px-4 text-center text-xs text-slate-500 space-y-2">
      <p>&copy; 2026 {_config["org_name"]}. All rights reserved. Registered non-profit EIN: {_config["org_tax_id"]}.</p>
      <p>Powered by PayPal v2 Checkout API &bull; Python FastAPI &bull; Automated Receipting</p>
    </div>
  </footer>

  <script>
    let selectedAmount = "25.00";
    let selectedCurrency = "USD";

    // Setup currency symbols
    const symbols = {{ "USD": "$", "EUR": "€", "GBP": "£", "CAD": "$", "AUD": "$" }};

    document.getElementById("currency-select").addEventListener("change", (e) => {{
      selectedCurrency = e.target.value;
      const sym = symbols[selectedCurrency] || "$";
      document.getElementById("curr-symbol").textContent = sym;
      document.getElementById("curr-label").textContent = selectedCurrency;
      document.querySelectorAll(".amount-pill").forEach(p => {{
        const amt = p.dataset.amount;
        p.textContent = `${{sym}}${{amt}}`;
      }});
    }});

    // Amount pill selection
    document.querySelectorAll(".amount-pill").forEach(btn => {{
      btn.addEventListener("click", () => {{
        document.querySelectorAll(".amount-pill").forEach(b => {{
          b.classList.remove("border-sky-500", "bg-sky-50", "text-sky-700", "glow-primary");
          b.classList.add("border-slate-200", "text-slate-700");
        }});
        btn.classList.remove("border-slate-200", "text-slate-700");
        btn.classList.add("border-sky-500", "bg-sky-50", "text-sky-700", "glow-primary");
        selectedAmount = btn.dataset.amount;
        document.getElementById("custom-amount").value = "";
      }});
    }});

    document.getElementById("custom-amount").addEventListener("input", (e) => {{
      if (e.target.value) {{
        selectedAmount = parseFloat(e.target.value).toFixed(2);
        document.querySelectorAll(".amount-pill").forEach(b => {{
          b.classList.remove("border-sky-500", "bg-sky-50", "text-sky-700", "glow-primary");
          b.classList.add("border-slate-200", "text-slate-700");
        }});
      }}
    }});

    // Load donor wall and statistics
    async function loadData() {{
      try {{
        const [statsRes, wallRes] = await Promise.all([
          fetch("/api/donations/stats"),
          fetch("/api/donations/donors?limit=10")
        ]);

        if (statsRes.ok) {{
          const stats = await statsRes.json();
          let totalUsd = stats.total_amount_by_currency["USD"] || "0.00";
          document.getElementById("stat-total-amount").textContent = `$${{parseFloat(totalUsd).toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}})}}`;
          document.getElementById("stat-donations-count").textContent = stats.total_donations;
          document.getElementById("stat-unique-donors").textContent = stats.unique_donors;
        }}

        if (wallRes.ok) {{
          const donors = await wallRes.json();
          const wall = document.getElementById("donor-wall-container");
          if (donors.length === 0) {{
            wall.innerHTML = '<div class="text-center py-6 text-slate-400 text-sm">Be the first generous supporter to donate!</div>';
          }} else {{
            wall.innerHTML = donors.map(d => `
              <div class="flex items-center justify-between p-3 rounded-xl bg-slate-50 border border-slate-100 hover:bg-sky-50/50 transition-colors">
                <div class="flex items-center space-x-3">
                  <div class="w-8 h-8 rounded-full bg-sky-100 text-sky-700 flex items-center justify-center font-bold text-xs">
                    ${{d.donor_name.charAt(0)}}
                  </div>
                  <div>
                    <div class="text-sm font-semibold text-slate-800">${{d.donor_name}}</div>
                    ${{d.dedication ? `<div class="text-xs text-slate-500 italic truncate max-w-[200px]">"${{d.dedication}}"</div>` : ''}}
                  </div>
                </div>
                <div class="text-sm font-bold text-slate-900">${{symbols[d.currency] || '$'}}${{parseFloat(d.amount).toFixed(2)}}</div>
              </div>
            `).join('');
          }}
        }}
      }} catch (err) {{
        console.error("Failed to fetch donor data:", err);
      }}
    }}

    // PayPal Checkout trigger
    document.getElementById("paypal-donate-btn").addEventListener("click", async () => {{
      const feedback = document.getElementById("checkout-feedback");
      const btn = document.getElementById("paypal-donate-btn");
      const name = document.getElementById("donor-name").value.trim();
      const email = document.getElementById("donor-email").value.trim();
      const dedication = document.getElementById("donor-dedication").value.trim();
      const isAnonymous = document.getElementById("is-anonymous").checked;

      if (!name) {{
        feedback.className = "rounded-xl p-3 bg-rose-50 text-rose-700 border border-rose-200 block";
        feedback.textContent = "Please enter your full name.";
        return;
      }}
      if (!email || !email.includes("@")) {{
        feedback.className = "rounded-xl p-3 bg-rose-50 text-rose-700 border border-rose-200 block";
        feedback.textContent = "Please enter a valid email address for your receipt.";
        return;
      }}
      const amtNum = parseFloat(selectedAmount);
      if (isNaN(amtNum) || amtNum <= 0) {{
        feedback.className = "rounded-xl p-3 bg-rose-50 text-rose-700 border border-rose-200 block";
        feedback.textContent = "Please select or enter a valid donation amount.";
        return;
      }}

      // Start checkout state
      btn.disabled = true;
      btn.classList.add("opacity-70", "cursor-wait");
      feedback.className = "rounded-xl p-3 bg-sky-50 text-sky-700 border border-sky-200 block";
      feedback.textContent = "Connecting to PayPal v2 checkout...";

      try {{
        // Step 1: Create PayPal Order
        const createRes = await fetch("/api/donations/create-order", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            amount: amtNum,
            currency: selectedCurrency,
            donor_name: name,
            donor_email: email,
            dedication: dedication || null,
            is_anonymous: isAnonymous
          }})
        }});

        if (!createRes.ok) {{
          const err = await createRes.json();
          throw new Error(err.detail || "Order creation failed.");
        }}

        const orderData = await createRes.json();
        feedback.textContent = `Order authorized (${{orderData.order_id}}). Capturing payment & dispatching receipt...`;

        // Step 2: Capture PayPal Order
        const capRes = await fetch("/api/donations/capture-order", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            order_id: orderData.order_id,
            donor_name: name,
            donor_email: email,
            dedication: dedication || null,
            is_anonymous: isAnonymous
          }})
        }});

        if (!capRes.ok) {{
          const err = await capRes.json();
          throw new Error(err.detail || "Order capture failed.");
        }}

        const captureData = await capRes.json();

        // Show Success Modal
        document.getElementById("modal-order-id").textContent = captureData.order_id;
        document.getElementById("modal-capture-id").textContent = captureData.capture_id;
        document.getElementById("modal-receipt-id").textContent = captureData.receipt_id;
        document.getElementById("modal-amount").textContent = `${{captureData.currency}} $${{captureData.amount}}`;
        document.getElementById("receipt-modal").classList.remove("hidden");

        feedback.className = "hidden";
        // Refresh stats and wall
        loadData();

      }} catch (err) {{
        feedback.className = "rounded-xl p-3 bg-rose-50 text-rose-700 border border-rose-200 block";
        feedback.textContent = `Error: ${{err.message}}`;
      }} finally {{
        btn.disabled = false;
        btn.classList.remove("opacity-70", "cursor-wait");
      }}
    }});

    document.getElementById("modal-close-btn").addEventListener("click", () => {{
      document.getElementById("receipt-modal").classList.add("hidden");
    }});

    // Initialize on load
    loadData();
  </script>
</body>
</html>"""
