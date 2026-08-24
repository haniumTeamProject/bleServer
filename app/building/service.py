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
    """건물과 **그 밑의 층 전부**를 지운다.

    예전에는 `buildings` 행 하나만 지웠다. 이 스키마에는 외래키 제약이 없어서
    (`building_id`·`floor_id` 가 전부 그냥 String) DB 가 대신 정리해 주지 않는다.
    그래서 층이 통째로 **주인 없는 행**으로 남았다.

    층 삭제(`delete_floor`)는 같은 문제를 이미 고쳤는데 건물 쪽이 빠져 있었다.
    그리고 층이 고아가 되면 관리자웹 목록에 안 뜨므로 **손으로 지울 방법조차
    없어진다.** 실제로 실측 DB 에 고아 층 4개(비콘 76개·목적지 44개)가 쌓여 있었고,
    폰이 그중 하나를 잡는 바람에 목적지가 3개만 내려가는 것을 한참 뒤에 알았다.

    정리는 `_purge_floor` 하나로 모은다 — 표가 늘 때 고칠 곳이 둘이면 또 갈라진다.
    """
    from app.floor.models import Floor
    from app.floor.service import _purge_floor

    building = get_building(db, building_id)

    for floor_id, in db.query(Floor.id).filter(Floor.building_id == building_id).all():
        _purge_floor(db, floor_id)

    db.delete(building)
    db.commit()
