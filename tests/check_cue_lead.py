"""안내를 몇 칸 앞 비콘에 얹는지(`lead_steps`)와 직진 보정을 확인한다.
DB 없이 도는 스모크 테스트.

    1 (기본)   한 칸 앞 비콘에서 말한다
    0          그 비콘에서 바로 말한다

도착만은 어느 쪽이든 마지막 비콘에 고정이다 — 앞당기면 거짓말이 되기 때문이다.

직진 보정은 비콘 셋이 일직선인 자리에서만 앞당김을 한 칸 줄인다. 일직선이면
판정이 두 비콘 중간에서 뒤집혀 한 칸 앞이 사실상 1.5칸 앞이 되기 때문이다.

    python tests/check_cue_lead.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.nav.cues import (  # noqa: E402
    CONNECTOR_NEAR_M, Cue, StepInfo, assign_by_owner, collapse,
    connector_at_start, extract, finalize, side_from_connector, shift_straight,
    straight_owners,
)
from app.nav.map_source import (  # noqa: E402
    BeaconInfo, Edge, Graph, LandmarkInfo, Node,
)

MPP = 0.05      # 1px = 0.05m — 100px 이 5m

fails = []
total = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global total
    total += 1
    print(f" {'✓' if ok else '✗'} {name:<44} {detail}")
    if not ok:
        fails.append(name)


def fixture():
    """비콘 3개(0m·10m·20m), 20m 지점에 회전, 30m 에 도착."""
    steps = [
        StepInfo(seq=1, beacon_id="B1", dist_m=0.0),
        StepInfo(seq=2, beacon_id="B2", dist_m=10.0),
        StepInfo(seq=3, beacon_id="B3", dist_m=20.0),
    ]
    cues = [
        Cue("turn", "N5", 20.0, direction="left", template=4, text="왼쪽으로 꺾으세요."),
        Cue("arrive", "N9", 30.0, template=12, text="410입니다."),
    ]
    owner = {"N5": 2, "N9": 2}      # 회전 노드의 주인은 B3(순번 2)
    return steps, cues, owner


def four_steps():
    return [StepInfo(seq=i + 1, beacon_id=f"B{i + 1}", dist_m=10.0 * i)
            for i in range(4)]


def line_beacons():
    """B1..B4 가 한 줄로. 사이가 10px 씩 벌어져 있다."""
    return [BeaconInfo(id=f"B{i + 1}", x=10.0 * i, y=0.0) for i in range(4)]


def corner_beacons():
    """B4 로 가면서 90도 꺾인다 — (0,0) (10,0) (20,0) (20,10).

    각을 재는 것은 셋의 **가운데** 비콘이다. (B1,B2,B3) 은 아직 직선이라 순번 2 는
    보정에 걸리고, (B2,B3,B4) 만 90도라 순번 3 이 빠진다.
    """
    return [BeaconInfo(id="B1", x=0.0, y=0.0), BeaconInfo(id="B2", x=10.0, y=0.0),
            BeaconInfo(id="B3", x=20.0, y=0.0), BeaconInfo(id="B4", x=20.0, y=10.0)]


def same_spot_beacons():
    """B2 와 B3 이 같은 자리 — 방향 벡터가 0 이라 각을 못 잰다."""
    return [BeaconInfo(id="B1", x=0.0, y=0.0), BeaconInfo(id="B2", x=10.0, y=0.0),
            BeaconInfo(id="B3", x=10.0, y=0.0), BeaconInfo(id="B4", x=20.0, y=0.0)]


def fixture4():
    """비콘 4개. 회전의 주인은 B4(순번 3), 도착도 B4."""
    steps = four_steps()
    cues = [
        Cue("turn", "N5", 30.0, direction="left", template=4, text="왼쪽으로 꺾으세요."),
        Cue("arrive", "N9", 40.0, template=12, text="410입니다."),
    ]
    return steps, cues, {"N5": 3, "N9": 3}


def graph_of(nodes, kinds=None):
    """노드를 순서대로 이은 그래프. `kinds` 로 간선 종류를 지정한다."""
    edges = [Edge(a=nodes[i].id, b=nodes[i + 1].id,
                  type=(kinds[i] if kinds else "corridor"), dist_m=5.0)
             for i in range(len(nodes) - 1)]
    return Graph(nodes=nodes, edges=edges), [n.id for n in nodes]


def corridor(n):
    """일자 복도. 노드 사이 100px = 5m 라 n=7 이면 30m."""
    return graph_of([Node(id=f"N{i}", x=i * 100.0, y=0.0) for i in range(n)])


def elbow():
    """15m 직진 → 코너 → 15m 직진."""
    return graph_of([Node(id="N0", x=0, y=0), Node(id="N1", x=300, y=0),
                     Node(id="N2", x=300, y=300)])


def cross_start():
    """첫 간선이 횡단이고, 건너편에서 오른쪽으로 꺾는다."""
    return graph_of([Node(id="N0", x=0, y=0), Node(id="N1", x=100, y=0),
                     Node(id="N2", x=100, y=200)], ["cross", "corridor"])


def cross_middle():
    """횡단이 경로 중간(N1→N2)에 있다."""
    return graph_of([Node(id="N0", x=0, y=0), Node(id="N1", x=100, y=0),
                     Node(id="N2", x=200, y=0), Node(id="N3", x=200, y=200)],
                    ["corridor", "cross", "corridor"])


def turn_then_cross():
    """회전을 한 번 한 뒤에 횡단이 온다. 그때는 이미 벽을 짚은 상태다."""
    return graph_of([Node(id="P0", x=0, y=0), Node(id="P1", x=200, y=0),
                     Node(id="P2", x=200, y=200), Node(id="P3", x=200, y=400),
                     Node(id="P4", x=400, y=400)],
                    ["corridor", "corridor", "cross", "corridor"])


def conn_fixture():
    """비콘 셋과 연결자들. B1 이 (0,0) 의 연결자와 가장 가깝다."""
    beacons = [BeaconInfo(id="B1", x=0, y=20), BeaconInfo(id="B2", x=100, y=20),
               BeaconInfo(id="B3", x=100, y=200)]
    elev = LandmarkInfo(id="C1", name="엘리베이터1", x=0, y=0,
                        type="elevator", is_connector=True)
    stair = LandmarkInfo(id="C2", name="계단1", x=0, y=0,
                         type="stairs", is_connector=True)
    # B1 의 최근접이긴 하지만 한참 멀다 — 잡히면 안 된다
    far = LandmarkInfo(id="C3", name="먼엘베", x=-5000, y=0,
                       type="elevator", is_connector=True)
    return beacons, elev, stair, far


def where(steps, kind):
    for st in steps:
        if any(c.kind == kind for c in st.cues_by_owner):
            return st.beacon_id
    return None


def main() -> int:
    print("── 한 칸 앞 (기본) ──")
    steps, cues, owner = fixture()
    orphans = assign_by_owner(steps, cues, owner, lead_steps=1)
    check(where(steps, "turn") == "B2", "회전이 주인의 한 칸 앞으로", where(steps, "turn"))
    check(where(steps, "arrive") == "B3", "  도착은 마지막 비콘 고정", where(steps, "arrive"))
    check(not orphans, "  미배정 없음", f"{len(orphans)}건")

    print("\n── 그 비콘에서 바로 ──")
    steps, cues, owner = fixture()
    orphans = assign_by_owner(steps, cues, owner, lead_steps=0)
    check(where(steps, "turn") == "B3", "회전이 주인 비콘 자신에게", where(steps, "turn"))
    check(where(steps, "arrive") == "B3", "  도착은 그대로 마지막", where(steps, "arrive"))
    check(not orphans, "  미배정 없음", f"{len(orphans)}건")

    print("\n── 기본값은 1 이다 ──")
    steps, cues, owner = fixture()
    assign_by_owner(steps, cues, owner)
    check(where(steps, "turn") == "B2", "인자를 안 주면 한 칸 앞", where(steps, "turn"))

    print("\n── 첫 비콘이 주인이면 앞이 없다 ──")
    steps, cues, owner = fixture()
    owner = {"N5": 0, "N9": 0}
    assign_by_owner(steps, cues, owner, lead_steps=1)
    check(where(steps, "turn") == "B1", "0번보다 앞은 없으니 자기 자신", where(steps, "turn"))

    print("\n── 음수는 1 칸으로 막는다 ──")
    steps, cues, owner = fixture()
    assign_by_owner(steps, cues, owner, lead_steps=-3)
    check(where(steps, "turn") == "B3", "0 으로 눌린다", where(steps, "turn"))

    # ── 직진 보정 ──────────────────────────────────────────────
    print("\n── 직진 판정 (straight_owners) ──")
    steps4 = four_steps()
    check(straight_owners(steps4, line_beacons()) == {2, 3},
          "일직선이면 2번부터 전부", str(sorted(straight_owners(steps4, line_beacons()))))
    check(straight_owners(steps4, corner_beacons()) == {2},
          "B4 에서 꺾이면 그 칸만 빠진다", str(sorted(straight_owners(steps4, corner_beacons()))))
    check(straight_owners(steps4[:2], line_beacons()) == set(),
          "비콘이 둘뿐이면 잴 것이 없다")
    check(straight_owners(steps4, same_spot_beacons()) == set(),
          "겹친 비콘은 방향이 없어 안 건다")
    check(straight_owners(steps4, line_beacons()[:2]) == set(),
          "위치를 모르는 비콘이 끼면 건드리지 않는다")

    print("\n── 보정이 걸린 칸은 한 칸 뒤로 ──")
    steps, cues, owner = fixture4()
    assign_by_owner(steps, cues, owner, lead_steps=1, straight={3})
    check(where(steps, "turn") == "B4", "직진이면 주인 자신에게", where(steps, "turn"))

    steps, cues, owner = fixture4()
    assign_by_owner(steps, cues, owner, lead_steps=1, straight=set())
    check(where(steps, "turn") == "B3", "코너면 그대로 한 칸 앞", where(steps, "turn"))

    print("\n── 줄이기만 하고 늘리지 않는다 ──")
    steps, cues, owner = fixture4()
    assign_by_owner(steps, cues, owner, lead_steps=0, straight={3})
    check(where(steps, "turn") == "B4", "0 에서 보정해도 음수로 안 간다", where(steps, "turn"))

    steps, cues, owner = fixture4()
    assign_by_owner(steps, cues, owner, lead_steps=2, straight={3})
    check(where(steps, "turn") == "B3", "2 칸 앞이면 1 칸 앞으로만", where(steps, "turn"))

    print("\n── 보정해도 도착은 마지막 고정 ──")
    steps, cues, owner = fixture4()
    assign_by_owner(steps, cues, owner, lead_steps=1, straight={2, 3})
    check(where(steps, "arrive") == "B4", "도착은 건드리지 않는다", where(steps, "arrive"))

    # ── 직진 안내를 구간 **시작**에서 낸다 ────────────────────────
    print("\n── 직진 안내 위치 ──")
    g, ids = corridor(7)                       # 사건 없는 30m
    cs = extract(g, ids, MPP, set(), "410호")
    sm = [round(c.dist_m, 1) for c in cs if c.kind == "straight"]
    check(sm == [0.0], "30m 복도면 0m 에서 (끝이 아니라)", str(sm))

    g, ids = elbow()                           # 15m → 코너 → 15m
    cs = extract(g, ids, MPP, set(), "410호")
    sm = [round(c.dist_m, 1) for c in cs if c.kind == "straight"]
    check(sm == [0.0, 15.0], "코너 앞뒤 두 구간 모두 시작에서", str(sm))
    order = [c.kind for c in cs if round(c.dist_m, 1) == 15.0]
    check(order == ["turn", "straight"], "같은 지점이면 회전을 먼저", str(order))

    g, ids = corridor(3)                       # 10m — 기준 미달
    cs = extract(g, ids, MPP, set(), "410호")
    check(not [c for c in cs if c.kind == "straight"], "12m 미만이면 안 낸다")

    print("\n── 겹친 직진 안내 (shift_straight) ──")

    def cue(kind):
        return Cue(kind, "N", 0.0, text=kind)

    rows = [[cue("turn"), cue("straight")], [], []]
    check(shift_straight(rows) == [] and [c.kind for c in rows[1]] == ["straight"],
          "다음 칸이 비면 옮긴다", str([[c.kind for c in r] for r in rows]))

    rows = [[cue("turn"), cue("straight")], [cue("turn")], []]
    check(len(shift_straight(rows)) == 1 and [c.kind for c in rows[1]] == ["turn"],
          "다음 칸도 차 있으면 버린다", str([[c.kind for c in r] for r in rows]))

    rows = [[cue("turn"), cue("straight")], [cue("straight")]]
    check(len(shift_straight(rows)) == 1 and len(rows[1]) == 1,
          "다음 칸에 직진이 있어도 버린다 — 두 번 말할 이유가 없다")

    rows = [[], [cue("turn"), cue("straight")]]
    check(len(shift_straight(rows)) == 1, "마지막 칸이면 버린다")

    rows = [[cue("straight")], [cue("turn")]]
    check(shift_straight(rows) == [] and [c.kind for c in rows[0]] == ["straight"],
          "혼자 있으면 그대로 둔다")

    print("\n── 연속 코너 문구 ──")
    st = StepInfo(seq=1, beacon_id="B1", dist_m=0.0)
    two = [Cue("turn", "N1", 5.0, direction="left", template=4, text="벽을 따라 왼쪽으로 꺾으세요."),
           Cue("turn", "N2", 8.0, direction="right", template=4, text="벽을 따라 오른쪽으로 꺾으세요.")]
    got = finalize(st, collapse(two))
    check(len(got) == 1 and got[0].text == "코너가 연속적으로 있습니다. 벽을 따라 계속 가세요.",
          "두 개 이상이면 한 문장으로", got[0].text if got else "")

    # ── 수직연결자에서 출발 ───────────────────────────────────────
    print("\n── 연결자 판정 (connector_at_start) ──")
    beacons, elev, stair, far = conn_fixture()
    check(connector_at_start("B1", beacons, [elev], MPP) is elev, "최근접 비콘이면 잡는다")
    check(connector_at_start("B2", beacons, [elev], MPP) is None, "다른 비콘에서 출발하면 안 잡는다")
    check(connector_at_start("B1", beacons, [far], MPP) is None,
          f"최근접이어도 {CONNECTOR_NEAR_M:.0f}m 밖이면 안 잡는다")
    check(connector_at_start("B1", beacons, [], MPP) is None, "연결자가 없으면 없다")
    check(connector_at_start("", beacons, [elev], MPP) is None, "출발 비콘을 모르면 안 건다")

    print("\n── 연결자 출발이면 횡단 문장을 바꾼다 ──")
    g, ids = cross_start()
    plain = extract(g, ids, MPP, {("N0", "N1")}, "410호")
    check([c.kind for c in plain][:2] == ["crossEnter", "crossExit"],
          "평소에는 진입·도달 둘", str([c.kind for c in plain][:2]))

    lift = extract(g, ids, MPP, {("N0", "N1")}, "410호", start_connector=elev)
    kinds = [c.kind for c in lift]
    check(kinds[0] == "connectorExit" and "crossEnter" not in kinds,
          "진입 문장이 연결자 것으로 바뀐다", str(kinds))
    check(lift[0].text == "엘리베이터를 등지고 곧장 걸어가세요.",
          "  엘리베이터 문구", lift[0].text)
    check(lift[0].dist_m == 0.0, "  출발 지점에 둔다 — 내리자마자 들어야 한다")
    check(lift[0].template == 8, "  표 8번(엘리베이터)", str(lift[0].template))

    # 도달 안내는 평소와 같은 자리·같은 종류로 남는다. 시점이 다르기 때문이다 —
    # 붙여 말하면 건너편의 회전을 지금 하라는 말로 들린다.
    check(kinds[1] == "crossExit", "  도달은 crossExit 로 따로", str(kinds))
    check(lift[1].text == "벽이 나오면 오른쪽으로 꺾으세요.", "  도달 문구", lift[1].text)
    check(lift[1].dist_m > 0.0, "  도달은 건너편 노드에", f"{lift[1].dist_m}m")

    st9 = extract(g, ids, MPP, {("N0", "N1")}, "410호", start_connector=stair)
    check(st9[0].text == "계단을 등지고 곧장 걸어가세요.", "계단 문구(받침 → 을)", st9[0].text)
    check(st9[0].template == 9, "  표 9번(계단)", str(st9[0].template))

    print("\n── 조사 ──")
    from app.nav.cues import _eul_reul
    for w, want in (("엘리베이터", "를"), ("계단", "을"), ("문", "을"), ("로비", "를"),
                    ("EV", "를"), ("", "를")):
        check(_eul_reul(w) == want, f"  {w or '(빈 문자열)'} → {want}", _eul_reul(w))

    print("\n── 첫 경로노드가 아니면 안 바꾼다 ──")
    g, ids = cross_middle()
    mid = extract(g, ids, MPP, {("N1", "N2")}, "410호", start_connector=elev)
    check("connectorExit" not in [c.kind for c in mid],
          "N1→N2 횡단은 이미 벽을 따라 걸은 뒤", str([c.kind for c in mid]))

    g, ids = turn_then_cross()
    aft = extract(g, ids, MPP, {("P2", "P3")}, "410호", start_connector=elev)
    check("connectorExit" not in [c.kind for c in aft],
          "회전을 한 뒤의 횡단도 평소 문구", str([c.kind for c in aft]))

    print("\n── 나오는 방향 기준 좌우 (side_from_connector) ──")
    # 엘베가 (0,-100), 첫 노드가 (0,0) → +y 를 향해 나온다.
    # 화면좌표라 y 가 아래로 증가하므로 +x 로 꺾는 것이 왼쪽이다.
    lift = LandmarkInfo(id="C9", name="엘베", x=0, y=-100,
                        type="elevator", is_connector=True)
    gl, il = graph_of([Node(id="N0", x=0, y=0), Node(id="N1", x=300, y=0)])
    gr, ir = graph_of([Node(id="N0", x=0, y=0), Node(id="N1", x=-300, y=0)])
    gs, iss = graph_of([Node(id="N0", x=0, y=0), Node(id="N1", x=0, y=300)])
    check(side_from_connector(lift, gl, il) == "left", "다음 노드가 왼쪽",
          str(side_from_connector(lift, gl, il)))
    check(side_from_connector(lift, gr, ir) == "right", "다음 노드가 오른쪽",
          str(side_from_connector(lift, gr, ir)))
    check(side_from_connector(lift, gs, iss) is None, "곧장 앞이면 어느 쪽도 아니다")

    on_node = LandmarkInfo(id="C8", name="엘베", x=0, y=0,
                           type="elevator", is_connector=True)
    check(side_from_connector(on_node, gl, il) is None,
          "연결자가 첫 노드 위면 향한 쪽을 모른다")
    check(side_from_connector(lift, gl, il[:1]) is None, "노드가 하나면 잴 것이 없다")

    print("\n" + "=" * 60)
    if fails:
        print(f"실패 {len(fails)} / 전체 {total}")
        for x in fails:
            print("  ✗", x)
        return 1
    print(f"전체 {total}개 통과 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
