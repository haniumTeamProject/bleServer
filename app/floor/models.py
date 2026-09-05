import uuid

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Floor(Base):
    __tablename__ = "floors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    building_id: Mapped[str] = mapped_column(
        String, ForeignKey("buildings.id", ondelete="CASCADE"), nullable=False)
    floor: Mapped[int] = mapped_column(Integer, nullable=False)
    major: Mapped[int] = mapped_column(Integer, nullable=False)

    # 도면 1px 이 실제로 몇 m 인지.
    #
    # 관리자가 지도 검수 화면에서 두 점을 찍고 실거리를 입력해 계산한다.
    # **보강비콘 자동배치(D_max 6m)가 이 값 없이는 못 돈다.**
    scale_m_per_px: Mapped[float | None] = mapped_column(Float, nullable=True)

    # status 컬럼은 두지 않는다.
    #
    # 예전에는 여기 저장해두고 단계마다 한 칸씩 올렸는데(bump_status), 그러면
    # 설계도를 지워도 상태가 안 되돌아간다. 관리자웹은 **조회할 때마다 실제
    # 데이터로부터 계산한 값**을 기대한다(mocks/handlers.ts 의 computeFloorStatus).
    # 그래서 status 는 응답을 만들 때 계산한다 — floor/service.py 참고.
