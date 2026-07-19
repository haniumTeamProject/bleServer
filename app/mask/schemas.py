from app.common import CamelModel


class FloorMaskRequest(CamelModel):
    width: int
    height: int
    data_url: str


class FloorMaskResponse(CamelModel):
    floor_id: str
    width: int | None
    height: int | None
    data_url: str | None
