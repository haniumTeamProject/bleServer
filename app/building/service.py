from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.building.models import Building
from app.building.schemas import BuildingRequest
from app.floor.models import Floor

# 층 진행 단계 순서. 건물 상태는 "가장 뒤처진 층" 기준으로 매김.
# connector_missing은 층 단위가 아니라 건물 단위(수직 연결자) 개념이라 여기 집계에는 안 씀.
_FLOOR_STAGE_ORDER = ["floorplan_missing", "review_needed", "beacon_missing", "ready"]


def recompute_status(db: Session, building_id: str) -> None:
    building = db.get(Building, building_id)
    if building is None:
        return

    floors = db.query(Floor).filter(Floor.building_id == building_id).all()
    if not floors:
        building.status = "floorplan_missing"
    else:
        building.status = min(
            floors,
            key=lambda f: _FLOOR_STAGE_ORDER.index(f.status)
            if f.status in _FLOOR_STAGE_ORDER
            else 0,
        ).status

    db.commit()


def list_buildings(db: Session) -> list[Building]:
    return db.query(Building).all()


def get_building(db: Session, building_id: str) -> Building:
    building = db.get(Building, building_id)
    if building is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"건물 없음: {building_id}")
    return building


def create_building(db: Session, req: BuildingRequest) -> Building:
    building = Building(
        code=req.code,
        name=req.name,
        address=req.address,
        floor_count=req.floor_count,
        status="floorplan_missing",
    )
    db.add(building)
    db.commit()
    db.refresh(building)
    return building


def update_building(db: Session, building_id: str, req: BuildingRequest) -> Building:
    building = get_building(db, building_id)
    if req.code is not None:
        building.code = req.code
    if req.name is not None:
        building.name = req.name
    if req.address is not None:
        building.address = req.address
    if req.floor_count is not None:
        building.floor_count = req.floor_count
    db.commit()
    db.refresh(building)
    return building


def delete_building(db: Session, building_id: str) -> None:
    building = get_building(db, building_id)
    db.delete(building)
    db.commit()
