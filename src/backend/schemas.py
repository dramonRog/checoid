from datetime import datetime, date
from typing import List, Optional
from pydantic import BaseModel, EmailStr, ConfigDict, Field

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
    quantity: float = 1.0
    price: float
    is_under_warranty: Optional[bool] = False
    warranty_end_date: Optional[date] = None
    category_id: Optional[int] = Field(default=None, gt=0)


class ReceiptItemResponse(ReceiptItemCreate):
    id: int
    receipt_id: int
    category: Optional[CategoryResponse] = None

    model_config = ConfigDict(from_attributes=True)


# =======================================================
# RECEIPT SCHEMAS
# =======================================================

class ReceiptBase(BaseModel):
    purchase_date: Optional[date] = None
    total_amount: Optional[float] = None
    status: str = "PROCESSING"
    image_url: Optional[str] = None
    company_id: Optional[int] = None


class ReceiptCreate(ReceiptBase):
    items: Optional[List[ReceiptItemCreate]] = None


class ReceiptResponse(ReceiptBase):
    id: int
    user_id: int
    created_at: datetime

    company: Optional[CompanyResponse] = None
    items: List[ReceiptItemResponse] = []

    model_config = ConfigDict(from_attributes=True)


class ReceiptUpdate(BaseModel):
    purchase_date: Optional[date] = None
    total_amount: Optional[float] = None
    status: Optional[str] = None
    company_id: Optional[int] = Field(default=None, gt=0)
    company_nip: Optional[str] = None
    company_name: Optional[str] = None
    items: Optional[List[ReceiptItemCreate]] = None


class ReceiptListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[ReceiptResponse]


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

    model_config = ConfigDict(from_attributes=True)


# =======================================================
# CATEGORY SCHEMAS
# =======================================================

class CategorySpending(BaseModel):
    category_id: Optional[int]
    category_name: str
    total_amount: float


class DashboardSummaryResponse(BaseModel):
    current_month: str
    total_spent_this_month: float
    total_spent_last_month: float
    category_breakdown: List[CategorySpending]


# =======================================================
# AUTHENTICATION SCHEMAS
# =======================================================

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


class TokenRefreshRequest(BaseModel):
    refresh_token: str