# Iterations & Git Commit Log

**Project**: PayPal Donation Platform (Build 124)  
**Repository**: [https://github.com/breakingthebot/paypal-donations-platform-build124](https://github.com/breakingthebot/paypal-donations-platform-build124)  
**Stack**: Python 3.12, FastAPI, Uvicorn, SQLite, Pydantic v2, Click, Rich, Jinja2, Pytest  

This document logs every incremental engineering iteration and git commit pushed to the public repository.

---

## Iteration Overview Table

| Iteration | Git Commit | Version | Focus / Summary | Tests Passed | Full Summary Archive |
| :---: | :---: | :---: | :--- | :---: | :--- |
| **01** | [`e0413ab`](https://github.com/breakingthebot/paypal-donations-platform-build124/commit/e0413ab) | `v1.0.0` | **Core PayPal v2 Integration, Automated Confirmation Receipts, Persistent SQLite Donor Log & Web Portal**<br>Complete PayPal Orders client with mock engine, SQLite database repository with anonymity masking, automated HTML/plaintext tax receipt mailer, FastAPI web backend, interactive Tailwind CSS donation portal, Click/Rich CLI suite (`paypal-donations`), and CI workflow. | 24 / 24 | [Iteration 01 Summary](docs/summaries/iteration_01_summary.md) |
| **02** | [`5f0ca38`](https://github.com/breakingthebot/paypal-donations-platform-build124/commit/5f0ca38) | `v1.1.0` | **PayPal Webhooks Verification, Asynchronous Event Processing & Automated Refund Lifecycle**<br>Cryptographic signature verification engine with SSRF domain whitelisting, SQLite `webhook_events` audit and idempotency store, asynchronous routing for capture and refund events, automated refund receipts, REST webhook/refund endpoints, and CLI administration suite. | 33 / 33 | [Iteration 02 Summary](docs/summaries/iteration_02_summary.md) |
| **03** | [`faf302c`](https://github.com/breakingthebot/paypal-donations-platform-build124/commit/faf302c) | `v1.2.0` | **Recurring Pledges & Monthly Sustainer Subscriptions, MRR Analytics Engine, Subscription Webhooks & CLI Management**<br>PayPal Subscriptions integration, SQLite `subscriptions` schema with multi-currency MRR calculation, automated cycle payment recording with tax receipt delivery via `PAYMENT.SALE.COMPLETED` webhooks, REST pledge/cancel endpoints, and `paypal-donations subscriptions` CLI suite. | 45 / 45 | [Iteration 03 Summary](docs/summaries/iteration_03_summary.md) |

---

## Chronological Iteration Entries

### Iteration 3: Recurring Pledges & Monthly Sustainer Subscriptions, MRR Analytics Engine, Subscription Webhooks & CLI Management
- **Git Commit**: [`faf302c`](https://github.com/breakingthebot/paypal-donations-platform-build124/commit/faf302c)
- **Tag / Version**: `v1.2.0`
- **Date**: 2026-09-04
- **Plain English Summary**:
  Expanded the platform into a monthly sustainer and subscription management platform. Added support for recurring pledges via PayPal Subscriptions API (`/v1/billing/subscriptions`), an SQLite `subscriptions` schema, Monthly Recurring Revenue (MRR) calculation grouped by currency, webhook lifecycle events (`BILLING.SUBSCRIPTION.ACTIVATED`, `CANCELLED`, and automated cycle payment recording via `PAYMENT.SALE.COMPLETED`), a "Give Monthly" toggle in the web portal, REST administration routes, and a terminal CLI command group (`paypal-donations subscriptions list`, `stats`, `cancel`).
- **Key Files Introduced / Modified**:
  - `src/paypal_donations/models.py`: Added `DonationFrequency`, `CreateSubscriptionRequest`, `SubscriptionResult`, `SubscriptionRecord`, and `RecurringMetrics`.
  - `src/paypal_donations/paypal_client.py`: Added subscription creation, cancellation, lookups, and mock state storage.
  - `src/paypal_donations/repository.py`: Added `subscriptions` table schema, CRUD operations, and MRR metrics calculations.
  - `src/paypal_donations/webhooks.py`: Handlers for `BILLING.SUBSCRIPTION.ACTIVATED`, `CANCELLED`, and recurring `PAYMENT.SALE.COMPLETED` donations.
  - `src/paypal_donations/api.py`: Added `/api/donations/create-subscription`, `/api/donations/recurring-stats`, admin routes, and frontend toggle.
  - `src/paypal_donations/cli.py`: Added `subscriptions` command group (`list`, `stats`, `cancel`).
  - `tests/test_subscriptions.py`: 12 unit & integration tests covering client, repository, webhooks, API, and CLI.
  - `docs/summaries/iteration_03_summary.md`: Iteration 3 technical archive.
- **Test Results**: 45 Pytest unit & integration tests passing (8.61s).

---

### Iteration 2: PayPal Webhooks Verification, Asynchronous Event Processing & Automated Refund Lifecycle
- **Git Commit**: [`5f0ca38`](https://github.com/breakingthebot/paypal-donations-platform-build124/commit/5f0ca38)
- **Tag / Version**: `v1.1.0`
- **Date**: 2026-09-04
- **Plain English Summary**:
  Introduced a PayPal Webhooks engine (`WebhookManager`) verifying transmission headers and cryptographic signatures with domain-whitelisted HTTPS certificate verification. Added an event deduplication mechanism backed by SQLite to prevent replay attacks. Built an automated refund lifecycle that updates donation statuses to `REFUNDED`, records reasons, adjusts financial aggregations, and automatically dispatches formal refund confirmation receipts via HTML/plaintext emails. Added REST endpoints (`POST /api/webhooks/paypal`, `POST /api/admin/donations/{id}/refund`, `GET /api/admin/webhooks`) and CLI management tools (`paypal-donations webhooks list`, `paypal-donations donations refund`).
- **Key Files Introduced / Modified**:
  - `src/paypal_donations/webhooks.py`: Webhook verification, security validation, and asynchronous event routing.
  - `src/paypal_donations/models.py`: Added `WebhookEventRecord`, `RefundRequest`, `RefundResult`, and `RefundReceiptPayload`.
  - `src/paypal_donations/repository.py`: Added `webhook_events` schema, idempotency lookup, and refund updates.
  - `src/paypal_donations/email_service.py`: Added refund receipt templates and dispatch methods.
  - `src/paypal_donations/api.py`: Added webhook receiver, refund endpoint, and audit logs route.
  - `src/paypal_donations/cli.py`: Added `donations refund` and `webhooks list` commands.
  - `tests/test_webhooks.py`: 5 tests covering signature verification, header security, idempotency, and refund events.
  - `tests/test_api.py`: Added integration tests for webhook ingestion and refund operations.
  - `tests/test_cli.py`: Added CLI tests for refund and webhook commands.
- **Test Results**: 33 Pytest unit & integration tests passing (3.41s).

---

### Iteration 1: Core PayPal v2 Integration, Automated Confirmation Receipts, Persistent SQLite Donor Log & Web Portal
- **Git Commit**: [`e0413ab`](https://github.com/breakingthebot/paypal-donations-platform-build124/commit/e0413ab)
- **Tag / Version**: `v1.0.0`
- **Date**: 2026-09-04
- **Plain English Summary**:
  Built the foundational end-to-end PayPal Donation Platform from scratch. Implemented a robust PayPal v2 Checkout Orders client (`PayPalClient`) supporting OAuth 2.0 bearer token caching, order creation, capture, and a deterministic offline mock sandbox for test reproducibility. Built a thread-safe SQLite donor repository (`DonorRepository`) with schema migrations, public donor roll filtering with anonymous masking, aggregate financial analytics, and CSV/JSON data export. Implemented an automated receipt generator and email service (`EmailService`) producing responsive HTML and plaintext tax receipts with non-profit disclosure notices. Constructed a responsive single-page web donation portal (`GET /`) and REST API (`paypal_donations.api`) alongside a terminal CLI tool (`paypal-donations`). Added a full Pytest test suite and GitHub Actions continuous integration workflow.
- **Key Files Introduced**:
  - `src/paypal_donations/models.py`: Pydantic data schemas, status enums, currency definitions, and receipt payloads.
  - `src/paypal_donations/paypal_client.py`: PayPal REST client with OAuth2 token caching, order creation, capture, and offline mock engine.
  - `src/paypal_donations/repository.py`: SQLite persistence layer, donor roll masking, stats aggregations, and CSV/JSON exports.
  - `src/paypal_donations/email_service.py`: HTML & Plaintext receipt template compiler and dual dispatch engine (SMTP/Mock).
  - `src/paypal_donations/api.py`: FastAPI REST API and embedded Tailwind CSS donation portal.
  - `src/paypal_donations/cli.py`: Interactive CLI with `--version`, `serve`, `donations list`, `donations stats`, `donations export`, and `donations test-email`.
  - `tests/test_paypal_client.py`: Unit tests for order creation, capture, and error cases.
  - `tests/test_repository.py`: Tests for SQLite operations, anonymous masking, stats, and exports.
  - `tests/test_email_service.py`: Tests for receipt rendering and mock file writing.
  - `tests/test_api.py`: Integration tests for FastAPI endpoints, donation lifecycle, and export downloads.
  - `tests/test_cli.py`: Integration tests for Click CLI commands.
  - `.github/workflows/ci.yml`: Multi-version Python GitHub Actions CI.
- **Test Results**: 15 Pytest unit & integration tests passing.
