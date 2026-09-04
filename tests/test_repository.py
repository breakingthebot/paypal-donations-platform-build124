"""Unit tests for DonorRepository."""

import json
from decimal import Decimal
from pathlib import Path
import pytest

from paypal_donations.models import CurrencyCode, DonationRecord, DonationStatus
from paypal_donations.repository import DonorRepository


@pytest.fixture
def repo(tmp_path: Path) -> DonorRepository:
    db_file = tmp_path / "test_donations.sqlite3"
    return DonorRepository(db_path=str(db_file))


def test_create_and_get_donation(repo: DonorRepository):
    record = DonationRecord(
        order_id="ORD-001",
        capture_id="CAP-001",
        donor_name="Alice Smith",
        donor_email="alice@example.com",
        amount=Decimal("100.00"),
        currency=CurrencyCode.USD,
        status=DonationStatus.COMPLETED,
        dedication="Great project!",
        is_anonymous=False,
    )
    saved = repo.create_donation(record)
    assert saved.id is not None

    retrieved = repo.get_by_order_id("ORD-001")
    assert retrieved is not None
    assert retrieved.donor_name == "Alice Smith"
    assert retrieved.amount == Decimal("100.00")
    assert retrieved.receipt_sent is False


def test_mark_receipt_sent(repo: DonorRepository):
    record = DonationRecord(
        order_id="ORD-002",
        donor_name="Bob Jones",
        donor_email="bob@example.com",
        amount=Decimal("50.00"),
        currency=CurrencyCode.USD,
        status=DonationStatus.COMPLETED,
    )
    repo.create_donation(record)
    assert repo.mark_receipt_sent("ORD-002") is True

    retrieved = repo.get_by_order_id("ORD-002")
    assert retrieved.receipt_sent is True


def test_public_wall_anonymity(repo: DonorRepository):
    # Public donor
    repo.create_donation(
        DonationRecord(
            order_id="ORD-PUB",
            donor_name="Public Donor",
            donor_email="public@example.com",
            amount=Decimal("30.00"),
            currency=CurrencyCode.USD,
            status=DonationStatus.COMPLETED,
            is_anonymous=False,
        )
    )
    # Anonymous donor
    repo.create_donation(
        DonationRecord(
            order_id="ORD-ANON",
            donor_name="Secret Benefactor",
            donor_email="secret@example.com",
            amount=Decimal("500.00"),
            currency=CurrencyCode.USD,
            status=DonationStatus.COMPLETED,
            is_anonymous=True,
        )
    )

    wall = repo.get_public_wall(limit=10)
    assert len(wall) == 2

    anon_entry = next(d for d in wall if d.amount == Decimal("500.00"))
    assert anon_entry.donor_name == "Anonymous Supporter"

    pub_entry = next(d for d in wall if d.amount == Decimal("30.00"))
    assert pub_entry.donor_name == "Public Donor"


def test_statistics_aggregation(repo: DonorRepository):
    repo.create_donation(
        DonationRecord(
            order_id="ORD-S1",
            donor_name="Donor 1",
            donor_email="d1@example.com",
            amount=Decimal("20.00"),
            currency=CurrencyCode.USD,
            status=DonationStatus.COMPLETED,
        )
    )
    repo.create_donation(
        DonationRecord(
            order_id="ORD-S2",
            donor_name="Donor 2",
            donor_email="d2@example.com",
            amount=Decimal("80.00"),
            currency=CurrencyCode.USD,
            status=DonationStatus.COMPLETED,
        )
    )
    repo.create_donation(
        DonationRecord(
            order_id="ORD-S3",
            donor_name="Donor 3",
            donor_email="d3@example.com",
            amount=Decimal("50.00"),
            currency=CurrencyCode.EUR,
            status=DonationStatus.COMPLETED,
        )
    )

    stats = repo.get_statistics()
    assert stats.total_donations == 3
    assert stats.total_amount_by_currency["USD"] == Decimal("100.00")
    assert stats.total_amount_by_currency["EUR"] == Decimal("50.00")
    assert stats.average_amount == Decimal("33.33")


def test_statistics_empty_database(repo: DonorRepository):
    stats = repo.get_statistics()
    assert stats.total_donations == 0
    assert stats.unique_donors == 0
    assert stats.anonymous_donations_count == 0
    assert stats.average_amount == Decimal("0.00")
    assert stats.total_amount_by_currency == {}


def test_exports_csv_and_json(repo: DonorRepository, tmp_path: Path):
    repo.create_donation(
        DonationRecord(
            order_id="ORD-EXP",
            donor_name="Export Tester",
            donor_email="export@example.com",
            amount=Decimal("45.00"),
            currency=CurrencyCode.USD,
            status=DonationStatus.COMPLETED,
        )
    )

    csv_path = tmp_path / "donations.csv"
    repo.export_csv(str(csv_path))
    assert csv_path.exists()
    assert "Export Tester" in csv_path.read_text(encoding="utf-8")

    json_path = tmp_path / "donations.json"
    repo.export_json(str(json_path))
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["order_id"] == "ORD-EXP"
