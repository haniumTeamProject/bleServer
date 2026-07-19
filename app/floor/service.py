from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.building.service import recompute_status
from app.floor.models import Floor
from app.floor.schemas import FloorRequest


def list_floors(db: Session, building_id: str) -> list[Floor]:
    return (
        db.query(Floor)
        .filter(Floor.building_id == building_id)
        .order_by(Floor.floor.asc())
        .all()
    )


def get_floor(db: Session, floor_id: str) -> Floor:
    floor = db.get(Floor, floor_id)
    if floor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"층 없음: {floor_id}")
    return floor


def create_floor(db: Session, building_id: str, req: FloorRequest) -> Floor:
    floor = Floor(
        building_id=building_id,
        floor=req.floor,
        major=100 + req.floor,
        status="floorplan_missing",
    )
    db.add(floor)
    db.commit()
    db.refresh(floor)

    # 새 층은 floorplan_missing으로 시작하니 건물 상태도 그만큼 뒤로 밀릴 수 있음
    recompute_status(db, building_id)
    return floor


def delete_floor(db: Session, floor_id: str) -> None:
    floor = get_floor(db, floor_id)
    building_id = floor.building_id
    db.delete(floor)
    db.commit()

    # 제일 뒤처진 층이 삭제되면 건물 상태가 앞으로 당겨질 수 있음
    recompute_status(db, building_id)


# 설계도/마스크/비콘 도메인에서 층 상태를 진행시킬 때 호출.
# 지금 상태가 from이면 to로 바꿈 (Java FloorService.bumpStatus()와 동일한 idempotent 가드)
def bump_status(db: Session, floor_id: str, from_status: str, to_status: str) -> None:
    floor = db.get(Floor, floor_id)
    if floor is not None and floor.status == from_status:
        floor.status = to_status
        db.commit()

        # 층 상태 갱신 -> 건물 상태(모든 층 중 가장 뒤처진 단계)도 재계산
        recompute_status(db, floor.building_id)
