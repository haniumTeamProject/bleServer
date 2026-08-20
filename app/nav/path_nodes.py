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

# 세 점이 이 안으로 일직선이면 가운데를 지운다. 손으로 칠한 마스크의 미세한
# 삐뚤어짐 때문에 곧은 벽이 여러 조각으로 남는 것을 정리한다.
COLLINEAR_MERGE_TOLERANCE_PX = 3.0

# 입구가 벽선에서 이 안이면 벽 위로 당겨 꼭짓점으로 끼운다. MAX_SNAP_PX(어느
# 덩어리에 속하는지 보는, 훨씬 관대한 값)와 용도가 달라 따로 둔다.
WALL_SPLICE_MAX_PX = 30.0

# 이보다 짧은 건너기는 버린다. 코너가 이미 벽에 거의 붙어 있으면 "자기 자신을
# 가리키는" 건너기가 생기고, 그 값이 SIMILAR_LENGTH_RATIO 의 기준이 되어 정작
# 필요한 방향들이 전부 걸러진다.
MIN_CROSSING_LENGTH_PX = 2.0

# 축척이 없을 때의 기본값. 관리자웹 DEFAULT_CROSSING_MAX_M(3m)을 20px/m 로 환산.
DEFAULT_CROSSING_MAX_PX = 60.0
DEFAULT_MIN_CLEARANCE_PX = 6.0

# 한 코너에서 여러 방향이 유효할 때, 가장 짧은 것의 이 배수 안이면 전부 남긴다.
SIMILAR_LENGTH_RATIO = 1.5

# 가는 길의 이 비율 이상에서 옆에 벽이 붙어 있으면 "그냥 벽 타면 닿는 곳"으로 본다.
# 100%로 두면 문틀처럼 잠깐 스치는 지점 하나 때문에 정상 횡단까지 걸러진다.
HUG_RATIO_THRESHOLD = 0.85

# 횡단은 상하좌우만 본다. 건물 복도가 대부분 격자에 맞춰져 있어서, 벽 각도로
# 법선을 추정하는 것보다 예측 가능하고 안정적이다.
CARDINAL_DIRECTIONS = [(1, 0), (-1, 0), (0, 1), (0, -1)]

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
    # type == "cross" 일 때만 의미 있음. True 면 **a(입구/벽 끝)에서 b(맞은편)로만**
    # 건널 수 있다. 벽을 만지며 걷는 사람에게 출발점은 벽에 붙어 있어야 하기
    # 때문이다 — 맞은편 지점은 허공이라 거기서 출발할 수가 없다.
    # (관리자웹 pathNodes.ts 의 `directed: type === 'cross' ? true : undefined`)
    directed: bool = False


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
    # 벽 경계선에서 얼마나 떨어져 있는지 — 벽선 트레이싱에 끼워도 되는지 판단용
    distance_to_loop: float = 0.0
    concave: bool = False
    # 코너·맞은편으로 **만들어졌을 때만** True. 나중에 입구와 병합돼 kind 가 바뀌어도
    # 이 값은 안 바뀐다 — 원래 코너였던 자리를 벽선에서 빼면 그 자리가 대각선으로
    # 이어져 버린다.
    is_wall_vertex: bool = False


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

# ---------------------------------------------------------------------------
# 벽선 정리
# ---------------------------------------------------------------------------
def _merge_near_collinear(loop: list[Point]) -> list[Point]:
    """거의 일직선인 세 점에서 가운데를 지운다.

    손으로 칠한 마스크는 곧은 벽도 미세하게 삐뚤어서, 단순화 뒤에도 한 벽이 여러
    조각으로 남는다. 그대로 두면 벽선이 꼭짓점마다 살짝씩 꺾여 보인다.

    **수평·수직으로 강제하지는 않는다.** 계단처럼 실제로 대각선인 벽을 직각으로
    펴면 지도 밖으로 삐져나간다(원본 주석에 실제로 겪은 일로 적혀 있다).
    """
    if len(loop) < 3:
        return loop
    result = list(loop)
    changed = True
    while changed and len(result) > 2:
        changed = False
        for i in range(len(result)):
            a = result[(i - 1 + len(result)) % len(result)]
            b = result[i]
            c = result[(i + 1) % len(result)]
            if _perpendicular_distance(b, a, c) <= COLLINEAR_MERGE_TOLERANCE_PX:
                result.pop(i)
                changed = True
                break
    return result


