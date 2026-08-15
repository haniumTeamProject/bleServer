from app.common import CamelModel


class FloorRequest(CamelModel):
    floor: int


class FloorResponse(CamelModel):
    id: str
    building_id: str
    floor: int
    major: int
    status: str            # 조회 시점에 계산 (floorplan/mask/scale/beacon/connector 로부터)
    scale_m_per_px: float | None = None


class FloorScaleRequest(CamelModel):
    """도면 1px 이 실제 몇 m 인지.

    관리자가 지도 검수 화면에서 두 점을 찍고 실거리를 입력하면 계산되는 값이다.
    보강비콘 자동배치(D_max 6m)가 이 값 없이는 돌지 않는다.
    """

    scale_m_per_px: float


class FloorScaleResponse(CamelModel):
    scale_m_per_px: float
