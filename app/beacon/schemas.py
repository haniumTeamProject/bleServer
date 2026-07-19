from app.common import CamelModel


# 생성/수정 요청 공용. 수정 시 None인 필드는 그대로 유지 (service에서 처리)
class BeaconRequest(CamelModel):
    name: str | None = None
    mac: str | None = None
    minor: int | None = None
    type: str | None = None
    connector_id: str | None = None
    x: float | None = None
    y: float | None = None


class BeaconResponse(CamelModel):
    id: str
    floor_id: str
    name: str | None
    mac: str | None
    major: int | None
    minor: int | None
    type: str | None
    connector_id: str | None
    is_anchor: bool
    x: float | None
    y: float | None
