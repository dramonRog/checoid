from datetime import datetime, date
from typing import List, Optional
from pydantic import BaseModel, EmailStr, ConfigDict, Field, AliasChoices, field_serializer

from src.backend.core.storage import resolve_public_image_url

# =======================================================
# USER SCHEMAS
# =======================================================

class UserCreate(BaseModel):
    """Schema for user registration"""
    first_name: str
    last_name: str
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None


class UserResponse(BaseModel):
    """
    Schema for sending user data back to the client.
    Password must be stripped out for security.
    """
    id: int
    first_name: str
    last_name: str
    email: EmailStr
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# =======================================================
# COMPANY & CATEGORY SCHEMAS
# =======================================================

class CompanyBase(BaseModel):
    nip: Optional[str] = None
    name: str
    address: Optional[str] = None


class CompanyResponse(CompanyBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CategoryResponse(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


# =======================================================
# RECEIPT ITEMS SCHEMAS
# =======================================================

class ReceiptItemCreate(BaseModel):
    name: str
    quantity: float = Field(
        default=1.0,
        description="Printed quantity (informational). Not multiplied into spend.",
    )
    price: float = Field(
        description="Amount paid for this line after discount (not unit price).",
    )
    # None = let LLM decide; True/False = explicit client override
    is_under_warranty: Optional[bool] = None
    warranty_end_date: Optional[date] = None
    category_id: Optional[int] = Field(default=None, gt=0)
    # Free-text category label (resolved to category_id). Alias "kategoria" kept for older clients.
    category: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("category", "kategoria"),
    )

    model_config = ConfigDict(populate_by_name=True)


class ReceiptItemResponse(BaseModel):
    name: str
    quantity: float = Field(
        default=1.0,
        description="Printed quantity (informational). Not multiplied into spend.",
    )
    price: float = Field(
        description="Amount paid for this line after discount (not unit price).",
    )
    is_under_warranty: Optional[bool] = False
    warranty_end_date: Optional[date] = None
    category_id: Optional[int] = None
    id: int
    receipt_id: int
    category: Optional[CategoryResponse] = None

    model_config = ConfigDict(from_attributes=True)


# =======================================================
# RECEIPT SCHEMAS
# =======================================================

class ReceiptBase(BaseModel):
    purchase_date: Optional[date] = None
    total_amount: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("total_amount", "total"),
    )
    status: str = "PROCESSING"
    image_url: Optional[str] = None
    shop_name: Optional[str] = None
    store_address: Optional[str] = None
    company_id: Optional[int] = None

    model_config = ConfigDict(populate_by_name=True)


class ReceiptCreate(ReceiptBase):
    nip: Optional[str] = None
    items: Optional[List[ReceiptItemCreate]] = None


class ReceiptResponse(ReceiptBase):
    id: int
    user_id: int
    created_at: datetime
    has_warranty_items: bool = False
    extraction_error: Optional[str] = None
    extraction_attempts: int = 0

    company: Optional[CompanyResponse] = None
    items: List[ReceiptItemResponse] = []

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("image_url")
    @classmethod
    def serialize_image_url(cls, value: Optional[str]) -> Optional[str]:
        return resolve_public_image_url(value)


class ReceiptUpdate(BaseModel):
    purchase_date: Optional[date] = None
    total_amount: Optional[float] = None
    status: Optional[str] = None
    shop_name: Optional[str] = None
    store_address: Optional[str] = None
    company_id: Optional[int] = Field(default=None, gt=0)
    company_nip: Optional[str] = None
    # Legacy: shop/brand label on this receipt only — never Company.name
    company_name: Optional[str] = None
    items: Optional[List[ReceiptItemCreate]] = None


class ReceiptListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[ReceiptResponse]


class ReceiptExtractAcceptedResponse(BaseModel):
    """Returned immediately from POST /extract and /extract-pdf (202). Poll GET /receipts/{id}."""
    receipt_id: int
    status: str = "PROCESSING"
    message: str = (
        "Receipt uploaded; extraction started. "
        "Poll GET /api/v1/receipts/{receipt_id} until status is not PROCESSING."
    )


class ExtractionJobItem(BaseModel):
    receipt_id: int
    status: str
    created_at: datetime
    extraction_started_at: Optional[datetime] = None
    extraction_attempts: int = 0
    extraction_error: Optional[str] = None
    image_url: Optional[str] = None
    is_stale: bool = False
    is_active_in_process: bool = False
    age_seconds: int = 0

    @field_serializer("image_url")
    @classmethod
    def serialize_image_url(cls, value: Optional[str]) -> Optional[str]:
        return resolve_public_image_url(value)


class ExtractionMetricsResponse(BaseModel):
    active_jobs: int
    queued_total: int
    succeeded_total: int
    failed_total: int
    cancelled_total: int
    retried_total: int
    stale_marked_total: int
    recovered_on_startup_total: int
    avg_duration_ms: Optional[float] = None
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    last_failure_error: Optional[str] = None


class ExtractionStatusResponse(BaseModel):
    """Observability snapshot for mobile debugging and stuck PROCESSING jobs."""
    metrics: ExtractionMetricsResponse
    processing: List[ExtractionJobItem]
    recent_failures: List[ExtractionJobItem]
    stale_minutes: int


# =======================================================
# WARRANTY SCHEMAS
# =======================================================

class WarrantyActiveResponse(BaseModel):
    item_id: int
    receipt_id: int
    item_name: str
    company_name: str
    purchase_date: date
    warranty_end_date: date
    days_remaining: int
    image_url: Optional[str] = None
    shop_name: Optional[str] = None
    store_address: Optional[str] = None
    price: float
    category: Optional[CategoryResponse] = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("image_url")
    @classmethod
    def serialize_image_url(cls, value: Optional[str]) -> Optional[str]:
        return resolve_public_image_url(value)


class WarrantyVaultResponse(WarrantyActiveResponse):
    """Same fields as active list; vault adds warranty_status for filtering context."""
    warranty_status: str  # active | expiring | expired


class WarrantyItemUpdate(BaseModel):
    """Toggle or correct warranty on a single receipt line (sejf edit)."""
    is_under_warranty: Optional[bool] = None
    warranty_end_date: Optional[date] = None


# =======================================================
# CATEGORY SCHEMAS
# =======================================================

class CategorySpending(BaseModel):
    category_id: Optional[int]
    category_name: str
    total_amount: float


class ShopSpending(BaseModel):
    shop_name: str
    total_amount: float


class DashboardSummaryResponse(BaseModel):
    current_month: str
    total_spent_this_month: float
    total_spent_last_month: float
    receipt_count: int
    average_ticket: float
    category_breakdown: List[CategorySpending]
    shop_breakdown: List[ShopSpending]


class TimelineDataPoint(BaseModel):
    date: date
    amount: float


class AnalyticsReportResponse(BaseModel):
    start_date: date
    end_date: date
    total_spent: float
    receipt_count: int
    average_ticket: float
    category_breakdown: List[CategorySpending]
    shop_breakdown: List[ShopSpending]
    timeline: List[TimelineDataPoint]


# =======================================================
# AUTHENTICATION SCHEMAS
# =======================================================

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


class TokenRefreshRequest(BaseModel):
    refresh_token: str