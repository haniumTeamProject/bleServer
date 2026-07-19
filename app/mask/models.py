from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# 층 하나당 마스크 1개(1:1)라서 floor_id를 그대로 PK로 사용
class FloorMask(Base):
    __tablename__ = "floor_masks"

    floor_id: Mapped[str] = mapped_column(String, primary_key=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 채워진 영역을 담은 투명배경 PNG data URL. 매우 길 수 있어 TEXT로 저장
    data_url: Mapped[str | None] = mapped_column(Text, nullable=True)
