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
from app.nav.map_source import BeaconInfo, MapDataError
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

    def speech(self, destination_name: str, tracking: bool = True) -> str:
        """사용자에게 읽어줄 문장.

        **추적이 걸렸는지에 따라 말을 다르게 한다.** 경로를 찾은 것과 안내를
        시작할 수 있는 것은 다른 일이다 — 잡히는 비콘이 부족하면 경로는 나와도
        위치를 따라갈 수 없다.

        그때도 "안내를 시작합니다"라고 말하면 사용자는 걷기 시작하는데 서버는
        아무것도 판정하지 않는다. 화면을 볼 수 없으니 알아챌 방법이 없다.
        """
        if not tracking:
            return (f"{destination_name}까지 경로를 찾았습니다. "
                    f"비콘 신호를 기다리는 중입니다.")
        return f"{destination_name}로 안내합니다."


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


def _mac(key: str) -> str:
    """비콘 키에서 MAC 부분만."""
    head, sep, _tail = key.partition("|")
    return head if sep else ""


def tracking_key(major: int | None, minor: int | None) -> str | None:
    """추적기가 쓸 비콘 식별자. major/minor 가 없으면 None.

    ── 왜 MAC 이 아니라 이것인가 ──────────────────────────────────

    추적기는 원래 RSSI 스트림의 키(`"MAC|이름"`)로 판정했다. 그런데 그 키는
    **폰이 그 비콘을 한 번이라도 잡아야** 생긴다. 그래서 목적지를 말하는 순간
    경로에 넣을 수 있는 것은 "지금 잡히는 것"뿐이었고, 앞쪽 비콘은 아직 안 잡혔으니
    전부 빠졌다. 20개짜리 경로가 2개로 줄어드는 식이다.

    major/minor 는 다르다. **DB 가 이미 알고 있고 펌웨어에 새겨져 있어서**,
    폰이 아직 못 봤어도 경로에 미리 세워둘 수 있다. 걸어가다 신호가 잡히면
    그때부터 그 자리가 채워진다.

    major 를 같이 넣는 이유: minor 는 층 안에서만 유일하다. 층이 다르면 minor 가
    겹치므로 major 없이 쓰면 다른 층 비콘이 같은 것으로 보인다.
    """
    if minor is None:
        return None
    return f"{major if major is not None else '?'}-{minor}"


def _match_key(beacon: BeaconInfo, known_keys: list[str], beacon_ids: dict) -> str | None:
    """설치된 비콘 하나를 지금 잡히는 스트림 키에 잇는다. **minor → MAC → 이름** 순.

    ── 이름으로 맞추면 안 된다 ────────────────────────────────────

    `beacons.name` 은 관리자가 붙인 **표시 이름**이다("중앙 갈림길", "복도 끝").
    폰이 올리는 것은 **광고 이름**이고("ESP32-B1"), DB 에는 그 값을 담는 칸이 없다.
    둘을 맞추려 하면 영영 안 맞는다.

    minor 가 정답이다. 관리자웹에 입력하는 값이고, 펌웨어에 새겨 넣는 값이라
    양쪽이 같은 것을 가리킨다. MAC 은 기기를 교체하면 달라지므로 그다음이다.
    """
    def contradicts(key: str) -> bool:
        """이 신호가 그 비콘일 리 없다고 **단정할 수 있는가.**

        양쪽 minor 를 다 아는데 서로 다르면 다른 기기다. 이걸 안 보면 아래 폴백이
        위험해진다 — 이름이나 MAC 이 우연히 겹칠 때 엉뚱한 기기에 경로 한 칸을
        붙여버리고, 그 지점부터 안내가 통째로 어긋난다.
        """
        seen = (beacon_ids.get(key) or {}).get("minor")
        return (beacon.minor is not None and seen is not None
                and seen != beacon.minor)

    candidates = [k for k in known_keys if not contradicts(k)]

    if beacon.minor is not None:
        for key in candidates:
            if (beacon_ids.get(key) or {}).get("minor") == beacon.minor:
                return key
    if beacon.mac:
        want = beacon.mac.upper()
        for key in candidates:
            if _mac(key).upper() == want:
                return key
    # 이름은 마지막 수단이다. 관리자가 표시 이름 자리에 광고 이름을 적어둔
    # 경우에만 맞는다(실측 중에는 흔히 그렇게 쓴다).
    if beacon.ble_name:
        for key in candidates:
            if _ble_name(key) == beacon.ble_name:
                return key
    return None


