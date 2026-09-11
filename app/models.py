"""SQLAlchemy models."""

from __future__ import annotations

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # Flask-Login interface
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return str(self.id)

    slicers: Mapped[list["Slicer"]] = relationship(
        secondary="user_slicers", back_populates="users"
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="user")

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class UplynkAccount(Base):
    __tablename__ = "uplynk_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # Legacy api_key used for CSL slicer control SHA1 signing
    legacy_api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # Scoped API Key (XAuth/JWT) — used for v4 API discovery.
    # All four fields come from the .env file downloaded from the Uplynk CMS.
    # Marked optional: an admin can skip this and just use manual slicer entry.
    scoped_kid: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scoped_sub: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scoped_private_b64_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    scoped_scp: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    slicers: Mapped[list["Slicer"]] = relationship(
        back_populates="uplynk_account", cascade="all, delete-orphan"
    )

    @property
    def has_scoped_key(self) -> bool:
        """True if all four scoped-key fields are populated."""
        return all(
            [
                self.scoped_kid,
                self.scoped_sub,
                self.scoped_private_b64_encrypted,
                self.scoped_scp,
            ]
        )

    def __repr__(self) -> str:
        return f"<UplynkAccount {self.label}>"


class Slicer(Base):
    __tablename__ = "slicers"

    id: Mapped[int] = mapped_column(primary_key=True)
    uplynk_account_id: Mapped[int] = mapped_column(
        ForeignKey("uplynk_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # slicer id as returned by the Uplynk v4 API (e.g. "slicer30158")
    slicer_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    slicer_api_url: Mapped[str] = mapped_column(String(500), nullable=False)
    region: Mapped[str | None] = mapped_column(String(50), nullable=True)
    protocol: Mapped[str | None] = mapped_column(String(50), nullable=True)
    plugin_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    plugin_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("uplynk_account_id", "slicer_id", name="uq_account_slicer"),
    )

    uplynk_account: Mapped["UplynkAccount"] = relationship(back_populates="slicers")
    users: Mapped[list["User"]] = relationship(
        secondary="user_slicers", back_populates="slicers"
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="slicer")

    def __repr__(self) -> str:
        return f"<Slicer {self.slicer_id}>"


class UserSlicer(Base):
    """Association table: which users can control which slicers."""

    __tablename__ = "user_slicers"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    slicer_id: Mapped[int] = mapped_column(
        ForeignKey("slicers.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class AuditEvent(Base):
    """Record of every slicer control action attempted."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable so we keep the record if a user/slicer is later deleted.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    slicer_id: Mapped[int | None] = mapped_column(
        ForeignKey("slicers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    method: Mapped[str] = mapped_column(String(50), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    user: Mapped["User | None"] = relationship(back_populates="audit_events")
    slicer: Mapped["Slicer | None"] = relationship(back_populates="audit_events")

    def __repr__(self) -> str:
        return f"<AuditEvent {self.method} status={self.status_code}>"
