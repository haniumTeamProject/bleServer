import uuid

from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Landmark(Base):
    """사용자가 목적지로 말할 수 있는 지점."""

    __tablename__ = "landmarks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    floor_id: Mapped[str] = mapped_column(String, nullable=False)

    # 사용자가 음성으로 말하는 목적지 이름
    name: Mapped[str | None] = mapped_column(String, nullable=True)

    # 자유 입력 분류 (예: 강의실, 화장실, 진료실). **고정 목록이 아니다.**
    #
    # 예전에는 type(room|restroom|facility|entrance) 4종 고정이었는데,
    # 건물 종류마다 필요한 분류가 달라 담을 수 없었다(병원의 "채혈실" 등).
    category: Mapped[str | None] = mapped_column(String, nullable=True)

    visual_tag_id: Mapped[str | None] = mapped_column(String, nullable=True)

    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)

    # map-tool 재가져오기 매칭 키 (Beacon 과 같은 목적)
    source_uid: Mapped[str | None] = mapped_column(String, nullable=True)
    source_label: Mapped[str | None] = mapped_column(String, nullable=True)
