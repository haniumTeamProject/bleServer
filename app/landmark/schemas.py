from app.common import CamelModel


class LandmarkRequest(CamelModel):
    name: str | None = None
    type: str | None = None
    x: float | None = None
    y: float | None = None


class LandmarkResponse(CamelModel):
    id: str
    floor_id: str
    name: str | None
    type: str | None
    visual_tag_id: str | None
    x: float | None
    y: float | None
