"""경로를 찾아 **비콘 순서**로 바꾼다.

지도 편집 도구(map_inspection.html)가 브라우저에서 하던 계산을 서버로 옮긴 것이다.
원본은 `runDijkstra()` 와 `computeBeaconSequenceForPath()` 두 함수이고,
아래 구현은 그 규칙을 그대로 따른다. **결과가 달라지면 안 된다** — 관리자가 도구에서
확인한 경로와 사용자가 실제로 안내받는 경로가 다르면 검수가 의미를 잃는다.

── 건너기 페널티 ────────────────────────────────────────────────────

핵심 규칙 하나만 짚어둔다. 이 시스템에서 "건너기"는 벽에서 손을 떼고 반대편으로
가로지르는 것이라, 시각장애인에게는 벽을 짚고 도는 것보다 훨씬 부담스럽다.
그래서 건너기 엣지에는 실제 길이에 **가상의 거리(기본 10m)를 더해서** 계산한다.

    비용 = 실제거리 + (건너기면 페널티)

결과적으로 건너기가 그 페널티보다 더 많은 거리를 절약할 때만 선택된다.
애매하게 조금 짧아지는 건너기는 자동으로 회피된다. 반환하는 `dist_m` 은 페널티를
뺀 **실제 거리**다 — 사용자에게 "48m 남았습니다"라고 말할 때 쓰는 값이므로
가상 비용이 섞이면 안 된다.

── 왜 노드가 아니라 비콘인가 ────────────────────────────────────────

경로 탐색은 노드 그래프에서 하지만, 앱에 내려주는 것은 비콘 순서다.
앱이 실제로 감지할 수 있는 건 비콘뿐이고, 노드는 지도 위의 기하학적 지점이라
그 앞에 서 있는지 알 방법이 없기 때문이다. 노드 경로를 구한 뒤 각 노드에서
반경 안의 가장 가까운 비콘을 찾고, 연속 중복을 접어서 비콘 순서를 만든다.
"""

from __future__ import annotations

import heapq
import math
import os
from dataclasses import dataclass

from app.nav.map_source import (
    BeaconInfo, Graph, LandmarkInfo, MapDataError, MapSource, Node,
)


@dataclass(frozen=True)
class NodePath:
    node_ids: list[str]
    dist_m: float                 # 실제 거리 (페널티 제외)
    crossings: int                # 건너기 횟수


@dataclass(frozen=True)
class BeaconStep:
    seq: int
    beacon_id: str
    node_id: str                  # 이 비콘에 매칭된 첫 노드 — 방향 계산에 쓴다
    turn: str | None = None       # left | right | None
    is_arrival: bool = False


# ---------------------------------------------------------------------------
# 관리자웹과 같은 경로 찾기
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PathfindResult:
    path: list[str]
    distance_px: float            # **페널티 포함** 가중치 합 — 실제 이동 거리와 다르다


def cross_edge_usable(node_type: str | None, node_id: str, start_id: str) -> bool:
    """그 건너기를 **지름길로 써도 되는가.** (`pathfind.ts` 의 `isCrossEdgeUsable`)

    건너기는 "여기서 출발해 반대편으로 건너세요"라는 안내다. 그런데 그 시작점이
    남의 목적지나 연결자 앞이면, **거기 있지도 않은 사람에게** 그 문 앞에서
    건너라고 말하는 셈이 된다.

    그래서 랜드마크·연결자에서 시작하는 건너기는 **그 지점에서 실제로 출발할
    때만** 쓴다. 코너는 실제 출입구가 아니라 벽 모양이 만든 점이라 제약이 없다.

    관리자웹이 이미 이 규칙으로 경로를 그린다. 서버만 빠져 있으면 화면에 그려진
    경로와 안내에 쓰는 경로가 갈라져서 검수 자체가 의미를 잃는다.

    `node_type` 이 없으면(테스트 픽스처 등) 제약을 안 건다 — 원본 TS 도
    `undefined !== 'landmark'` 로 통과시킨다.
    """
    return node_type not in ("landmark", "connector") or node_id == start_id


