"""Data models and schemas for the PayPal Donation Platform."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, EmailStr


class DonationStatus(str, Enum):
    """Payment status lifecycle for donations."""
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class CurrencyCode(str, Enum):
    """Supported currency codes for donations."""
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    CAD = "CAD"
    AUD = "AUD"


class DonationFrequency(str, Enum):
    """Donation recurrence frequency."""
    ONE_TIME = "ONE_TIME"
    MONTHLY = "MONTHLY"
    ANNUAL = "ANNUAL"


class CreateOrderRequest(BaseModel):
    """Payload to initiate a PayPal donation order."""
    amount: Decimal = Field(..., gt=Decimal("0.00"), description="Donation amount must be positive.")
    currency: CurrencyCode = Field(default=CurrencyCode.USD, description="Three-letter currency code.")
    donor_name: str = Field(..., min_length=1, max_length=120, description="Full name or alias of the donor.")
    donor_email: str = Field(..., description="Email address for receipt delivery.")
    dedication: Optional[str] = Field(None, max_length=250, description="Optional honor or memory dedication message.")
    is_anonymous: bool = Field(default=False, description="Whether to hide donor name on public donor roll.")

    @field_validator("donor_email")
    @classmethod
    def validate_email_format(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("Invalid email format. Must contain '@' and domain.")
        return value

    @field_validator("amount")
    @classmethod
    def round_amount(cls, value: Decimal) -> Decimal:
        return Decimal(f"{value:.2f}")


class OrderCreationResult(BaseModel):
    """PayPal order creation response sent back to frontend button."""
    order_id: str = Field(..., description="PayPal Order ID (e.g. 5O190127TN364715T).")
    status: str = Field(default="CREATED")
    amount: Decimal
    currency: CurrencyCode
    approval_url: Optional[str] = Field(None, description="PayPal checkout redirect URL if redirect-based.")
    mode: str = Field(default="mock", description="Execution mode: mock, sandbox, or live.")


class CaptureOrderRequest(BaseModel):
    """Request payload to capture an approved PayPal order."""
    order_id: str = Field(..., description="The PayPal Order ID to finalize.")
    donor_name: Optional[str] = None
    donor_email: Optional[str] = None
    dedication: Optional[str] = None
    is_anonymous: Optional[bool] = None


class DonationRecord(BaseModel):
    """Complete persistent donor log entry."""
    id: Optional[int] = None
    order_id: str
    capture_id: Optional[str] = None
    donor_name: str
    donor_email: str
    amount: Decimal
    currency: CurrencyCode
    status: DonationStatus = DonationStatus.COMPLETED
    dedication: Optional[str] = None
    is_anonymous: bool = False
    receipt_sent: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def public_donor_name(self) -> str:
        """Returns Anonymous if the donor opted for anonymity."""
        return "Anonymous Supporter" if self.is_anonymous else self.donor_name


class PublicDonorEntry(BaseModel):
    """Safe, sanitized representation for public donor roll."""
    donor_name: str
    amount: Decimal
    currency: CurrencyCode
    dedication: Optional[str] = None
    created_at: datetime


class DonationStats(BaseModel):
    """Aggregated donation metrics."""
    total_donations: int = 0
    total_amount_by_currency: Dict[str, Decimal] = Field(default_factory=dict)
    average_amount: Decimal = Decimal("0.00")
    unique_donors: int = 0
    anonymous_donations_count: int = 0


class EmailReceiptPayload(BaseModel):
    """Data packaged to render and dispatch a confirmation receipt."""
    receipt_id: str
    order_id: str
    capture_id: str
    donor_name: str
    donor_email: str
    amount: Decimal
    currency: CurrencyCode
    date_formatted: str
    dedication: Optional[str] = None
    org_name: str
    org_email: str
    org_tax_id: str
    org_website: str


class WebhookEventRecord(BaseModel):
    """Processed PayPal webhook audit log record."""
    id: Optional[int] = None
    event_id: str
    event_type: str
    resource_id: str
    status: str = "PROCESSED"
    payload_json: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RefundRequest(BaseModel):
    """Payload to initiate or record a donation refund."""
    reason: Optional[str] = Field(None, max_length=250, description="Reason for the refund.")


class RefundResult(BaseModel):
    """Result of processing a donation refund."""
    order_id: str
    capture_id: Optional[str]
    refund_id: str
    amount: Decimal
    currency: CurrencyCode
    status: str = "REFUNDED"
    receipt_sent: bool = False
    message: str = "Donation refund processed successfully."


class RefundReceiptPayload(BaseModel):
    """Data packaged to render and dispatch a refund notification."""
    receipt_id: str
    order_id: str
    capture_id: str
    refund_id: str
    donor_name: str
    donor_email: str
    amount: Decimal
    currency: CurrencyCode
    date_formatted: str
    reason: Optional[str] = None
    org_name: str
    org_email: str
    org_website: str


class CreateSubscriptionRequest(BaseModel):
    """Payload to initiate a recurring subscription pledge."""
    amount: Decimal = Field(..., gt=Decimal("0.00"), description="Recurring donation amount.")
    currency: CurrencyCode = Field(default=CurrencyCode.USD, description="Three-letter currency code.")
    frequency: DonationFrequency = Field(default=DonationFrequency.MONTHLY, description="Recurring schedule.")
    donor_name: str = Field(..., min_length=1, max_length=120)
    donor_email: str = Field(..., description="Donor email address.")
    dedication: Optional[str] = Field(None, max_length=250)
    is_anonymous: bool = Field(default=False)

    @field_validator("donor_email")
    @classmethod
    def validate_email_format(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("Invalid email format.")
        return value

    @field_validator("amount")
    @classmethod
    def round_amount(cls, value: Decimal) -> Decimal:
        return Decimal(f"{value:.2f}")


class SubscriptionResult(BaseModel):
    """Subscription response returned to frontend or client."""
    subscription_id: str
    status: str = "APPROVAL_PENDING"
    approval_url: Optional[str] = None
    amount: Decimal
    currency: CurrencyCode
    frequency: DonationFrequency = DonationFrequency.MONTHLY
    mode: str = "mock"


class SubscriptionRecord(BaseModel):
    """Persistent SQLite record for ongoing recurring pledges."""
    id: Optional[int] = None
    subscription_id: str
    donor_name: str
    donor_email: str
    amount: Decimal
    currency: CurrencyCode
    frequency: DonationFrequency = DonationFrequency.MONTHLY
    status: str = "ACTIVE"
    dedication: Optional[str] = None
    is_anonymous: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RecurringMetrics(BaseModel):
    """Aggregated subscription pledge metrics."""
    active_subscribers: int = 0
    mrr_by_currency: Dict[str, Decimal] = Field(default_factory=dict)
    total_active_pledged_monthly: Decimal = Decimal("0.00")

