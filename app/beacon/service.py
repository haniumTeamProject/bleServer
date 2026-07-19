from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.beacon.models import Beacon
from app.beacon.schemas import BeaconRequest
from app.floor.service import bump_status, get_floor


def list_beacons(db: Session, floor_id: str) -> list[Beacon]:
    return db.query(Beacon).filter(Beacon.floor_id == floor_id).all()


def create_beacon(db: Session, floor_id: str, req: BeaconRequest) -> Beacon:
    floor = get_floor(db, floor_id)  # 없으면 404

    beacon = Beacon(
        floor_id=floor_id,
        name=req.name,
        mac=req.mac,
        major=floor.major,
        minor=req.minor,
        type=req.type,
        connector_id=req.connector_id,
        is_anchor=(req.type == "anchor"),
        x=req.x,
        y=req.y,
    )
    db.add(beacon)
    db.commit()
    db.refresh(beacon)

    # 첫 비콘 등록 시 층 상태: beacon_missing -> ready
    bump_status(db, floor_id, "beacon_missing", "ready")
    return beacon


def update_beacon(db: Session, beacon_id: str, req: BeaconRequest) -> Beacon:
    beacon = db.get(Beacon, beacon_id)
    if beacon is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"비콘 없음: {beacon_id}")

    if req.name is not None:
        beacon.name = req.name
    if req.mac is not None:
        beacon.mac = req.mac
    if req.minor is not None:
        beacon.minor = req.minor
    if req.type is not None:
        beacon.type = req.type
    if req.connector_id is not None:
        beacon.connector_id = req.connector_id
    if req.x is not None:
        beacon.x = req.x
    if req.y is not None:
        beacon.y = req.y
    beacon.is_anchor = beacon.type == "anchor"

    db.commit()
    db.refresh(beacon)
    return beacon


def delete_beacon(db: Session, beacon_id: str) -> None:
    beacon = db.get(Beacon, beacon_id)
    if beacon is not None:
        db.delete(beacon)
        db.commit()
