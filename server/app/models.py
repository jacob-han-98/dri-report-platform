"""SQLAlchemy ORM models. Mirror the schema in spec §4."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String, primary_key=True)
    google_sub: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    invited_at: Mapped[datetime | None] = mapped_column(nullable=True)
    invited_by: Mapped[str | None] = mapped_column(String, nullable=True)


class Report(Base):
    __tablename__ = "reports"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_email: Mapped[str] = mapped_column(
        String, ForeignKey("users.email"), nullable=False
    )
    visibility: Mapped[str] = mapped_column(String, nullable=False, default="restricted")
    type: Mapped[str] = mapped_column(String, nullable=False, default="static")
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    entry_point: Mapped[str] = mapped_column(String, nullable=False, default="index.html")
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    last_viewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    viewers: Mapped[list["ReportViewer"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class ReportViewer(Base):
    __tablename__ = "report_viewers"

    slug: Mapped[str] = mapped_column(
        String, ForeignKey("reports.slug", ondelete="CASCADE"), primary_key=True
    )
    user_email: Mapped[str] = mapped_column(
        String, ForeignKey("users.email"), primary_key=True
    )
    granted_by: Mapped[str] = mapped_column(String, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())

    report: Mapped[Report] = relationship(back_populates="viewers")


class ApiToken(Base):
    __tablename__ = "api_tokens"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    prefix: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_email: Mapped[str] = mapped_column(
        String, ForeignKey("users.email"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_email: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    token_prefix: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource: Mapped[str | None] = mapped_column(String, nullable=True)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
