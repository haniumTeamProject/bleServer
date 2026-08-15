"""층·건물의 세팅 진행 상태를 **조회 시점에 계산**한다.

── 왜 저장하지 않고 계산하나 ──────────────────────────────────────

예전에는 `floors.status` 컬럼에 저장해두고 단계마다 한 칸씩 올렸다
(`bump_status(db, floor_id, "floorplan_missing", "review_needed")`).

그런데 그 방식은 **되돌아가지 못한다.** 설계도를 지워도 상태는 그대로 남는다.
비콘을 전부 지워도 `ready` 로 남는다. 관리자가 화면에서 "안내 가능"이라고 보는데
실제로는 안내가 안 되는 상태가 만들어진다.

관리자웹은 처음부터 **계산된 값**을 기대하고 있었다.

    // WEB-FE/src/mocks/db.ts
    // status는 더 이상 시드로 넣지 않는다 — handlers.ts가 매 조회마다
    // 실제 데이터(설계도·마스크·비콘·연결자)로부터 계산해 내려준다.

그래서 그 규칙을 그대로 옮겼다. 아래 `floor_status()` 는
`WEB-FE/src/mocks/handlers.ts` 의 `computeFloorStatus()` 와 같은 순서로 판정한다.
**두 곳이 갈라지면 관리자가 보는 뱃지가 서버와 달라지므로** 고칠 때 같이 고쳐야 한다.
"""

from sqlalchemy.orm import Session

from app.beacon.models import Beacon
from app.connector.models import Connector, ConnectorPosition
from app.floor.models import Floor
from app.floorplan.models import Floorplan
from app.mask.models import FloorMask

# 진행 순서. 앞쪽일수록 덜 된 상태.
STATUS_ORDER = [
    "floorplan_missing",    # 설계도 미업로드
    "review_needed",        # 이동영역(마스크) 미작성
    "scale_missing",        # 축척 미설정
    "beacon_missing",       # 비콘 미등록
    "connector_missing",    # 운행층인데 연결자 좌표가 빠짐
    "ready",                # 안내 가능
]


def floor_status(db: Session, floor: Floor) -> str:
    """이 층이 어느 단계까지 왔는지.

    handlers.ts 의 computeFloorStatus 와 판정 순서가 같아야 한다.
    """
    if db.get(Floorplan, floor.id) is None:
        return "floorplan_missing"

    mask = db.get(FloorMask, floor.id)
    if mask is None or not mask.data_url:
        return "review_needed"

    if floor.scale_m_per_px is None:
        return "scale_missing"

    has_beacon = (
        db.query(Beacon.id).filter(Beacon.floor_id == floor.id).first() is not None
    )
    if not has_beacon:
        return "beacon_missing"

    # 이 층을 운행하는 연결자 중 좌표가 안 찍힌 게 있으면 결손.
    connectors = (
        db.query(Connector).filter(Connector.building_id == floor.building_id).all()
    )
    for c in connectors:
        if floor.floor not in (c.floors or []):
            continue
        placed = (
            db.query(ConnectorPosition.floor_id)
            .filter(
                ConnectorPosition.connector_id == c.id,
                ConnectorPosition.floor_id == floor.id,
            )
            .first()
        )
        if placed is None:
            return "connector_missing"

    return "ready"


def building_status(db: Session, building_id: str) -> str:
    """건물 대표 상태 = 그 건물 층 중 **가장 진행이 덜 된** 것.

    층이 하나도 없으면 설계도 미업로드로 친다.
    """
    floors = db.query(Floor).filter(Floor.building_id == building_id).all()
    if not floors:
        return "floorplan_missing"
    return min(
        (floor_status(db, f) for f in floors),
        key=lambda s: STATUS_ORDER.index(s) if s in STATUS_ORDER else 0,
    )
