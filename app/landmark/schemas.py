from app.common import CamelModel


class LandmarkRequest(CamelModel):
    name: str | None = None
    category: str | None = None      # 자유 입력 분류 (고정 목록 아님)
    x: float | None = None
    y: float | None = None
    source_uid: str | None = None
    source_label: str | None = None


class LandmarkResponse(CamelModel):
    id: str
    floor_id: str
    name: str | None
    category: str | None
    visual_tag_id: str | None
    x: float | None
    y: float | None
    source_uid: str | None
    source_label: str | None
