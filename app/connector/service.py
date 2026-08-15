from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.connector.models import Connector, ConnectorPosition
from app.connector.schemas import (
    ConnectorPositionResponse, ConnectorRequest, ConnectorResponse,
)


def to_response(db: Session, connector: Connector) -> ConnectorResponse:
    rows = (
        db.query(ConnectorPosition)
        .filter(ConnectorPosition.connector_id == connector.id)
        .all()
    )
    return ConnectorResponse(
        id=connector.id,
        building_id=connector.building_id,
        name=connector.name,
        type=connector.type,
        floors=connector.floors or [],
        positions=[
            ConnectorPositionResponse(floor_id=r.floor_id, x=r.x, y=r.y) for r in rows
        ],
    )


def list_connectors(db: Session, building_id: str) -> list[ConnectorResponse]:
    rows = db.query(Connector).filter(Connector.building_id == building_id).all()
    return [to_response(db, c) for c in rows]


def get_connector(db: Session, connector_id: str) -> Connector:
    connector = db.get(Connector, connector_id)
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"연결자 없음: {connector_id}")
    return connector


def create_connector(db: Session, building_id: str, req: ConnectorRequest) -> ConnectorResponse:
    connector = Connector(
        building_id=building_id,
        name=req.name,
        type=req.type,
        floors=sorted(req.floors),
    )
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return to_response(db, connector)


def delete_connector(db: Session, connector_id: str) -> None:
    connector = get_connector(db, connector_id)
    # 좌표도 같이 지운다 (FK 제약을 안 걸었으므로 직접)
    db.query(ConnectorPosition).filter(
        ConnectorPosition.connector_id == connector_id
    ).delete()
    db.delete(connector)
    db.commit()


def set_position(
    db: Session, connector_id: str, floor_id: str, x: float, y: float
) -> ConnectorResponse:
    """이 연결자가 이 층에서 어디에 있는지 지정한다. 이미 있으면 덮어쓴다."""
    connector = get_connector(db, connector_id)
    row = (
        db.query(ConnectorPosition)
        .filter(
            ConnectorPosition.connector_id == connector_id,
            ConnectorPosition.floor_id == floor_id,
        )
        .first()
    )
    if row is None:
        db.add(ConnectorPosition(connector_id=connector_id, floor_id=floor_id, x=x, y=y))
    else:
        row.x, row.y = x, y
    db.commit()
    return to_response(db, connector)


def clear_position(db: Session, connector_id: str, floor_id: str) -> ConnectorResponse:
    connector = get_connector(db, connector_id)
    db.query(ConnectorPosition).filter(
        ConnectorPosition.connector_id == connector_id,
        ConnectorPosition.floor_id == floor_id,
    ).delete()
    db.commit()
    return to_response(db, connector)