def find_shortest_path(nodes, edges, start_id: str, end_id: str,
                       cross_penalty_px: float) -> PathfindResult | None:
    """`WEB-FE/src/features/mapEditor/pathfind.ts` 를 그대로 옮긴 것.

    관리자웹이 화면에서 보여주는 경로와 **정확히 같은 답**을 내야 하므로 반환값의
    의미까지 원본을 따른다. `distance_px` 에 페널티가 섞여 있는 것도 원본 그대로다
    (사용자에게 말할 거리로 쓰면 안 된다 — 그건 `find_node_path` 의 `dist_m` 이다).

    nodes/edges 는 dict 나 속성 접근이 되는 객체 아무거나 받는다.
    """
    def get(o, k):
        return o[k] if isinstance(o, dict) else getattr(o, k)

    def opt(o, k, default=None):
        """없어도 되는 필드. 픽스처는 `type`·`directed` 를 안 담는다."""
        if isinstance(o, dict):
            return o.get(k, default)
        return getattr(o, k, default)

    if start_id == end_id:
        return PathfindResult(path=[start_id], distance_px=0.0)

    by_id = {get(n, "id"): n for n in nodes}
    adjacency: dict[str, list[tuple[str, float]]] = {get(n, "id"): [] for n in nodes}
    for e in edges:
        a = by_id.get(get(e, "a"))
        b = by_id.get(get(e, "b"))
        if a is None or b is None:
            continue
        dist = math.hypot(get(a, "x") - get(b, "x"), get(a, "y") - get(b, "y"))
        is_cross = get(e, "type") == "cross"
        weight = dist + cross_penalty_px if is_cross else dist
        # 남의 목적지 앞을 지름길로 가로지르지 않는다.
        if is_cross and not cross_edge_usable(opt(a, "type"), get(a, "id"), start_id):
            continue
        adjacency[get(a, "id")].append((get(b, "id"), weight))
        # **건너기는 단방향이다.** 맞은편(b)은 벽에서 떨어진 허공이라 거기서
        # 출발할 자격이 없다. 한때 양방향으로 열어뒀는데, 그것은 코너 건너기가
        # 생기기 전 이야기였고 지금 TS 는 `if (!e.directed)` 로 막는다.
        if not opt(e, "directed", False):
            adjacency[get(b, "id")].append((get(a, "id"), weight))

    if start_id not in adjacency or end_id not in adjacency:
        return None

    dist: dict[str, float] = {get(n, "id"): math.inf for n in nodes}
    dist[start_id] = 0.0
    prev: dict[str, str] = {}
    visited: set[str] = set()

    while True:
        current = None
        current_dist = math.inf
        for node_id, d in dist.items():
            if node_id not in visited and d < current_dist:
                current = node_id
                current_dist = d
        if current is None or current == end_id:
            break
        visited.add(current)
        for to, weight in adjacency.get(current, ()):
            if to in visited:
                continue
            alt = current_dist + weight
            if alt < dist.get(to, math.inf):
                dist[to] = alt
                prev[to] = current

    end_dist = dist.get(end_id)
    if end_dist is None or end_dist == math.inf:
        return None

    path = [end_id]
    cur = end_id
    while cur != start_id:
        p = prev.get(cur)
        if p is None:
            return None
        path.insert(0, p)
        cur = p
    return PathfindResult(path=path, distance_px=end_dist)


