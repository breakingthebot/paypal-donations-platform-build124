# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
