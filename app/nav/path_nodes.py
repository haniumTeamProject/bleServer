"""이동영역 마스크에서 경로 노드와 연결을 만든다.

`WEB-FE/src/features/mapEditor/pathNodes.ts` 를 **동작이 같도록** 옮긴 것이다.

── 왜 옮기나 ──────────────────────────────────────────────────────

관리자가 화면에서 확인한 경로와 사용자가 안내받는 경로가 달라지면 안 된다.
그래서 노드 생성기를 한 벌만 두고 싶은데, 관리자웹은 브라우저에서 마스크를
캔버스로 읽어 만들고 서버는 그럴 수단이 없다. 결국 같은 알고리즘이 두 곳에
있게 되므로, **원본을 그대로 옮기고 출력이 같은지 기계로 대조한다**
(`tests/test_path_nodes.py` 가 실제 TS 를 돌려 얻은 값과 비교한다).

── 옮길 때 지킨 것 ────────────────────────────────────────────────

보기 좋게 고치고 싶은 곳이 여럿 있었지만 **손대지 않았다.** 예를 들어
`_trace_boundary` 는 닫히는 마지막 점을 버리는데(원본이 `slice(0,-1)` 를 한다),
고치면 노드 하나가 어긋나 두 구현이 갈라진다. 개선은 원본 쪽에서 하고 여기로
다시 옮기는 순서여야 한다.

바꾼 것은 언어 차이로 어쩔 수 없는 것뿐이다.

    JS `Map` 삽입 순서        →  파이썬 dict (같은 성질)
    `Array.prototype.sort`    →  `sorted` (둘 다 안정 정렬)
    재귀 깊이                  →  긴 경계에서 기본 한도를 넘어 아래에서 올린다
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field

# 원본의 상수. 값을 바꾸면 관리자웹과 갈라진다.
MIN_COMPONENT_PIXELS = 25
SIMPLIFY_EPSILON_PX = 3.0
MAX_SNAP_PX = 50.0
MERGE_RADIUS_PX = 6.0
DEFAULT_CROSSING_MAX_PX = 240.0
FACING_T_MARGIN = 0.02

# 경계 단순화(Douglas-Peucker)가 재귀다. 실측 평면도(2372×1790)의 외곽선은 점이
# 수만 개라 최악의 경우 기본 한도(1000)를 넘는다. 반복문으로 바꾸면 연결 순서가
# 달라질 위험이 있어 한도를 올리는 쪽을 택했다.
_MIN_RECURSION = 20000
if sys.getrecursionlimit() < _MIN_RECURSION:
    sys.setrecursionlimit(_MIN_RECURSION)

Point = tuple[float, float]


@dataclass
class PathNode:
    id: str
    x: float
    y: float
    type: str            # corner | connector | landmark | facing
    concave: bool        # corner 에서만 의미 있음
    pair_kind: str | None = None   # facing 일 때만 — 맞은편이 어느 종류의 입구인지


@dataclass
class PathEdge:
    a: str
    b: str
    type: str            # wall | cross


@dataclass
class EntrancePoint:
    x: float
    y: float
    kind: str            # connector | landmark


@dataclass
class _LoopEntry:
    point: Point
    kind: str
    segment_index: int
    t: float
    pair_kind: str | None = None


# ---------------------------------------------------------------------------
# 연결 성분
# ---------------------------------------------------------------------------
def _label_components(mask, w: int, h: int) -> tuple[list[int], list[int]]:
    """4방향으로 이어진 덩어리에 번호를 매긴다. (라벨 배열, 덩어리별 픽셀 수)

    번호는 래스터 순서로 처음 만난 순서대로 붙는다 — 원본과 같아야 뒤에서
    만들어지는 노드 id(N01, N02…)가 일치한다.
    """
    labels = [-1] * (w * h)
    counts: list[int] = []
    total = w * h
    for index in range(total):
        if not mask[index] or labels[index] != -1:
            continue
        component_id = len(counts)
        stack = [index]
        count = 0
        labels[index] = component_id
        while stack:
            current = stack.pop()
            x = current % w
            count += 1
            for nxt in (current - 1, current + 1, current - w, current + w):
                if nxt < 0 or nxt >= total or abs((nxt % w) - x) > 1:
                    continue
                if mask[nxt] and labels[nxt] == -1:
                    labels[nxt] = component_id
                    stack.append(nxt)
        counts.append(count)
    return labels, counts


def _trace_boundary(mask, w: int, h: int, labels: list[int], component_id: int) -> list[Point]:
    """덩어리 하나의 바깥 경계를 픽셀 격자선을 따라 딴다. 가장 긴 고리를 돌려준다."""

    def is_fg(x: int, y: int) -> bool:
        return (0 <= x < w and 0 <= y < h
                and mask[y * w + x] == 1 and labels[y * w + x] == component_id)

    # key = 시작점. 같은 시작점이 여러 번 나오면 나중 것이 덮어쓴다(원본의 Map.set).
    segments: dict[Point, tuple[Point, Point]] = {}

    def add(a: Point, b: Point) -> None:
        segments[a] = (a, b)

    for y in range(h):
        for x in range(w):
            if not is_fg(x, y):
                continue
            if not is_fg(x, y - 1):
                add((x, y), (x + 1, y))
            if not is_fg(x + 1, y):
                add((x + 1, y), (x + 1, y + 1))
            if not is_fg(x, y + 1):
                add((x + 1, y + 1), (x, y + 1))
            if not is_fg(x - 1, y):
                add((x, y + 1), (x, y))

    loops: list[list[Point]] = []
    while segments:
        first = next(iter(segments.values()))
        loop: list[Point] = [first[0]]
        segment: tuple[Point, Point] | None = first
        while segment is not None:
            segments.pop(segment[0], None)
            loop.append(segment[1])
            segment = segments.get(segment[1])
            # 다음 조각이 출발점으로 되돌아오면 그것은 넣지 않고 끝낸다.
            if segment is not None and segment[1] == loop[0]:
                break
        if len(loop) > 3:
            # 원본이 마지막 점을 버린다. 고치지 않는다 — 고치면 두 구현이 갈라진다.
            loops.append(loop[:-1])

    if not loops:
        return []
    return sorted(loops, key=lambda pts: -len(pts))[0]


# ---------------------------------------------------------------------------
# 경계 단순화
# ---------------------------------------------------------------------------
def _perpendicular_distance(p: Point, a: Point, b: Point) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    len_sq = dx * dx + dy * dy
    if len_sq == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len_sq))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _simplify_open(points: list[Point], epsilon: float) -> list[Point]:
    if len(points) < 3:
        return points
    max_distance = 0.0
    index = 0
    for i in range(1, len(points) - 1):
        d = _perpendicular_distance(points[i], points[0], points[-1])
        if d > max_distance:
            max_distance = d
            index = i
    if max_distance <= epsilon:
        return [points[0], points[-1]]
    left = _simplify_open(points[:index + 1], epsilon)[:-1]
    right = _simplify_open(points[index:], epsilon)
    return left + right


def _simplify(points: list[Point], epsilon: float) -> list[Point]:
    """닫힌 고리를 단순화한다. 첫 점에서 가장 먼 점을 골라 두 토막으로 나눠 처리한다."""
    if len(points) < 4:
        return points
    pivot = points[0]
    best = 1
    for i, p in enumerate(points):
        if (math.hypot(p[0] - pivot[0], p[1] - pivot[1])
                > math.hypot(points[best][0] - pivot[0], points[best][1] - pivot[1])):
            best = i
    head = _simplify_open(points[:best + 1], epsilon)[:-1]
    tail = _simplify_open(points[best:] + [pivot], epsilon)[:-1]
    return head + tail


# ---------------------------------------------------------------------------
# 고리 위의 점 찾기
# ---------------------------------------------------------------------------
@dataclass
class _Nearest:
    point: Point
    segment_index: int
    t: float
    distance: float


def _nearest_point_on_loop(loop: list[Point], point: Point) -> _Nearest:
    best = _Nearest(loop[0], 0, 0.0, math.inf)
    n = len(loop)
    for i in range(n):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        dx = x2 - x1
        dy = y2 - y1
        len_sq = dx * dx + dy * dy
        if len_sq == 0:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((point[0] - x1) * dx + (point[1] - y1) * dy) / len_sq))
        proj = (x1 + t * dx, y1 + t * dy)
        d = math.hypot(point[0] - proj[0], point[1] - proj[1])
        if d < best.distance:
            best = _Nearest(proj, i, t, d)
    return best


def _nearest_facing_segment(loop: list[Point], point: Point) -> _Nearest | None:
    """그 점을 **수직으로 마주보는** 벽 조각. 없으면 None.

    투영점이 조각 끝(모서리)에 걸리는 것은 뺀다. 문틀 옆기둥처럼 거리는 가깝지만
    법선이 통로를 가로지르지 않고 문턱을 따라가는 방향으로 나오기 때문이다.
    """
    best: _Nearest | None = None
    n = len(loop)
    for i in range(n):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        dx = x2 - x1
        dy = y2 - y1
        len_sq = dx * dx + dy * dy
        if len_sq == 0:
            continue
        t = ((point[0] - x1) * dx + (point[1] - y1) * dy) / len_sq
        if t <= FACING_T_MARGIN or t >= 1 - FACING_T_MARGIN:
            continue
        proj = (x1 + t * dx, y1 + t * dy)
        d = math.hypot(point[0] - proj[0], point[1] - proj[1])
        if best is None or d < best.distance:
            best = _Nearest(proj, i, t, d)
    return best


def _estimate_normal(simplified: list[Point], segment_index: int) -> Point:
    """그 조각의 접선을 90도 돌린 단위 법선."""
    x1, y1 = simplified[segment_index]
    x2, y2 = simplified[(segment_index + 1) % len(simplified)]
    tx = x2 - x1
    ty = y2 - y1
    length = math.hypot(tx, ty)
    if length == 0:
        return (0.0, 0.0)
    return (-ty / length, tx / length)


def _ray_cast_to_opposite_wall(mask, w: int, h: int, start: Point, normal: Point) -> Point | None:
    """법선(또는 그 반대) 방향으로 1px 씩 나아가 통행영역을 벗어나기 직전 지점."""
    if normal[0] == 0 and normal[1] == 0:
        return None
    max_steps = max(w, h)

    def march(dir_x: float, dir_y: float) -> Point | None:
        x, y = start
        last: Point | None = None
        for _ in range(max_steps):
            x += dir_x
            y += dir_y
            cx = math.floor(x)
            cy = math.floor(y)
            if cx < 0 or cy < 0 or cx >= w or cy >= h:
                return last
            if mask[cy * w + cx] == 0:
                return last
            last = (x, y)
        return None

    return march(normal[0], normal[1]) or march(-normal[0], -normal[1])


def _kind_priority(kind: str) -> int:
    if kind in ("connector", "landmark"):
        return 2
    if kind == "facing":
        return 1
    return 0


# ---------------------------------------------------------------------------
@dataclass
class PathGraph:
    nodes: list[PathNode] = field(default_factory=list)
    edges: list[PathEdge] = field(default_factory=list)


def generate_path_nodes(mask, w: int, h: int,
                        entrances: list[EntrancePoint] | None = None,
                        crossing_max_px: float = DEFAULT_CROSSING_MAX_PX) -> PathGraph:
    """마스크에서 노드와 연결을 만든다.

    mask 는 길이 w*h 의 0/1 시퀀스(list, bytes, numpy 배열 모두 됨).
    """
    entrances = entrances or []
    nodes: list[PathNode] = []
    edges: list[PathEdge] = []
    edge_keys: set[str] = set()

    def add_edge(a: PathNode, b: PathNode, type_: str) -> None:
        if a.id == b.id:
            return
        pair = f"{a.id}|{b.id}" if a.id < b.id else f"{b.id}|{a.id}"
        key = f"{type_}:{pair}"
        if key in edge_keys:
            return
        edge_keys.add(key)
        edges.append(PathEdge(a=a.id, b=b.id, type=type_))

    labels, counts = _label_components(mask, w, h)

    components: list[tuple[list[Point], list[Point]]] = []
    for component_id, count in enumerate(counts):
        if count < MIN_COMPONENT_PIXELS:
            continue
        raw_loop = _trace_boundary(mask, w, h, labels, component_id)
        simplified = _simplify(raw_loop, SIMPLIFY_EPSILON_PX)
        if raw_loop and len(simplified) >= 3:
            components.append((raw_loop, simplified))

    # 입구를 가장 가까운 덩어리에 배정한다. 50px 넘게 떨어져 있으면 버린다.
    assigned: list[list[tuple[EntrancePoint, Point]]] = [[] for _ in components]
    for entrance in entrances:
        best_index = -1
        best: _Nearest | None = None
        for index, (raw_loop, _s) in enumerate(components):
            snap = _nearest_point_on_loop(raw_loop, (entrance.x, entrance.y))
            if best is None or snap.distance < best.distance:
                best = snap
                best_index = index
        if best is None or best.distance > MAX_SNAP_PX:
            continue
        assigned[best_index].append((entrance, best.point))

    for component_index, (raw_loop, simplified) in enumerate(components):
        entries: list[_LoopEntry] = [
            _LoopEntry(point=p, kind="corner", segment_index=i, t=0.0)
            for i, p in enumerate(simplified)
        ]

        def find_or_insert(point: Point, kind: str, pair_kind: str | None = None,
                           exclude: _LoopEntry | None = None) -> _LoopEntry:
            for entry in entries:
                if entry is exclude:
                    continue
                if math.hypot(entry.point[0] - point[0], entry.point[1] - point[1]) <= MERGE_RADIUS_PX:
                    if _kind_priority(kind) > _kind_priority(entry.kind):
                        entry.kind = kind
                        entry.pair_kind = pair_kind
                    return entry
            nearest = _nearest_point_on_loop(simplified, point)
            entry = _LoopEntry(point=point, kind=kind, pair_kind=pair_kind,
                               segment_index=nearest.segment_index, t=nearest.t)
            entries.append(entry)
            return entry

        pairs: list[tuple[_LoopEntry, _LoopEntry]] = []

        for entrance, snap in assigned[component_index]:
            entrance_entry = find_or_insert(snap, entrance.kind)
            # 모서리에 정확히 붙은 입구는 접선이 엉뚱하게 나온다. 원본 좌표 기준으로
            # 진짜 마주보는 조각을 먼저 찾고, 레이도 그 조각 위 투영점에서 쏜다.
            facing_segment = _nearest_facing_segment(simplified, (entrance.x, entrance.y))
            ray_start = facing_segment.point if facing_segment else snap
            if facing_segment:
                normal = _estimate_normal(simplified, facing_segment.segment_index)
            else:
                normal = _estimate_normal(
                    simplified, _nearest_point_on_loop(simplified, snap).segment_index)
            facing_raw = _ray_cast_to_opposite_wall(mask, w, h, ray_start, normal)
            if facing_raw is None:
                continue
            facing_snap = _nearest_point_on_loop(raw_loop, facing_raw).point
            # 좁은 복도에서 맞은편이 자기 짝과 합쳐지는 것을 막는다.
            facing_entry = find_or_insert(facing_snap, "facing", entrance.kind, entrance_entry)
            pairs.append((entrance_entry, facing_entry))

        # 코너에서도 마주보는 지점을 찾아 건너기 후보로 넣는다.
        # 목록을 먼저 뜬다 — 아래에서 entries 에 새 항목이 붙기 때문이다(원본의 filter 와 같다).
        corner_entries = [e for e in entries if e.kind == "corner"]
        for corner_entry in corner_entries:
            normal = _estimate_normal(simplified, corner_entry.segment_index)
            facing_raw = _ray_cast_to_opposite_wall(mask, w, h, corner_entry.point, normal)
            if facing_raw is None:
                continue
            facing_snap = _nearest_point_on_loop(raw_loop, facing_raw).point
            facing_entry = find_or_insert(facing_snap, "facing", None, corner_entry)
            pairs.append((corner_entry, facing_entry))

        entries.sort(key=lambda e: (e.segment_index, e.t))

        entry_to_node: dict[int, PathNode] = {}
        component_nodes: list[PathNode] = []
        n = len(entries)
        for index in range(n):
            entry = entries[index]
            previous = entries[(index - 1 + n) % n]
            nxt = entries[(index + 1) % n]
            x, y = entry.point
            concave = entry.kind == "corner" and (
                (x - previous.point[0]) * (nxt.point[1] - y)
                - (y - previous.point[1]) * (nxt.point[0] - x) < 0
            )
            node = PathNode(
                id=f"N{len(nodes) + len(component_nodes) + 1:02d}",
                x=x, y=y, type=entry.kind, concave=concave,
                pair_kind=entry.pair_kind if entry.kind == "facing" else None,
            )
            entry_to_node[id(entry)] = node
            component_nodes.append(node)
        nodes.extend(component_nodes)

        for index in range(len(component_nodes)):
            add_edge(component_nodes[index],
                     component_nodes[(index + 1) % len(component_nodes)], "wall")

        for entry_a, entry_b in pairs:
            a = entry_to_node[id(entry_a)]
            b = entry_to_node[id(entry_b)]
            if a.id == b.id:
                continue
            if math.hypot(a.x - b.x, a.y - b.y) <= crossing_max_px:
                add_edge(a, b, "cross")

    return PathGraph(nodes=nodes, edges=edges)