# ---------------------------------------------------------------------------
# 다익스트라
# ---------------------------------------------------------------------------
def find_node_path(graph: Graph, start_id: str, end_id: str,
                   cross_penalty_m: float = 10.0) -> NodePath | None:
    """노드 그래프에서 최단 경로. 못 찾으면 None.

    원본(JS)은 매 단계 미방문 집합을 선형 탐색한다. 노드가 수백 개라 실측에서는
    문제가 없었지만, 여기서는 힙을 쓴다 — 결과는 같고 계산량만 줄어든다.
    """
    if graph.node(start_id) is None or graph.node(end_id) is None:
        return None
    if start_id == end_id:
        return NodePath(node_ids=[start_id], dist_m=0.0, crossings=0)

    adj: dict[str, list[tuple[str, float, float, int]]] = {n.id: [] for n in graph.nodes}
    for e in graph.edges:
        if e.a not in adj or e.b not in adj:
            continue
        is_cross = 1 if e.type == "cross" else 0
        cost = e.dist_m + is_cross * cross_penalty_m
        # 남의 목적지·연결자 앞의 건너기를 지름길로 쓰지 않는다
        # (`cross_edge_usable` 참고 — 관리자웹 pathfind.ts 와 같은 규칙).
        if is_cross:
            a_node = graph.node(e.a)
            if a_node is not None and not cross_edge_usable(
                    getattr(a_node, "type", None), a_node.id, start_id):
                continue
        adj[e.a].append((e.b, cost, e.dist_m, is_cross))
        # **건너기는 단방향이다.** a(입구/벽 끝) → b(맞은편) 으로만 건넌다.
        #
        # 맞은편 지점은 벽에서 떨어진 허공이라 거기서 출발할 수가 없다. 벽을
        # 만지며 걷는 사람에게 출발점은 반드시 벽에 붙어 있어야 한다.
        #
        # 한때 양방향으로 열어뒀는데 **관리자웹과 다른 경로가 나왔다.**
        # pathfind.ts 는 `if (!e.directed)` 일 때만 역방향을 열고, 관리자웹은
        # cross 를 항상 directed 로 만든다(pathNodes.ts). 화면에 그려진 경로와
        # 안내에 쓰는 경로가 갈라지면 검수 자체가 의미를 잃는다.
        if not e.directed:
            adj[e.b].append((e.a, cost, e.dist_m, is_cross))

    g = {start_id: 0.0}
    dist = {start_id: 0.0}
    cross = {start_id: 0}
    came: dict[str, str] = {}
    seen: set[str] = set()
    heap: list[tuple[float, str]] = [(0.0, start_id)]

    while heap:
        cur_cost, cur = heapq.heappop(heap)
        if cur in seen:
            continue
        seen.add(cur)
        if cur == end_id:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            path.reverse()
            return NodePath(node_ids=path, dist_m=round(dist[end_id], 2),
                            crossings=cross[end_id])
        for nxt, cost, d, is_cross in adj.get(cur, ()):
            if nxt in seen:
                continue
            cand = cur_cost + cost
            if cand < g.get(nxt, math.inf):
                g[nxt] = cand
                dist[nxt] = dist[cur] + d
                cross[nxt] = cross[cur] + is_cross
                came[nxt] = cur
                heapq.heappush(heap, (cand, nxt))
    return None


# ---------------------------------------------------------------------------
# 노드 경로 → 비콘 순서
# ---------------------------------------------------------------------------
# 경로 선을 따라 이 간격으로 훑는다(미터).
#
# 노드 사이가 멀면 그 중간을 지나는 비콘이 통째로 빠지므로, 노드만 보지 않고
# 선 위를 잘게 훑는다. 자세한 이유는 `to_beacon_sequence` 참고.
SAMPLE_STEP_M = 1.0


def _walk(graph: Graph, node_ids: list[str], meters_per_px: float):
    """경로 선 위를 일정 간격으로 훑으며 (좌표, 그 지점이 속한 노드 id)를 내놓는다.

    간격은 실거리 기준이라 축척이 달라져도 촘촘함이 같다.
    """
    pts = [(graph.node(nid), nid) for nid in node_ids]
    pts = [(n, nid) for n, nid in pts if n is not None]
    if not pts:
        return
    step_px = SAMPLE_STEP_M / meters_per_px if meters_per_px > 0 else 1.0

    yield (pts[0][0].x, pts[0][0].y), pts[0][1]
    for (a, a_id), (b, b_id) in zip(pts, pts[1:]):
        seg = math.hypot(b.x - a.x, b.y - a.y)
        n = max(1, int(seg / step_px))
        for i in range(1, n + 1):
            t = i / n
            # 어느 노드에 속한 지점인지 — 가까운 쪽으로 준다. 회전 방향을
            # 계산할 때 이 값을 쓰므로 중간 지점이 엉뚱한 노드에 붙으면 안 된다.
            yield (a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t), (a_id if t < 0.5 else b_id)


