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

---

## Chronological Iteration Entries

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
