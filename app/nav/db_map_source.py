"""DB 에서 지도 데이터를 읽는다. `FileMapSource` 의 짝.

── 왜 필요한가 ────────────────────────────────────────────────────

`map_source.py` 는 처음부터 이 전환을 전제로 만들었다.

    지금        FileMapSource   — mappin_project.json
    나중        DbMapSource     — SQLAlchemy 모델

관리자웹(WEB-FE)이 건물·층·비콘·랜드마크를 DB 에 넣으므로, 안내도 결국 거기서
읽어야 한다. 파일은 실측용으로 남긴다.

── 읽기 전용이다 ──────────────────────────────────────────────────

**이 모듈은 DB 에 쓰지 않는다.** 쓰기 경로는 관리자웹 하나뿐이어야 한다.
두 곳에서 같은 층을 고치면 나중에 덮어쓰기가 생기는데, 그게 언제 일어났는지
추적할 방법이 없다.

── 좌표계가 세 개다 (여기서 헷갈리면 전부 어긋난다) ────────────────

    원본 픽셀      업로드한 평면도 이미지 그대로.        origW × origH
    설계도(900)    폭을 900 으로 맞춘 좌표.             DB 의 x, y 가 이것
    마스크 픽셀    지도검수 캔버스 해상도.               floor_masks.width/height
    작업 픽셀      map-tool 이 처리하는 해상도.          workW × workH

`floors.scale_m_per_px` 는 **마스크 픽셀** 기준이다. 관리자가 지도검수 캔버스
위에서 두 점을 찍어 계산하기 때문이다.

    // WEB-FE/src/lib/reinforcementBeacons.ts:80
    // scaleMPerPx는 지도검수 캔버스(마스크 픽셀) 기준으로 캘리브레이션된 값이라,
    const mPerDesignPx = ratio * scaleMPerPx      // ratio = mask.w / 900

반면 map-tool 의 `scale_m_per_px` 는 **원본 픽셀** 기준이다. 그래서 그대로 넘기면
안 되고 아래처럼 환산해야 한다.

    m/원본px = scale_m_per_px(DB) × maskW / origW

`origW` 는 이미지를 실제로 디코딩해야 알 수 있어서 서버가 모른다. 그래서
`map_project()` 는 환산하지 않고 `scaleMPerPx` 와 `maskW` 를 **그대로** 실어
보내고, 브라우저가 이미지를 연 뒤 계산한다.
"""

from __future__ import annotations

import base64
import hashlib
import io
import math
import os

from PIL import Image
from sqlalchemy.orm import Session

from app.beacon.models import Beacon
from app.building.models import Building
from app.connector.models import Connector, ConnectorPosition
from app.floor.models import Floor
from app.floorplan.models import Floorplan
from app.landmark.models import Landmark
from app.mask.models import FloorMask
from app.nav.map_source import (
    BeaconInfo,
    Edge,
    FloorInfo,
    Graph,
    LandmarkInfo,
    MapDataError,
    Node,
)
from app.nav.path_nodes import EntrancePoint, generate_path_nodes
from app.pathnode.models import FloorPathNodes

# 관리자웹이 쓰는 설계도 좌표 기준 폭. WEB-FE/src/lib/constants.ts 의 MAP_DESIGN_W.
# 여기서 바꾸면 프론트와 갈라지므로 같이 고쳐야 한다.
DESIGN_W = 900

# 관리자웹 경로노드 화면의 기본값 (PathNodePage.tsx).
# 관리자가 화면에서 바꿀 수 있는 값인데 DB 에 저장하는 자리가 없어서, 지금은
# 서버도 같은 기본값을 쓴다. 화면에서 바꾼 값을 서버가 따르게 하려면 컬럼이 필요하다.
CROSSING_MAX_M = 12.0        # 이보다 넓으면 건너기를 만들지 않는다
CROSS_PENALTY_M = 5.0        # 이만큼 이상 절약될 때만 건넌다

