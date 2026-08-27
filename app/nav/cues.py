"""경로 노드에서 안내 사건을 뽑고, 그것을 비콘에 배정한다.

── 트리거와 내용은 다른 것이다 ────────────────────────────────────

무엇을 말할지는 **경로 노드**가 정한다. 코너가 어디인지, 벽이 어디서 끊기는지는
노드 기하가 알고 있다. 언제 말할지는 **비콘**이 정한다. 폰이 감지할 수 있는 것은
비콘뿐이기 때문이다.

그래서 사건을 먼저 뽑고(`extract`), 그것을 비콘에 얹는다(`assign_*`).

── 배정 방식이 두 가지다 ──────────────────────────────────────────

    거리   사건의 누적거리보다 앞선 마지막 비콘. 여유(lead_m)를 두고 고른다
    소유   노드에서 **가장 가까운 비콘**을 주인으로 보고, 그 **한 칸 앞** 에서 말한다
    절충   한 칸 앞을 쓰되, 그것이 MAX_LEAD_M 보다 멀면 거리 방식으로 되돌린다

본질은 같다 — 둘 다 "사건보다 앞선 비콘에서 미리 말한다". 다른 것은 **얼마나
앞설지를 무엇으로 재느냐**다. 거리는 미터로, 소유는 칸수로 잰다.

    비콘 간격이 좁으면(2m)  2m 앞 = 한 칸 앞      → 같은 답
    비콘 간격이 넓으면(6m)  2m 앞 = 직전 칸       → 거리는 여유를 못 채우고
                           한 칸 앞 = 6m 앞      → 소유는 너무 일찍 말한다

어느 쪽이 나은지는 실제 배치에서 봐야 알 수 있어서 둘 다 남기고 `/monitor` 에서
나란히 비교한다.

── 왜 미리 말하는가 ───────────────────────────────────────────────

코너에 있는 비콘에서 "꺾으세요"라고 하면 **이미 코너를 지난 뒤**다. 게다가 그
비콘의 판정이 늦어지면 아예 말할 기회를 놓친다. 그래서 한 칸 앞에서 말한다.

횡단은 더 앞당긴다. 코너는 늦어도 벽을 짚고 있어 손으로 느끼지만, 횡단은 벽에서
손을 떼는 일이라 안내를 놓치면 사용자가 벽 끝에서 멈춰 선다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from app.nav.map_source import BeaconInfo, Graph, Node
from app.nav.route_engine import SAMPLE_STEP_M, turn_at

# 코너보다 횡단을 더 앞당긴다(m). 손을 떼기 전에 준비할 시간이 필요하다.
LEAD_TURN_M = 2.0
LEAD_CROSS_M = 4.0

# 절충 방식에서 "한 칸 앞"이 이보다 멀면 너무 이른 것으로 본다.
#
# 실측 4층에서 한 칸 앞이 15m 되는 자리가 있었다. 15m 앞에서 "곧 꺾으세요"를 들으면
# 사용자는 그 사이 다른 코너를 여럿 지나고, 정작 그 코너에 닿았을 때는 잊는다.
# 0.7m/s 로 걸으면 10m 는 14초다 — 예고로 쓸 수 있는 한계쯤이다.
MAX_LEAD_M = 10.0

# 코너 없이 이만큼 이어지면 "계속 직진하세요"를 넣는다.
LONG_STRAIGHT_M = 12.0

# 이 각도 미만은 직진으로 본다.
TURN_MIN_DEG = 30.0


@dataclass
class Cue:
    kind: str                 # turn | crossEnter | crossExit | straight | arrive
    node_id: str
    dist_m: float             # 출발점에서 이 사건까지 경로를 따라 잰 거리
    direction: str | None = None      # left | right
    template: int = 0                 # 안내 템플릿 표의 번호
    text: str = ""

    @property
    def lead_m(self) -> float:
        return LEAD_CROSS_M if self.kind.startswith("cross") else LEAD_TURN_M


@dataclass
class StepInfo:
    """비콘 하나 — `BeaconStep` 에 배정 결과를 얹은 것."""
    seq: int
    beacon_id: str
    dist_m: float
    cues_by_distance: list[Cue] = field(default_factory=list)
    cues_by_owner: list[Cue] = field(default_factory=list)
    cues_by_hybrid: list[Cue] = field(default_factory=list)


@dataclass
class RouteCues:
    steps: list[StepInfo]
    cues: list[Cue]
    # 어느 방식으로도 비콘을 못 찾은 사건 — 그대로 두면 조용히 사라지므로 드러낸다
    orphan_distance: list[Cue] = field(default_factory=list)
    orphan_owner: list[Cue] = field(default_factory=list)
    orphan_hybrid: list[Cue] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1) 노드에서 사건 뽑기
# ---------------------------------------------------------------------------
def extract(graph: Graph, node_ids: list[str], meters_per_px: float,
            cross_edges: set[tuple[str, str]], destination: str = "목적지") -> list[Cue]:
    """경로 노드를 따라가며 안내할 사건을 순서대로 뽑는다.

    **비콘은 보지 않는다.** 여기서 나오는 것은 "경로의 몇 m 지점에서 무슨 일이
    일어나는가"뿐이고, 그것을 누가 알려줄지는 배정 단계가 정한다.
    """
    nodes = [graph.node(n) for n in node_ids]
    pts: list[Node] = [n for n in nodes if n is not None]
    if len(pts) < 2:
        return []

    # 각 노드까지의 누적거리
    dist = [0.0]
    for a, b in zip(pts, pts[1:]):
        dist.append(dist[-1] + math.hypot(b.x - a.x, b.y - a.y) * meters_per_px)

    cues: list[Cue] = []
    last_event_m = 0.0
    # 횡단 도달 안내가 이미 회전을 말한 노드. 여기서 또 "꺾으세요"를 내면
    # 같은 코너를 두 번 말하게 된다 — 실측 경로에서 실제로 그랬다.
    covered: set[str] = set()

    for i in range(len(pts)):
        node = pts[i]
        here = dist[i]

        # 횡단 — 엣지의 양 끝이 각각 사건이다
        if i + 1 < len(pts):
            pair = (pts[i].id, pts[i + 1].id)
            if pair in cross_edges or (pair[1], pair[0]) in cross_edges:
                cues.append(Cue("crossEnter", node.id, here, template=6,
                                text="벽이 끊깁니다. 손을 떼고 직진하세요."))
                # 건너편에서 꺾는지 — 다음 노드에서의 회전을 미리 실어준다
                turn = None
                if i + 2 < len(pts):
                    turn = turn_at(pts[i], pts[i + 1], pts[i + 2], TURN_MIN_DEG)
                side = {"left": "왼쪽", "right": "오른쪽"}.get(turn or "")
                # 진입과 도달이 서로 다른 비콘에 배정될 수 있다. 그때 도달 안내만
                # 들으면 "벽에 도달하면"이 무슨 말인지 알 수 없으므로, 도달 문장도
                # 벽이 끊긴다는 사실을 스스로 담는다.
                cues.append(Cue("crossExit", pts[i + 1].id, dist[i + 1], direction=turn,
                                template=7,
                                text=(f"벽이 끊깁니다. 벽에 도달하면 {side}으로 꺾으세요."
                                      if side else
                                      "벽이 끊깁니다. 벽에 도달하면 다시 벽을 짚으세요.")))
                covered.add(pts[i + 1].id)
                last_event_m = dist[i + 1]
                continue

        # 회전 — 가운데 노드에서만 각도가 정의된다
        if 0 < i < len(pts) - 1 and node.id not in covered:
            turn = turn_at(pts[i - 1], node, pts[i + 1], TURN_MIN_DEG)
            if turn is not None:
                side = "왼쪽" if turn == "left" else "오른쪽"
                cues.append(Cue("turn", node.id, here, direction=turn, template=4,
                                text=f"벽을 따라 {side}으로 꺾으세요."))
                last_event_m = here
                continue

        # 장시간 직진 — 아무 사건 없이 오래 이어질 때만
        if here - last_event_m >= LONG_STRAIGHT_M:
            cues.append(Cue("straight", node.id, here, template=3,
                            text="계속 직진하세요."))
            last_event_m = here

    cues.append(Cue("arrive", pts[-1].id, dist[-1], template=12,
                    text=f"{destination}입니다."))

    # 같은 지점에 겹친 것을 순서대로
    cues.sort(key=lambda c: c.dist_m)
    return cues


# ---------------------------------------------------------------------------
# 2) 배정 — 세 방식
# ---------------------------------------------------------------------------
def assign_by_distance(steps: list[StepInfo], cues: list[Cue]) -> list[Cue]:
    """누적거리로 고른다. 사건보다 `lead_m` 이상 앞선 마지막 비콘.

    여유를 채우는 비콘이 없으면 그냥 사건보다 앞선 마지막 비콘에 붙인다 —
    조금 늦더라도 안 하는 것보다는 낫다.
    """
    orphans: list[Cue] = []
    for cue in cues:
        want = cue.dist_m - cue.lead_m
        pick = None
        for st in steps:
            if st.dist_m <= want:
                pick = st
        if pick is None:
            for st in steps:
                if st.dist_m < cue.dist_m:
                    pick = st
        if pick is None:
            orphans.append(cue)
            continue
        pick.cues_by_distance.append(cue)
    return orphans


def walk_owners(graph: Graph, node_ids: list[str], beacons: list[BeaconInfo],
                radius_m: float, meters_per_px: float) -> tuple[list[StepInfo], dict[str, int]]:
    """경로를 한 번 훑으며 비콘 순서와 "노드 → 그때 잡히던 비콘"을 같이 만든다.

    `to_beacon_sequence` 와 같은 규칙으로 돌지만 두 가지를 더 남긴다.

        dist_m   그 비콘이 처음 잡힌 지점의 누적거리
        owner    각 노드를 지날 때 잡히던 비콘의 순번

    **비콘의 물리적 위치가 아니라 "그 비콘이 잡히기 시작한 지점"** 이다. 비콘은
    벽에 붙어 있어 경로선 위에 없으므로, 위치를 그대로 쓰면 순서가 어긋난다.
    비콘이 하나도 안 잡히는 구간에서는 직전 비콘을 그대로 물고 간다 — 사용자에게는
    그 구간에서도 여전히 그 비콘이 제일 세게 잡히므로 실제와 맞는다.
    """
    from app.nav.route_engine import _walk

    steps: list[StepInfo] = []
    owner: dict[str, int] = {}
    travelled = 0.0
    step_px = SAMPLE_STEP_M / meters_per_px if meters_per_px > 0 else 1.0
    prev_xy: tuple[float, float] | None = None

    for (x, y), nid in _walk(graph, node_ids, meters_per_px):
        if prev_xy is not None:
            travelled += math.hypot(x - prev_xy[0], y - prev_xy[1]) * meters_per_px
        prev_xy = (x, y)

        best, best_d = None, math.inf
        for b in beacons:
            d = math.hypot(x - b.x, y - b.y) * meters_per_px
            if d <= radius_m and d < best_d:
                best, best_d = b, d
        if best is not None and not (steps and steps[-1].beacon_id == best.id):
            steps.append(StepInfo(seq=len(steps) + 1, beacon_id=best.id,
                                  dist_m=round(travelled, 2)))
        if steps and nid not in owner:
            owner[nid] = len(steps) - 1

    _ = step_px  # 간격은 _walk 이 이미 쓴다. 여기서는 거리 누적만 한다.
    return steps, owner


def nearest_owners(graph: Graph, node_ids: list[str], steps: list[StepInfo],
                   beacons: list[BeaconInfo], meters_per_px: float) -> dict[str, int]:
    """노드 → **가장 가까운 비콘**의 순번.

    경로노드 1번은 B1 옆이니 B1 소유, 2·3번은 B2 옆이니 B2 소유, 하는 식.

    후보는 **경로에 오른 비콘뿐**이다. 경로 밖 비콘이 아무리 가까워도 그것이
    잡히는 순간을 추적기가 안 보므로 안내를 걸 수 없다.

    ── 반경으로 자르지 않는다 ────────────────────────────────────

    처음에는 반경(3m) 안에서만 주인을 찾았다. 그랬더니 **2.9m 는 살고 3.2m 는
    안내가 통째로 사라졌다.** 0.3m 차이로 회전 안내가 없어지는 셈이다.

    반경은 "경로에 어느 비콘을 세울지" 고를 때 쓰는 값이고(`to_beacon_sequence`),
    이미 세워진 비콘에 노드를 나눠 줄 때는 쓸 데가 없다. 노드는 반드시 경로 위에
    있고 비콘 중 하나는 어쨌든 가장 가까우므로, 멀더라도 그게 맞는 주인이다.
    """
    by_id = {b.id: b for b in beacons}
    route = [(i, by_id[s.beacon_id]) for i, s in enumerate(steps) if s.beacon_id in by_id]

    # 경로에 같은 비콘이 두 번 이상 나오는가.
    #
    # ㄷ자로 들어갔다 나오는 복도에서는 같은 비콘 옆을 두 번 지난다.
    # `to_beacon_sequence` 는 떨어진 중복을 접지 않는다(왕복 경로에서는 그게 맞다).
    # 그러면 좌표만으로는 **어느 쪽 칸인지 가릴 수가 없다** — 같은 비콘이니 거리가
    # 똑같이 나온다. 그때 앞쪽 칸을 집으면 한참 뒤에 있는 회전을 한참 앞 비콘이
    # 말하게 된다(실측 4층 B25→412: 22m 회전을 6.1m 지점 비콘이 15.9m 앞서 안내).
    repeated = len({b.id for _, b in route}) < len(route)

    owner: dict[str, int] = {}
    floor_index = 0          # 여기보다 앞으로는 되돌아가지 않는다
    for nid in node_ids:
        node = graph.node(nid)
        if node is None:
            continue
        best_i, best_d = None, math.inf
        for i, b in route:
            # **노드 순서를 건너뛰지 않게 한다.** 앞 노드가 쓴 칸보다 뒤로는 못 간다.
            #
            # 중복이 없으면 이 제약을 걸지 않는다 — 순서가 꼬일 일이 없는데 괜히
            # 걸면 정상 배정까지 틀어진다.
            if repeated and i < floor_index:
                continue
            d = math.hypot(node.x - b.x, node.y - b.y) * meters_per_px
            if d < best_d:
                best_i, best_d = i, d
        if best_i is not None:
            owner[nid] = best_i
            floor_index = best_i
    return owner


def assign_by_owner(steps: list[StepInfo], cues: list[Cue],
                    owner: dict[str, int]) -> list[Cue]:
    """그 노드를 지날 때 잡히던 비콘을 찾아, **한 칸 앞** 비콘에서 말한다.

    `owner` 는 노드 id → 비콘 순번(steps 의 인덱스). 경로를 훑으며 만든 것이라
    직선거리로 고를 때 생기는 순서 뒤바뀜이 없다(ㄷ자 복도에서 반대편 비콘이
    더 가까운 경우 등).
    """
    orphans: list[Cue] = []
    for cue in cues:
        # 도착만은 앞당기지 않는다.
        #
        # 다른 안내는 "곧 무엇을 하라"는 예고라 미리 말해야 하지만, 도착은
        # **지금 여기가 그곳이다**라는 사실이라 앞당기면 거짓말이 된다.
        # 한 칸 앞에 두면 실측 4층에서 평균 6m, 최악 15m 전에 "계단1입니다"가
        # 나갔다 — 사용자는 엉뚱한 문 앞에 선다.
        if cue.kind == "arrive":
            if steps:
                steps[-1].cues_by_owner.append(cue)
            else:
                orphans.append(cue)
            continue

        idx = owner.get(cue.node_id)
        if idx is None:
            orphans.append(cue)
            continue
        target = idx - 1
        if target < 0:
            target = 0      # 첫 비콘이면 출발 안내에 얹는 수밖에 없다
        steps[target].cues_by_owner.append(cue)
    return orphans


def assign_by_hybrid(steps: list[StepInfo], cues: list[Cue],
                     owner: dict[str, int]) -> list[Cue]:
    """절충 — **한 칸 앞을 기본으로 하되, 그것이 너무 멀면 거리로 되돌린다.**

    두 방식의 실패가 서로 반대라서 하나씩 막을 수 있다.

        소유   한 칸은 반드시 확보되지만, 그 한 칸이 15m 일 수도 있다(너무 이름)
        거리   여유가 일정하지만, 비콘 간격이 넓으면 여유를 못 채워 직전 칸에 붙는다(너무 늦음)

    그래서 한 칸 앞을 먼저 잡고, 그 비콘에서 사건까지가 `MAX_LEAD_M` 을 넘으면
    거리 방식으로 다시 고른다. 비콘 간격이 좁은 데서는 소유와 같고, 넓은 데서만
    거리 쪽으로 물러난다.

    도착은 `assign_by_owner` 와 같이 마지막 비콘에 고정한다 — 앞당기면 거짓말이 된다.
    """
    orphans: list[Cue] = []
    for cue in cues:
        if cue.kind == "arrive":
            if steps:
                steps[-1].cues_by_hybrid.append(cue)
            else:
                orphans.append(cue)
            continue

        idx = owner.get(cue.node_id)
        pick = None
        if idx is not None:
            target = max(0, idx - 1)
            if cue.dist_m - steps[target].dist_m <= MAX_LEAD_M:
                pick = steps[target]

        if pick is None:
            # 너무 이르거나 주인을 못 찾았다 — 거리로 고른다
            want = cue.dist_m - cue.lead_m
            for st in steps:
                if st.dist_m <= want:
                    pick = st
            if pick is None:
                for st in steps:
                    if st.dist_m < cue.dist_m:
                        pick = st

        if pick is None:
            orphans.append(cue)
            continue
        pick.cues_by_hybrid.append(cue)
    return orphans


# ---------------------------------------------------------------------------
# 3) 얼마나 뒤인지 말로 붙이기
# ---------------------------------------------------------------------------
def lead_phrase(lead_m: float) -> str:
    """안내하는 비콘에서 그 일이 벌어지는 지점까지의 거리를 말로.

    ── 왜 필요한가 ────────────────────────────────────────────────

    비콘은 벽에 붙어 있고 코너는 복도 어딘가에 있어서, **한 칸 앞에서 말하면
    그 거리가 2m 일 때도 15m 일 때도 있다.** 둘 다 "왼쪽으로 꺾으세요"라고만
    하면 사용자는 지금 손을 움직여야 하는지 한참 더 걸어야 하는지 알 수 없다.

    그래서 배정을 바꾸는 대신 "지금 할 일"과 "조금 뒤에 할 일"만 갈라 준다.

    ── 어떻게 끊었나 ─────────────────────────────────────────────

    0.7m/s 로 걷는 속도 기준이다.

        6m 미만    9초 안 — 벽을 짚고 걸으면 몇 걸음이라 "지금 할 일"에 가깝다
        6m 이상    "조금 뒤"

    처음에는 3m 부터 붙였는데 **거의 모든 안내에 수식이 달렸다.** 4m 짜리에도
    "조금 뒤"가 붙으니 정작 정말 먼 것과 구분이 안 됐다. 수식은 드물어야 뜻이 산다.

    "잠시 후"도 뺐다. "조금 뒤"와 어느 쪽이 더 먼지 사용자가 알 수 없어서 두 단계로
    나눌 값어치가 없다.

    ── 숫자는 말하지 않는다 ──────────────────────────────────────

    15m 이상이면 "약 20미터 뒤"처럼 숫자를 줬는데 **뺐다.**

    사용자는 걸으면서 미터를 셀 수 없다. 흰지팡이로 벽을 짚고 가는 사람에게
    "20미터"는 확인할 방법이 없는 숫자이고, 그 숫자가 맞는지 서버도 장담할 수
    없다 — 경로 길이는 노드를 잇는 직선의 합이라 실제 걷는 거리와 다르다.
    맞지도 않는 숫자를 정확한 척 말하면 그것에 맞춰 걷다가 지나친다.

    "조금 뒤" 하나면 "지금은 아니다"라는 뜻이 전달되고, 그 이상은 어차피 다음
    비콘에서 다시 말해준다.
    """
    if lead_m < 6.0:
        return ""
    return "조금 뒤 "


def finalize(step: StepInfo, cues: list[Cue]) -> list[Cue]:
    """한 비콘에 모인 안내를 접고, 각각에 거리 표현을 붙인다.

    **접은 뒤에 붙인다.** 먼저 붙이면 접힌 문장이 첫 회전의 거리만 물고 가서,
    합쳐진 나머지 회전의 거리가 사라진다.
    """
    out = collapse(cues)
    return [
        c if c.kind == "straight"
        # "계속 직진하세요"는 지점을 가리키는 말이 아니라 지금 상태를 확인해 주는
        # 말이다. "조금 뒤 계속 직진하세요"는 뜻이 안 통한다.
        else replace(c, text=lead_phrase(c.dist_m - step.dist_m) + c.text)
        for c in out
    ]


# ---------------------------------------------------------------------------
# 4) 한 비콘에 모인 것 다듬기
# ---------------------------------------------------------------------------
def merge_crossing(cues: list[Cue]) -> list[Cue]:
    """같은 비콘에서 말하게 된 횡단 진입·도달을 한 문장으로.

    진입과 도달은 원래 다른 비콘에 배정될 수 있어서 **각자 "벽이 끊깁니다"를
    담고 있다.** 도달 안내만 따로 들었을 때 "벽에 도달하면"이 무슨 말인지
    알 수 있어야 하기 때문이다.

    그런데 둘이 같은 비콘에 모이면 그 사실을 두 번 말하게 된다.

        벽이 끊깁니다. 손을 떼고 직진하세요.
        벽이 끊깁니다. 벽에 도달하면 왼쪽으로 꺾으세요.

    한 번 끊기는 벽을 두 번 끊긴다고 하는 셈이라 한 문장으로 잇는다.
    """
    out: list[Cue] = []
    i = 0
    while i < len(cues):
        cur = cues[i]
        nxt = cues[i + 1] if i + 1 < len(cues) else None
        if cur.kind == "crossEnter" and nxt is not None and nxt.kind == "crossExit":
            side = {"left": "왼쪽", "right": "오른쪽"}.get(nxt.direction or "")
            tail = (f"벽에 도달하면 {side}으로 꺾으세요." if side
                    else "벽에 도달하면 다시 벽을 짚으세요.")
            out.append(replace(cur, kind="cross", template=6,
                               text=f"벽이 끊깁니다. 손을 떼고 직진하다가, {tail}"))
            i += 2
            continue
        out.append(cur)
        i += 1
    return out


def collapse(cues: list[Cue]) -> list[Cue]:
    """한 비콘에 회전이 여러 개 모이면 하나로 접는다(템플릿 5).

    ── 왜 접나 ────────────────────────────────────────────────────

    "왼쪽으로 꺾으세요. 오른쪽으로 꺾으세요. 왼쪽으로 꺾으세요" 를 연달아 들으면
    **어느 것이 지금 할 일인지 알 수 없다.** 게다가 0.7m/s 로 걷는 사람에게 세
    문장은 너무 길어서, 다 듣기 전에 이미 첫 코너를 지난다.

    이런 자리는 대개 복도가 계단식으로 두어 번 꺾이는 곳이고, 벽을 짚고 있으면
    손이 알아서 따라간다. 그래서 방향을 하나씩 세는 대신 "계속 가세요" 하나로
    바꾼다 — 실제로 사용자가 해야 할 일이 그것뿐이다.

    회전만 접는다. 횡단은 손을 떼는 일이라 하나하나가 다른 동작이므로 그대로 둔다.
    """
    cues = merge_crossing(cues)

    turns = [c for c in cues if c.kind == "turn"]
    if len(turns) < 2:
        return cues

    first = turns[0]
    merged = Cue("turnMulti", first.node_id, first.dist_m, template=5,
                 text="벽을 따라 계속 가세요.")
    out: list[Cue] = []
    for c in cues:
        if c.kind != "turn":
            out.append(c)
        elif c is first:
            out.append(merged)
    return out


# ---------------------------------------------------------------------------
# 바깥에서 부르는 것
# ---------------------------------------------------------------------------
def build(graph: Graph, node_ids: list[str], beacons: list[BeaconInfo],
          radius_m: float, meters_per_px: float, destination: str = "목적지") -> RouteCues:
    """경로 하나에 대해 사건을 뽑고 두 방식으로 배정해 나란히 돌려준다."""
    cross = {(e.a, e.b) for e in graph.edges if getattr(e, "type", "") == "cross"}
    steps, _walk_owner = walk_owners(graph, node_ids, beacons, radius_m, meters_per_px)
    cues = extract(graph, node_ids, meters_per_px, cross, destination)
    owner = nearest_owners(graph, node_ids, steps, beacons, meters_per_px)
    result = RouteCues(
        steps=steps,
        cues=cues,
        orphan_distance=assign_by_distance(steps, cues),
        orphan_owner=assign_by_owner(steps, cues, owner),
        orphan_hybrid=assign_by_hybrid(steps, cues, owner),
    )
    # 배정이 끝난 뒤에 접고 거리 표현을 붙인다 — 어느 비콘에 몇 개가 모였는지,
    # 그 비콘에서 얼마나 떨어진 일인지는 배정해봐야 안다.
    for st in result.steps:
        st.cues_by_distance = finalize(st, st.cues_by_distance)
        st.cues_by_owner = finalize(st, st.cues_by_owner)
        st.cues_by_hybrid = finalize(st, st.cues_by_hybrid)
    return result
