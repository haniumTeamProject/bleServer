from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.pathnode import service
from app.pathnode.schemas import FloorPathNodesRequest, FloorPathNodesResponse
from app.security.deps import get_current_admin

router = APIRouter(
    prefix="/api/floors/{floor_id}/path-nodes",
    tags=["path-nodes"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=FloorPathNodesResponse | None)
def get_path_nodes(floor_id: str, db: Session = Depends(get_db)):
    return service.get_path_nodes(db, floor_id)


@router.put("")
def save_path_nodes(floor_id: str, req: FloorPathNodesRequest, db: Session = Depends(get_db)):
    service.save_path_nodes(db, floor_id, req)
    return {"ok": True}