def _pick_mode() -> str:
    """비콘을 고르는 규칙. `BEACON_PICK=nearest` 로 옛 방식으로 되돌릴 수 있다."""
    return (os.environ.get("BEACON_PICK") or "approach").strip().lower()


def _seq_nearest(graph: Graph, node_ids: list[str], beacons: list[BeaconInfo],
                 radius_m: float, meters_per_px: float) -> list[BeaconStep]:
    """옛 방식 — 표본마다 **반경 안 가장 가까운 하나**를 고르고 연속 중복을 접는다.

    **어느 표본에서도 1등을 못 하는 비콘은 영원히 안 들어온다.** 반경을 아무리
    키워도 마찬가지다 — 옆에 더 가까운 것이 늘 있기 때문이다. 실측 1층 로비에서
    홀 한가운데 비콘 넷이 이래서 통째로 빠졌다(반경 12m 로 올려도 그대로였다).

    같은 비콘을 두 번 지나는 경로(ㄷ자)에서 **두 번 세는 것**은 이쪽만 된다.
    """
    steps: list[BeaconStep] = []
    for (x, y), nid in _walk(graph, node_ids, meters_per_px):
        best, best_d = None, math.inf
        for b in beacons:
            d = math.hypot(x - b.x, y - b.y) * meters_per_px
            if d <= radius_m and d < best_d:
                best, best_d = b, d
        if best is None:
            continue
        if steps and steps[-1].beacon_id == best.id:
            continue
        steps.append(BeaconStep(seq=len(steps) + 1, beacon_id=best.id, node_id=nid))
    return steps


def _seq_approach(graph: Graph, node_ids: list[str], beacons: list[BeaconInfo],
                  radius_m: float, meters_per_px: float) -> list[BeaconStep]:
    """지금 방식 — **경로에 반경만큼 가까워지는 비콘을 전부**, 지나는 순서대로.

    비콘마다 경로 위에서 **가장 가까워지는 지점**(최근접점)을 찾는다. 그 거리가
    반경 안이면 경로에 세우고, 최근접점이 나오는 순서대로 늘어놓는다.

    ── 왜 바꿨나 ─────────────────────────────────────────────────

    옛 방식은 표본마다 1등 하나만 골라서, 넓은 홀처럼 **경로가 벽을 따라 도는데
    비콘은 가운데 있는** 배치에서 그 비콘이 한 번도 안 뽑혔다. 1등을 못 한다는
    이유만으로 6m 옆의 비콘이 통째로 사라지는 셈이다.

    이쪽은 "지나가면서 이만큼 가까워졌나"만 보므로 그런 구멍이 없다. 반경이
    실제로 반경으로 동작한다.

    ── 잃는 것 ───────────────────────────────────────────────────

    **연속 중복만 접히던 것이 통과 구간 단위로 바뀐다.** 옛 방식은 표본마다 1등을
    보고 직전과 같으면 접었다. 이쪽은 비콘이 반경 안에 들어와 있던 구간을 하나로
    묶으므로, 반경 밖으로 나갔다 다시 들어오면 두 칸이 된다 — 왕복 경로에서 같은
    비콘을 두 번 세는 성질은 그대로다.

    되돌리려면 `BEACON_PICK=nearest`.

    ── 주의 ──────────────────────────────────────────────────────

    경로에 세운다고 폰이 그 비콘을 **가장 세게** 잡는다는 보장은 없다. 걷는 내내
    한 번도 1등을 못 하는 비콘이 경로에 끼면 추적기가 그 칸에서 멈춘다(실측 4층
    B7 이 그랬다). 반경을 넓게 잡을수록 그 위험이 커진다.
    """
    samples = list(_walk(graph, node_ids, meters_per_px))

    # 비콘마다 **반경 안에 들어와 있던 구간**을 끊어서 본다. 한 구간이 한 번
    # 지나간 것이고, 그 구간에서 가장 가까웠던 표본이 그 칸의 자리다.
    #
    # 전체 최소 하나만 잡으면 왕복 경로에서 같은 비콘 옆을 두 번 지나도 한 칸이
    # 된다. 두 번 지나는 것은 실제로 일어나는 일이라 두 번 세야 한다.
    picks: list[tuple[int, float, str, str]] = []     # (표본 순번, 거리, 비콘, 노드)
    for b in beacons:
        run: tuple[int, float, str] | None = None     # 이번 통과의 최근접
        for i, ((x, y), nid) in enumerate(samples):
            d = math.hypot(x - b.x, y - b.y) * meters_per_px
            if d <= radius_m:
                if run is None or d < run[1]:
                    run = (i, d, nid)
            elif run is not None:
                picks.append((run[0], run[1], b.id, run[2]))
                run = None                            # 반경 밖으로 나갔다 — 한 번 지나감
        if run is not None:
            picks.append((run[0], run[1], b.id, run[2]))

    # 최근접점 순서 = 지나가는 순서. 같은 표본이면 가까운 쪽을 먼저 만난 것으로 본다.
    picks.sort(key=lambda p: (p[0], p[1]))
    return [BeaconStep(seq=n + 1, beacon_id=bid, node_id=nid)
            for n, (_i, _d, bid, nid) in enumerate(picks)]


