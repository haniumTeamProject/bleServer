"""위치 판정 파라미터 평가 하네스.

tests/measurements/ 의 실측 CSV 전부를 대상으로 PathTracker를 돌려서
정지 오탐 / 이동 검출률 / 안내 지연을 한 번에 채점한다.

    python tests/eval_tracker.py                # 현재 기본 설정 평가
    python tests/eval_tracker.py --sweep        # 임계값 훑어서 최적점 찾기
    python tests/eval_tracker.py --sweep-full   # 임계값 x 창 x 구간 전부

정지 데이터에서 오탐 0을 지키는 것이 최우선이고(서 있는데 안내가 나가면 안 됨),
그 조건을 만족하는 것들 중 이동 안내가 빠른 쪽을 고른다.
"""

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ws.path_tracker import PathTracker  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "measurements"
GROUND_TRUTH_BIN_MS = 1000   # 정답 산출용 구간 크기
MATCH_TOLERANCE_S = 2.0      # 실제 전환보다 이만큼 앞선 판정까지는 같은 전환으로 인정


def load_csv(path: Path) -> dict[str, list[tuple[int, float]]]:
    by: dict[str, list[tuple[int, float]]] = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by[row["beacon"]].append((int(row["elapsed_ms"]), float(row["filtered_rssi"])))
    for k in by:
        by[k].sort()
    return dict(by)


def resolve_path(by: dict, order_names: list[str] | None) -> list[str]:
    """manifest의 이름 목록을 실제 비콘 키로 바꾼다. 없으면 키 정렬 순서를 쓴다."""
    if not order_names:
        return sorted(by)
    name_to_key = {k.split("|")[-1]: k for k in by}
    keys = [name_to_key[n] for n in order_names if n in name_to_key]
    return keys if len(keys) >= 2 else sorted(by)


def ground_truth(by: dict, path: list[str]) -> list[tuple[float, int]]:
    """1초 구간마다 가장 센 비콘을 구하고, 그게 바뀌는 순간을 전환으로 본다."""
    end = max(v[-1][0] for v in by.values())
    switches, prev = [], None
    for t0 in range(0, end + GROUND_TRUTH_BIN_MS, GROUND_TRUTH_BIN_MS):
        means = {}
        for i, k in enumerate(path):
            win = [v for t, v in by[k] if t0 <= t < t0 + GROUND_TRUTH_BIN_MS]
            if win:
                means[i + 1] = sum(win) / len(win)
        if not means:
            continue
        best = max(means, key=means.get)
        if prev is not None and best != prev:
            switches.append((t0 / 1000, best))
        prev = best
    return switches


def run_tracker(by: dict, path: list[str], **kw) -> list[tuple[float, int, str]]:
    rows = sorted((t, k, v) for k in by for t, v in by[k])
    tr = PathTracker()
    tr.set_path(path, **kw)
    tr.start_session()
    events = []
    for t, k, v in rows:
        tr.feed(k, v, now_ms=t)
        ev = tr.evaluate()
        if ev:
            events.append((t / 1000, ev["number"], ev["direction"]))
    return events


def score_moving(events, truth):
    """실제 전환마다 같은 번호의 판정을 하나씩 짝지어 지연을 잰다."""
    used, lags = set(), []
    for t_true, num in truth:
        cand = [
            (i, e) for i, e in enumerate(events)
            if i not in used and e[1] == num and e[0] >= t_true - MATCH_TOLERANCE_S
        ]
        if cand:
            used.add(cand[0][0])
            lags.append(cand[0][1][0] - t_true)
    return len(used), len(events) - len(used), lags


def min_traverse_seconds(**kw) -> int | None:
    """비콘 3개를 6m 간격으로 두고, 몇 초에 통과해야 끝까지 따라오는지 찾는다.

    실측 데이터는 걷는 속도가 한 가지뿐이라 "빨리 지나가면 놓치는" 문제를 잴 수 없다.
    그래서 경로손실 모델로 합성한 보행을 같이 채점한다. 창을 짧게 잡으면 정지 오탐은
    줄지만 이 값이 나빠지는 트레이드오프가 있어 함께 봐야 한다.
    """
    import math

    from app.ws.rssi_filter import RssiFilterPipeline

    for total in (4, 5, 6, 7, 8, 10, 12, 16, 20):
        spacing, offset, hz = 6.0, 1.5, 10
        beacons = [0.0, spacing, 2 * spacing]
        end = 2 * spacing + 6
        rows = []
        for i in range(int(total * hz)):
            t = i / hz
            x = -6 + (end + 6) * (t / total)
            for bi, bx in enumerate(beacons):
                d = math.hypot(x - bx, offset)
                rows.append((int(t * 1000), f"B{bi+1}", -59 - 20 * math.log10(max(d, 0.5))))
        rows.sort()

        path = [f"B{i+1}" for i in range(3)]
        pipes = {k: RssiFilterPipeline() for k in path}
        tr = PathTracker()
        tr.set_path(path, **kw)
        tr.start_session()
        for t, k, rssi in rows:
            tr.feed(k, pipes[k].filter(rssi), now_ms=t)
            tr.evaluate()
        if tr.index == len(path) - 1:
            return total
    return None


