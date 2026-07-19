from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.connector.models import Connector
from app.connector.schemas import ConnectorRequest


def list_connectors(db: Session, building_id: str) -> list[Connector]:
    return db.query(Connector).filter(Connector.building_id == building_id).all()


def create_connector(db: Session, building_id: str, req: ConnectorRequest) -> Connector:
    connector = Connector(
        building_id=building_id,
        name=req.name,
        type=req.type,
        floors=sorted(req.floors),
    )
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return connector


def delete_connector(db: Session, connector_id: str) -> None:
    connector = db.get(Connector, connector_id)
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"연결자 없음: {connector_id}")
    db.delete(connector)
    db.commit()
