from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.floorplan import service
from app.floorplan.schemas import FloorplanRequest, FloorplanResponse
from app.security.deps import get_current_admin

router = APIRouter(
    prefix="/api/floors/{floor_id}/floorplan",
    tags=["floorplan"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=FloorplanResponse | None)
def get_floorplan(floor_id: str, db: Session = Depends(get_db)):
    return service.get_floorplan(db, floor_id)


@router.put("", response_model=FloorplanResponse)
def upload_floorplan(floor_id: str, req: FloorplanRequest, db: Session = Depends(get_db)):
    return service.upload_floorplan(db, floor_id, req)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_floorplan(floor_id: str, db: Session = Depends(get_db)):
    service.delete_floorplan(db, floor_id)
