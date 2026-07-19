from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.building import service
from app.building.schemas import BuildingRequest, BuildingResponse
from app.database import get_db
from app.security.deps import get_current_admin

router = APIRouter(
    prefix="/api/buildings",
    tags=["buildings"],
    dependencies=[Depends(get_current_admin)],  # 이 router 전체가 로그인 필요
)


@router.get("", response_model=list[BuildingResponse])
def list_buildings(db: Session = Depends(get_db)):
    return service.list_buildings(db)


@router.post("", response_model=BuildingResponse, status_code=status.HTTP_201_CREATED)
def create_building(req: BuildingRequest, db: Session = Depends(get_db)):
    return service.create_building(db, req)


@router.get("/{building_id}", response_model=BuildingResponse)
def get_building(building_id: str, db: Session = Depends(get_db)):
    return service.get_building(db, building_id)


@router.patch("/{building_id}", response_model=BuildingResponse)
def update_building(building_id: str, req: BuildingRequest, db: Session = Depends(get_db)):
    return service.update_building(db, building_id, req)


@router.delete("/{building_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_building(building_id: str, db: Session = Depends(get_db)):
    service.delete_building(db, building_id)
