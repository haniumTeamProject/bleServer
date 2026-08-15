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
    """층과 **거기 딸린 것 전부**를 지운다.

    예전에는 `floors` 행 하나만 지웠다. 이 스키마에는 외래키 제약이 없어서
    (`floor_id` 가 전부 그냥 String 이다) DB 가 대신 정리해 주지 않는다.
    그래서 비콘·랜드마크·설계도·마스크·연결자 좌표가 **주인 없는 행으로 남았다.**

    남은 행은 조용히 쌓이기만 하는 게 아니다. 목적지 목록이나 통계처럼 층을
    거치지 않고 테이블을 직접 훑는 곳에서는 지운 층의 데이터가 그대로 나온다.
    """
    from app.beacon.models import Beacon
    from app.connector.models import ConnectorPosition
    from app.floorplan.models import Floorplan
    from app.landmark.models import Landmark
    from app.mask.models import FloorMask

    floor = get_floor(db, floor_id)
    db.query(Beacon).filter(Beacon.floor_id == floor_id).delete()
    db.query(Landmark).filter(Landmark.floor_id == floor_id).delete()
    db.query(ConnectorPosition).filter(ConnectorPosition.floor_id == floor_id).delete()
    db.query(Floorplan).filter(Floorplan.floor_id == floor_id).delete()
    db.query(FloorMask).filter(FloorMask.floor_id == floor_id).delete()
    db.delete(floor)
    db.commit()


def set_scale(db: Session, floor_id: str, scale_m_per_px: float) -> None:
    """지도 검수 화면에서 두 점을 찍어 계산한 축척을 저장한다."""
    floor = get_floor(db, floor_id)
    floor.scale_m_per_px = scale_m_per_px
    db.commit()


def get_scale(db: Session, floor_id: str) -> float | None:
    return get_floor(db, floor_id).scale_m_per_px