def to_beacon_sequence(graph: Graph, node_ids: list[str], beacons: list[BeaconInfo],
                       radius_m: float, meters_per_px: float) -> list[BeaconStep]:
    """경로가 지나는 비콘을 순서대로.

    ── 노드만 보면 비콘이 빠진다 ────────────────────────────────────

    예전 지도 도구는 경로에 **1m 간격 노드**를 깔아두고 그것을 훑었다. 지금 쓰는
    그래프(관리자웹 것)는 노드가 **코너·맞은편·목적지**에만 있어서 간격이 훨씬 넓다.
    긴 복도는 양 끝 코너 두 점뿐이라, 그 사이에 늘어선 비콘이 통째로 빠진다.

    그렇다고 그래프에 노드를 더 넣으면 관리자웹이 만드는 것과 갈라진다. 대신
    **경로 선 위를 1m 간격으로 훑는다.** 노드는 그대로 두고 훑기만 촘촘히 하는
    것이라 결과는 옛 도구와 같아진다.

    같은 비콘이 연속으로 나오면 하나로 접는다 — 안 접으면 비콘 하나가 수십 번
    반복된다.

    **연속이 아닌 중복은 접지 않는다.** 왕복 경로에서 같은 비콘을 두 번 지나는
    것은 실제로 일어나는 일이고, 그때는 두 번 세는 것이 맞다.
    """
    if _pick_mode() == "nearest":
        steps = _seq_nearest(graph, node_ids, beacons, radius_m, meters_per_px)
    else:
        steps = _seq_approach(graph, node_ids, beacons, radius_m, meters_per_px)
    if steps:
        last = steps[-1]
        steps[-1] = BeaconStep(seq=last.seq, beacon_id=last.beacon_id,
                               node_id=last.node_id, turn=last.turn, is_arrival=True)
    return steps


# ---------------------------------------------------------------------------
# 회전 방향
# ---------------------------------------------------------------------------
def turn_at(prev: Node, cur: Node, nxt: Node, min_deg: float = 30.0) -> str | None:
    """세 점에서 회전 방향. 거의 직진이면 None.

    화면 좌표계라 y 가 아래로 증가한다. 그래서 외적 부호의 의미가 수학 좌표계와
    반대다 — cross > 0 이 오른쪽이다. 이걸 뒤집으면 "왼쪽으로 꺾으세요"가
    오른쪽으로 나가고, 사용자는 그걸 확인할 방법이 없다.
    """
    v1x, v1y = cur.x - prev.x, cur.y - prev.y
    v2x, v2y = nxt.x - cur.x, nxt.y - cur.y
    n1 = math.hypot(v1x, v1y)
    n2 = math.hypot(v2x, v2y)
    if n1 == 0 or n2 == 0:
        return None
    cross = (v1x * v2y - v1y * v2x) / (n1 * n2)
    dot = (v1x * v2x + v1y * v2y) / (n1 * n2)
    angle = math.degrees(math.atan2(abs(cross), dot))
    if angle < min_deg:
        return None
    return "right" if cross > 0 else "left"


