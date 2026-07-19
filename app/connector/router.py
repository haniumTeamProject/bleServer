from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.connector import service
from app.connector.schemas import ConnectorRequest, ConnectorResponse
from app.database import get_db
from app.security.deps import get_current_admin

router = APIRouter(
    prefix="/api/buildings/{building_id}/connectors",
    tags=["connectors"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=list[ConnectorResponse])
def list_connectors(building_id: str, db: Session = Depends(get_db)):
    return service.list_connectors(db, building_id)


@router.post("", response_model=ConnectorResponse, status_code=status.HTTP_201_CREATED)
def create_connector(building_id: str, req: ConnectorRequest, db: Session = Depends(get_db)):
    return service.create_connector(db, building_id, req)


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connector(building_id: str, connector_id: str, db: Session = Depends(get_db)):
    service.delete_connector(db, connector_id)
