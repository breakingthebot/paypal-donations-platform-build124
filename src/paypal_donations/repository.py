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
from typing import Any, Dict, Generator, List, Optional, Tuple

from paypal_donations.models import (
    CurrencyCode,
    DonationRecord,
    DonationStats,
    DonationStatus,
    PublicDonorEntry,
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
                    receipt_sent INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_donations_created ON donations(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_donations_order ON donations(order_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_donations_status ON donations(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_donations_capture ON donations(capture_id)")

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
                    is_anonymous, receipt_sent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def get_public_wall(self, limit: int = 20) -> List[PublicDonorEntry]:
        """Retrieve recent donations sanitized for public donor rolls."""
        query = """
            SELECT donor_name, amount, currency, dedication, is_anonymous, created_at
            FROM donations
            WHERE status = 'COMPLETED'
            ORDER BY created_at DESC
            LIMIT ?
        """
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (limit,))
            rows = cur.fetchall()
            entries: List[PublicDonorEntry] = []
            for r in rows:
                is_anon = bool(r["is_anonymous"])
                entries.append(
                    PublicDonorEntry(
                        donor_name="Anonymous Supporter" if is_anon else r["donor_name"],
                        amount=Decimal(str(r["amount"])),
                        currency=CurrencyCode(r["currency"]),
                        dedication=r["dedication"],
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
                "Amount", "Currency", "Status", "Dedication", "Anonymous",
                "Receipt Sent", "Created At"
            ])
            for d in donations:
                writer.writerow([
                    d.id, d.order_id, d.capture_id or "", d.donor_name, d.donor_email,
                    f"{d.amount:.2f}", d.currency.value, d.status.value, d.dedication or "",
                    d.is_anonymous, d.receipt_sent, d.created_at.isoformat()
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

    def _row_to_record(self, row: sqlite3.Row) -> DonationRecord:
        """Transform an SQLite Row into a validated DonationRecord model."""
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
            receipt_sent=bool(row["receipt_sent"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