def annotate_turns(graph: Graph, steps: list[BeaconStep],
                   min_deg: float = 30.0) -> list[BeaconStep]:
    """각 비콘 지점에서의 회전 방향을 채운다."""
    out: list[BeaconStep] = []
    for i, st in enumerate(steps):
        turn = None
        if 0 < i < len(steps) - 1:
            a = graph.node(steps[i - 1].node_id)
            b = graph.node(st.node_id)
            c = graph.node(steps[i + 1].node_id)
            if a and b and c:
                turn = turn_at(a, b, c, min_deg)
        out.append(BeaconStep(seq=st.seq, beacon_id=st.beacon_id, node_id=st.node_id,
                              turn=turn, is_arrival=st.is_arrival))
    return out


# ---------------------------------------------------------------------------
# 바깥에서 부르는 것
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RouteResult:
    steps: list[BeaconStep]
    total_distance_m: float
    crossings: int
    node_ids: list[str]
    destination: LandmarkInfo


WALK_SPEED_MPS = float(__import__("os").environ.get("NAV_WALK_SPEED", "0.7"))


def build_route(source: MapSource, floor_id: str, *, from_beacon_id: str,
                to_landmark_id: str) -> RouteResult:
    """출발 비콘에서 목적지 랜드마크까지의 비콘 순서를 만든다.

    출발점이 비콘인 이유: 사용자가 지금 서 있는 곳을 알 수 있는 유일한 단서가
    "방금 잡힌 비콘"이기 때문이다. 그래서 그 비콘에 가장 가까운 노드를
    출발 노드로 삼는다.
    """
    graph = source.graph(floor_id)
    if graph.empty:
        raise MapDataError("경로 그래프가 비어 있습니다.")

    beacons = source.beacons(floor_id)
    landmarks = source.landmarks(floor_id)
    mpp = source.meters_per_px(floor_id)

    dest = next((lm for lm in landmarks if lm.id == to_landmark_id), None)
    if dest is None:
        raise MapDataError(f"목적지를 찾을 수 없습니다: {to_landmark_id}")

    origin = next((b for b in beacons if b.id == from_beacon_id), None)
    if origin is None:
        raise MapDataError(f"출발 비콘을 찾을 수 없습니다: {from_beacon_id}")

    start_node = _nearest_node(graph, origin.x, origin.y)
    if start_node is None:
        raise MapDataError("출발 비콘 근처에 경로 노드가 없습니다.")

    # 랜드마크는 그래프에 자기 id 로 노드가 들어가 있다(도구가 그렇게 만든다).
    # 없으면 좌표로 가장 가까운 노드를 찾는다.
    end_node = graph.node(dest.id) or _nearest_node(graph, dest.x, dest.y)
    if end_node is None:
        raise MapDataError("목적지 근처에 경로 노드가 없습니다.")

    found = find_node_path(graph, start_node.id, end_node.id,
                           source.cross_penalty_m(floor_id))
    if found is None:
        raise MapDataError(f"{origin.id} 에서 {dest.name} 까지 갈 수 있는 길이 없습니다.")

    steps = to_beacon_sequence(graph, found.node_ids, beacons,
                               source.beacon_match_radius_m(floor_id), mpp)
    steps = annotate_turns(graph, steps)
    return RouteResult(steps=steps, total_distance_m=found.dist_m,
                       crossings=found.crossings, node_ids=found.node_ids,
                       destination=dest)


def estimated_seconds(distance_m: float) -> int:
    """예상 소요 시간.

    보행 속도를 0.7m/s 로 잡는다. 일반 성인 보행(1.2~1.4)보다 훨씬 느린 값인데,
    벽을 짚고 확인하며 걷기 때문이다. 짧게 잡으면 "곧 도착"이라고 해놓고 한참을
    더 걷게 되어 신뢰를 잃는다.
    """
    return max(1, round(distance_m / max(WALK_SPEED_MPS, 0.1)))


def _nearest_node(graph: Graph, x: float, y: float) -> Node | None:
    best, best_d = None, math.inf
    for n in graph.nodes:
        d = math.hypot(n.x - x, n.y - y)
        if d < best_d:
            best, best_d = n, d
    return best
