from app.common import CamelModel


class ConnectorRequest(CamelModel):
    name: str | None = None
    type: str | None = None
    floors: list[int] = []


class ConnectorResponse(CamelModel):
    id: str
    building_id: str
    name: str | None
    type: str | None
    floors: list[int]
