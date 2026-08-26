"""DB 하나만 가지고 경로가 나오는지 본다.

    python tests/test_route_from_db.py

    DB(마스크·비콘·랜드마크)  →  노드 생성  →  다익스트라  →  비콘 순서

파일(`mappin_project.json`)에 의존하지 않고 도는 것이 요점이다. 실측 도구가 만든
파일 없이 관리자웹이 넣은 데이터만으로 안내가 되어야 실제 서비스가 된다.

Postgres 없이 돈다(SQLite 메모리). 다만 **마스크와 좌표는 실측 4층 것**을 쓴다 —
지어낸 도형으로는 "길이 없다" 같은 실제 실패가 안 잡힌다.
"""

import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sqlalchemy as sa  # noqa: E402


class _PortableArray(sa.types.TypeDecorator):
    impl = sa.JSON
    cache_ok = True

    def __init__(self, item_type, *a, **kw):
        super().__init__()


sa.ARRAY = _PortableArray

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.beacon.models import Beacon  # noqa: E402
from app.database import Base  # noqa: E402
from app.floor.models import Floor  # noqa: E402
from app.floorplan.models import Floorplan  # noqa: E402
from app.landmark.models import Landmark  # noqa: E402
from app.mask.models import FloorMask  # noqa: E402
from app.nav.db_map_source import DESIGN_W, DbMapSource  # noqa: E402
from app.nav.map_source import MapDataError  # noqa: E402
from app.nav.route_engine import build_route, estimated_seconds  # noqa: E402
from tests.seed_from_project import gray_alpha_png  # noqa: E402

OK, BAD = "✓", "✗"
FLOOR_ID = "f-test-4"


def find_project() -> Path | None:
    for cand in (ROOT / "map-tool" / "static" / "mappin_project.json",
                 ROOT.parent / "map-tool" / "static" / "mappin_project.json"):
        if cand.is_file():
            return cand
    return None


