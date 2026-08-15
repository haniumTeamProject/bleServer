from app.common import CamelModel


# 생성/수정 요청 공용. 수정 시 None인 필드는 그대로 유지 (service에서 처리)
#
# 필드 구성은 관리자웹의 CreateBeaconInput / UpdateBeaconInput 과 1:1로 맞췄다
# (WEB-FE/src/features/beacons/api.ts).
class BeaconRequest(CamelModel):
    name: str | None = None
    mac: str | None = None
    minor: int | None = None
    type: str | None = None          # semantic | reinforcement
    x: float | None = None
    y: float | None = None
    source_uid: str | None = None    # map-tool 재가져오기 매칭 키
    source_label: str | None = None


class BeaconResponse(CamelModel):
    id: str
    floor_id: str
    name: str | None
    mac: str | None
    major: int | None                # 층에서 복사 (서버 계산)
    minor: int | None
    type: str | None
    x: float | None
    y: float | None
    source_uid: str | None
    source_label: str | None
