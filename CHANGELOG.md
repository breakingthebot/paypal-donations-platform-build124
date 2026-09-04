# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-09-04

### Added
- **Recurring Pledges & Monthly Subscription Engine**:
  - Full support for sustaining donor subscriptions via PayPal Subscriptions API (`/v1/billing/subscriptions`).
  - Frontend "Give Once" vs "Give Monthly" frequency switch with dynamic button labels and modal feedback.
  - Pydantic models: `CreateSubscriptionRequest`, `SubscriptionResult`, `SubscriptionRecord`, and `RecurringMetrics`.
- **Database Schema Expansion (`subscriptions` table)**:
  - Persistent SQLite table with indexing on `subscription_id`, `donor_email`, and `status`.
  - Repository methods: `create_subscription`, `get_subscription`, `update_subscription_status`, `list_subscriptions`, and `get_recurring_metrics`.
- **Monthly Recurring Revenue (MRR) Analytics Engine**:
  - Computes active sustaining supporter counts and currency-segmented monthly recurring revenue.
  - Aggregates metrics available via `GET /api/donations/recurring-stats` and CLI.
- **Subscription Webhook Handlers**:
  - `BILLING.SUBSCRIPTION.ACTIVATED`: Marks subscription state as `ACTIVE`.
  - `BILLING.SUBSCRIPTION.CANCELLED`: Marks subscription state as `CANCELLED`.
  - `PAYMENT.SALE.COMPLETED`: Automatically generates a completed `DonationRecord` for each billing cycle payment and delivers an official tax receipt email.
- **REST Endpoints for Recurring Pledges**:
  - `POST /api/donations/create-subscription`: Creates a recurring pledge agreement.
  - `GET /api/donations/recurring-stats`: Exposes active subscriber counts and MRR.
  - `GET /api/admin/subscriptions`: Administrative endpoint to filter and audit subscriptions.
  - `POST /api/admin/subscriptions/{id}/cancel`: Safely cancels an active subscription with reason notes.
- **New CLI Subscriptions Commands**:
  - `paypal-donations subscriptions list`: Displays formatted table of active and cancelled pledges.
  - `paypal-donations subscriptions stats`: Renders a Rich panel with total MRR (USD) and active sustainers.
  - `paypal-donations subscriptions cancel`: Cancels an active subscription directly from the terminal.
- **Comprehensive Subscriptions Test Suite**:
  - 12 new unit and integration tests covering PayPal client subscriptions, repository CRUD & MRR metrics, webhook activation/cancellation/sales cycles, API endpoints, and CLI commands (45 total passing tests).

## [1.1.0] - 2026-09-04

### Added
- **PayPal Webhook Verification Engine (`WebhookManager`)**: Cryptographic signature validation verifying transmission headers (`PAYPAL-AUTH-ALGO`, `PAYPAL-CERT-URL`, `PAYPAL-TRANSMISSION-SIG`, etc.) with domain-whitelisted HTTPS certificate verification preventing SSRF attacks.
- **Asynchronous Event Handlers**:
  - `PAYMENT.CAPTURE.COMPLETED`: Ensures donations are recorded as completed and dispatches receipts if not yet sent.
  - `PAYMENT.CAPTURE.REFUNDED`: Transitions donation status to `REFUNDED`, appends reason to donor notes, and automatically delivers a formal **Refund Confirmation Receipt**.
  - `PAYMENT.CAPTURE.DENIED` / `CHECKOUT.ORDER.VOIDED`: Transitions status to `FAILED`.
- **Persistent Webhook Audit Log**: SQLite `webhook_events` table for audit trails and idempotent deduplication of incoming events.
- **REST Webhook & Refund Endpoints**:
  - `POST /api/webhooks/paypal`: Real-time asynchronous PayPal webhook receiver.
  - `POST /api/admin/donations/{order_id}/refund`: Administrative route to refund donations.
  - `GET /api/admin/webhooks`: Audit inspection endpoint for received webhooks.
- **Refund Notice Templates**: Responsive HTML and plaintext refund receipt templates in `EmailService`.
- **New CLI Commands**:
  - `paypal-donations webhooks list`: Terminal table of recent webhook notifications.
  - `paypal-donations donations refund`: Command to issue refunds and dispatch notifications.
- **Expanded Test Suite**: 9 new unit & integration tests covering webhook security, signature validation, idempotency, refund processing, and CLI commands (33 total passing tests).

## [1.0.0] - 2026-09-04

### Added
- **PayPal REST API Client (`PayPalClient`)**: Implementation of PayPal v2 Orders API (`/v2/checkout/orders` and `/capture`) with OAuth 2.0 access token caching, error handling, and a built-in deterministic offline mock sandbox for reproducible testing and zero-credential development.
- **SQLite Persistent Donor Store (`DonorRepository`)**: Auto-initializing schema tracking order IDs, capture IDs, donor names, email addresses, amounts, currencies, timestamps, anonymity flags, and receipt delivery status.
- **Aggregated Analytics & Reporting**: Calculation of total donations, totals grouped by currency, average donation, unique donor counts, and public donor rolls with anonymity masking.
- **Data Export**: Full export capabilities to both CSV and JSON formats from database records.
- **Automated Tax Receipt Email Service (`EmailService`)**: Responsive HTML and plaintext email receipt generation with Jinja2 templates, tax exemption disclosures (EIN/Tax ID), transaction reference numbers, custom dedications, and dual delivery modes (SMTP and offline mock storage).
- **FastAPI REST Application (`paypal_donations.api`)**: REST API endpoints for order creation, capture, configuration, donor wall, stats, admin inspections, and exports, with auto-generated Swagger UI (`/docs`).
- **Interactive Tailwind CSS Donation Portal (`GET /`)**: Responsive web interface with preset amount chips, custom amount inputs, multi-currency switcher, donor information collection, dedicated message support, live impact metrics, and recent supporter wall.
- **Installable Command Line Suite (`paypal-donations`)**: Click and Rich CLI offering `--version`, `serve`, `donations list`, `donations stats`, `donations export`, and `donations test-email` commands.
- **Comprehensive Test Suite**: 15 unit and integration tests covering PayPal client workflows, SQLite storage, receipt generation, FastAPI routes, and CLI commands.
- **GitHub Actions CI**: Automated continuous integration test matrix across Python 3.10, 3.11, and 3.12.
- **MIT License & Documentation**: Full project license, `.env.example`, architecture sequence diagrams, and visual UI preview.
