from sqlalchemy.orm import Session

from app.floor.service import bump_status
from app.floorplan.models import Floorplan
from app.floorplan.schemas import FloorplanRequest


def get_floorplan(db: Session, floor_id: str) -> Floorplan | None:
    return db.get(Floorplan, floor_id)


def upload_floorplan(db: Session, floor_id: str, req: FloorplanRequest) -> Floorplan:
    # TODO: 실제 서버는 여기서 벽·이동영역을 자동 추출해야 함 (이미지 분석 로직).
    # 우선은 즉시 완료 처리.
    floorplan = db.get(Floorplan, floor_id)
    if floorplan is None:
        floorplan = Floorplan(floor_id=floor_id)
        db.add(floorplan)

    floorplan.image_url = req.image_url
    floorplan.extracted = True
    db.commit()
    db.refresh(floorplan)

    bump_status(db, floor_id, "floorplan_missing", "review_needed")
    return floorplan


def delete_floorplan(db: Session, floor_id: str) -> None:
    floorplan = db.get(Floorplan, floor_id)
    if floorplan is not None:
        db.delete(floorplan)
        db.commit()
