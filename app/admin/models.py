import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    # BCrypt로 해싱된 값만 저장 (평문 저장 금지)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)

    name: Mapped[str | None] = mapped_column(String, nullable=True)
    org: Mapped[str | None] = mapped_column(String, nullable=True)  # 소속 기관
    position: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    building: Mapped[str | None] = mapped_column(String, nullable=True)  # 담당 건물

    # pending | active | rejected
    status: Mapped[str] = mapped_column(String, default="pending")

    # super_admin | admin
    role: Mapped[str] = mapped_column(String, default="admin")

    official_doc_url: Mapped[str | None] = mapped_column(String, nullable=True)

    # 승인 감사 로그
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
