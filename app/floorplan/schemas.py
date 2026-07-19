from app.common import CamelModel


class FloorplanRequest(CamelModel):
    image_url: str


class FloorplanResponse(CamelModel):
    floor_id: str
    image_url: str | None
    extracted: bool
