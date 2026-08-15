from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.landmark.models import Landmark
from app.landmark.schemas import LandmarkRequest


def list_landmarks(db: Session, floor_id: str) -> list[Landmark]:
    return db.query(Landmark).filter(Landmark.floor_id == floor_id).all()


def create_landmark(db: Session, floor_id: str, req: LandmarkRequest) -> Landmark:
    landmark = Landmark(
        floor_id=floor_id,
        name=req.name,
        category=req.category,
        x=req.x,
        y=req.y,
        source_uid=req.source_uid,
        source_label=req.source_label,
    )
    db.add(landmark)
    db.commit()
    db.refresh(landmark)
    return landmark


def update_landmark(db: Session, landmark_id: str, req: LandmarkRequest) -> Landmark:
    landmark = db.get(Landmark, landmark_id)
    if landmark is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"목적지 없음: {landmark_id}")

    if req.name is not None:
        landmark.name = req.name
    if req.category is not None:
        landmark.category = req.category
    if req.x is not None:
        landmark.x = req.x
    if req.y is not None:
        landmark.y = req.y
    # source_uid 는 재가져오기 매칭 키라 바꾸지 않는다 (Beacon 과 동일)
    if req.source_label is not None:
        landmark.source_label = req.source_label

    db.commit()
    db.refresh(landmark)
    return landmark


def delete_landmark(db: Session, landmark_id: str) -> None:
    landmark = db.get(Landmark, landmark_id)
    if landmark is not None:
        db.delete(landmark)
        db.commit()
