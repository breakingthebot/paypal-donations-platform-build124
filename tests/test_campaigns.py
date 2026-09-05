"""Unit and integration tests for fundraising campaign drives and goal progress tracking."""

from decimal import Decimal
from pathlib import Path
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from paypal_donations.models import (
    CampaignCreate,
    CampaignStatus,
    CurrencyCode,
    DonationRecord,
    DonationStatus,
)
from paypal_donations.repository import DonorRepository
from paypal_donations.cli import main
import paypal_donations.api as api_mod


@pytest.fixture
def repo(tmp_path: Path) -> DonorRepository:
    db_file = tmp_path / "test_campaigns.sqlite3"
    return DonorRepository(db_path=str(db_file))


# =========================================================================
# 1. Repository Campaign CRUD & Progress Analytics
# =========================================================================

def test_create_and_get_campaign(repo: DonorRepository):
    camp_input = CampaignCreate(
        title="Open Source Server Fund",
        slug="server-fund-2026",
        description="Replacing community build servers.",
        goal_amount=Decimal("5000.00"),
        currency=CurrencyCode.USD,
    )
    saved = repo.create_campaign(camp_input)
    assert saved.id is not None
    assert saved.slug == "server-fund-2026"
    assert saved.status == CampaignStatus.ACTIVE

    retrieved = repo.get_campaign("server-fund-2026")
    assert retrieved is not None
    assert retrieved.title == "Open Source Server Fund"
    assert retrieved.goal_amount == Decimal("5000.00")

    # Lookup by ID
    by_id = repo.get_campaign(saved.id)
    assert by_id is not None
    assert by_id.slug == "server-fund-2026"


def test_create_campaign_duplicate_slug_fails(repo: DonorRepository):
    camp_input = CampaignCreate(
        title="First Fund",
        slug="dup-slug",
        goal_amount=Decimal("1000.00"),
    )
    repo.create_campaign(camp_input)

    with pytest.raises(ValueError, match="already exists"):
        repo.create_campaign(camp_input)


def test_update_campaign_status_and_filtering(repo: DonorRepository):
    camp = repo.create_campaign(
        CampaignCreate(
            title="Temporary Drive",
            slug="temp-drive",
            goal_amount=Decimal("2000.00"),
        )
    )
    assert camp.status == CampaignStatus.ACTIVE

    updated = repo.update_campaign_status("temp-drive", CampaignStatus.COMPLETED)
    assert updated is True

    retrieved = repo.get_campaign("temp-drive")
    assert retrieved.status == CampaignStatus.COMPLETED

    # Filter campaigns
    active_camps = repo.list_campaigns(status=CampaignStatus.ACTIVE)
    assert not any(c.slug == "temp-drive" for c in active_camps)

    completed_camps = repo.list_campaigns(status=CampaignStatus.COMPLETED)
    assert any(c.slug == "temp-drive" for c in completed_camps)


def test_campaign_progress_calculation(repo: DonorRepository):
    slug = "clean-water-progress"
    repo.create_campaign(
        CampaignCreate(
            title="Clean Water Project",
            slug=slug,
            goal_amount=Decimal("1000.00"),
            currency=CurrencyCode.USD,
        )
    )

    # Initially 0 progress
    initial_prog = repo.get_campaign_progress(slug)
    assert initial_prog.current_amount == Decimal("0.00")
    assert initial_prog.percent_raised == 0.0
    assert initial_prog.donations_count == 0
    assert initial_prog.is_goal_met is False

    # Add first donation ($250.00)
    repo.create_donation(
        DonationRecord(
            order_id="ORD-CAMP-01",
            donor_name="Alice",
            donor_email="alice@example.com",
            amount=Decimal("250.00"),
            currency=CurrencyCode.USD,
            status=DonationStatus.COMPLETED,
            campaign_slug=slug,
        )
    )

    # Add second donation ($750.00)
    repo.create_donation(
        DonationRecord(
            order_id="ORD-CAMP-02",
            donor_name="Bob",
            donor_email="bob@example.com",
            amount=Decimal("750.00"),
            currency=CurrencyCode.USD,
            status=DonationStatus.COMPLETED,
            campaign_slug=slug,
        )
    )

    # Add donation for a different campaign (should not affect this one)
    repo.create_donation(
        DonationRecord(
            order_id="ORD-OTHER-01",
            donor_name="Charlie",
            donor_email="charlie@example.com",
            amount=Decimal("500.00"),
            currency=CurrencyCode.USD,
            status=DonationStatus.COMPLETED,
            campaign_slug="unrelated-slug",
        )
    )

    prog = repo.get_campaign_progress(slug)
    assert prog.current_amount == Decimal("1000.00")
    assert prog.percent_raised == 100.0
    assert prog.donations_count == 2
    assert prog.unique_donors == 2
    assert prog.is_goal_met is True


