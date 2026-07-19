from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# 층 하나당 설계도 1개(1:1)라서 floor_id를 그대로 PK로 사용
class Floorplan(Base):
    __tablename__ = "floorplans"

    floor_id: Mapped[str] = mapped_column(String, primary_key=True)

    # 업로드된 이미지 data URL. 매우 길 수 있어 TEXT로 저장
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted: Mapped[bool] = mapped_column(Boolean, default=False)
