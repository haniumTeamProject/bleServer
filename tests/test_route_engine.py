"""경로 탐색 엔진 테스트.

    python tests/test_route_engine.py

지도 프로젝트 파일이 없어도 돌아간다. 그래프를 손으로 만들어 규칙만 확인하기
때문이다. 실제 지도로 확인하는 것은 `tests/try_route.py` 가 한다.

여기서 지키려는 것은 **브라우저(map_inspection.html)와 같은 답을 내는 것**이다.
관리자가 도구에서 확인한 경로와 사용자가 안내받는 경로가 다르면 검수가 무의미해진다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.nav.map_source import (  # noqa: E402
    BeaconInfo, Edge, Graph, Node,
)
from app.nav.route_engine import (  # noqa: E402
    annotate_turns, estimated_seconds, find_node_path, to_beacon_sequence, turn_at,
)


def line(ok: bool, label: str, detail: str = "") -> bool:
    print(f" {'✓' if ok else '✗'} {label:<44} {detail}")
    return ok


def main() -> int:
    fails: list[str] = []
    total = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal total
        total += 1
        line(ok, label, detail)
        if not ok:
            fails.append(label)

    # ── 건너기 페널티 ─────────────────────────────────────────────
    #
    # A ─10m─ B ─10m─ C    벽을 따라가면 20m
    # └──── 5m 건너기 ────┘  실제 5m
    #
    # 절약분은 15m. 규칙: 절약분이 페널티보다 클 때만 건너기를 고른다.
    print("\n── 건너기 페널티 (벽 20m vs 건너기 5m, 절약 15m) ──")
    g = Graph(
        nodes=[Node("A", 0, 0), Node("B", 100, 0), Node("C", 100, 100)],
        edges=[Edge("A", "B", 10.0, "wall"), Edge("B", "C", 10.0, "wall"),
               Edge("A", "C", 5.0, "cross", directed=True)],
    )
    for penalty in (0.0, 10.0, 14.9, 15.1, 30.0):
        r = find_node_path(g, "A", "C", penalty)
        crossed = len(r.node_ids) == 2
        want_cross = penalty < 15.0
        check(crossed == want_cross,
              f"페널티 {penalty:5.1f}m → {'건너기' if crossed else '벽'}",
              f"실거리 {r.dist_m}m, 건너기 {r.crossings}회")

    r = find_node_path(g, "A", "C", 30.0)
    check(r.dist_m == 20.0, "실거리에 가상 페널티가 안 섞인다",
          f"{r.dist_m}m (사용자에게 말하는 값)")

    # ── 건너기는 단방향 ───────────────────────────────────────────
    #
    # 벽 끝에서 출발하는 것만 안전하다. 반대편은 벽 끝이 아닐 수 있어서
    # 거슬러 건너면 짚을 벽이 없는 곳에 서게 된다.
    print("\n── 건너기 단방향 ──")
    r = find_node_path(g, "C", "A", 0.0)
    check(r is not None and len(r.node_ids) == 3,
          "역방향에서는 건너기를 못 쓴다", f"{r.node_ids if r else None}")

    # ── 못 가는 경우 ──────────────────────────────────────────────
    print("\n── 길이 없을 때 ──")
    check(find_node_path(Graph(nodes=[Node("X", 0, 0), Node("Y", 1, 1)], edges=[]),
                         "X", "Y", 0.0) is None,
          "끊긴 그래프는 None", "예외를 던지지 않는다")
    check(find_node_path(g, "A", "ZZ", 0.0) is None, "없는 노드는 None")
    r = find_node_path(g, "A", "A", 0.0)
    check(r is not None and r.dist_m == 0.0, "출발=도착이면 0m")

    # ── 회전 방향 ─────────────────────────────────────────────────
    #
    # 화면 좌표라 y가 아래로 증가한다. 부호를 뒤집으면 왼쪽/오른쪽이 반대로
    # 나가고, 사용자는 그걸 확인할 방법이 없다.
    print("\n── 회전 방향 (화면 좌표: y가 아래로 증가) ──")
    turns = [
        ((0, 0), (10, 0), (10, 10), "right", "동쪽으로 가다 남쪽으로"),
        ((0, 0), (10, 0), (10, -10), "left", "동쪽으로 가다 북쪽으로"),
        ((0, 0), (10, 0), (20, 0), None, "직진"),
        ((0, 0), (10, 0), (20, 1), None, "미세한 흔들림은 회전이 아니다"),
        ((0, 10), (0, 0), (10, 0), "right", "북쪽으로 가다 동쪽으로"),
        ((0, 10), (0, 0), (-10, 0), "left", "북쪽으로 가다 서쪽으로"),
    ]
    for p, c, n, want, why in turns:
        got = turn_at(Node("p", *p), Node("c", *c), Node("n", *n))
        check(got == want, f"{why}", f"{got}")

    # ── 비콘 시퀀스 ───────────────────────────────────────────────
    #
    # 경로 노드는 1m 간격으로 촘촘하다. 접지 않으면 비콘 하나가 수십 번 반복된다.
    print("\n── 노드 경로 → 비콘 순서 ──")
    nodes = [Node(f"N{i:02d}", i * 10, 0) for i in range(10)]
    seq_graph = Graph(nodes=nodes, edges=[])
    beacons = [BeaconInfo("B1", 0, 0), BeaconInfo("B2", 45, 0), BeaconInfo("B3", 90, 0)]
    ids = [n.id for n in nodes]
    steps = to_beacon_sequence(seq_graph, ids, beacons, radius_m=1.5, meters_per_px=0.05)
    got = [s.beacon_id for s in steps]
    check(got == ["B1", "B2", "B3"], "연속 중복은 하나로 접는다", f"{got} (노드 10개 → 비콘 3개)")
    check(steps[-1].is_arrival and not any(s.is_arrival for s in steps[:-1]),
          "마지막만 도착 표시")
    check([s.seq for s in steps] == [1, 2, 3], "seq는 1부터 연속")

    # 왕복 — 같은 비콘을 두 번 지나는 것은 실제로 일어난다. 접으면 안 된다.
    round_trip = ids + list(reversed(ids[:-1]))
    steps2 = to_beacon_sequence(seq_graph, round_trip, beacons, 1.5, 0.05)
    got2 = [s.beacon_id for s in steps2]
    check(got2 == ["B1", "B2", "B3", "B2", "B1"],
          "왕복에서 연속이 아닌 재방문은 남긴다", f"{got2}")

    # 반경 밖 비콘은 무시
    far = [BeaconInfo("BF", 0, 10_000)]
    check(to_beacon_sequence(seq_graph, ids, far, 1.5, 0.05) == [],
          "반경 밖 비콘은 잡지 않는다")

    # ── 회전 주석 ─────────────────────────────────────────────────
    print("\n── 비콘 지점의 회전 ──")
    corner_nodes = [Node("N1", 0, 0), Node("N2", 100, 0), Node("N3", 100, 100)]
    cg = Graph(nodes=corner_nodes, edges=[])
    cbeacons = [BeaconInfo("B1", 0, 0), BeaconInfo("B2", 100, 0), BeaconInfo("B3", 100, 100)]
    csteps = annotate_turns(cg, to_beacon_sequence(cg, ["N1", "N2", "N3"], cbeacons, 1.0, 0.005))
    check([s.turn for s in csteps] == [None, "right", None],
          "가운데 비콘에서만 회전이 붙는다", f"{[s.turn for s in csteps]}")

    # ── 예상 시간 ─────────────────────────────────────────────────
    print("\n── 예상 소요 시간 (0.7m/s — 벽을 짚고 걷는 속도) ──")
    check(estimated_seconds(48.5) == 69, "48.5m → 69초", f"{estimated_seconds(48.5)}초")
    check(estimated_seconds(0) == 1, "0m 라도 최소 1초")

    print(f"\n{'=' * 60}")
    if fails:
        print(f"실패 {len(fails)} / 전체 {total}")
        for f in fails:
            print("  ✗", f)
        return 1
    print(f"전체 {total}개 통과 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
