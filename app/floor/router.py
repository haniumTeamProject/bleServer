from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.floor import service
from app.floor.schemas import (
    FloorRequest, FloorResponse, FloorScaleRequest, FloorScaleResponse,
)
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


# 축척은 층 하나에 붙지만 경로가 /api/floors/... 라 prefix 가 달라서 라우터를 따로 둔다.
# (층 CRUD 는 /api/buildings/{buildingId}/floors 아래에 있다)
scale_router = APIRouter(
    prefix="/api/floors/{floor_id}",
    tags=["floors"],
    dependencies=[Depends(get_current_admin)],
)


@scale_router.get("/scale", response_model=FloorScaleResponse | None)
def get_scale(floor_id: str, db: Session = Depends(get_db)):
    """아직 안 정했으면 null. 관리자웹이 그 경우를 scale_missing 으로 표시한다."""
    value = service.get_scale(db, floor_id)
    if value is None:
        return None
    return FloorScaleResponse(scale_m_per_px=value)


@scale_router.put("/scale")
def put_scale(floor_id: str, req: FloorScaleRequest, db: Session = Depends(get_db)):
    service.set_scale(db, floor_id, req.scale_m_per_px)
    return {"ok": True}
