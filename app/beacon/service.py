from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.beacon.models import Beacon
from app.beacon.schemas import BeaconRequest
from app.floor.service import get_floor


def list_beacons(db: Session, floor_id: str) -> list[Beacon]:
    return db.query(Beacon).filter(Beacon.floor_id == floor_id).all()


def create_beacon(db: Session, floor_id: str, req: BeaconRequest) -> Beacon:
    floor = get_floor(db, floor_id)  # 없으면 404

    beacon = Beacon(
        floor_id=floor_id,
        name=req.name,
        mac=req.mac,
        major=floor.major,          # 층에서 복사 — 클라이언트가 보내지 않는다
        minor=req.minor,
        type=req.type,              # semantic | reinforcement
        x=req.x,
        y=req.y,
        source_uid=req.source_uid,
        source_label=req.source_label,
    )
    db.add(beacon)
    db.commit()
    db.refresh(beacon)
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
    if req.x is not None:
        beacon.x = req.x
    if req.y is not None:
        beacon.y = req.y
    # source_uid 는 map-tool 이 부여한 원본 식별자라 바꾸지 않는다.
    # 라벨만 갱신될 수 있다(도구에서 표시 이름을 바꾼 경우).
    if req.source_label is not None:
        beacon.source_label = req.source_label

    db.commit()
    db.refresh(beacon)
    return beacon


def delete_beacon(db: Session, beacon_id: str) -> None:
    beacon = db.get(Beacon, beacon_id)
    if beacon is not None:
        db.delete(beacon)
        db.commit()