def plan_route(landmark_id: str, known_keys: list[str], filters: dict,
               from_beacon_id: str | None = None,
               beacon_ids: dict | None = None) -> RoutePlan:
    """목적지까지의 경로를 만든다. 못 만들면 MapDataError.

    known_keys 는 지금까지 한 번이라도 잡힌 비콘 키 목록이다. 경로에 나온 비콘을
    이 목록의 이름과 맞춰서 추적기용 키로 바꾼다 — 추적기는 RSSI 스트림의 키로만
    판정할 수 있기 때문이다.

    `from_beacon_id` 를 주면 그 비콘에서 출발한 것으로 친다. 폰 없이 `/monitor`
    에서 경로만 확인할 때 쓴다.
    """
    ids_map = beacon_ids or {}
    db = SessionLocal()
    try:
        row = db.get(LandmarkRow, landmark_id)
        if row is None:
            raise MapDataError(f"목적지를 DB 에서 찾을 수 없습니다: {landmark_id}")
        floor_id = row.floor_id
        source = DbMapSource(db)
        installed = source.beacons(floor_id)

        if from_beacon_id:
            origin = next((b for b in installed if b.id == from_beacon_id), None)
            if origin is None:
                raise MapDataError(f"지정한 출발 비콘이 이 층에 없습니다: {from_beacon_id}")
        else:
            start_key = strongest_beacon_key(filters)
            if start_key is None:
                raise MapDataError(
                    "지금 잡히는 비콘이 없어 출발점을 알 수 없습니다.\n"
                    "폰이 비콘을 스캔하고 있는지 확인해 주세요."
                )
            origin = next(
                (b for b in installed if _match_key(b, [start_key], ids_map) == start_key),
                None)
            if origin is None:
                seen = ids_map.get(start_key) or {}
                raise MapDataError(
                    f"지금 잡히는 비콘이 이 층에 등록되어 있지 않습니다 — "
                    f"{_ble_name(start_key)} (minor={seen.get('minor')}, MAC={_mac(start_key)})\n"
                    "관리자웹에서 그 비콘의 minor 또는 MAC 이 맞는지 확인해 주세요.\n"
                    "(표시 이름은 매칭에 쓰지 않습니다 — 폰이 올리는 광고 이름과 다른 값입니다)"
                )

        route = build_route(source, floor_id,
                            from_beacon_id=origin.id, to_landmark_id=landmark_id)

        # 경로 전체를 추적 키로 세운다.
        #
        # **지금 잡히는 것만 넣지 않는다.** 예전에는 스트림 키(`"MAC|이름"`)를 써서
        # 폰이 이미 본 비콘만 경로에 들어갔다. 목적지를 말하는 순간에는 앞쪽 비콘이
        # 아직 안 잡혔으므로 경로가 두어 개로 쪼그라들었다.
        #
        # minor 기반 키는 DB 만으로 만들 수 있어서 미리 세워둘 수 있다. 걸어가다
        # 신호가 잡히면 그때부터 그 자리가 채워진다.
        by_id = {b.id: b for b in installed}
        seen_now = set()
        for b in installed:
            if _match_key(b, known_keys, ids_map):
                seen_now.add(b.id)

        keys: list[str] = []
        missing: list[str] = []       # 경로에는 있지만 아직 안 잡히는 것 (표시용)
        no_minor: list[str] = []      # minor 가 없어 추적에 못 넣는 것
        for step in route.steps:
            info = by_id.get(step.beacon_id)
            key = tracking_key(info.major, info.minor) if info else None
            if key is None:
                no_minor.append(step.beacon_id)
                continue
            keys.append(key)
            if info.id not in seen_now:
                missing.append(step.beacon_id)

        if no_minor:
            # minor 가 없으면 추적 키를 만들 수 없다 — 관리자웹에서 안 넣은 것이다.
            print(f"[경로] minor 없는 비콘 {len(no_minor)}개는 추적에서 빠짐: "
                  f"{', '.join(no_minor[:5])}")

        return RoutePlan(keys=keys, route=route, floor_id=floor_id,
                         from_beacon=origin.id, missing=missing)
    finally:
        db.close()
