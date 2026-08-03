from datetime import datetime, date
from typing import List, Optional
from sqlalchemy import String, Boolean, Numeric, Date, DateTime, ForeignKey, BigInteger, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.backend.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    first_name: Mapped[str] = mapped_column(String(50))
    last_name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    receipts: Mapped[List["Receipt"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    nip: Mapped[Optional[str]] = mapped_column(String(15), unique=True)  # FIXED: Added unique=True
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    receipts: Mapped[List["Receipt"]] = relationship(back_populates="company")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)

    # Relationships
    items: Mapped[List["ReceiptItem"]] = relationship(back_populates="category")


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    purchase_date: Mapped[Optional[date]] = mapped_column(Date)
    total_amount: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20), default="PROCESSING")  # FIXED: Changed to PROCESSING
    image_url: Mapped[Optional[str]] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Foreign Keys
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))

    # Relationships
    user: Mapped["User"] = relationship(back_populates="receipts")
    company: Mapped[Optional["Company"]] = relationship(back_populates="receipts")
    items: Mapped[List["ReceiptItem"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")


class ReceiptItem(Base):
    __tablename__ = "receipt_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[float] = mapped_column(Numeric(10, 3), default=1.0)
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    is_under_warranty: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    warranty_end_date: Mapped[Optional[date]] = mapped_column(Date)

    # Foreign Keys
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"))
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))

    # Relationships
    receipt: Mapped["Receipt"] = relationship(back_populates="items")
    category: Mapped[Optional["Category"]] = relationship(back_populates="items")