# 경로 선에서 이 거리 안에 있는 비콘만 경로에 세운다.
#
# 3.0m 이었는데 **경로에서 벗어난 비콘까지 딸려 들어왔다.** 복도를 지나갈 뿐인데
# 옆방 앞 비콘이 경로에 끼면, 사용자는 그 비콘을 제대로 못 잡거나 엉뚱한 지점에서
# 안내를 받는다.
#
# 실측 4층 B1 → 407 로 재보면(반경만 바꿔가며):
#
#     3.0m   9칸   B1 B31 B4 B18 B8 B19 B20 B21 B22
#     2.0m   7칸   B1 B31 B18 B19 B20 B21 B22       ← 경로 밖 B4·B8 빠짐
#     1.5m   5칸   B1 B31 B20 B21 B22               ← B18·B19 까지 잃어 구간이 벌어짐
#
# 더 줄이면 경로 위 비콘까지 빠져서 안내 간격이 벌어진다. 2.0m 가 그 경계다.
BEACON_MATCH_RADIUS_M = 3.0

# ── 층마다 다른 값이 필요하다 ──────────────────────────────────────
#
# 위 실측은 **좁은 복도**(4층) 이야기다. 넓은 홀은 정반대 문제가 난다.
#
# 경로는 벽을 따라 돈다 — 벽을 짚고 걷는 사람을 위한 안내라서 그렇게 만든다.
# 그런데 홀 한가운데 세운 비콘은 그 벽선에서 5~10m 떨어져 있어, 3m 반경으로는
# **한 번도 안 걸린다.** 실측 1층 로비에서 가운데 비콘 넷이 통째로 빠졌다.
#
# 좁은 복도에서 반경을 키우면 옆 복도 비콘이 딸려 들어오고, 넓은 홀에서 줄이면
# 가운데 비콘이 사라진다. 한 값으로 둘 다 맞출 수가 없다.
#
# 그래서 층마다 따로 준다. DB 컬럼과 관리자웹 입력칸을 만드는 것이 맞지만
# 그건 마이그레이션이 필요해서, 우선 환경변수로 뺀다.
#
#     BEACON_MATCH_RADIUS_M=3.0
#     BEACON_MATCH_RADIUS_BY_FLOOR="<층id>=8.0,<다른층id>=2.5"
#
# **기본값은 안 건드린다.** 안 적은 층은 예전 그대로 3.0m 로 돌아서, 이 값을
# 쓰지 않으면 아무것도 달라지지 않는다.
def _radius_default() -> float:
    try:
        return max(0.1, float(os.environ.get("BEACON_MATCH_RADIUS_M", "")
                              or BEACON_MATCH_RADIUS_M))
    except ValueError:
        return BEACON_MATCH_RADIUS_M


def _radius_by_floor() -> dict[str, float]:
    out: dict[str, float] = {}
    for part in (os.environ.get("BEACON_MATCH_RADIUS_BY_FLOOR") or "").split(","):
        key, sep, val = part.partition("=")
        if not sep:
            continue
        try:
            out[key.strip()] = max(0.1, float(val))
        except ValueError:
            # 오타 하나 때문에 안내 전체가 죽으면 안 된다. 그 층만 기본값으로 둔다.
            print(f"[map] 비콘 반경 설정을 못 읽음 — 무시함: {part!r}")
    return out

# 마스크를 푼 결과를 들고 있는다. {floor_id: (해시, 비트, 폭, 높이)}
# 마스크 내용이 키라서, 관리자가 고치면 자동으로 다시 만든다.
_MASK_CACHE: dict[str, tuple[str, bytes, int, int]] = {}

# 만들어 둔 그래프. {floor_id: (키, Graph)}
# 키에 마스크·축척·입구가 전부 들어가서, 무엇이 바뀌든 다시 만든다.
_GRAPH_CACHE: dict[str, tuple[str, "Graph"]] = {}


