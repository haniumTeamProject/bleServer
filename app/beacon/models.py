import uuid

from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Beacon(Base):
    __tablename__ = "beacons"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    floor_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    mac: Mapped[str | None] = mapped_column(String, nullable=True)

    # 소속 층의 major 값을 그대로 복사 저장 (서버가 계산)
    major: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minor: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # anchor | checkpoint | connector
    type: Mapped[str | None] = mapped_column(String, nullable=True)

    # 엘베/계단(connector) 타입일 때만 사용
    connector_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # type == "anchor" 여부 (서버가 계산)
    is_anchor: Mapped[bool] = mapped_column(Boolean, default=False)

    # 설계도 좌표 (900 기준)
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
