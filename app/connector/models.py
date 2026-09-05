import uuid

from sqlalchemy import ARRAY, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    building_id: Mapped[str] = mapped_column(
        String, ForeignKey("buildings.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)

    # elevator | stairs
    type: Mapped[str | None] = mapped_column(String, nullable=True)

    # 운행 층 목록. PostgreSQL의 배열 타입 그대로 사용 (Java에서는 별도 테이블이 필요했던 부분)
    floors: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)


class ConnectorPosition(Base):
    """연결자가 각 층에서 어디에 있는지.

    **왜 비콘이 아니라 연결자 쪽에 두나.**

    엘리베이터 하나가 1~5층을 운행하면 층마다 입구가 있다. 이걸 비콘 쪽에
    `connector_id` 로 달아두면 "어느 층 입구가 빠졌는지"를 보려고 전 층의 비콘을
    뒤져야 한다. 연결자 쪽에 모아두면 그 연결자만 보면 결손이 바로 드러난다.

    관리자웹의 연결자 검수 화면이 정확히 그렇게 동작한다 —
    운행층인데 좌표가 없는 칸을 결손으로 표시한다(ConnectorReviewPage.tsx).
    """

    __tablename__ = "connector_positions"

    connector_id: Mapped[str] = mapped_column(
        String, ForeignKey("connectors.id", ondelete="CASCADE"), primary_key=True)
    floor_id: Mapped[str] = mapped_column(
        String, ForeignKey("floors.id", ondelete="CASCADE"), primary_key=True)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