class DbMapSource:
    """MapSource 를 DB 로 구현한 것.

    세션을 생성자로 받는다. 요청마다 새로 만들어 쓰는 것을 전제로 한다
    (FastAPI 의 Depends(get_db) 수명과 같다).
    """

    def __init__(self, db: Session):
        self.db = db

    # -- MapSource ----------------------------------------------------------
    def floors(self) -> list[FloorInfo]:
        rows = (
            self.db.query(Floor, Building)
            .join(Building, Building.id == Floor.building_id)
            .order_by(Building.name, Floor.floor)
            .all()
        )
        return [self._floor_info(f, b) for f, b in rows]

    def floor(self, floor_id: str) -> FloorInfo:
        f = self.db.get(Floor, floor_id)
        if f is None:
            raise MapDataError(f"층을 찾을 수 없습니다: {floor_id}")
        return self._floor_info(f, self.db.get(Building, f.building_id))

    def beacons(self, floor_id: str) -> list[BeaconInfo]:
        rows = (
            self.db.query(Beacon)
            .filter(Beacon.floor_id == floor_id)
            .order_by(Beacon.minor, Beacon.name)
            .all()
        )
        return [
            BeaconInfo(
                id=b.source_label or b.name or b.id,
                x=float(b.x or 0),
                y=float(b.y or 0),
                minor=b.minor,
                major=b.major,
                mac=b.mac,
                # 관리자가 붙인 **표시 이름**이다("중앙 갈림길"). 폰이 올리는 광고
                # 이름과는 다른 값이라 매칭에 먼저 쓰면 안 된다 — minor 가 우선이다.
                ble_name=b.name,
            )
            for b in rows
        ]

    def landmarks(self, floor_id: str) -> list[LandmarkInfo]:
        """목적지 후보. **수직연결자도 포함한다.**

        DB 는 랜드마크와 연결자를 다른 테이블에 두지만, 사용자에게는 둘 다
        "갈 수 있는 곳"이다. 엘리베이터를 목적지로 말할 수 없으면 층 이동이
        불가능해진다. 그래서 여기서 합쳐서 내보낸다.
        """
        out = [
            LandmarkInfo(
                id=lm.id,
                name=lm.name or lm.source_label or lm.id,
                x=float(lm.x or 0),
                y=float(lm.y or 0),
                type=lm.category or "room",
                door_side=None,     # DB 에 아직 컬럼이 없다 (docs/WEBFE_접합_변경기록.md)
                is_connector=False,
            )
            for lm in self.db.query(Landmark).filter(Landmark.floor_id == floor_id).all()
        ]
        out.extend(self._connector_landmarks(floor_id))
        return out

    def graph(self, floor_id: str) -> Graph:
        """이동영역 마스크에서 경로 그래프를 만든다.

        관리자웹의 경로노드 화면(`PathNodePage.tsx`)이 하는 것과 **같은 순서·같은
        인자**로 호출한다. 다르면 관리자가 검수한 그래프와 사용자가 안내받는
        그래프가 갈라진다.

            마스크         floor_masks 의 PNG (alpha>0 = 통행 가능)
            입구           연결자 먼저, 그다음 랜드마크 (순서가 노드 번호를 정한다)
            좌표           마스크 픽셀 — DB 의 900 좌표에 maskW/900 을 곱한다
            crossingMaxPx  12m / scaleMPerPx

        결과 노드는 **900 좌표로 되돌려** 내보낸다. 비콘·랜드마크가 900 기준이라
        같은 좌표계여야 "이 비콘에서 가장 가까운 노드"를 찾을 수 있다.

        관리자웹에서 경로노드를 저장해둔 층이면(PathNodePage.tsx의 "저장"), 아래
        자동계산을 건너뛰고 그 값을 그대로 쓴다 — 관리자가 점을 옮기거나 잘못된
        건너기를 지운 결과가 실제 안내에도 반영되게 하려면 이렇게 해야 한다.
        아직 아무도 저장한 적 없는 층만(신규 층 등) 아래 자동계산으로 폴백한다.
        """
        saved = self.db.get(FloorPathNodes, floor_id)
        if saved and saved.nodes:
            return self._graph_from_saved(saved)

        mask, mw, mh, mask_key = self._mask_bits(floor_id)
        f = self.db.get(Floor, floor_id)
        scale = float(f.scale_m_per_px) if f and f.scale_m_per_px else None
        if scale is None:
            raise MapDataError(
                "축척이 없어 경로 그래프를 만들 수 없습니다.\n"
                "관리자웹의 지도 검수 화면에서 축척을 먼저 정해주세요."
            )

        to_mask = mw / DESIGN_W
        entrances = [
            EntrancePoint(x=lm.x * to_mask, y=lm.y * to_mask,
                          kind="connector" if lm.is_connector else "landmark")
            for lm in self._entrance_order(floor_id)
        ]
        crossing_max_px = CROSSING_MAX_M / scale

        # 노드 생성이 이 모듈에서 제일 비싸다(실측 4층 기준 0.8초). 안내 한 번에
        # 여러 번 불리므로 결과를 들고 있는다.
        #
        # 키에는 **결과를 바꾸는 것 전부**가 들어가야 한다. 하나라도 빠지면
        # 관리자가 고친 뒤에도 옛 그래프가 나가는데, 그건 화면과 안내가 갈라지는
        # 가장 알아채기 어려운 형태다.
        key = hashlib.sha1(repr((
            mask_key, mw, mh, scale, crossing_max_px,
            [(round(e.x, 6), round(e.y, 6), e.kind) for e in entrances],
        )).encode()).hexdigest()
        cached = _GRAPH_CACHE.get(floor_id)
        if cached and cached[0] == key:
            return cached[1]

        built = generate_path_nodes(mask, mw, mh, entrances, crossing_max_px)

        to_design = DESIGN_W / mw
        nodes = [
            Node(id=n.id, x=n.x * to_design, y=n.y * to_design,
                 type=n.type, concave=n.concave, name=None)
            for n in built.nodes
        ]
        by_id = {n.id: n for n in built.nodes}
        edges = []
        for e in built.edges:
            a, b = by_id[e.a], by_id[e.b]
            # 마스크 픽셀 거리 × (m/마스크px) = 실거리. scale 이 마스크 픽셀 기준이라
            # 중간 환산 없이 바로 곱하면 된다 (PathNodePage.tsx 주석과 같은 이야기).
            dist_m = math.hypot(a.x - b.x, a.y - b.y) * scale
            edges.append(Edge(a=e.a, b=e.b, dist_m=dist_m, type=e.type,
                              directed=e.directed))
        graph = Graph(nodes=nodes, edges=edges)
        _GRAPH_CACHE[floor_id] = (key, graph)
        return graph

    def _graph_from_saved(self, saved: FloorPathNodes) -> Graph:
        """관리자웹이 저장한 경로노드 그래프를 그대로 안내에 쓴다(자동계산 생략).

        좌표는 저장 당시의 마스크 픽셀 기준(saved.mask_w/mask_h)이라, graph()의
        자동계산 경로와 같은 방식으로 900(DESIGN_W) 좌표로 환산해 내보낸다.
        거리(m)도 같은 방식으로 마스크 픽셀 거리 × scale_m_per_px 로 계산한다 —
        scale_m_per_px 는 마스크 픽셀 기준이라(모듈 docstring 참고) 중간 환산 없이
        바로 곱하면 된다.

        **건너기는 단방향이다.** a(입구/벽 끝) → b(맞은편) 으로만 갈 수 있다.
        맞은편 지점은 벽에서 떨어진 허공이라 거기서 출발할 수가 없기 때문이다.
        관리자웹 pathfind.ts 도 같다(`if (!e.directed)` 일 때만 역방향을 연다).
        """
        f = self.db.get(Floor, saved.floor_id)
        scale = float(f.scale_m_per_px) if f and f.scale_m_per_px else None
        if scale is None:
            raise MapDataError(
                "축척이 없어 경로 그래프를 만들 수 없습니다.\n"
                "관리자웹의 지도 검수 화면에서 축척을 먼저 정해주세요."
            )

        mask_w = saved.mask_w or DESIGN_W
        to_design = DESIGN_W / mask_w

        nodes = [
            Node(id=n["id"], x=n["x"] * to_design, y=n["y"] * to_design,
                 type=n["type"], concave=bool(n.get("concave")), name=None)
            for n in saved.nodes
        ]
        by_raw = {n["id"]: n for n in saved.nodes}
        edges = []
        for e in saved.edges or []:
            a, b = by_raw.get(e["a"]), by_raw.get(e["b"])
            if a is None or b is None:
                continue
            dist_m = math.hypot(a["x"] - b["x"], a["y"] - b["y"]) * scale
            # 저장된 값을 그대로 쓴다. 옛 JSON 에 없으면 건너기 여부로 정한다 —
            # 관리자웹이 cross 를 항상 directed 로 만들기 때문이다.
            edges.append(Edge(a=e["a"], b=e["b"], dist_m=dist_m, type=e["type"],
                              directed=bool(e.get("directed", e["type"] == "cross"))))
        return Graph(nodes=nodes, edges=edges)

    def meters_per_px(self, floor_id: str) -> float:
        """설계도(900) 좌표 1px 이 몇 m 인가.

        `floors.scale_m_per_px` 는 **마스크 픽셀** 기준이라 그대로 쓰면 안 된다.
        """
        f = self.db.get(Floor, floor_id)
        scale = float(f.scale_m_per_px) if f and f.scale_m_per_px else 0.05
        mask = self.db.get(FloorMask, floor_id)
        mw = (mask.width if mask and mask.width else DESIGN_W) or DESIGN_W
        return scale * mw / DESIGN_W

    def cross_penalty_m(self, floor_id: str) -> float:
        # 관리자웹 기본값과 맞춘다 (PathNodePage.tsx 의 DEFAULT_CROSS_PENALTY_M).
        return CROSS_PENALTY_M

    def beacon_match_radius_m(self, floor_id: str) -> float:
        """이 층에서 "경로 위 비콘"으로 칠 거리. 위 상수 주석 참고.

        환경변수는 **매번 읽는다.** 실측 중에 값을 바꿔 서버만 다시 띄우면 되고,
        어디에 캐시가 남아 헷갈릴 일이 없다. 문자열 파싱 몇 번이라 비용도 없다.
        """
        return _radius_by_floor().get(floor_id, _radius_default())

    # -- 마스크 ------------------------------------------------------------
    def _mask_bits(self, floor_id: str):
        """마스크 PNG 를 0/1 배열로 편다. 같은 마스크면 다시 안 푼다.

        노드 생성은 마스크 전체를 훑는 계산이라 요청마다 하면 느리다. 마스크
        내용으로 키를 잡으므로, 관리자가 마스크를 고치면 자동으로 다시 만든다.
        """
        row = self.db.get(FloorMask, floor_id)
        if row is None or not row.data_url:
            raise MapDataError(
                "이동영역(마스크)이 없어 경로 그래프를 만들 수 없습니다.\n"
                "관리자웹의 지도 검수 화면에서 통행 영역을 먼저 칠해주세요."
            )
        key = hashlib.sha1(row.data_url.encode()).hexdigest()
        cached = _MASK_CACHE.get(floor_id)
        if cached and cached[0] == key:
            return cached[1], cached[2], cached[3], key

        head, _, b64 = row.data_url.partition(",")
        if "base64" not in head:
            raise MapDataError("마스크 형식을 알 수 없습니다 (base64 data URL 이 아님).")
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        w = row.width or img.width
        h = row.height or img.height
        if (img.width, img.height) != (w, h):
            # 저장된 크기와 실제 이미지가 다르면 저장된 쪽을 따른다 — 좌표 환산이
            # 그 값을 기준으로 이뤄지기 때문이다.
            img = img.resize((w, h), Image.NEAREST)
        alpha = img.convert("RGBA").getchannel("A")
        # 관리자웹과 같은 기준: alpha > 0 이면 통행 가능 (maskRaster.ts)
        bits = bytes(1 if v > 0 else 0 for v in alpha.tobytes())

        _MASK_CACHE[floor_id] = (key, bits, w, h)
        return bits, w, h, key

    def _entrance_order(self, floor_id: str) -> list[LandmarkInfo]:
        """입구를 관리자웹과 **같은 순서로** 늘어놓는다.

        순서가 곧 노드 번호(N01, N02…)를 정하므로 바꾸면 그래프가 갈라진다.
        PathNodePage.tsx 는 연결자를 먼저, 랜드마크를 나중에 넣는다.
        """
        connectors = self._connector_landmarks(floor_id)
        landmarks = [
            LandmarkInfo(id=lm.id, name=lm.name or lm.id,
                         x=float(lm.x or 0), y=float(lm.y or 0),
                         type=lm.category or "room", door_side=None, is_connector=False)
            for lm in self.db.query(Landmark).filter(Landmark.floor_id == floor_id).all()
            if lm.x is not None and lm.y is not None
        ]
        return connectors + landmarks

    # -- 내부 --------------------------------------------------------------
    def _floor_info(self, f: Floor, b: Building | None) -> FloorInfo:
        name = f"{b.name} {f.floor}층" if b else f"{f.floor}층"
        return FloorInfo(
            id=f.id,
            name=name,
            major=f.major,
            # 주의: 이 값은 **마스크 픽셀** 기준이다. 모듈 문서 참고.
            scale_m_per_px=float(f.scale_m_per_px) if f.scale_m_per_px else 0.05,
        )

    def _connector_landmarks(self, floor_id: str) -> list[LandmarkInfo]:
        f = self.db.get(Floor, floor_id)
        if f is None:
            return []
        rows = (
            self.db.query(Connector, ConnectorPosition)
            .join(ConnectorPosition, ConnectorPosition.connector_id == Connector.id)
            .filter(ConnectorPosition.floor_id == floor_id)
            .all()
        )
        return [
            LandmarkInfo(
                id=c.id,
                name=c.name,
                x=float(p.x or 0),
                y=float(p.y or 0),
                type=c.type or "connector",
                door_side=None,
                is_connector=True,
            )
            for c, p in rows
        ]


