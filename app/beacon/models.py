import uuid

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Beacon(Base):
    """설치된 비콘 하나.

    필드 구성은 **관리자웹(WEB-FE)의 도메인 모델을 기준**으로 맞췄다.
    자세한 근거는 docs/WEBFE_접합_변경기록.md 참고.
    """

    __tablename__ = "beacons"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    floor_id: Mapped[str] = mapped_column(
        String, ForeignKey("floors.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    mac: Mapped[str | None] = mapped_column(String, nullable=True)

    # 소속 층의 major 값을 그대로 복사 저장 (서버가 계산). 층 = 100 + 층번호
    major: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minor: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # semantic | reinforcement
    #
    #   semantic       앵커·코너·연결자입구·랜드마크출입구 등 경로상 의미 있는 지점.
    #                  사람이 지도에서 직접 찍는다.
    #   reinforcement  의미비콘 사이 간격이 D_max(6m)를 넘을 때 자동으로 채워 넣는 비콘.
    #
    # 관리자웹의 보강비콘 자동배치 기능이 이 구분을 전제로 동작한다
    # (WEB-FE/src/lib/reinforcementBeacons.ts).
    type: Mapped[str | None] = mapped_column(String, nullable=True)

    # 설계도 좌표 (900 기준)
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 지도 편집 도구(map-tool)에서 가져온 원본 식별자.
    #
    # **재가져오기의 매칭 키다.** 지도에서 비콘 위치를 조정하고 다시 가져올 때
    # 이 값으로 기존 항목을 찾아 좌표만 갱신한다. 저장되지 않으면 매번 전부
    # 새로 만들어져서 관리자가 입력한 MAC·minor 가 통째로 날아간다
    # (WEB-FE/src/lib/mapImport.ts 의 diffImport).
    source_uid: Mapped[str | None] = mapped_column(String, nullable=True)
    source_label: Mapped[str | None] = mapped_column(String, nullable=True)
