"""Integration tests for Click CLI commands."""

from decimal import Decimal
from pathlib import Path
from click.testing import CliRunner

from paypal_donations.cli import main
from paypal_donations.models import CurrencyCode, DonationRecord, DonationStatus
from paypal_donations.repository import DonorRepository


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "paypal-donations" in result.output
    assert "1.0.0" in result.output


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "PayPal Donation Platform CLI" in result.output


def test_cli_donations_list_and_stats(tmp_path: Path):
    db_file = tmp_path / "cli_donations.sqlite3"
    repo = DonorRepository(db_path=str(db_file))
    repo.create_donation(
        DonationRecord(
            order_id="CLI-ORD-01",
            donor_name="Morgan Freeman",
            donor_email="morgan@example.com",
            amount=Decimal("150.00"),
            currency=CurrencyCode.USD,
            status=DonationStatus.COMPLETED,
        )
    )

    runner = CliRunner()

    # Test list
    list_res = runner.invoke(main, ["donations", "list", "--db", str(db_file)])
    assert list_res.exit_code == 0
    assert "Morgan" in list_res.output
    assert "CLI-ORD-01" in list_res.output

    # Test stats
    stats_res = runner.invoke(main, ["donations", "stats", "--db", str(db_file)])
    assert stats_res.exit_code == 0
    assert "$150.00" in stats_res.output


def test_cli_export(tmp_path: Path):
    db_file = tmp_path / "cli_donations.sqlite3"
    repo = DonorRepository(db_path=str(db_file))
    repo.create_donation(
        DonationRecord(
            order_id="CLI-ORD-EXP",
            donor_name="CLI Exporter",
            donor_email="cliexp@example.com",
            amount=Decimal("40.00"),
            currency=CurrencyCode.USD,
            status=DonationStatus.COMPLETED,
        )
    )

    runner = CliRunner()
    out_csv = tmp_path / "cli_out.csv"
    res = runner.invoke(main, ["donations", "export", "--format", "csv", "--output", str(out_csv), "--db", str(db_file)])
    assert res.exit_code == 0
    assert out_csv.exists()
    assert "CLI Exporter" in out_csv.read_text(encoding="utf-8")


def test_cli_test_email(tmp_path: Path):
    runner = CliRunner()
    res = runner.invoke(main, ["donations", "test-email", "--recipient", "testuser@example.com", "--amount", "50.0"])
    assert res.exit_code == 0
    assert "Confirmation receipt successfully generated" in res.output