def _snap_facing_to_wall(point: Point, simplified: list[Point], fixed_axis: int) -> Point:
    """맞은편 지점을 벽선 위로 당긴다. **건너기 축은 건드리지 않는다.**

    맞은편은 원시 픽셀에서 찾은 값이라 정리된 벽선과 미세하게 어긋난다. 그대로
    두면 그 점에서만 벽선이 꺾여 보인다. 다만 캐스팅한 축(origin 과 반드시 같아야
    하는 축)을 건드리면 건너기가 사선이 되므로, 반대 축만 선형보간으로 맞춘다.
    """
    nearest = _nearest_point_on_loop(simplified, point)
    if nearest.distance > MERGE_RADIUS_PX:
        return point
    p1 = simplified[nearest.segment_index]
    p2 = simplified[(nearest.segment_index + 1) % len(simplified)]
    span = p2[fixed_axis] - p1[fixed_axis]
    if span == 0:
        return point
    t = (point[fixed_axis] - p1[fixed_axis]) / span
    free_axis = 1 - fixed_axis
    snapped = [0.0, 0.0]
    snapped[fixed_axis] = point[fixed_axis]
    snapped[free_axis] = p1[free_axis] + t * (p2[free_axis] - p1[free_axis])
    return (snapped[0], snapped[1])


# ---------------------------------------------------------------------------
# 횡단 후보 찾기 — 상하좌우로만 쏜다
# ---------------------------------------------------------------------------
def _is_walkable(mask, w: int, h: int, p: Point) -> bool:
    cx = math.floor(p[0])
    cy = math.floor(p[1])
    if cx < 0 or cy < 0 or cx >= w or cy >= h:
        return False
    return mask[cy * w + cx] == 1


def _cast_to_wall(mask, w: int, h: int, start: Point, d: Point) -> Point | None:
    """start 에서 d 방향으로 1px 씩 나아가 통행영역을 벗어나기 직전 지점."""
    max_steps = max(w, h)
    x, y = start
    last: Point | None = None
    for _ in range(max_steps):
        x += d[0]
        y += d[1]
        cx = math.floor(x)
        cy = math.floor(y)
        if cx < 0 or cy < 0 or cx >= w or cy >= h:
            return last
        if mask[cy * w + cx] == 0:
            return last
        last = (x, y)
    return None


def _has_wall_within(mask, w: int, h: int, origin: Point, d: Point, max_px: float) -> bool:
    x, y = origin
    for _ in range(1, int(max_px) + 1):
        x += d[0]
        y += d[1]
        if not _is_walkable(mask, w, h, (x, y)):
            return True
    return False


def _hugs_wall_along_path(mask, w: int, h: int, origin: Point, d: Point,
                          hit: Point, min_clearance_px: float) -> bool:
    """가는 길 내내 한쪽 옆에 벽이 붙어 있는가 — "그냥 벽 타면 닿는 곳"인지 본다.

    **출발점 하나만 보면 안 된다.** 코너가 넓은 홀과 좁은 복도의 경계에 있으면
    코너 자체는 옆이 넓어 보이지만 한 걸음만 들어가도 좁아진다. 그러면 복도 전체
    길이를 "횡단"이라고 안내하게 된다(원본이 실제로 겪은 버그).
    """
    length = round(math.hypot(hit[0] - origin[0], hit[1] - origin[1]))
    perpendicular = [(0, 1), (0, -1)] if d[0] != 0 else [(1, 0), (-1, 0)]
    total = length + 1
    for perp in perpendicular:
        narrow = 0
        for step in range(length + 1):
            sample = (origin[0] + d[0] * step, origin[1] + d[1] * step)
            if _has_wall_within(mask, w, h, sample, perp, min_clearance_px):
                narrow += 1
        if narrow / total >= HUG_RATIO_THRESHOLD:
            return True
    return False


