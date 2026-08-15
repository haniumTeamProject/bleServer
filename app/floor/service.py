from fastapi import HTTPException, status as http_status
from sqlalchemy.orm import Session

from app.floor.models import Floor
from app.floor.schemas import FloorRequest, FloorResponse
from app.status import floor_status


def to_response(db: Session, floor: Floor) -> FloorResponse:
    """status 는 저장된 값이 아니라 지금 계산한 값이다 (app/status.py 참고)."""
    return FloorResponse(
        id=floor.id,
        building_id=floor.building_id,
        floor=floor.floor,
        major=floor.major,
        status=floor_status(db, floor),
        scale_m_per_px=floor.scale_m_per_px,
    )


def list_floors(db: Session, building_id: str) -> list[FloorResponse]:
    floors = (
        db.query(Floor)
        .filter(Floor.building_id == building_id)
        .order_by(Floor.floor.asc())
        .all()
    )
    return [to_response(db, f) for f in floors]


def get_floor(db: Session, floor_id: str) -> Floor:
    floor = db.get(Floor, floor_id)
    if floor is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"층 없음: {floor_id}")
    return floor


def create_floor(db: Session, building_id: str, req: FloorRequest) -> FloorResponse:
    floor = Floor(
        building_id=building_id,
        floor=req.floor,
        major=100 + req.floor,      # 층 major 규칙
    )
    db.add(floor)
    db.commit()
    db.refresh(floor)
    return to_response(db, floor)


def delete_floor(db: Session, floor_id: str) -> None:
    floor = get_floor(db, floor_id)
    db.delete(floor)
    db.commit()


def set_scale(db: Session, floor_id: str, scale_m_per_px: float) -> None:
    """지도 검수 화면에서 두 점을 찍어 계산한 축척을 저장한다."""
    floor = get_floor(db, floor_id)
    floor.scale_m_per_px = scale_m_per_px
    db.commit()


def get_scale(db: Session, floor_id: str) -> float | None:
    return get_floor(db, floor_id).scale_m_per_px
