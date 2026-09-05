"""SQLite persistence repository for donation logs and analytics."""

from __future__ import annotations

import contextlib
import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

from paypal_donations.models import (
    CampaignCreate,
    CampaignProgress,
    CampaignRecord,
    CampaignStatus,
    CurrencyCode,
    DonationFrequency,
    DonationRecord,
    DonationStats,
    DonationStatus,
    PublicDonorEntry,
    RecurringMetrics,
    SubscriptionRecord,
    WebhookEventRecord,
)


class DonorRepository:
    """Thread-safe SQLite database manager for recording and querying donor transactions."""

    def __init__(self, db_path: str = "storage/donations.sqlite3"):
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextlib.contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Create a new database connection and guarantee cleanup upon exit."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize tables and indexes if they do not exist."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS donations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT UNIQUE NOT NULL,
                    capture_id TEXT,
                    donor_name TEXT NOT NULL,
                    donor_email TEXT NOT NULL,
                    amount NUMERIC NOT NULL,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dedication TEXT,
                    is_anonymous INTEGER NOT NULL DEFAULT 0,
                    campaign_slug TEXT,
                    receipt_sent INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_donations_created ON donations(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_donations_order ON donations(order_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_donations_status ON donations(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_donations_capture ON donations(capture_id)")
            # Safe migration: ensure campaign_slug column exists in existing tables
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(donations)")
            existing_cols = [r["name"] for r in cur.fetchall()]
            if "campaign_slug" not in existing_cols:
                conn.execute("ALTER TABLE donations ADD COLUMN campaign_slug TEXT")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_donations_campaign ON donations(campaign_slug)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS webhook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    event_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_webhook_event_id ON webhook_events(event_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_webhook_event_type ON webhook_events(event_type)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subscription_id TEXT UNIQUE NOT NULL,
                    donor_name TEXT NOT NULL,
                    donor_email TEXT NOT NULL,
                    amount NUMERIC NOT NULL,
                    currency TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dedication TEXT,
                    is_anonymous INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_subs_id ON subscriptions(subscription_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_subs_status ON subscriptions(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_subs_email ON subscriptions(donor_email)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    goal_amount NUMERIC NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    start_date TEXT,
                    end_date TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_campaigns_slug ON campaigns(slug)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status)")

            # Seed default campaign if table is empty
            cur.execute("SELECT COUNT(*) as cnt FROM campaigns")
            row = cur.fetchone()
            if row and row["cnt"] == 0:
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO campaigns (slug, title, description, goal_amount, currency, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "clean-water-2026",
                        "Clean Water Initiative 2026",
                        "Solar-powered clean water filtration wells for rural communities.",
                        "10000.00",
                        "USD",
                        "ACTIVE",
                        now_str,
                        now_str,
                    ),
                )
            conn.commit()

    def create_donation(self, donation: DonationRecord) -> DonationRecord:
        """Save a new donation record into SQLite."""
        created_at_str = donation.created_at.isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO donations (
                    order_id, capture_id, donor_name, donor_email,
                    amount, currency, status, dedication,
                    is_anonymous, campaign_slug, receipt_sent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    donation.order_id,
                    donation.capture_id,
                    donation.donor_name,
                    donation.donor_email,
                    str(donation.amount),
                    donation.currency.value if hasattr(donation.currency, "value") else str(donation.currency),
                    donation.status.value if hasattr(donation.status, "value") else str(donation.status),
                    donation.dedication,
                    1 if donation.is_anonymous else 0,
                    donation.campaign_slug,
                    1 if donation.receipt_sent else 0,
                    created_at_str,
                ),
            )
            donation_id = cur.lastrowid
            conn.commit()
            donation.id = donation_id
            return donation

    def get_by_order_id(self, order_id: str) -> Optional[DonationRecord]:
        """Fetch a single donation record by PayPal order ID."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM donations WHERE order_id = ?", (order_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def get_by_capture_id(self, capture_id: str) -> Optional[DonationRecord]:
        """Fetch a single donation record by PayPal capture ID."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM donations WHERE capture_id = ?", (capture_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def record_refund(self, order_id: str, reason: Optional[str] = None) -> Optional[DonationRecord]:
        """Mark a donation as REFUNDED and append reason to dedication note."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            note_suffix = f" [Refunded: {reason}]" if reason else " [Refunded]"
            cur.execute(
                """
                UPDATE donations
                SET status = 'REFUNDED',
                    dedication = COALESCE(dedication, '') || ?
                WHERE order_id = ?
                """,
                (note_suffix, order_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return None
        return self.get_by_order_id(order_id)

    def is_webhook_event_processed(self, event_id: str) -> bool:
        """Check if a webhook event has already been recorded (idempotency)."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM webhook_events WHERE event_id = ?", (event_id,))
            return cur.fetchone() is not None

    def record_webhook_event(
        self,
        event_id: str,
        event_type: str,
        resource_id: str,
        status: str = "PROCESSED",
        payload_json: Optional[str] = None,
    ) -> WebhookEventRecord:
        """Record a verified webhook event for audit trail."""
        created_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO webhook_events (event_id, event_type, resource_id, status, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_id, event_type, resource_id, status, payload_json, created_str),
            )
            conn.commit()
            return WebhookEventRecord(
                id=cur.lastrowid,
                event_id=event_id,
                event_type=event_type,
                resource_id=resource_id,
                status=status,
                payload_json=payload_json,
                created_at=datetime.fromisoformat(created_str),
            )

    def list_webhook_events(self, limit: int = 50) -> List[WebhookEventRecord]:
        """Retrieve recent webhook audit entries."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM webhook_events ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
            return [
                WebhookEventRecord(
                    id=r["id"],
                    event_id=r["event_id"],
                    event_type=r["event_type"],
                    resource_id=r["resource_id"],
                    status=r["status"],
                    payload_json=r["payload_json"],
                    created_at=datetime.fromisoformat(r["created_at"]),
                )
                for r in rows
            ]

    def create_subscription(self, sub: SubscriptionRecord) -> SubscriptionRecord:
        """Store a new recurring pledge record."""
        now_str = sub.created_at.isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO subscriptions (
                    subscription_id, donor_name, donor_email, amount, currency,
                    frequency, status, dedication, is_anonymous, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sub.subscription_id,
                    sub.donor_name,
                    sub.donor_email,
                    str(sub.amount),
                    sub.currency.value if hasattr(sub.currency, "value") else str(sub.currency),
                    sub.frequency.value if hasattr(sub.frequency, "value") else str(sub.frequency),
                    sub.status,
                    sub.dedication,
                    1 if sub.is_anonymous else 0,
                    now_str,
                    now_str,
                ),
            )
            conn.commit()
            sub.id = cur.lastrowid
            return sub

    def get_subscription(self, subscription_id: str) -> Optional[SubscriptionRecord]:
        """Lookup a subscription by its PayPal subscription ID."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM subscriptions WHERE subscription_id = ?", (subscription_id,))
            row = cur.fetchone()
            if not row:
                return None
            return SubscriptionRecord(
                id=row["id"],
                subscription_id=row["subscription_id"],
                donor_name=row["donor_name"],
                donor_email=row["donor_email"],
                amount=Decimal(str(row["amount"])),
                currency=CurrencyCode(row["currency"]),
                frequency=DonationFrequency(row["frequency"]),
                status=row["status"],
                dedication=row["dedication"],
                is_anonymous=bool(row["is_anonymous"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def update_subscription_status(self, subscription_id: str, status: str, reason: Optional[str] = None) -> bool:
        """Transition subscription status (e.g. ACTIVE, CANCELLED, SUSPENDED)."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE subscriptions
                SET status = ?, updated_at = ?
                WHERE subscription_id = ?
                """,
                (status, now_str, subscription_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_subscriptions(self, limit: int = 50, status: Optional[str] = None) -> List[SubscriptionRecord]:
        """List ongoing subscriptions with optional status filter."""
        query = "SELECT * FROM subscriptions"
        params: List[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()
            return [
                SubscriptionRecord(
                    id=r["id"],
                    subscription_id=r["subscription_id"],
                    donor_name=r["donor_name"],
                    donor_email=r["donor_email"],
                    amount=Decimal(str(r["amount"])),
                    currency=CurrencyCode(r["currency"]),
                    frequency=DonationFrequency(r["frequency"]),
                    status=r["status"],
                    dedication=r["dedication"],
                    is_anonymous=bool(r["is_anonymous"]),
                    created_at=datetime.fromisoformat(r["created_at"]),
                    updated_at=datetime.fromisoformat(r["updated_at"]),
                )
                for r in rows
            ]

    def get_recurring_metrics(self) -> RecurringMetrics:
        """Calculate Monthly Recurring Revenue (MRR) and active subscriber counts."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    currency,
                    COUNT(*) as active_count,
                    SUM(amount) as mrr_val
                FROM subscriptions
                WHERE status = 'ACTIVE'
                GROUP BY currency
            """)
            rows = cur.fetchall()

            total_active = 0
            mrr_map: Dict[str, Decimal] = {}
            total_monthly_usd = Decimal("0.00")

            for r in rows:
                curr = r["currency"]
                cnt = r["active_count"]
                mrr = Decimal(str(r["mrr_val"]))
                total_active += cnt
                mrr_map[curr] = mrr
                if curr == "USD":
                    total_monthly_usd += mrr

            return RecurringMetrics(
                active_subscribers=total_active,
                mrr_by_currency=mrr_map,
                total_active_pledged_monthly=Decimal(f"{total_monthly_usd:.2f}"),
            )

    def mark_receipt_sent(self, order_id: str) -> bool:
        """Update the receipt_sent flag to True."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE donations SET receipt_sent = 1 WHERE order_id = ?", (order_id,))
            conn.commit()
            return cur.rowcount > 0

    def list_donations(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> List[DonationRecord]:
        """Retrieve paginated donation records for admin reporting."""
        query = "SELECT * FROM donations"
        params: List[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()
            return [self._row_to_record(r) for r in rows]

    def get_public_wall(self, limit: int = 20, campaign_slug: Optional[str] = None) -> List[PublicDonorEntry]:
        """Retrieve recent donations sanitized for public donor rolls with optional campaign filter."""
        query = """
            SELECT donor_name, amount, currency, dedication, is_anonymous, campaign_slug, created_at
            FROM donations
            WHERE status = 'COMPLETED'
        """
        params: List[Any] = []
        if campaign_slug:
            query += " AND campaign_slug = ?"
            params.append(campaign_slug)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
            entries: List[PublicDonorEntry] = []
            for r in rows:
                is_anon = bool(r["is_anonymous"])
                keys = r.keys()
                c_slug = r["campaign_slug"] if "campaign_slug" in keys else None
                entries.append(
                    PublicDonorEntry(
                        donor_name="Anonymous Supporter" if is_anon else r["donor_name"],
                        amount=Decimal(str(r["amount"])),
                        currency=CurrencyCode(r["currency"]),
                        dedication=r["dedication"],
                        campaign_slug=c_slug,
                        created_at=datetime.fromisoformat(r["created_at"]),
                    )
                )
            return entries

    def get_statistics(self) -> DonationStats:
        """Calculate high-level financial metrics and aggregate donor statistics."""
        with self._get_connection() as conn:
            cur = conn.cursor()

            # Total donations & unique donors
            cur.execute("""
                SELECT
                    COUNT(*) as total_count,
                    COUNT(DISTINCT donor_email) as unique_donors,
                    SUM(CASE WHEN is_anonymous = 1 THEN 1 ELSE 0 END) as anon_count
                FROM donations
                WHERE status = 'COMPLETED'
            """)
            summary_row = cur.fetchone()
            total_count = (summary_row["total_count"] or 0) if summary_row else 0
            unique_donors = (summary_row["unique_donors"] or 0) if summary_row else 0
            anon_count = (summary_row["anon_count"] or 0) if summary_row else 0

            # Totals grouped by currency
            cur.execute("""
                SELECT currency, SUM(amount) as total_val, AVG(amount) as avg_val
                FROM donations
                WHERE status = 'COMPLETED'
                GROUP BY currency
            """)
            currency_rows = cur.fetchall()
            currency_totals: Dict[str, Decimal] = {}
            total_sum = Decimal("0.00")

            for r in currency_rows:
                curr = r["currency"]
                tot = Decimal(str(r["total_val"]))
                currency_totals[curr] = tot
                if curr == "USD":
                    total_sum += tot

            avg_amount = (total_sum / total_count) if total_count > 0 else Decimal("0.00")

            return DonationStats(
                total_donations=total_count,
                total_amount_by_currency=currency_totals,
                average_amount=Decimal(f"{avg_amount:.2f}"),
                unique_donors=unique_donors,
                anonymous_donations_count=anon_count,
            )

    def export_csv(self, file_path: str) -> str:
        """Export all donation logs to a CSV file."""
        donations = self.list_donations(limit=100000, offset=0)
        target = Path(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        with open(target, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "ID", "Order ID", "Capture ID", "Donor Name", "Donor Email",
                "Amount", "Currency", "Status", "Dedication", "Campaign", "Anonymous",
                "Receipt Sent", "Created At"
            ])
            for d in donations:
                writer.writerow([
                    d.id, d.order_id, d.capture_id or "", d.donor_name, d.donor_email,
                    f"{d.amount:.2f}", d.currency.value, d.status.value, d.dedication or "",
                    d.campaign_slug or "", d.is_anonymous, d.receipt_sent, d.created_at.isoformat()
                ])
        return str(target)

    def export_json(self, file_path: str) -> str:
        """Export all donation logs to a JSON file."""
        donations = self.list_donations(limit=100000, offset=0)
        target = Path(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        data = [d.model_dump(mode="json") for d in donations]
        with open(target, mode="w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return str(target)

    def create_campaign(self, campaign: CampaignCreate) -> CampaignRecord:
        """Create and persist a new fundraising campaign."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM campaigns WHERE slug = ?", (campaign.slug,))
            if cur.fetchone():
                raise ValueError(f"Campaign with slug '{campaign.slug}' already exists.")

            cur.execute(
                """
                INSERT INTO campaigns (
                    slug, title, description, goal_amount, currency,
                    status, start_date, end_date, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign.slug,
                    campaign.title,
                    campaign.description,
                    str(campaign.goal_amount),
                    campaign.currency.value,
                    CampaignStatus.ACTIVE.value,
                    campaign.start_date,
                    campaign.end_date,
                    now_str,
                    now_str,
                ),
            )
            conn.commit()
            return CampaignRecord(
                id=cur.lastrowid,
                slug=campaign.slug,
                title=campaign.title,
                description=campaign.description,
                goal_amount=campaign.goal_amount,
                currency=campaign.currency,
                status=CampaignStatus.ACTIVE,
                start_date=campaign.start_date,
                end_date=campaign.end_date,
                created_at=datetime.fromisoformat(now_str),
                updated_at=datetime.fromisoformat(now_str),
            )

    def get_campaign(self, slug_or_id: Union[str, int]) -> Optional[CampaignRecord]:
        """Fetch campaign by slug or integer ID."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            if isinstance(slug_or_id, int) or (isinstance(slug_or_id, str) and slug_or_id.isdigit()):
                cur.execute("SELECT * FROM campaigns WHERE id = ?", (int(slug_or_id),))
            else:
                cur.execute("SELECT * FROM campaigns WHERE slug = ?", (str(slug_or_id),))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_campaign(row)

    def list_campaigns(self, status: Optional[CampaignStatus] = None) -> List[CampaignRecord]:
        """List campaigns with optional status filtering."""
        query = "SELECT * FROM campaigns"
        params: List[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status.value if hasattr(status, "value") else str(status))
        query += " ORDER BY created_at DESC"

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()
            return [self._row_to_campaign(r) for r in rows]

    def update_campaign_status(self, slug: str, status: CampaignStatus) -> bool:
        """Update campaign operational status."""
        now_str = datetime.now(timezone.utc).isoformat()
        status_val = status.value if hasattr(status, "value") else str(status)
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE campaigns SET status = ?, updated_at = ? WHERE slug = ?",
                (status_val, now_str, slug),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_campaign_progress(self, slug: str) -> Optional[CampaignProgress]:
        """Calculate real-time progress, percentage raised, and donor counts for a campaign."""
        campaign = self.get_campaign(slug)
        if not campaign:
            return None

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(amount), 0) as current_raised,
                    COUNT(*) as donations_count,
                    COUNT(DISTINCT donor_email) as unique_donors
                FROM donations
                WHERE campaign_slug = ? AND status = 'COMPLETED'
                """,
                (slug,),
            )
            row = cur.fetchone()
            current_raised = Decimal(str(row["current_raised"] if row else 0))
            donations_cnt = row["donations_count"] if row else 0
            unique_donors_cnt = row["unique_donors"] if row else 0

            percent = 0.0
            if campaign.goal_amount > Decimal("0.00"):
                percent = float(round((current_raised / campaign.goal_amount) * 100, 1))

            return CampaignProgress(
                id=campaign.id or 0,
                slug=campaign.slug,
                title=campaign.title,
                description=campaign.description,
                goal_amount=campaign.goal_amount,
                currency=campaign.currency,
                status=campaign.status,
                current_amount=Decimal(f"{current_raised:.2f}"),
                percent_raised=percent,
                donations_count=donations_cnt,
                unique_donors=unique_donors_cnt,
                is_goal_met=current_raised >= campaign.goal_amount,
                start_date=campaign.start_date,
                end_date=campaign.end_date,
                created_at=campaign.created_at,
            )

    def list_campaign_progress(self, status: Optional[CampaignStatus] = None) -> List[CampaignProgress]:
        """Return progress metrics for all campaigns."""
        campaigns = self.list_campaigns(status=status)
        progress_list: List[CampaignProgress] = []
        for c in campaigns:
            prog = self.get_campaign_progress(c.slug)
            if prog:
                progress_list.append(prog)
        return progress_list

    def _row_to_campaign(self, row: sqlite3.Row) -> CampaignRecord:
        """Convert SQLite row to CampaignRecord model."""
        return CampaignRecord(
            id=row["id"],
            slug=row["slug"],
            title=row["title"],
            description=row["description"],
            goal_amount=Decimal(str(row["goal_amount"])),
            currency=CurrencyCode(row["currency"]),
            status=CampaignStatus(row["status"]),
            start_date=row["start_date"],
            end_date=row["end_date"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_record(self, row: sqlite3.Row) -> DonationRecord:
        """Transform an SQLite Row into a validated DonationRecord model."""
        keys = row.keys()
        camp_slug = row["campaign_slug"] if "campaign_slug" in keys else None
        return DonationRecord(
            id=row["id"],
            order_id=row["order_id"],
            capture_id=row["capture_id"],
            donor_name=row["donor_name"],
            donor_email=row["donor_email"],
            amount=Decimal(str(row["amount"])),
            currency=CurrencyCode(row["currency"]),
            status=DonationStatus(row["status"]),
            dedication=row["dedication"],
            is_anonymous=bool(row["is_anonymous"]),
            campaign_slug=camp_slug,
            receipt_sent=bool(row["receipt_sent"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
