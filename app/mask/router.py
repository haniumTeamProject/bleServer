from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.mask import service
from app.mask.schemas import FloorMaskRequest, FloorMaskResponse
from app.security.deps import get_current_admin

router = APIRouter(
    prefix="/api/floors/{floor_id}/mask",
    tags=["mask"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=FloorMaskResponse | None)
def get_mask(floor_id: str, db: Session = Depends(get_db)):
    return service.get_mask(db, floor_id)


@router.put("")
def save_mask(floor_id: str, req: FloorMaskRequest, db: Session = Depends(get_db)):
    service.save_mask(db, floor_id, req)
    return {"ok": True}
