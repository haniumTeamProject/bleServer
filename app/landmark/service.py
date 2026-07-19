from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.landmark.models import Landmark
from app.landmark.schemas import LandmarkRequest


def list_landmarks(db: Session, floor_id: str) -> list[Landmark]:
    return db.query(Landmark).filter(Landmark.floor_id == floor_id).all()


def create_landmark(db: Session, floor_id: str, req: LandmarkRequest) -> Landmark:
    landmark = Landmark(floor_id=floor_id, name=req.name, type=req.type, x=req.x, y=req.y)
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
    if req.type is not None:
        landmark.type = req.type
    if req.x is not None:
        landmark.x = req.x
    if req.y is not None:
        landmark.y = req.y

    db.commit()
    db.refresh(landmark)
    return landmark


def delete_landmark(db: Session, landmark_id: str) -> None:
    landmark = db.get(Landmark, landmark_id)
    if landmark is not None:
        db.delete(landmark)
        db.commit()
