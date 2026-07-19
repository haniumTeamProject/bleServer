from sqlalchemy.orm import Session

from app.floor.service import bump_status
from app.mask.models import FloorMask
from app.mask.schemas import FloorMaskRequest


def get_mask(db: Session, floor_id: str) -> FloorMask | None:
    return db.get(FloorMask, floor_id)


def save_mask(db: Session, floor_id: str, req: FloorMaskRequest) -> FloorMask:
    mask = db.get(FloorMask, floor_id)
    if mask is None:
        mask = FloorMask(floor_id=floor_id)
        db.add(mask)

    mask.width = req.width
    mask.height = req.height
    mask.data_url = req.data_url
    db.commit()
    db.refresh(mask)

    bump_status(db, floor_id, "review_needed", "beacon_missing")
    return mask
