"""안내에 필요한 지도 데이터를 서버에 공급한다.

**이 모듈의 존재 이유는 출처를 감추는 것이다.**

지금 데이터는 map-tool이 내보낸 `mappin_project.json` 파일 하나에 들어 있고,
층도 하나뿐이다. 하지만 최종적으로는 DB(건물·층·비콘·랜드마크·경로노드)에서
읽어야 한다 — 관리자웹이 거기에 저장하기 때문이다.

그 전환이 확정되어 있으므로, 경로 탐색이나 안내 로직이 "파일을 읽는다"는 사실을
알게 두면 안 된다. 알게 두면 DB로 옮길 때 그 코드를 전부 다시 손대야 한다.
그래서 아래 `MapSource` 하나만 보게 하고, 구현을 갈아끼운다.

    지금        FileMapSource   — mappin_project.json
    나중        DbMapSource     — SQLAlchemy 모델

── 비콘 식별자에 대해 ────────────────────────────────────────────────

지금은 식별자가 **전환 중**이다.

    펌웨어      전 비콘이 major=1, minor=1 (파일 하나로 구움)
    앱          MAC 과 광고 이름만 씀
    map-tool    비콘에 bleName 을 적을 수 있음
    DB·관리자웹  major/minor 를 이미 갖고 있음

목표는 minor 다(층이 major, 비콘이 minor). 펌웨어를 비콘별로 다시 구우면 된다.
하지만 그 전에도 서버가 돌아야 하므로, `match()` 가 **minor → MAC → 이름** 순으로
찾는다. 셋 중 무엇이 채워져 있든 동작하고, 재플래시가 끝나면 minor 만 남는다.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


# ---------------------------------------------------------------------------
# 자료 구조
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FloorInfo:
    id: str
    name: str
    major: int | None = None
    scale_m_per_px: float = 0.05


@dataclass(frozen=True)
class BeaconInfo:
    """설치된 비콘 하나.

    id 는 지도 도구의 표시 라벨(B1, B2…)이다. 앱은 이 체계를 모르고, MAC 이나
    minor 만 올려보낸다. 그 둘을 잇는 것이 이 클래스의 일이다.
    """

    id: str
    x: float
    y: float
    minor: int | None = None
    # 층 번호가 들어 있다(100 + 층). 추적 키를 만들 때 minor 와 짝으로 쓴다 —
    # minor 는 층 안에서만 유일해서 major 없이 쓰면 다른 층 비콘과 겹친다.
    major: int | None = None
    mac: str | None = None
    ble_name: str | None = None

    def matches(self, *, minor: int | None = None, mac: str | None = None,
                name: str | None = None) -> bool:
        if minor is not None and self.minor is not None:
            return minor == self.minor
        if mac and self.mac:
            return mac.upper() == self.mac.upper()
        if name and self.ble_name:
            return name == self.ble_name
        return False


@dataclass(frozen=True)
class LandmarkInfo:
    """사용자가 목적지로 말할 수 있는 지점."""

    id: str
    name: str
    x: float
    y: float
    type: str = "room"
    door_side: str | None = None      # left | right | None — 도착 안내에 쓴다
    is_connector: bool = False


@dataclass(frozen=True)
class Node:
    id: str
    x: float
    y: float
    type: str = "corner"              # corner | junction | waypoint | landmark
    concave: bool = False             # "벽 끝" — 건너기가 시작될 수 있는 지점
    name: str | None = None


@dataclass(frozen=True)
class Edge:
    a: str
    b: str
    dist_m: float
    type: str = "wall"                # wall | cross
    directed: bool = False            # 건너기는 단방향


@dataclass
class Graph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_id = {n.id: n for n in self.nodes}

    def node(self, node_id: str) -> Node | None:
        return self._by_id.get(node_id)

    @property
    def empty(self) -> bool:
        return not self.nodes


class MapDataError(RuntimeError):
    """지도 데이터가 없거나 안내에 쓸 수 없는 상태."""


# ---------------------------------------------------------------------------
# 공급자
# ---------------------------------------------------------------------------
class MapSource(Protocol):
    """안내 로직이 지도에 대해 알아야 하는 전부."""

    def floors(self) -> list[FloorInfo]: ...
    def floor(self, floor_id: str) -> FloorInfo: ...
    def beacons(self, floor_id: str) -> list[BeaconInfo]: ...
    def landmarks(self, floor_id: str) -> list[LandmarkInfo]: ...
    def graph(self, floor_id: str) -> Graph: ...
    def cross_penalty_m(self, floor_id: str) -> float: ...
    def beacon_match_radius_m(self, floor_id: str) -> float: ...


# ---------------------------------------------------------------------------
# 파일 구현 — 지금 쓰는 것
# ---------------------------------------------------------------------------
DEFAULT_FLOOR_ID = os.environ.get("NAV_FLOOR_ID", "suwon_ict-4")
DEFAULT_FLOOR_NAME = os.environ.get("NAV_FLOOR_NAME", "수원대학교 ICT융합대학 4층")


class FileMapSource:
    """map-tool 이 내보낸 `mappin_project.json` 하나를 읽는다.

    파일이 13MB(마스크 이미지가 base64로 들어 있다)라 매번 파싱하면 느리다.
    수정 시각으로 캐시한다 — handler 의 랜드마크 캐시와 같은 방식이다.

    **층이 하나뿐이다.** 파일 형식에 층 개념이 없기 때문이다. floor_id 를 받긴
    하지만 값은 무시하고 항상 같은 층을 돌려준다. DB 구현이 들어오면 이 제약이
    사라지므로, 부르는 쪽은 처음부터 floor_id 를 넘기도록 해 둔다.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._cache: dict | None = None
        self._mtime: float | None = None

    # -- 파일 읽기 ----------------------------------------------------------
    def _data(self) -> dict:
        if not self.path.is_file():
            raise MapDataError(f"지도 프로젝트 파일이 없습니다: {self.path}")
        mtime = self.path.stat().st_mtime
        if self._cache is None or mtime != self._mtime:
            self._cache = json.loads(self.path.read_text(encoding="utf-8"))
            self._mtime = mtime
        return self._cache

    # -- MapSource ----------------------------------------------------------
    def floors(self) -> list[FloorInfo]:
        return [self.floor(DEFAULT_FLOOR_ID)]

    def floor(self, floor_id: str) -> FloorInfo:
        d = self._data()
        return FloorInfo(
            id=floor_id or DEFAULT_FLOOR_ID,
            name=DEFAULT_FLOOR_NAME,
            major=None,
            scale_m_per_px=float(d.get("scale_m_per_px") or 0.05),
        )

    def beacons(self, floor_id: str) -> list[BeaconInfo]:
        out = []
        for b in self._data().get("beacons") or []:
            if not isinstance(b, dict) or not b.get("id"):
                continue
            out.append(BeaconInfo(
                id=str(b["id"]),
                x=float(b.get("x") or 0), y=float(b.get("y") or 0),
                minor=_as_int(b.get("minor")),
                mac=b.get("mac") or None,
                ble_name=b.get("bleName") or None,
            ))
        return out

    def landmarks(self, floor_id: str) -> list[LandmarkInfo]:
        out = []
        for lm in self._data().get("landmarks") or []:
            if not isinstance(lm, dict) or not lm.get("id"):
                continue
            out.append(LandmarkInfo(
                id=str(lm["id"]),
                name=str(lm.get("name") or lm["id"]),
                x=float(lm.get("x") or 0), y=float(lm.get("y") or 0),
                type=str(lm.get("type") or "room"),
                door_side=lm.get("doorSide") or None,
                is_connector=bool(lm.get("isConnector")),
            ))
        return out

    def graph(self, floor_id: str) -> Graph:
        d = self._data()
        raw_nodes = d.get("pathNodes")
        raw_edges = d.get("pathEdges")
        if not raw_nodes or not raw_edges:
            # 예전에 저장한 파일에는 그래프가 없다. 이유를 정확히 알려준다 —
            # "경로를 못 찾음"으로 뭉뚱그리면 원인을 찾는 데 한참 걸린다.
            raise MapDataError(
                f"{self.path.name} 에 경로 그래프(pathNodes/pathEdges)가 없습니다.\n"
                "지도 편집 도구(map_inspection.html)에서 프로젝트를 열고 다시 저장해 주세요.\n"
                "저장할 때 경로 노드와 연결이 함께 기록됩니다."
            )
        nodes = [
            Node(id=str(n["id"]), x=float(n.get("x") or 0), y=float(n.get("y") or 0),
                 type=str(n.get("type") or "corner"), concave=bool(n.get("concave")),
                 name=n.get("name"))
            for n in raw_nodes if isinstance(n, dict) and n.get("id")
        ]
        edges = [
            Edge(a=str(e["a"]), b=str(e["b"]), dist_m=float(e.get("dist_m") or 0),
                 type=str(e.get("type") or "wall"), directed=bool(e.get("directed")))
            for e in raw_edges if isinstance(e, dict) and e.get("a") and e.get("b")
        ]
        return Graph(nodes=nodes, edges=edges)

    def cross_penalty_m(self, floor_id: str) -> float:
        return float(self._data().get("crossPenalty") or 10.0)

    def beacon_match_radius_m(self, floor_id: str) -> float:
        return float(self._data().get("beaconNodeMatch") or 3.0)

    # -- 좌표 환산 ----------------------------------------------------------
    def meters_per_px(self, floor_id: str) -> float:
        """노드 좌표(작업 픽셀) 1px 이 실제 몇 미터인가.

        저장된 scale_m_per_px 는 **원본 이미지 픽셀** 기준이고 노드 좌표는 작업
        좌표계라, workScale 로 나눠야 맞는다. 지도 도구도 같은 계산을 한다
        (`mPerWorkPx = mPerOrigPx / workScale`).
        """
        d = self._data()
        scale = float(d.get("scale_m_per_px") or 0.05)
        work_scale = float(d.get("workScale") or 1) or 1.0
        return scale / work_scale