def _cardinal_facing_points(mask, w: int, h: int, point: Point,
                            crossing_max_px: float,
                            min_clearance_px: float | None) -> list[Point]:
    """상하좌우로 쏴서 건너갈 만한 맞은편 지점들.

    방향마다 따로 보므로 한 점에서 여러 개가 나올 수 있다(교차로 등).
    """
    results: list[Point] = []
    for d in CARDINAL_DIRECTIONS:
        hit = _cast_to_wall(mask, w, h, point, d)
        if hit is None:
            continue
        distance = math.hypot(hit[0] - point[0], hit[1] - point[1])
        if distance > crossing_max_px:
            continue
        if distance < MIN_CROSSING_LENGTH_PX:
            continue
        if (min_clearance_px is not None
                and _hugs_wall_along_path(mask, w, h, point, d, hit, min_clearance_px)):
            continue
        results.append(hit)
    return results


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
                        crossing_max_px: float = DEFAULT_CROSSING_MAX_PX,
                        min_clearance_px: float = DEFAULT_MIN_CLEARANCE_PX) -> PathGraph:
    """마스크 → 노드·연결. `generatePathNodes` 와 같은 값을 내야 한다."""
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
        edges.append(PathEdge(a=a.id, b=b.id, type=type_,
                              directed=(type_ == "cross")))

    labels, counts = _label_components(mask, w, h)

    components: list[tuple[list[Point], list[Point]]] = []
    for component_id, count in enumerate(counts):
        if count < MIN_COMPONENT_PIXELS:
            continue
        raw_loop = _trace_boundary(mask, w, h, labels, component_id)
        simplified = _merge_near_collinear(_simplify(raw_loop, SIMPLIFY_EPSILON_PX))
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
        loop_length = len(simplified)
        entries: list[_LoopEntry] = []
        for index, (x, y) in enumerate(simplified):
            px, py = simplified[(index - 1 + loop_length) % loop_length]
            nx, ny = simplified[(index + 1) % loop_length]
            concave = (x - px) * (ny - y) - (y - py) * (nx - x) < 0
            entries.append(_LoopEntry(point=(x, y), kind="corner", segment_index=index,
                                      t=0.0, distance_to_loop=0.0, concave=concave,
                                      is_wall_vertex=True))

        def find_or_insert(point: Point, kind: str, pair_kind: str | None = None,
                           exclude: list[_LoopEntry] | None = None,
                           axis_lock: tuple[int, float] | None = None,
                           block_merge_into: tuple[str, ...] = (),
                           force_wall_vertex: bool = False) -> _LoopEntry:
            """이미 있는 점에 합치거나 새로 넣는다.

            `axis_lock` — 캐스팅 축이 다른 점에는 아무리 가까워도 합치지 않는다.
            안 그러면 다른 방향에서 찾은 맞은편에 흡수돼 건너기가 사선이 된다.

            `block_merge_into` — 이 종류에는 합치지 않는다. 서로 다른 목적지가
            우연히 6px 안에 있어도 하나로 합쳐지면 하나가 통째로 사라진다.
            다만 넣으려는 것이 맞은편이고 기존 것이 이미 벽 위 꼭짓점이면 예외로
            합친다 — 그건 사실상 코너와 같은 "진짜 벽 위 지점"이다.
            """
            exclude_list = exclude or []
            existing = None
            for entry in entries:
                if entry in exclude_list:
                    continue
                if (entry.kind in block_merge_into
                        and not (kind == "facing" and entry.is_wall_vertex)):
                    continue
                if math.hypot(entry.point[0] - point[0],
                              entry.point[1] - point[1]) > MERGE_RADIUS_PX:
                    continue
                if axis_lock is not None and entry.point[axis_lock[0]] != axis_lock[1]:
                    continue
                existing = entry
                break
            if existing is not None:
                if _kind_priority(kind) > _kind_priority(existing.kind):
                    existing.kind = kind
                    existing.pair_kind = pair_kind
                elif (kind == "facing" and existing.kind == "facing"
                      and pair_kind is None):
                    # 코너의 맞은편이 어떤 입구의 맞은편과 겹쳤다. 이제 특정 입구
                    # 하나의 것이 아니므로 색 표시를 지운다.
                    existing.pair_kind = None
                return existing
            nearest = _nearest_point_on_loop(simplified, point)
            entry = _LoopEntry(point=point, kind=kind, pair_kind=pair_kind,
                               segment_index=nearest.segment_index, t=nearest.t,
                               distance_to_loop=nearest.distance,
                               is_wall_vertex=(kind == "facing" or force_wall_vertex))
            entries.append(entry)
            return entry

        pairs: list[tuple[_LoopEntry, _LoopEntry]] = []

        for entrance, snap in assigned[component_index]:
            # 입구 원래 자리가 통행영역이면 거기서 쏜다. 방 안쪽(마스크 밖)이면
            # 첫 걸음부터 실패하므로 경계에 스냅된 지점에서 쏜다.
            raw_origin = ((entrance.x, entrance.y)
                          if _is_walkable(mask, w, h, (entrance.x, entrance.y)) else snap)
            # 벽선에 충분히 가까우면 그 위로 당겨 꼭짓점으로 끼운다 — 직선이
            # 코너A—목적지—코너B 로 곧게 쪼개진다. 판정 거리는 MERGE_RADIUS_PX 가
            # 아니라 넉넉한 WALL_SPLICE_MAX_PX 다. 목적지는 사람이 손으로 찍는 거라
            # 벽 바로 위를 정확히 누르길 기대할 수 없다.
            wall_projection = _nearest_point_on_loop(simplified, raw_origin)
            on_wall_line = wall_projection.distance <= WALL_SPLICE_MAX_PX
            proposed_origin = wall_projection.point if on_wall_line else raw_origin
            entrance_entry = find_or_insert(
                proposed_origin, entrance.kind, None, None, None,
                ("connector", "landmark", "facing"), on_wall_line)
            # 맞은편 탐색은 **노드의 최종 위치**에서 한다. 스냅하려던 좌표가 근처
            # 코너와 병합되면 노드는 코너 자리를 쓰는데 캐스팅만 옛 좌표에서 하면
            # 건너기가 사선이 된다.
            facing_origin = entrance_entry.point
            facing_points = _cardinal_facing_points(mask, w, h, facing_origin,
                                                    crossing_max_px, min_clearance_px)
            facing_so_far: list[_LoopEntry] = []
            for facing_raw_cast in facing_points:
                fixed_axis = 0 if facing_raw_cast[0] == facing_origin[0] else 1
                facing_raw = _snap_facing_to_wall(facing_raw_cast, simplified, fixed_axis)
                # 벽선에 맞추다 원점과 같은 자리로 당겨지면 길이 0짜리 건너기가
                # 생겨 "자기 자신을 가리키는" 것처럼 보인다.
                if math.hypot(facing_raw[0] - facing_origin[0],
                              facing_raw[1] - facing_origin[1]) < MIN_CROSSING_LENGTH_PX:
                    continue
                axis_lock = (fixed_axis, facing_raw[fixed_axis])
                facing_entry = find_or_insert(
                    facing_raw, "facing", entrance.kind,
                    [entrance_entry, *facing_so_far], axis_lock,
                    ("connector", "landmark"))
                facing_so_far.append(facing_entry)
                pairs.append((entrance_entry, facing_entry))

        # 벽 끝(concave 코너)에서만 건너기를 만든다.
        #
        # 벽이 계속 이어지는 볼록 코너는 그냥 복도가 꺾이는 자리라, 거기서 건너라고
        # 하면 벽을 만지는 중인데 갑자기 손을 떼라는 안내가 된다.
        for corner_entry in [e for e in entries if e.kind == "corner" and e.concave]:
            facing_points = _cardinal_facing_points(mask, w, h, corner_entry.point,
                                                    crossing_max_px, min_clearance_px)
            if not facing_points:
                continue
            distances = [math.hypot(p[0] - corner_entry.point[0],
                                    p[1] - corner_entry.point[1]) for p in facing_points]
            min_distance = min(distances)
            # 가장 짧은 것 하나만 남기면 교차로처럼 둘 다 필요한 경우가 사라지고,
            # 전부 남기면 요철 벽에서 화살표가 무더기로 몰린다. 그 중간을 잡는다.
            kept = [p for p, d in zip(facing_points, distances)
                    if d <= min_distance * SIMILAR_LENGTH_RATIO]
            facing_so_far = []
            for facing_raw_cast in kept:
                fixed_axis = 0 if facing_raw_cast[0] == corner_entry.point[0] else 1
                facing_raw = _snap_facing_to_wall(facing_raw_cast, simplified, fixed_axis)
                if math.hypot(facing_raw[0] - corner_entry.point[0],
                              facing_raw[1] - corner_entry.point[1]) < MIN_CROSSING_LENGTH_PX:
                    continue
                axis_lock = (fixed_axis, facing_raw[fixed_axis])
                facing_entry = find_or_insert(
                    facing_raw, "facing", None,
                    [corner_entry, *facing_so_far], axis_lock,
                    ("connector", "landmark"))
                facing_so_far.append(facing_entry)
                pairs.append((corner_entry, facing_entry))

        entries.sort(key=lambda e: (e.segment_index, e.t))

        entry_to_node: dict[int, PathNode] = {}
        component_nodes: list[PathNode] = []
        for entry in entries:
            x, y = entry.point
            node = PathNode(
                id=f"N{len(nodes) + len(component_nodes) + 1:02d}",
                x=x, y=y, type=entry.kind,
                concave=(entry.kind == "corner" and bool(entry.concave)),
                pair_kind=entry.pair_kind if entry.kind == "facing" else None,
            )
            entry_to_node[id(entry)] = node
            component_nodes.append(node)
        nodes.extend(component_nodes)

        # 벽선은 **실제로 경계 위에 있는 점들끼리만** 순서대로 잇는다.
        #
        # 방 안쪽 깊숙이 찍힌 목적지가 정렬 순서상 중간에 끼면, 그 점과 이웃을 잇는
        # 선이 방을 대각선으로 가로지른다. 그런 점은 메인 루프에서 빼고 가장 가까운
        # 경계 점 하나에만 짧게 잇는다.
        def on_main_loop(entry: _LoopEntry) -> bool:
            return entry.is_wall_vertex and entry.distance_to_loop <= MERGE_RADIUS_PX

        on_loop_indices = [i for i, e in enumerate(entries) if on_main_loop(e)]
        for i in range(len(on_loop_indices)):
            a = component_nodes[on_loop_indices[i]]
            b = component_nodes[on_loop_indices[(i + 1) % len(on_loop_indices)]]
            add_edge(a, b, "wall")

        for index, entry in enumerate(entries):
            if on_main_loop(entry):
                continue
            point = entry.point
            nearest_index = -1
            nearest_distance = math.inf
            for candidate_index in on_loop_indices:
                candidate = entries[candidate_index].point
                d = math.hypot(candidate[0] - point[0], candidate[1] - point[1])
                if d < nearest_distance:
                    nearest_distance = d
                    nearest_index = candidate_index
            # 최단 후보가 사선이면, 비슷한 거리 안에 직선으로 놓인 후보를 우선한다.
            # 안내선이 괜히 사선으로 붕 떠 보이는 것을 줄인다.
            if nearest_index != -1:
                nearest_point = entries[nearest_index].point
                if point[0] != nearest_point[0] and point[1] != nearest_point[1]:
                    straight_index = -1
                    straight_distance = math.inf
                    for candidate_index in on_loop_indices:
                        candidate = entries[candidate_index].point
                        if candidate[0] != point[0] and candidate[1] != point[1]:
                            continue
                        d = math.hypot(candidate[0] - point[0], candidate[1] - point[1])
                        if d < straight_distance:
                            straight_distance = d
                            straight_index = candidate_index
                    if (straight_index != -1
                            and straight_distance <= nearest_distance * SIMILAR_LENGTH_RATIO):
                        nearest_index = straight_index
            if nearest_index != -1:
                add_edge(component_nodes[index], component_nodes[nearest_index], "wall")

        for entry_a, entry_b in pairs:
            a = entry_to_node[id(entry_a)]
            b = entry_to_node[id(entry_b)]
            if a.id == b.id:
                continue
            # 건너기는 반드시 좌우 또는 상하로만 — 사선 금지. 병합 과정에서 축이
            # 어긋난 노드에 흡수됐을 수 있어 마지막으로 한 번 더 거른다.
            if a.x != b.x and a.y != b.y:
                continue
            if math.hypot(a.x - b.x, a.y - b.y) <= crossing_max_px:
                add_edge(a, b, "cross")

    return PathGraph(nodes=nodes, edges=edges)
