"""파이썬 포팅본이 관리자웹 원본과 **같은 값을 내는지** 대조한다.

    python tests/test_path_nodes.py

기대값은 손으로 적은 게 아니라 **원본 TS 를 실제로 돌려서** 뽑은 것이다
(`tests/fixtures/gen_pathnodes_reference.mjs`). 손으로 적으면 내가 원본을
잘못 읽은 것까지 같이 베끼게 되어 대조가 의미를 잃는다.

기대값을 다시 뽑으려면:

    node --experimental-strip-types tests/fixtures/gen_pathnodes_reference.mjs

관리자웹이 알고리즘을 고치면 위를 다시 돌린다. 그러면 이 테스트가 깨지고,
**따라가야 할 변경이 무엇인지** 그 자리에서 드러난다.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.nav.path_nodes import (  # noqa: E402
    DEFAULT_CROSSING_MAX_PX,
    EntrancePoint,
    generate_path_nodes,
)
from app.nav.route_engine import find_shortest_path  # noqa: E402

OK, BAD = "✓", "✗"
REFERENCE = Path(__file__).parent / "fixtures" / "pathnodes_reference.json"

# 좌표는 부동소수 계산 결과라 마지막 자리가 다를 수 있다. 1e-9 는 픽셀 기준으로
# 사실상 같은 값이다(실거리로 나노미터 수준).
EPS = 1e-9


def build_mask(w: int, h: int, rects) -> bytearray:
    """기대값 파일에 적힌 사각형 목록으로 마스크를 만든다. JS 쪽과 같은 방식."""
    mask = bytearray(w * h)
    for x0, y0, x1, y1 in rects:
        for y in range(y0, y1):
            base = y * w
            for x in range(x0, x1):
                mask[base + x] = 1
    return mask


def main() -> int:
    if not REFERENCE.is_file():
        print(f"{BAD} 기대값 파일이 없습니다: {REFERENCE}")
        print("   node --experimental-strip-types tests/fixtures/gen_pathnodes_reference.mjs")
        return 1

    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))
    fails: list[str] = []
    total = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal total
        total += 1
        print(f" {OK if ok else BAD} {label:<46} {detail}")
        if not ok:
            fails.append(label)

    # ── 노드 생성 ────────────────────────────────────────────
    print("\n── 노드 생성 (원본 TS 출력과 대조) ──")
    for case in ref["pathNodes"]:
        name = case["name"]
        mask = build_mask(case["w"], case["h"], case["rects"])
        entrances = [EntrancePoint(x=e["x"], y=e["y"], kind=e["kind"])
                     for e in case["entrances"]]
        crossing = case["crossingMaxPx"]
        got = generate_path_nodes(
            mask, case["w"], case["h"], entrances,
            DEFAULT_CROSSING_MAX_PX if crossing is None else crossing,
        )

        want_nodes = case["nodes"]
        want_edges = case["edges"]
        problems: list[str] = []

        if len(got.nodes) != len(want_nodes):
            problems.append(f"노드 수 {len(got.nodes)} != {len(want_nodes)}")
        else:
            for g, w_ in zip(got.nodes, want_nodes, strict=True):
                if g.id != w_["id"]:
                    problems.append(f'id {g.id} != {w_["id"]}')
                    break
                if abs(g.x - w_["x"]) > EPS or abs(g.y - w_["y"]) > EPS:
                    problems.append(f'{g.id} 좌표 ({g.x},{g.y}) != ({w_["x"]},{w_["y"]})')
                    break
                if g.type != w_["type"]:
                    problems.append(f'{g.id} type {g.type} != {w_["type"]}')
                    break
                if g.concave != w_["concave"]:
                    problems.append(f'{g.id} concave {g.concave} != {w_["concave"]}')
                    break
                if g.pair_kind != w_["pairKind"]:
                    problems.append(f'{g.id} pairKind {g.pair_kind} != {w_["pairKind"]}')
                    break

        if len(got.edges) != len(want_edges):
            problems.append(f"연결 수 {len(got.edges)} != {len(want_edges)}")
        else:
            for g, w_ in zip(got.edges, want_edges, strict=True):
                if (g.a, g.b, g.type) != (w_["a"], w_["b"], w_["type"]):
                    problems.append(f'연결 {g.a}-{g.b}({g.type}) != {w_["a"]}-{w_["b"]}({w_["type"]})')
                    break

        cross = sum(1 for e in got.edges if e.type == "cross")
        check(not problems, name,
              f"노드 {len(got.nodes)} / 연결 {len(got.edges)} (건너기 {cross})"
              if not problems else problems[0])

    # ── 실측 평면도 ──────────────────────────────────────────
    #
    # 손으로 만든 도형은 알고리즘의 갈래를 다 밟지 못한다. 실제 4층 마스크로
    # 노드 100여 개가 전부 맞는지 봐야 포팅이 됐다고 할 수 있다.
    print("\n── 실측 4층 평면도 ──")
    real = ref.get("realFloor")
    project = None
    for cand in (ROOT / "map-tool" / "static" / "mappin_project.json",
                 ROOT.parent / "map-tool" / "static" / "mappin_project.json"):
        if cand.is_file():
            project = cand
            break
    if real is None or project is None:
        print(" ! 실측 프로젝트 파일이 없어 건너뜀")
    else:
        import base64
        d = json.loads(project.read_text(encoding="utf-8"))
        w, h = d["workW"], d["workH"]
        mask = bytes(1 if v else 0 for v in base64.b64decode(d["corridorMaskB64"]))
        entrances = [
            EntrancePoint(x=e["x"], y=e["y"], kind=e["kind"]) for e in real["entrances"]
        ]
        got = generate_path_nodes(mask, w, h, entrances, real["crossingMaxPx"])

        check(len(got.nodes) == len(real["nodes"]),
              "노드 수", f'{len(got.nodes)} / {len(real["nodes"])}')
        check(len(got.edges) == len(real["edges"]),
              "연결 수", f'{len(got.edges)} / {len(real["edges"])}')

        if len(got.nodes) == len(real["nodes"]):
            worst = 0.0
            bad = None
            for g, w_ in zip(got.nodes, real["nodes"], strict=True):
                if (g.id, g.type, g.concave, g.pair_kind) != (
                        w_["id"], w_["type"], w_["concave"], w_["pairKind"]):
                    bad = f'{g.id}: {g.type}/{g.concave}/{g.pair_kind} != {w_["type"]}/{w_["concave"]}/{w_["pairKind"]}'
                    break
                worst = max(worst, abs(g.x - w_["x"]), abs(g.y - w_["y"]))
            check(bad is None, "노드 id·종류·concave·pairKind", bad or "전부 일치")
            check(worst <= EPS, "노드 좌표", f"최대 오차 {worst:.3e}px")

        if len(got.edges) == len(real["edges"]):
            same = all((g.a, g.b, g.type) == (w_["a"], w_["b"], w_["type"])
                       for g, w_ in zip(got.edges, real["edges"], strict=True))
            cross = sum(1 for e in got.edges if e.type == "cross")
            check(same, "연결 순서·종류", f"건너기 {cross}개")

    # ── 경로 찾기 ────────────────────────────────────────────
    print("\n── 경로 찾기 (원본 TS 출력과 대조) ──")
    for case in ref["pathfind"]:
        nodes = [{"id": i, "x": x, "y": y} for i, x, y in case["nodes"]]
        edges = [{"a": a, "b": b, "type": t} for a, b, t in case["edges"]]
        got = find_shortest_path(nodes, edges, case["start"], case["end"], case["penalty"])
        want = case["result"]

        if want is None:
            check(got is None, case["name"], "길 없음")
            continue
        if got is None:
            check(False, case["name"], "길을 못 찾음 (원본은 찾음)")
            continue
        ok = (got.path == want["path"]
              and abs(got.distance_px - want["distancePx"]) < 1e-9)
        check(ok, case["name"],
              f'{"→".join(got.path)}  {got.distance_px:.4f}'
              if ok else f'{got.path} {got.distance_px} != {want["path"]} {want["distancePx"]}')

    print(f"\n{'=' * 62}")
    if fails:
        print(f"실패 {len(fails)} / 전체 {total}")
        for x in fails:
            print("  ✗", x)
        return 1
    print(f"전체 {total}개 통과 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
