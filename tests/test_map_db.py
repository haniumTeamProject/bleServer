"""`/map-db` — /monitor 지도가 DB 를 읽는 피드를 왕복시켜 확인한다.

    python tests/test_map_db.py

Postgres 없이 돈다(SQLite 메모리). 확인하는 것은 세 가지다.

  1. **좌표를 건드리지 않는가** — DB 는 설계도(900) 기준이고 map-tool 은 작업
     픽셀 기준이다. 환산에는 workW 가 필요한데 그건 브라우저가 이미지를 열어야
     정해지므로 서버가 하면 안 된다. 서버가 몰래 바꾸면 지도 위 비콘이 어긋난다.

  2. **연결자가 목적지에 포함되는가** — DB 는 랜드마크와 연결자를 다른 테이블에
     두지만 사용자에게는 둘 다 갈 수 있는 곳이다. 빠지면 층 이동을 말할 수 없다.

  3. **없을 때 이유를 말하는가** — 설계도가 없으면 404 와 함께 무엇이 없는지
     알려줘야 한다. 실측 현장에서 "안 열린다"만 보면 손을 못 댄다.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sqlalchemy as sa  # noqa: E402


# SQLite 에서 못 쓰는 PostgreSQL ARRAY 를 JSON 으로 바꿔 끼운다.
# 모델을 import 하기 **전에** 해야 한다. (test_admin_api.py 와 같은 수법)
class _PortableArray(sa.types.TypeDecorator):
    impl = sa.JSON
    cache_ok = True

    def __init__(self, item_type, *a, **kw):
        super().__init__()


sa.ARRAY = _PortableArray

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security.jwt import create_access_token  # noqa: E402

OK, BAD = "✓", "✗"

engine = sa.create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=sa.pool.StaticPool,
)
TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def override_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_db

PNG_1PX = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


def main() -> int:
    fails: list[str] = []
    total = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal total
        total += 1
        print(f" {OK if ok else BAD} {label:<50} {detail}")
        if not ok:
            fails.append(label)

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {create_access_token('a@t', 'super_admin')}"})

    # ── 층 목록 — 아무것도 없을 때 ────────────────────────────
    print("\n── 층 목록 ──")
    r = c.get("/map-db/floors")
    check(r.status_code == 200 and r.json()["floors"] == [],
          "비어 있으면 빈 목록", f"{r.status_code}")

    b = c.post("/api/buildings", json={"code": "suwon_ict", "name": "ICT융합대학"}).json()
    f = c.post(f"/api/buildings/{b['id']}/floors", json={"floor": 4}).json()
    fid = f["id"]

    rows = c.get("/map-db/floors").json()["floors"]
    check(len(rows) == 1 and rows[0]["label"] == "ICT융합대학 4층",
          "건물명 + 층으로 이름을 만든다", rows[0]["label"] if rows else "")
    check(rows[0]["hasFloorplan"] is False and rows[0]["hasScale"] is False,
          "덜 된 항목을 표시한다", "설계도·축척 없음")

    # ── 설계도가 없으면 이유를 말한다 ─────────────────────────
    print("\n── 설계도가 없을 때 ──")
    r = c.get(f"/map-db/floors/{fid}/project")
    body = r.json()
    check(r.status_code == 404, "404 를 준다", f"{r.status_code}")
    check("설계도" in body.get("error", ""), "무엇이 없는지 말한다",
          body.get("error", "").splitlines()[0][:34])

    r = c.get("/map-db/floors/없는층/project")
    check(r.status_code == 404, "없는 층도 404", f"{r.status_code}")

    # ── 갖춰지면 프로젝트를 준다 ──────────────────────────────
    print("\n── 프로젝트 페이로드 ──")
    c.put(f"/api/floors/{fid}/floorplan", json={"imageUrl": PNG_1PX})
    c.put(f"/api/floors/{fid}/mask", json={"width": 1200, "height": 800, "dataUrl": PNG_1PX})
    c.put(f"/api/floors/{fid}/scale", json={"scaleMPerPx": 0.0413})

    c.post(f"/api/floors/{fid}/beacons", json={
        "name": "ESP32-Beacon3", "mac": "44:B1:76:1A:13:B2", "minor": 3,
        "type": "semantic", "x": 237.5, "y": 1135.5,
        "sourceUid": "u-b3", "sourceLabel": "B3",
    })
    c.post(f"/api/floors/{fid}/landmarks",
           json={"name": "407호", "category": "강의실", "x": 94, "y": 1130})

    conn = c.post(f"/api/buildings/{b['id']}/connectors",
                  json={"name": "1번 엘리베이터", "type": "elevator", "floors": [4]}).json()
    c.put(f"/api/buildings/{b['id']}/connectors/{conn['id']}/positions/{fid}",
          json={"x": 500, "y": 300})

    p = c.get(f"/map-db/floors/{fid}/project").json()

    check(p.get("mappinProject") is True and p.get("source") == "db",
          "map-tool 이 알아보는 모양이다", f"source={p.get('source')}")
    check(p.get("designW") == 900, "좌표 기준 폭을 알려준다", str(p.get("designW")))
    check(p.get("imageDataUrl") == PNG_1PX, "설계도 이미지를 담는다")

    # (1) 좌표를 건드리지 않는가
    bc = p["beacons"][0]
    check(bc["x"] == 237.5 and bc["y"] == 1135.5,
          "비콘 좌표를 그대로 보낸다 (900 기준)", f'({bc["x"]}, {bc["y"]})')
    check(bc["id"] == "B3" and bc["bleName"] == "ESP32-Beacon3",
          "id 는 표시 라벨, bleName 은 광고 이름", f'{bc["id"]} / {bc["bleName"]}')
    check(bc["minor"] == 3 and bc["major"] == 104, "minor·major 를 같이 준다",
          f'{bc["major"]}/{bc["minor"]}')

    # 축척은 **마스크 픽셀** 기준이라 그대로 보내고 maskW 를 같이 준다
    check(p.get("scaleMPerPx") == 0.0413 and p.get("maskW") == 1200,
          "축척과 마스크 폭을 같이 준다", f'{p.get("scaleMPerPx")} / {p.get("maskW")}')
    check(p.get("maskDataUrl") == PNG_1PX, "마스크는 PNG 그대로 (바이트 변환은 브라우저)")

    # (2) 연결자가 목적지에 들어가는가
    names = {lm["name"]: lm for lm in p["landmarks"]}
    check("407호" in names, "랜드마크가 들어간다", "407호")
    check("1번 엘리베이터" in names, "연결자도 목적지로 들어간다", "엘리베이터")
    check(names.get("1번 엘리베이터", {}).get("isConnector") is True,
          "연결자에는 표시가 붙는다", "isConnector=True")
    check(names.get("1번 엘리베이터", {}).get("x") == 500,
          "연결자 좌표는 그 층의 배치 좌표", "500")
    check(names.get("407호", {}).get("isConnector") is False,
          "일반 랜드마크는 표시가 없다")

    # 다른 층의 연결자 좌표가 섞이면 안 된다
    f5 = c.post(f"/api/buildings/{b['id']}/floors", json={"floor": 5}).json()
    c.post(f"/api/buildings/{b['id']}/connectors",
           json={"name": "2번 엘리베이터", "type": "elevator", "floors": [5]})
    c.put(f"/api/buildings/{b['id']}/connectors/{conn['id']}/positions/{f5['id']}",
          json={"x": 900, "y": 900})
    p2 = c.get(f"/map-db/floors/{fid}/project").json()
    lifts = [lm for lm in p2["landmarks"] if lm["isConnector"]]
    check(len(lifts) == 1 and lifts[0]["x"] == 500,
          "다른 층의 연결자 좌표가 섞이지 않는다", f"{len(lifts)}개")

    # ── 쓰기 경로가 없는가 ────────────────────────────────────
    print("\n── 읽기 전용 ──")
    from app.main import app as fastapi_app
    writes = [
        (m, r_.path)
        for r_ in fastapi_app.routes
        for m in (getattr(r_, "methods", None) or [])
        if getattr(r_, "path", "").startswith("/map-db")
        and m in ("POST", "PUT", "PATCH", "DELETE")
    ]
    check(not writes, "/map-db 에 쓰기 메서드가 없다", str(writes) if writes else "GET 뿐")

    # ── 좌표·축척 환산 (틀려도 조용해서 제일 위험한 곳) ──────
    print("\n── 환산 (숫자로 확인) ──")
    from app.nav.db_map_source import DESIGN_W

    # WEB-FE 와 기준 폭이 갈라지면 지도 위 비콘이 통째로 어긋난다.
    consts = (ROOT.parent / "WEB-FE" / "src" / "lib" / "constants.ts")
    if consts.is_file():
        import re
        m = re.search(r"MAP_DESIGN_W\s*=\s*(\d+)", consts.read_text(encoding="utf-8"))
        fe = int(m.group(1)) if m else None
        check(fe == DESIGN_W, "기준 폭이 WEB-FE 와 같다", f"FE {fe} / 서버 {DESIGN_W}")
    check(p["designW"] == DESIGN_W, "응답의 designW 도 같은 값", str(p["designW"]))

    # 설계도(900) → 작업 픽셀 → 설계도. 실측 평면도 크기(2372px)로 왕복시킨다.
    orig_w, work_w = 2372, 2372          # MAX_DIM=3200 이라 이 이미지는 축소되지 않는다
    k = work_w / DESIGN_W
    x900 = 237.5
    check(abs((x900 * k) / k - x900) < 1e-9, "좌표 왕복이 제자리로 온다", f"{x900}")
    check(abs(x900 * k - 625.9) < 0.1, "900 → 작업 픽셀", f"{x900} → {x900 * k:.1f}px")

    # 축척: DB 는 마스크 픽셀 기준, map-tool 은 원본 픽셀 기준.
    #   m/원본px = scaleMPerPx × maskW / origW
    # 마스크가 원본을 등비 축소한 것이므로 같은 실거리가 나와야 한다.
    mask_w, scale_db = 1200, 0.0413
    m_per_orig = scale_db * mask_w / orig_w
    # 같은 복도를 두 좌표계에서 재면 길이가 같아야 한다
    corridor_mask_px = 100
    corridor_orig_px = corridor_mask_px * orig_w / mask_w
    check(abs(corridor_mask_px * scale_db - corridor_orig_px * m_per_orig) < 1e-9,
          "축척 환산 후 실거리가 같다", f"{corridor_mask_px * scale_db:.2f}m")
    check(abs(m_per_orig - 0.0209) < 0.0001, "m/원본px 값", f"{m_per_orig:.4f}")

    # ── map-tool 이 실제로 이 피드를 부르는가 ────────────────
    print("\n── map-tool 배선 ──")
    tool = None
    for cand in (ROOT / "map-tool", ROOT.parent / "map-tool"):
        if (cand / "map_inspection.html").is_file():
            tool = cand / "map_inspection.html"
            break
    if tool is None:
        print(" ! map-tool 을 못 찾아 배선 확인을 건너뜀")
    else:
        src = tool.read_text(encoding="utf-8")
        check("/map-db/floors" in src, "층 목록을 부른다", "/map-db/floors")
        check("/map-db/floors/' + encodeURIComponent(floorId) + '/project" in src
              or "/project" in src, "프로젝트를 부른다")
        check("bootMapSource()" in src, "시작할 때 출처를 고른다", "bootMapSource")
        check("autoLoadFromStatic" in src, "파일 모드가 남아 있다", "폴백 유지")
        check("workW / (p.designW || 900)" in src, "900 → 작업 픽셀 환산이 있다")
        check("p.scaleMPerPx * p.maskW / origW" in src, "축척 환산이 있다")
        check("imageSmoothingEnabled = false" in src,
              "마스크 리샘플에서 보간을 끈다", "부풀림 방지")

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
