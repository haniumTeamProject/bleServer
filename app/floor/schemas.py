from app.common import CamelModel


class FloorRequest(CamelModel):
    floor: int


class FloorResponse(CamelModel):
    id: str
    building_id: str
    floor: int
    major: int
    status: str
