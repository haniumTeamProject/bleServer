import uuid

from sqlalchemy import ARRAY, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    building_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)

    # elevator | stairs
    type: Mapped[str | None] = mapped_column(String, nullable=True)

    # 운행 층 목록. PostgreSQL의 배열 타입 그대로 사용 (Java에서는 별도 테이블이 필요했던 부분)
    floors: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