def evaluate(datasets, **kw) -> dict:
    """전체 데이터셋에 대해 한 설정을 채점."""
    false_pos = 0
    matched = missed = extra = 0
    all_lags: list[float] = []
    per_file = []

    for ds in datasets:
        by = load_csv(DATA_DIR / ds["file"])
        path = resolve_path(by, ds.get("path_order"))
        events = run_tracker(by, path, **kw)

        if ds["kind"] == "still":
            false_pos += len(events)
            per_file.append((ds["file"], f"오탐 {len(events)}회"))
        else:
            truth = ground_truth(by, path)
            m, x, lags = score_moving(events, truth)
            matched += m
            missed += len(truth) - m
            extra += x
            all_lags += lags
            per_file.append((ds["file"], f"{m}/{len(truth)} 매칭, 초과 {x}"))

    return {
        "min_traverse": min_traverse_seconds(**kw),
        "false_pos": false_pos,
        "matched": matched,
        "missed": missed,
        "extra": extra,
        "lag_mean": statistics.mean(all_lags) if all_lags else None,
        "lag_max": max(all_lags, key=abs) if all_lags else None,
        "per_file": per_file,
    }


def fmt(r) -> str:
    lag = f"{r['lag_mean']:+.2f}" if r["lag_mean"] is not None else "  —  "
    mx = f"{r['lag_max']:+.2f}" if r["lag_max"] is not None else "  —  "
    mt = f"{r['min_traverse']}초" if r["min_traverse"] else "실패"
    return (f"{r['false_pos']:5} {r['matched']:5} {r['missed']:5} {r['extra']:5} "
            f"{lag:>8} {mx:>8} {mt:>7}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true", help="임계값만 훑기")
    ap.add_argument("--sweep-full", action="store_true", help="임계값 x 창 x 구간")
    args = ap.parse_args()

    manifest = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    datasets = manifest["datasets"]

    still = sum(1 for d in datasets if d["kind"] == "still")
    moving = len(datasets) - still
    print(f"데이터셋 {len(datasets)}개 (정지 {still}, 이동 {moving})\n")

    header = (f"{'정지오탐':>5} {'매칭':>5} {'누락':>5} {'초과':>5} "
              f"{'평균지연':>8} {'최대지연':>8} {'최소통과':>7}")

    if not (args.sweep or args.sweep_full):
        for mode, kw in (("trend  (기존)", dict(mode="trend", threshold=3.0)),
                         ("segment(이전 기본)", dict(mode="segment", threshold=3.0,
                                                    window_ms=2500, segments=5)),
                         ("segment(새 기본)", dict(mode="segment", threshold=2.5,
                                                   window_ms=2000, segments=5))):
            r = evaluate(datasets, **kw)
            print(f"── {mode}")
            print(f"   {header}")
            print(f"   {fmt(r)}")
            for f, s in r["per_file"]:
                print(f"     {f:38} {s}")
            print()
        return

    combos = []
    if args.sweep:
        combos = [dict(mode="segment", threshold=th, window_ms=win, segments=5)
                  for win in (1500, 2000, 2500, 3000)
                  for th in (2.0, 2.5, 3.0, 3.5)]
    else:
        for win in (1500, 2000, 2500, 3000):
            for seg in (4, 5, 6):
                for th in (2.0, 2.5, 3.0, 3.5, 4.0):
                    combos.append(dict(mode="segment", threshold=th,
                                       window_ms=win, segments=seg))

    print(f"{'창':>5} {'구간':>4} {'임계':>5} | {header}")
    print("-" * 72)
    ok = []
    for kw in combos:
        r = evaluate(datasets, **kw)
        clean = r["false_pos"] == 0 and r["missed"] == 0 and r["extra"] == 0
        if clean:
            ok.append((r["min_traverse"] or 99, abs(r["lag_max"] or 9), abs(r["lag_mean"] or 9), kw, r))
        print(f"{kw['window_ms']/1000:5.1f} {kw['segments']:4} {kw['threshold']:5.1f} | "
              f"{fmt(r)} {'✓' if clean else ''}")

    print()
    if ok:
        ok.sort()
        print("오탐 0 · 누락 0 · 초과 0 을 만족하는 것 중 (빠른 통과 대응 → 최대지연) 순:")
        for _mt, mx, lag, kw, r in ok[:6]:
            print(f"  창 {kw['window_ms']/1000:.1f}초 / {kw['segments']}구간 / 임계 {kw['threshold']}dB"
                  f"  → 평균 {r['lag_mean']:+.2f}초, 최대 {r['lag_max']:+.2f}초,"
                  f" 최소통과 {r['min_traverse']}초")
    else:
        print("모든 조합에서 조건을 만족하지 못했습니다.")


if __name__ == "__main__":
    main()