def _as_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 비콘 매칭 — 앱이 올린 것을 설치된 비콘에 잇는다
# ---------------------------------------------------------------------------
def resolve_beacon(observed: dict, known: list[BeaconInfo]) -> BeaconInfo | None:
    """앱이 올린 비콘 하나를 설치된 비콘에 맞춘다.

    observed 예: {"minor": 3, "mac": "44:B1:...", "name": "ESP32-Beacon3-tx"}
    무엇이 들어 있든 되도록 만든다 — 식별자가 전환 중이기 때문이다.
    """
    minor = _as_int(observed.get("minor"))
    mac = observed.get("mac") or None
    name = observed.get("name") or observed.get("bleName") or None
    for b in known:
        if b.matches(minor=minor, mac=mac, name=name):
            return b
    return None


def nearest_beacon_to_node(node: Node, beacons: list[BeaconInfo],
                           radius_m: float, meters_per_px: float) -> BeaconInfo | None:
    """노드에서 가장 가까운 비콘. 반경 밖이면 None.

    지도 도구의 `computeBeaconSequenceForPath` 와 같은 규칙이다.
    """
    best, best_d = None, math.inf
    for b in beacons:
        d = math.hypot(node.x - b.x, node.y - b.y) * meters_per_px
        if d <= radius_m and d < best_d:
            best, best_d = b, d
    return best
