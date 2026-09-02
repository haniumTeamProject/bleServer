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
from app.pathnode.models import FloorPathNodes

# 관리자웹이 쓰는 설계도 좌표 기준 폭. WEB-FE/src/lib/constants.ts 의 MAP_DESIGN_W.
# 여기서 바꾸면 프론트와 갈라지므로 같이 고쳐야 한다.
DESIGN_W = 900

# 관리자가 경로노드 화면에서 값을 정하지 않고 저장한(또는 컬럼이 생기기 전에 저장된)
# 층에 쓰는 기본값. 관리자웹 PathNodePage.tsx 의 DEFAULT_CROSS_PENALTY_M 과 같아야 한다.
CROSS_PENALTY_M = 5.0        # 우회 대비 이만큼 이상 짧아야 건넌다

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
#
# ── 위 표는 옛 규칙 것이다 ────────────────────────────────────────
#
# 저 실측은 `_seq_nearest`(표본마다 1등 하나만) 시절 것이다. 그때는 반경을 키우면
# 옆방 비콘이 1등을 뺏어서 정말로 딸려 들어왔다. 지금 쓰는 `_seq_approach` 는
# **가까워지는 비콘을 전부** 세우므로 성질이 다르다 — 반경이 그냥 반경이다.
#
# 지금 규칙으로 다시 재면(실측 4층 B1 → 계단2):
#
#     3.0m   19칸   B1 B4 B18 B8 B19 …
#     4.0m   21칸   B31 추가
#     5.0m   23칸   B30 추가
#     6.0m   23칸   B6 추가
#     8.0m   26칸   B3 추가
#    10.0m   29칸   B7 이 들어온다
#
# **10m 는 안 된다.** B7 은 걷는 내내 한 번도 1등을 못 해서 추적기가 그 칸에서
# 멈추는 비콘이다(`_seq_approach` 주의 참고).
#
# 그래도 기본값은 3.0 으로 둔다. 이 숫자는 **좁은 복도(4층)** 것이고, 넓은 홀은
# 아래처럼 층별로 줘야 한다. 한 값으로 둘 다 맞출 수가 없다.
# 층별 값을 정할 때는 `tests/check_radius.py` 로 그 층 DB 를 직접 재면 된다.
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
# 그건 마이그레이션이 필요해서, 우선 여기 적어둔다.
#
# ── 왜 .env 가 아니라 코드인가 ────────────────────────────────────
#
# **`.env` 에 적으면 안 먹힌다.** 이 프로젝트에는 `load_dotenv` 를 부르는 곳이
# 없다. `config.py` 의 pydantic 이 `.env` 를 읽기는 하지만 그건 거기 선언된
# 필드(`database_url` 등)뿐이고, `os.environ` 에는 안 실린다. 아래 반경은
# `os.environ.get` 으로 읽으므로 `.env` 에 적어봐야 조용히 무시된다.
#
# 그래서 값은 코드에 두고, 환경변수는 **덮어쓰는 용도로만** 남긴다
# (`BEACON_MATCH_RADIUS_BY_FLOOR="<층id>=8.0"` — 실제 환경변수로 내보낼 때만).
#
# 층 id 는 이렇게 찾는다.
#
#     SELECT id, floor, name FROM floors ORDER BY floor;
#     python tests/check_radius.py            ← 같은 목록이 나온다
FLOOR_RADIUS_M: dict[str, float] = {
    # 1층 로비. 넓은 홀이라 경로는 벽을 따라 도는데 비콘은 가운데 있다.
    # 3m 로는 B1~B4 넷만 서고 나머지가 통째로 빠졌다.
    "439d51eb-6634-4856-8311-726c90c9f46c": 8.0,
}


def _radius_default() -> float:
    try:
        return max(0.1, float(os.environ.get("BEACON_MATCH_RADIUS_M", "")
                              or BEACON_MATCH_RADIUS_M))
    except ValueError:
        return BEACON_MATCH_RADIUS_M


def _radius_by_floor() -> dict[str, float]:
    """층별 반경. 코드에 적은 것이 기본이고, 환경변수가 있으면 그것이 이긴다."""
    out: dict[str, float] = dict(FLOOR_RADIUS_M)
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
        """안내에 쓸 경로 그래프. **관리자웹이 저장해둔 것만 쓴다.**

        노드·간선을 만드는 것은 관리자웹(`pathNodes.ts`)의 몫이다. 관리자가 화면에서
        점을 옮기고 잘못된 건너기를 지운 뒤 저장한 결과가 곧 안내에 쓰이는 그래프다.

        서버가 같은 계산을 따로 돌던 폴백은 없앴다. 사람이 검수하지 않은 그래프로
        시각장애인을 안내하는 것보다 안내를 거절하는 편이 낫고, 두 벌의 생성 코드를
        똑같이 유지하는 일은 화면과 안내가 조용히 갈라지는 원인이 된다.

        저장된 좌표는 마스크 픽셀 기준이라 900(`DESIGN_W`) 좌표로 되돌려 내보낸다.
        비콘·랜드마크가 900 기준이라 같은 좌표계여야 "이 비콘에서 가장 가까운 노드"를
        찾을 수 있다.
        """
        saved = self.db.get(FloorPathNodes, floor_id)
        if saved is None or not saved.nodes:
            raise MapDataError(
                "이 층은 경로노드가 저장되어 있지 않아 안내할 수 없습니다.\n"
                "관리자웹의 경로노드 화면에서 그래프를 확인하고 저장해주세요."
            )
        return self._graph_from_saved(saved)

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
        """건너기 간선에 얹는 가중치(m). 관리자가 경로노드 화면에서 정한 값을 따른다.

        예전에는 관리자웹이 이 값을 화면 안에서만 쓰고 저장하지 않아서, 서버는 같은
        기본값을 따로 박아두고 있었다. 관리자가 화면에서 값을 바꿔 경로를 검수해도
        안내는 옛 값으로 나가는 상태였다. 지금은 경로노드와 함께 저장된 값을 읽는다.
        """
        saved = self.db.get(FloorPathNodes, floor_id)
        if saved is not None and saved.cross_penalty_m is not None:
            return float(saved.cross_penalty_m)
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
