from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.building.models import Building
from app.building.schemas import BuildingRequest, BuildingResponse
from app.status import building_status


def to_response(db: Session, building: Building) -> BuildingResponse:
    """status 는 저장값이 아니라 층들로부터 지금 계산한 값 (app/status.py)."""
    return BuildingResponse(
        id=building.id,
        code=building.code,
        name=building.name,
        address=building.address,
        floor_count=building.floor_count,
        favorite=building.favorite,
        status=building_status(db, building.id),
        created_at=building.created_at,
    )


def list_buildings(db: Session) -> list[BuildingResponse]:
    return [to_response(db, b) for b in db.query(Building).all()]


def get_building(db: Session, building_id: str) -> Building:
    building = db.get(Building, building_id)
    if building is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"건물 없음: {building_id}")
    return building


def create_building(db: Session, req: BuildingRequest) -> BuildingResponse:
    building = Building(
        code=req.code,
        name=req.name,
        address=req.address,
        floor_count=req.floor_count,
    )
    db.add(building)
    db.commit()
    db.refresh(building)
    return to_response(db, building)


def update_building(db: Session, building_id: str, req: BuildingRequest) -> BuildingResponse:
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
    return to_response(db, building)


def delete_building(db: Session, building_id: str) -> None:
    building = get_building(db, building_id)
    db.delete(building)
    db.commit()
