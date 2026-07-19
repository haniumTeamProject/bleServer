from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.beacon import service
from app.beacon.schemas import BeaconRequest, BeaconResponse
from app.database import get_db
from app.security.deps import get_current_admin

router = APIRouter(
    prefix="/api/floors/{floor_id}/beacons",
    tags=["beacons"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=list[BeaconResponse])
def list_beacons(floor_id: str, db: Session = Depends(get_db)):
    return service.list_beacons(db, floor_id)


@router.post("", response_model=BeaconResponse, status_code=status.HTTP_201_CREATED)
def create_beacon(floor_id: str, req: BeaconRequest, db: Session = Depends(get_db)):
    return service.create_beacon(db, floor_id, req)


@router.patch("/{beacon_id}", response_model=BeaconResponse)
def update_beacon(floor_id: str, beacon_id: str, req: BeaconRequest, db: Session = Depends(get_db)):
    return service.update_beacon(db, beacon_id, req)


@router.delete("/{beacon_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_beacon(floor_id: str, beacon_id: str, db: Session = Depends(get_db)):
    service.delete_beacon(db, beacon_id)