def test_public_wall_campaign_filtering(repo: DonorRepository):
    repo.create_donation(
        DonationRecord(
            order_id="ORD-CAMP-A",
            donor_name="Donor Camp A",
            donor_email="a@example.com",
            amount=Decimal("100.00"),
            currency=CurrencyCode.USD,
            status=DonationStatus.COMPLETED,
            campaign_slug="campaign-alpha",
        )
    )
    repo.create_donation(
        DonationRecord(
            order_id="ORD-CAMP-B",
            donor_name="Donor Camp B",
            donor_email="b@example.com",
            amount=Decimal("200.00"),
            currency=CurrencyCode.USD,
            status=DonationStatus.COMPLETED,
            campaign_slug="campaign-beta",
        )
    )

    # All donors
    all_donors = repo.get_public_wall()
    assert len(all_donors) >= 2

    # Filter for Alpha
    alpha_donors = repo.get_public_wall(campaign_slug="campaign-alpha")
    assert len(alpha_donors) == 1
    assert alpha_donors[0].donor_name == "Donor Camp A"


# =========================================================================
# 2. REST API Endpoints
# =========================================================================

@pytest.fixture
def client_with_db(tmp_path: Path):
    db_file = tmp_path / "api_campaigns_test.sqlite3"
    test_repo = DonorRepository(db_path=str(db_file))
    api_mod.repo = test_repo
    api_mod.webhook_manager.repo = test_repo
    api_mod.webhook_manager.email_service = api_mod.email_service
    client = TestClient(api_mod.app)
    return client, test_repo


def test_api_campaigns_lifecycle(client_with_db):
    client, repo = client_with_db

    # 1. Create campaign
    create_payload = {
        "title": "Hospital Relief 2026",
        "slug": "hospital-relief-2026",
        "description": "Essential medical supplies for emergency clinics.",
        "goal_amount": 15000.0,
        "currency": "USD",
    }
    create_resp = client.post("/api/campaigns", json=create_payload)
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["slug"] == "hospital-relief-2026"
    assert created["status"] == "ACTIVE"

    # 2. List campaigns
    list_resp = client.get("/api/campaigns")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert any(c["slug"] == "hospital-relief-2026" for c in data)

    # 3. Get specific campaign
    get_resp = client.get("/api/campaigns/hospital-relief-2026")
    assert get_resp.status_code == 200
    prog = get_resp.json()
    assert prog["title"] == "Hospital Relief 2026"
    assert prog["percent_raised"] == 0.0

    # 4. Donate towards this campaign
    order_payload = {
        "amount": 1500.0,
        "currency": "USD",
        "donor_name": "Dr. House",
        "donor_email": "house@hospital.org",
        "dedication": "Clinic supplies",
        "campaign_slug": "hospital-relief-2026",
        "is_anonymous": False,
    }
    ord_resp = client.post("/api/donations/create-order", json=order_payload)
    assert ord_resp.status_code == 200
    order_id = ord_resp.json()["order_id"]

    # Capture order
    cap_resp = client.post("/api/donations/capture-order", json={"order_id": order_id})
    assert cap_resp.status_code == 200
    assert cap_resp.json()["campaign_slug"] == "hospital-relief-2026"

    # 5. Check updated progress
    prog_after = client.get("/api/campaigns/hospital-relief-2026").json()
    assert Decimal(str(prog_after["current_amount"])) == Decimal("1500.00")
    assert prog_after["percent_raised"] == 10.0
    assert prog_after["donations_count"] == 1

    # 6. Campaign donor wall
    donors_resp = client.get("/api/campaigns/hospital-relief-2026/donors")
    assert donors_resp.status_code == 200
    donors = donors_resp.json()
    assert len(donors) == 1
    assert donors[0]["donor_name"] == "Dr. House"

    # 7. Update status
    status_resp = client.post("/api/campaigns/hospital-relief-2026/status?status=COMPLETED")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "COMPLETED"


# =========================================================================
# 3. CLI Campaigns Commands
# =========================================================================

def test_cli_campaigns_commands(tmp_path: Path):
    db_file = str(tmp_path / "cli_campaigns.sqlite3")
    runner = CliRunner()

    # 1. Create campaign via CLI
    create_res = runner.invoke(
        main,
        [
            "campaigns", "create",
            "--title", "Library Book Drive",
            "--slug", "book-drive-2026",
            "--goal", "3000.00",
            "--currency", "USD",
            "--db", db_file,
        ],
    )
    assert create_res.exit_code == 0
    assert "created successfully" in create_res.output
    assert "book-drive-2026" in create_res.output

    # 2. List campaigns via CLI
    list_res = runner.invoke(main, ["campaigns", "list", "--db", db_file])
    assert list_res.exit_code == 0
    assert "book-drive-2026" in list_res.output
    assert "3000.00" in list_res.output

    # 3. Show campaign details
    show_res = runner.invoke(main, ["campaigns", "show", "book-drive-2026", "--db", db_file])
    assert show_res.exit_code == 0
    assert "Library Book Drive" in show_res.output
    assert "% Funded" in show_res.output
