"""**실제 DB** 로 비콘 매칭 반경을 재본다.

경로에 비콘이 몇 개만 서는 이유가 반경인지, 좌표인지, minor 중복인지를 여기서
가린다. 서버도 폰도 필요 없다 — `.env` 의 `DATABASE_URL` 만 있으면 된다.

    python tests/check_radius.py                       # 층 목록만
    python tests/check_radius.py <층id>                # 그 층 비콘·목적지 목록
    python tests/check_radius.py <층id> B1 407         # 출발 비콘 → 목적지, 반경 훑기

── 왜 실측 파일이 아니라 DB 인가 ──────────────────────────────────

`tests/test_route_from_db.py` 는 map-tool 프로젝트 파일(4층)로 돈다. 그건 좁은
복도라 넓은 홀과 성질이 정반대다. **1층 로비에서 나는 문제를 4층 데이터로 재면
답이 안 나온다.** 그래서 이건 지금 쓰는 DB 를 그대로 읽는다.

── 무엇을 보나 ────────────────────────────────────────────────────

    반경별 칸 수      늘렸을 때 비콘이 실제로 들어오는가
    연속 중복         `B12 → B12` 가 생기면 추적기가 그 칸에서 멈춘다
    빠진 비콘과 거리  4~6m 가 여럿이면 반경 문제. 20m 넘는 것이 섞이면 좌표 문제
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.nav import db_map_source as dms  # noqa: E402
from app.nav.db_map_source import DbMapSource  # noqa: E402
from app.nav.route_engine import build_route  # noqa: E402

RADII = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0)


def show_floors(db) -> None:
    from app.beacon.models import Beacon
    from app.floor.models import Floor

    print("층 목록\n")
    print(f"  {'id':38} {'층':>4} {'major':>6} {'비콘':>5}")
    for f in db.query(Floor).order_by(Floor.floor).all():
        n = db.query(Beacon).filter(Beacon.floor_id == f.id).count()
        print(f"  {f.id:38} {f.floor or '?':>4} {f.major or '?':>6} {n:>5}")
    print("\n층 id 를 붙여 다시 실행하세요.")


def show_floor(db, floor_id: str) -> None:
    from app.beacon.models import Beacon

    beacons = db.query(Beacon).filter(Beacon.floor_id == floor_id).all()
    print(f"비콘 {len(beacons)}개\n")
    print(f"  {'이름':16} {'major':>6} {'minor':>6}  {'x':>8} {'y':>8}  종류")
    seen: dict[tuple, list[str]] = {}
    for b in sorted(beacons, key=lambda x: (x.minor or 0)):
        key = (b.major, b.minor)
        seen.setdefault(key, []).append(b.name or b.id)
        print(f"  {(b.name or b.id):16} {b.major or 0:>6} {b.minor or 0:>6} "
              f" {b.x or 0:>8.1f} {b.y or 0:>8.1f}  {b.type or '-'}")

    dup = {k: v for k, v in seen.items() if len(v) > 1}
    if dup:
        print("\n  ⚠ minor 가 겹친다 — 추적 키가 같아져서 두 비콘이 한 칸으로 합쳐진다")
        for (maj, mnr), names in dup.items():
            print(f"    major {maj} minor {mnr} : {', '.join(names)}")

    # **연결자도 목적지다.** DB 는 표를 나눠 두지만 사용자에게는 둘 다 갈 수 있는
    # 곳이고, 경로 엔진도 `DbMapSource.landmarks()` 에서 합쳐 본다. 여기서 안 보여
    # 주면 "엘리베이터 2호기" 를 찾을 수 없다.
    names = sorted(x.name for x in DbMapSource(db).landmarks(floor_id) if x.name)
    print(f"\n목적지 {len(names)}개 (연결자 포함)")
    print("  " + ", ".join(names))
    print("\n출발 비콘 이름과 목적지 이름을 붙여 다시 실행하세요.")


def sweep(db, floor_id: str, start_name: str, dest_name: str) -> None:
    from app.beacon.models import Beacon

    start = (db.query(Beacon)
             .filter(Beacon.floor_id == floor_id, Beacon.name == start_name).first())
    if start is None:
        start = (db.query(Beacon)
                 .filter(Beacon.floor_id == floor_id,
                         Beacon.source_label == start_name).first())
    if start is None:
        print(f"✗ 출발 비콘 '{start_name}' 을 이 층에서 못 찾음")
        return

    # **경로 엔진이 쓰는 id 는 DB 의 UUID 가 아니다.**
    # `DbMapSource.beacons()` 가 `source_label or name or id` 로 만든다.
    # UUID 를 넘기면 "출발 비콘을 찾을 수 없습니다" 로 떨어진다.
    start_id = start.source_label or start.name or start.id

    # 목적지는 `landmarks` 표만 보면 안 된다 — 연결자(엘리베이터·계단)는 다른
    # 표에 있고, 경로 엔진은 둘을 합쳐서 본다(`DbMapSource.landmarks`).
    cands = DbMapSource(db).landmarks(floor_id)
    dest = next((x for x in cands if x.name == dest_name), None)
    if dest is None:
        near = [x.name for x in cands if dest_name.replace(" ", "") in (x.name or "").replace(" ", "")]
        print(f"✗ 목적지 '{dest_name}' 을 이 층에서 못 찾음")
        if near:
            print("  비슷한 이름: " + ", ".join(near))
        else:
            print("  전체 목록은 층 id 만 주고 다시 실행하면 나옵니다.")
        return

    print(f"{start_name} → {dest_name}\n")
    print(f"  {'반경':>6} {'칸':>4} {'연속중복':>8}  경로")

    missed_at: dict[float, str] = {}
    for r in RADII:
        # 반경은 `_radius_default()` 가 부를 때마다 환경변수를 다시 읽으므로
        # 여기서 바꾸면 그대로 먹는다. 캐시는 비워야 그래프를 다시 훑는다.
        os.environ["BEACON_MATCH_RADIUS_M"] = str(r)
        os.environ.pop("BEACON_MATCH_RADIUS_BY_FLOOR", None)   # 층별 값이 있으면 못 잰다
        dms._MASK_CACHE.clear()
        dms._GRAPH_CACHE.clear()
        src = DbMapSource(db)

        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                route = build_route(src, floor_id,
                                    from_beacon_id=start_id, to_landmark_id=dest.id)
        except Exception as e:
            print(f"  {r:>5.1f}m  경로 실패 — {e}")
            continue

        ids = [s.beacon_id for s in route.steps]
        dup = sum(1 for i in range(len(ids) - 1) if ids[i] == ids[i + 1])
        flag = "  ⚠" if dup else ""
        print(f"  {r:>5.1f}m {len(ids):>4} {dup:>8}{flag}  {'→'.join(ids[:12])}"
              + ("…" if len(ids) > 12 else ""))
        for line in buf.getvalue().splitlines():
            if "반경" in line:
                missed_at[r] = line.strip()

    print("\n빠진 비콘 (가까운 순)\n")
    for r in RADII:
        if r in missed_at:
            print("  " + missed_at[r])

    print("""
읽는 법
  4~6m 짜리가 여럿      반경 문제. 그 층만 넓히면 된다
  20m 넘는 것이 섞임    좌표가 이상하다. 관리자웹에서 그 비콘 위치를 확인
  연속중복 ⚠            그 반경에서는 안내가 그 칸에서 멈춘다. 쓰면 안 된다

정했으면 .env 에 (기본값은 안 건드리고 그 층만)
  BEACON_MATCH_RADIUS_BY_FLOOR=<층id>=<값>
""")


def main() -> int:
    db = SessionLocal()
    try:
        if len(sys.argv) == 1:
            show_floors(db)
        elif len(sys.argv) == 2:
            show_floor(db, sys.argv[1])
        elif len(sys.argv) == 4:
            sweep(db, sys.argv[1], sys.argv[2], sys.argv[3])
        else:
            print(__doc__)
            return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
