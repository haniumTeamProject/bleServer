from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.connector import service
from app.connector.schemas import (
    ConnectorPositionRequest, ConnectorRequest, ConnectorResponse,
)
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


# 연결자가 각 층에서 어디에 있는지.
#
# 비콘에 connector_id 를 달지 않고 여기 모아두는 이유는 결손 검수 때문이다 —
# 운행층인데 좌표가 없는 칸을 한눈에 찾으려면 연결자 쪽에 있어야 한다.
# (app/connector/models.py 의 ConnectorPosition 주석 참고)
@router.put("/{connector_id}/positions/{floor_id}", response_model=ConnectorResponse)
def set_position(
    building_id: str,
    connector_id: str,
    floor_id: str,
    req: ConnectorPositionRequest,
    db: Session = Depends(get_db),
):
    return service.set_position(db, connector_id, floor_id, req.x, req.y)


@router.delete("/{connector_id}/positions/{floor_id}", response_model=ConnectorResponse)
def clear_position(
    building_id: str, connector_id: str, floor_id: str, db: Session = Depends(get_db)
):
    return service.clear_position(db, connector_id, floor_id)