# ---------------------------------------------------------------------------
# map-tool 이 그대로 삼킬 수 있는 모양으로 포장한다
# ---------------------------------------------------------------------------
def floor_choices(db: Session) -> list[dict]:
    """건물/층 선택 상자에 채울 목록.

    설계도가 없는 층은 지도로 열 수 없으므로 `hasFloorplan` 을 같이 준다
    (선택은 되게 두되 화면에서 표시해 준다 — 왜 안 열리는지 알 수 있게).
    """
    rows = (
        db.query(Floor, Building)
        .join(Building, Building.id == Floor.building_id)
        .order_by(Building.name, Floor.floor)
        .all()
    )
    out = []
    for f, b in rows:
        plan = db.get(Floorplan, f.id)
        mask = db.get(FloorMask, f.id)
        out.append({
            "floorId": f.id,
            "buildingId": b.id,
            "buildingName": b.name,
            "floor": f.floor,
            "major": f.major,
            "label": f"{b.name} {f.floor}층",
            "hasFloorplan": bool(plan and plan.image_url),
            "hasMask": bool(mask and mask.data_url),
            "hasScale": f.scale_m_per_px is not None,
        })
    return out


def map_project(db: Session, floor_id: str) -> dict:
    """DB 한 층을 map-tool 이 읽는 프로젝트 모양으로 만든다.

    **좌표를 환산하지 않고 설계도(900) 기준 그대로 보낸다.** 작업 좌표로 바꾸려면
    workW 가 필요한데 그건 브라우저가 이미지를 열어야 정해지는 값이라 서버가 모른다.
    받는 쪽에서 `x * workW / 900` 으로 바꾼다.

    마찬가지로 마스크도 PNG data URL 그대로 보낸다. map-tool 이 쓰는 바이트 배열로
    펴는 건 캔버스가 있는 브라우저 쪽이 맡는다.
    """
    f = db.get(Floor, floor_id)
    if f is None:
        raise MapDataError(f"층을 찾을 수 없습니다: {floor_id}")
    b = db.get(Building, f.building_id)

    plan = db.get(Floorplan, floor_id)
    if plan is None or not plan.image_url:
        raise MapDataError(
            f"{b.name if b else ''} {f.floor}층에 설계도가 없습니다.\n"
            "관리자웹에서 평면도를 먼저 올려주세요."
        )

    mask = db.get(FloorMask, floor_id)
    src = DbMapSource(db)

    return {
        # map-tool 의 applyProjectData 가 이 두 값으로 파일을 가려낸다.
        "mappinProject": True,
        "version": 1,
        # 파일에서 온 것과 구분한다. 받는 쪽이 좌표 환산 여부를 이걸로 정한다.
        "source": "db",

        "floorId": f.id,
        "buildingId": f.building_id,
        "buildingName": b.name if b else "",
        "floor": f.floor,
        "major": f.major,
        "label": f"{b.name} {f.floor}층" if b else f"{f.floor}층",

        "imageDataUrl": plan.image_url,

        # 마스크는 PNG data URL. alpha > 0 인 픽셀이 통행 가능이다
        # (WEB-FE/src/lib/maskRaster.ts 와 같은 규칙).
        "maskDataUrl": mask.data_url if mask else None,
        "maskW": mask.width if mask else None,
        "maskH": mask.height if mask else None,

        # **마스크 픽셀** 기준이다. 원본 픽셀 기준으로 바꾸려면 × maskW / origW.
        "scaleMPerPx": float(f.scale_m_per_px) if f.scale_m_per_px is not None else None,

        # 아래 좌표들의 기준 폭.
        "designW": DESIGN_W,

        "beacons": [
            {
                "id": bc.source_label or bc.name or bc.id,   # 표시 라벨 (B1, B2…)
                "uid": bc.id,                                # DB 기본키
                "x": float(bc.x or 0), "y": float(bc.y or 0),  # 설계도(900) 기준
                # DB 의 `name` 이 곧 광고 이름이다. map-tool 은 bleName 이라 부른다.
                "bleName": bc.name or "",
                "mac": bc.mac or "",
                "minor": bc.minor,
                "major": bc.major,
                "type": bc.type or "semantic",
            }
            for bc in db.query(Beacon)
                       .filter(Beacon.floor_id == floor_id)
                       .order_by(Beacon.minor, Beacon.name)
                       .all()
        ],
        "landmarks": [
            {
                "id": lm.id,
                "uid": lm.id,
                "name": lm.name,
                "x": lm.x, "y": lm.y,   # 설계도(900) 기준
                "category": lm.type,
                "isConnector": lm.is_connector,
            }
            for lm in src.landmarks(floor_id)
        ],

        # 튜닝값은 DB 에 자리가 없다. 넣지 않고 map-tool 의 화면 기본값을 그대로 쓴다.
        # (파일 모드는 저장된 값을 쓰므로 두 모드의 값이 다를 수 있다 — 의도된 것)
    }
