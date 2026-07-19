from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.floor import service
from app.floor.schemas import FloorRequest, FloorResponse
from app.security.deps import get_current_admin

router = APIRouter(
    prefix="/api/buildings/{building_id}/floors",
    tags=["floors"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=list[FloorResponse])
def list_floors(building_id: str, db: Session = Depends(get_db)):
    return service.list_floors(db, building_id)


@router.post("", response_model=FloorResponse, status_code=status.HTTP_201_CREATED)
def create_floor(building_id: str, req: FloorRequest, db: Session = Depends(get_db)):
    return service.create_floor(db, building_id, req)


@router.delete("/{floor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_floor(building_id: str, floor_id: str, db: Session = Depends(get_db)):
    service.delete_floor(db, floor_id)
