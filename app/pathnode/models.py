from sqlalchemy import JSON, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# 층 하나당 경로노드 그래프 1개(1:1)라서 floor_id를 그대로 PK로 사용 (floor_masks와 같은 방식).
#
# nodes/edges는 관리자웹(PathNodePage.tsx)이 다루는 단위 그대로 — 코너·연결자입구·목적지입구·
# 맞은편(facing) 노드 전체 + 벽선·건너기 엣지 전체를 층 하나 분량 통째로 저장한다. 종류별로 쪼개
# 저장하지 않는 이유는 WEB-FE/src/features/mapEditor/api.ts의 PathNodesData 주석 참고 — 조회할 때
# 여러 번 합칠 필요 없이 이 테이블 한 행이면 그 층 그래프 전체가 복원된다.
class FloorPathNodes(Base):
    __tablename__ = "floor_path_nodes"

    floor_id: Mapped[str] = mapped_column(String, primary_key=True)
    mask_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mask_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nodes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    edges: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 관리자가 경로노드 화면에서 정한 값. 그래프를 만들 때 쓴 값(crossing_max_m)과
    # 경로를 고를 때 쓸 값(cross_penalty_m)을 같이 남긴다.
    #
    # crossing_max_m 은 서버가 계산에 쓰지는 않는다 — 간선은 이미 만들어져 저장된
    # 상태다. 화면을 다시 열었을 때 저장 당시 값으로 되돌아가야 하고, 이 그래프가
    # 어떤 값으로 만들어졌는지 나중에 알 수 있어야 해서 같이 보관한다.
    #
    # 컬럼이 생기기 전에 저장된 행은 NULL 이다. 읽는 쪽에서 기본값으로 대체한다.
    cross_penalty_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    crossing_max_m: Mapped[float | None] = mapped_column(Float, nullable=True)