def main() -> int:
    project = find_project()
    if project is None:
        print(f"{BAD} 실측 프로젝트 파일이 없어 실행할 수 없습니다 (map-tool/static/).")
        return 1

    fails: list[str] = []
    total = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal total
        total += 1
        print(f" {OK if ok else BAD} {label:<44} {detail}")
        if not ok:
            fails.append(label)

    d = json.loads(project.read_text(encoding="utf-8"))
    w, h = d["workW"], d["workH"]
    k = DESIGN_W / w                          # 작업 픽셀 → 설계도(900)
    scale_db = d["scale_m_per_px"] * d["origW"] / w   # 원본px 기준 → 마스크px 기준

    engine = sa.create_engine("sqlite+pysqlite:///:memory:",
                              connect_args={"check_same_thread": False},
                              poolclass=sa.pool.StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    print(f"\n── DB 채우기 (실측 4층) ──")
    corridor = base64.b64decode(d["corridorMaskB64"])
    png = gray_alpha_png(corridor, w, h)
    db.add(Floor(id=FLOOR_ID, building_id="b-test", floor=4, major=104,
                 scale_m_per_px=scale_db))
    db.add(Floorplan(floor_id=FLOOR_ID, image_url="data:image/png;base64,AA", extracted=True))
    db.add(FloorMask(floor_id=FLOOR_ID, width=w, height=h,
                     data_url="data:image/png;base64," + base64.b64encode(png).decode()))
    for i, b in enumerate(d["beacons"]):
        db.add(Beacon(id=f"bc{i}", floor_id=FLOOR_ID, name=b.get("bleName") or b["id"],
                      minor=i + 1, major=104, type="semantic",
                      x=b["x"] * k, y=b["y"] * k, source_label=b["id"]))
    for i, lm in enumerate(d["landmarks"]):
        db.add(Landmark(id=f"lm{i}", floor_id=FLOOR_ID, name=lm["name"],
                        category="수직연결자" if lm.get("isConnector") else "미분류",
                        x=lm["x"] * k, y=lm["y"] * k, source_label=lm["id"]))
    db.commit()
    print(f" 마스크 {w}×{h}, 비콘 {len(d['beacons'])}개, 목적지 {len(d['landmarks'])}개")

    src = DbMapSource(db)

    # ── 그래프가 만들어지는가 ────────────────────────────────
    print("\n── 마스크에서 그래프 만들기 ──")
    t = time.perf_counter()
    graph = src.graph(FLOOR_ID)
    elapsed = time.perf_counter() - t
    check(not graph.empty, "그래프가 비어 있지 않다",
          f"노드 {len(graph.nodes)} / 연결 {len(graph.edges)}  {elapsed:.1f}s")

    crossings = sum(1 for e in graph.edges if e.type == "cross")
    check(crossings > 0, "건너기 연결이 있다", f"{crossings}개")
    check(all(0 <= n.x <= DESIGN_W * 1.01 for n in graph.nodes),
          "노드가 설계도(900) 좌표계다", f"최대 x {max(n.x for n in graph.nodes):.0f}")

    t = time.perf_counter()
    src.graph(FLOOR_ID)
    cached = time.perf_counter() - t
    check(cached < elapsed / 2, "두 번째 호출은 캐시를 탄다", f"{cached:.3f}s")

    # ── 실제 경로 ────────────────────────────────────────────
    print("\n── 경로 만들기 (비콘 → 목적지) ──")
    beacons = src.beacons(FLOOR_ID)
    landmarks = src.landmarks(FLOOR_ID)
    start = beacons[0].id
    targets = [lm for lm in landmarks if lm.name][:4]

    ok_count = 0
    for lm in targets:
        try:
            route = build_route(src, FLOOR_ID, from_beacon_id=start, to_landmark_id=lm.id)
        except MapDataError as e:
            check(False, f"{start} → {lm.name}", str(e).splitlines()[0])
            continue
        # 거리가 0 인 경우가 정상으로 나온다 — 목적지가 출발 비콘 바로 옆일 때다.
        # (실측 4층에서 "엘베1"은 B1 에서 2.0m 떨어져 있어 같은 노드에 붙는다)
        # 그래서 "거리 > 0" 이 아니라 "경로가 나왔는가"만 본다.
        ok = len(route.steps) > 0
        if ok:
            ok_count += 1
        seq = "→".join(s.beacon_id for s in route.steps[:6])
        note = "  ← 이미 근처" if route.total_distance_m < 1 else ""
        check(ok, f"{start} → {lm.name}",
              f"{route.total_distance_m:.0f}m / {estimated_seconds(route.total_distance_m)}초 / {seq}{note}")

    check(ok_count == len(targets), "목적지 전부 경로가 나왔다", f"{ok_count}/{len(targets)}")

    # ── 안내에 쓸 수 있는 모양인가 ───────────────────────────
    print("\n── 결과 점검 ──")
    route = build_route(src, FLOOR_ID, from_beacon_id=start, to_landmark_id=targets[-1].id)
    ids = [s.beacon_id for s in route.steps]
    check(len(ids) == len(set(ids)) or all(ids[i] != ids[i + 1] for i in range(len(ids) - 1)),
          "같은 비콘이 연속으로 나오지 않는다", f"{len(ids)}단계")
    check(route.steps[-1].is_arrival, "마지막 단계가 도착 표시")
    # 순번은 1부터다. 화면에 "3단계 중 1번째"로 그대로 쓰는 값이라 그게 맞다.
    check(all(s.seq == i + 1 for i, s in enumerate(route.steps)), "순번이 1부터 이어진다")
    turns = sum(1 for s in route.steps if s.turn)
    print(f"   방향 전환 {turns}회, 건너기 {route.crossings}회")

    # ── 없을 때 이유를 말하는가 ──────────────────────────────
    print("\n── 못 만들 때 ──")
    db.query(Floor).filter(Floor.id == FLOOR_ID).update({"scale_m_per_px": None})
    db.commit()
    from app.nav import db_map_source as dms
    dms._MASK_CACHE.clear()
    try:
        src.graph(FLOOR_ID)
        check(False, "축척이 없으면 이유를 말한다", "예외가 안 났다")
    except MapDataError as e:
        check("축척" in str(e), "축척이 없으면 이유를 말한다", str(e).splitlines()[0])

    db.query(FloorMask).filter(FloorMask.floor_id == FLOOR_ID).delete()
    db.commit()
    dms._MASK_CACHE.clear()
    try:
        src.graph(FLOOR_ID)
        check(False, "마스크가 없으면 이유를 말한다", "예외가 안 났다")
    except MapDataError as e:
        check("이동영역" in str(e), "마스크가 없으면 이유를 말한다", str(e).splitlines()[0])

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
