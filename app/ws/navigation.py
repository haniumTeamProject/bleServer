"""목적지가 정해지면 경로를 만들어 추적기에 얹는다.

── 여기가 끊겨 있었다 ────────────────────────────────────────────

    STT 텍스트 → LLM 매칭 → 목적지 확정 → [끊김] → 경로 생성 → 비콘 순서 → 추적 판정

`route_engine` 은 만들어져 있었지만 `handler.py` 가 import 조차 하지 않았다.
목적지를 알아들으면 "407호로 안내합니다" 하고 끝났고, 경로는 `/monitor` 에서
사람이 손으로 비콘 순서를 등록해야 돌았다. 이 모듈이 그 사이를 잇는다.

── 출발점을 어떻게 아나 ──────────────────────────────────────────

경로를 만들려면 "지금 어디"가 필요한데, 알 수 있는 단서는 **방금 잡힌 비콘**뿐이다.
그래서 RSSI 스트림에서 **가장 세게 잡히는 비콘**을 출발점으로 삼는다.

한 가지 함정이 있다. 신호가 제일 센 비콘이 늘 제일 가까운 비콘은 아니다 —
사람이 가리거나 벽에 반사되면 뒤바뀐다. 다만 출발점이 한 칸 어긋나도 경로 자체는
같은 복도를 타므로 실용상 문제가 되지 않는다. 정확한 위치 판정은 그 뒤 `PathTracker`
가 계속한다.

── 층은 목적지가 알려준다 ────────────────────────────────────────

비콘의 major(=100+층)로 층을 알아낼 수도 있지만, **목적지 랜드마크가 이미 자기
층을 알고 있다.** 그쪽이 확실하다 — 신호를 해석할 필요가 없다.
(층을 넘나드는 안내는 아직 다루지 않는다. §층간이동 미구현)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.database import SessionLocal
from app.landmark.models import Landmark as LandmarkRow
from app.nav.db_map_source import DbMapSource
from app.nav.map_source import MapDataError
from app.nav.route_engine import RouteResult, build_route, estimated_seconds


@dataclass
class RoutePlan:
    """만들어진 경로. 추적기에 넣을 키와 사람이 볼 값을 같이 담는다."""

    keys: list[str]                  # 추적기용 비콘 키 ("MAC|이름" 형태)
    route: RouteResult
    floor_id: str
    from_beacon: str
    missing: list[str]               # 경로에 있지만 지금 안 잡히는 비콘 이름

    @property
    def distance_m(self) -> float:
        return self.route.total_distance_m

    @property
    def seconds(self) -> int:
        return estimated_seconds(self.route.total_distance_m)

    def speech(self, destination_name: str) -> str:
        return (f"{destination_name}까지 약 {self.distance_m:.0f}미터, "
                f"{self.seconds}초 걸립니다. 안내를 시작합니다.")


def strongest_beacon_key(filters: dict) -> str | None:
    """지금 가장 세게 잡히는 비콘 키.

    `RssiFilterPipeline.x` 가 칼만 필터의 현재 추정값이다. 원본 RSSI 가 아니라
    이걸 쓰는 이유는, 원본은 한 번씩 크게 튀어서 그 순간에 출발점이 엉뚱한 곳으로
    잡히기 때문이다. `initialized` 가 False 면 아직 표본이 없다는 뜻이라 건너뛴다.
    """
    best, best_v = None, float("-inf")
    for key, pipeline in filters.items():
        if not getattr(pipeline, "initialized", False):
            continue
        v = float(pipeline.x)
        if v > best_v:
            best, best_v = key, v
    return best


def _ble_name(key: str) -> str:
    """비콘 키는 "MAC|이름" 형태 — 이름 부분만."""
    head, sep, tail = key.partition("|")
    return tail if sep else head


def plan_route(landmark_id: str, known_keys: list[str], filters: dict,
               from_beacon_id: str | None = None) -> RoutePlan:
    """목적지까지의 경로를 만든다. 못 만들면 MapDataError.

    known_keys 는 지금까지 한 번이라도 잡힌 비콘 키 목록이다. 경로에 나온 비콘을
    이 목록의 이름과 맞춰서 추적기용 키로 바꾼다 — 추적기는 RSSI 스트림의 키로만
    판정할 수 있기 때문이다.

    `from_beacon_id` 를 주면 그 비콘에서 출발한 것으로 친다. 폰 없이 `/monitor`
    에서 경로만 확인할 때 쓴다.
    """
    db = SessionLocal()
    try:
        row = db.get(LandmarkRow, landmark_id)
        if row is None:
            raise MapDataError(f"목적지를 DB 에서 찾을 수 없습니다: {landmark_id}")
        floor_id = row.floor_id
        source = DbMapSource(db)

        if from_beacon_id:
            origin = next((b for b in source.beacons(floor_id)
                           if b.id == from_beacon_id), None)
            if origin is None:
                raise MapDataError(f"지정한 출발 비콘이 이 층에 없습니다: {from_beacon_id}")
        else:
            start_key = strongest_beacon_key(filters)
            if start_key is None:
                raise MapDataError(
                    "지금 잡히는 비콘이 없어 출발점을 알 수 없습니다.\n"
                    "폰이 비콘을 스캔하고 있는지 확인해 주세요."
                )
            start_name = _ble_name(start_key)
            origin = next((b for b in source.beacons(floor_id)
                           if b.ble_name == start_name), None)
            if origin is None:
                raise MapDataError(
                    f"지금 잡히는 비콘({start_name})이 이 층에 등록되어 있지 않습니다.\n"
                    "관리자웹에서 비콘 이름이 실제 광고 이름과 같은지 확인해 주세요."
                )

        route = build_route(source, floor_id,
                            from_beacon_id=origin.id, to_landmark_id=landmark_id)

        # 경로의 비콘을 지금 잡히는 키에 잇는다.
        by_name = {_ble_name(k): k for k in known_keys}
        beacons = {b.id: b for b in source.beacons(floor_id)}
        keys: list[str] = []
        missing: list[str] = []
        for step in route.steps:
            info = beacons.get(step.beacon_id)
            name = info.ble_name if info else None
            key = by_name.get(name) if name else None
            if key:
                keys.append(key)
            else:
                missing.append(name or step.beacon_id)

        return RoutePlan(keys=keys, route=route, floor_id=floor_id,
                         from_beacon=origin.id, missing=missing)
    finally:
        db.close()
