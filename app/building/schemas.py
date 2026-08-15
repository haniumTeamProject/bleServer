from datetime import datetime

from app.common import CamelModel


class BuildingRequest(CamelModel):
    code: str | None = None
    name: str | None = None
    address: str | None = None
    floor_count: int | None = None


class BuildingResponse(CamelModel):
    id: str
    code: str | None
    name: str | None
    address: str | None
    floor_count: int | None
    favorite: bool
    status: str            # 층 상태를 모아 조회 시점에 계산
    created_at: datetime | None = None
