from sqlalchemy.orm import Session

from app.pathnode.models import FloorPathNodes
from app.pathnode.schemas import FloorPathNodesRequest


def get_path_nodes(db: Session, floor_id: str) -> FloorPathNodes | None:
    return db.get(FloorPathNodes, floor_id)


def save_path_nodes(db: Session, floor_id: str, req: FloorPathNodesRequest) -> FloorPathNodes:
    row = db.get(FloorPathNodes, floor_id)
    if row is None:
        row = FloorPathNodes(floor_id=floor_id)
        db.add(row)

    row.mask_w = req.mask_w
    row.mask_h = req.mask_h
    # 보내지 않은 값은 기존 값을 지우지 않고 그대로 둔다 — 값을 보내지 않는 옛 관리자웹이
    # 저장하면 이미 정해둔 설정이 NULL 로 날아가고, 안내가 조용히 기본값으로 돌아간다.
    if req.cross_penalty_m is not None:
        row.cross_penalty_m = req.cross_penalty_m
    if req.crossing_max_m is not None:
        row.crossing_max_m = req.crossing_max_m
    # camelCase 별칭 그대로(by_alias=True) 저장해둔다 — 그래야 다시 읽을 때(JSON 컬럼 → 응답 스키마)
    # 관리자웹이 보낸 것과 같은 모양으로 그대로 돌려줄 수 있다.
    row.nodes = [n.model_dump(by_alias=True) for n in req.nodes]
    row.edges = [e.model_dump(by_alias=True) for e in req.edges]
    db.commit()
    db.refresh(row)

    return row
