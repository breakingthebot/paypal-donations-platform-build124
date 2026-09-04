# Iteration 01 Summary: Core PayPal v2 Integration, Automated Confirmation Receipts, Persistent SQLite Donor Log & Web Portal

**Version**: `1.0.0`  
**Date**: 2026-09-04  
**Status**: Completed & Verified  

---

## 1. Plain English Summary

In this initial iteration, we established the end-to-end architecture for the **PayPal Donation Platform**:
1. **Payment Engine**: Created a PayPal v2 Checkout Orders client that supports one-time donation order creation and capture. It handles OAuth2 token acquisition and renewal, and includes an offline mock sandbox for zero-dependency development and continuous integration testing without needing live PayPal credentials.
2. **Persistent Donor Store**: Developed a thread-safe SQLite database manager (`DonorRepository`) tracking all transactions, order statuses, donor details, dedication notes, and receipt delivery flags. Includes automatic calculation of donation metrics and CSV/JSON export.
3. **Automated Tax Receipts**: Created an automated email engine producing both responsive HTML and clean plaintext tax receipts with non-profit 501(c)(3) tax ID disclosures and transaction reference numbers.
4. **FastAPI Web Application & Portal**: Implemented a responsive single-page web portal (`GET /`) built with Tailwind CSS, supporting preset donation amounts, custom values, currency selection, donor roll inspection, and live impact statistics.
5. **Installable CLI Suite (`paypal-donations`)**: Built an administrative CLI tool with Rich table formatting, metrics summaries, email tests, and server startup commands.
6. **Automated Testing & CI**: Added 24 unit and integration tests passing in pytest and configured GitHub Actions CI across Python 3.10, 3.11, and 3.12.

---

## 2. File Manifest & Architecture Connections

| File | Primary Responsibility | Connects To |
| :--- | :--- | :--- |
| `src/paypal_donations/models.py` | Pydantic data schemas, status enums, currencies, and validation rules. | Imported by `paypal_client`, `repository`, `email_service`, `api`, and `cli`. |
| `src/paypal_donations/paypal_client.py` | PayPal v2 Orders API client, OAuth token management, order creation, capture, and mock sandbox. | Consumed by `api.py` during checkout lifecycle. |
| `src/paypal_donations/repository.py` | Thread-safe SQLite storage, schema initialization, queries, donor masking, stats, and CSV/JSON export. | Consumed by `api.py` and `cli.py` for persistent logging. |
| `src/paypal_donations/email_service.py` | Jinja2 HTML/plaintext tax receipt renderer and SMTP/Mock dispatch service. | Triggered by `api.py` on order capture and `cli.py test-email`. |
| `src/paypal_donations/api.py` | FastAPI application serving REST endpoints and embedded Tailwind CSS donation portal. | Bridges web clients to `paypal_client`, `repository`, and `email_service`. |
| `src/paypal_donations/cli.py` | Command-line interface with Rich table formatting, stats panels, data export, and server launch. | Interfaces with `repository`, `email_service`, and `uvicorn`. |
| `tests/test_paypal_client.py` | Tests for order creation, capture, mock mode, and duplicate handling. | Validates `paypal_client.py`. |
| `tests/test_repository.py` | Tests for SQLite CRUD, anonymous masking, stats aggregation, and file exports. | Validates `repository.py`. |
| `tests/test_email_service.py` | Tests for HTML/text receipt rendering and mock file saving. | Validates `email_service.py`. |
| `tests/test_api.py` | Integration tests for FastAPI endpoints, donation lifecycle, and CSV/JSON exports. | Validates `api.py`. |
| `tests/test_cli.py` | Integration tests for Click CLI commands via `CliRunner`. | Validates `cli.py`. |
| `.github/workflows/ci.yml` | Multi-version Python GitHub Actions workflow. | Triggers on push and pull requests to `main`. |
| `pyproject.toml` | Build metadata, dependencies, and installable script `paypal-donations`. | Used by `pip install -e .`. |
| `requirements.txt` | Locked dependency list for reproducible installs. | Pip dependency resolution. |
| `LICENSE` | Standard MIT License. | Legal license file. |
| `README.md` | Complete documentation, visual previews, quickstart, and API specs. | Project landing page. |
| `CHANGELOG.md` | Semantic versioning release history. | Project version tracking. |
