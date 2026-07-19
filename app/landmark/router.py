from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.landmark import service
from app.landmark.schemas import LandmarkRequest, LandmarkResponse
from app.security.deps import get_current_admin

router = APIRouter(
    prefix="/api/floors/{floor_id}/landmarks",
    tags=["landmarks"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=list[LandmarkResponse])
def list_landmarks(floor_id: str, db: Session = Depends(get_db)):
    return service.list_landmarks(db, floor_id)


@router.post("", response_model=LandmarkResponse, status_code=status.HTTP_201_CREATED)
def create_landmark(floor_id: str, req: LandmarkRequest, db: Session = Depends(get_db)):
    return service.create_landmark(db, floor_id, req)


@router.patch("/{landmark_id}", response_model=LandmarkResponse)
def update_landmark(floor_id: str, landmark_id: str, req: LandmarkRequest, db: Session = Depends(get_db)):
    return service.update_landmark(db, landmark_id, req)


@router.delete("/{landmark_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_landmark(floor_id: str, landmark_id: str, db: Session = Depends(get_db)):
    service.delete_landmark(db, landmark_id)
