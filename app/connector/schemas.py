from app.common import CamelModel


class ConnectorRequest(CamelModel):
    name: str | None = None
    type: str | None = None          # elevator | stairs
    floors: list[int] = []


class ConnectorPositionRequest(CamelModel):
    """연결자가 이 층에서 어디에 있는지."""

    x: float
    y: float


class ConnectorPositionResponse(CamelModel):
    floor_id: str
    x: float
    y: float


class ConnectorResponse(CamelModel):
    id: str
    building_id: str
    name: str | None
    type: str | None
    floors: list[int]
    # 층별 입구 좌표. 운행층인데 여기 없으면 관리자웹이 "결손"으로 표시한다.
    positions: list[ConnectorPositionResponse] = []
