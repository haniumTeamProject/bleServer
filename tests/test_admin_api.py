"""관리자웹 API 를 실제로 왕복시켜 확인한다.

    python tests/test_admin_api.py

**Postgres 없이 돈다.** SQLite 메모리 DB 를 쓰고, PostgreSQL 전용인 `ARRAY` 컬럼만
시험용으로 JSON 으로 바꿔 끼운다(운영 코드는 그대로 ARRAY 를 쓴다).

여기서 보는 것은 경로가 아니라 **동작**이다. 경로·필드 대조는
`tests/test_webfe_contract.py` 가 따로 한다.

특히 아래 두 가지를 확인한다.

  1. 층 상태가 **계산값**인가 — 데이터를 지우면 되돌아가야 한다.
     예전에는 저장해두고 한 칸씩 올리기만 해서, 설계도를 지워도 `ready` 로 남았다.

  2. `sourceUid` 가 **저장되는가** — map-tool 재가져오기의 매칭 키다.
     안 남으면 다시 가져올 때마다 비콘이 중복 생성되고 관리자가 입력한 MAC 이 날아간다.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sqlalchemy as sa  # noqa: E402


# ── SQLite 에서 못 쓰는 PostgreSQL ARRAY 를 JSON 으로 바꿔 끼운다 ──────────
# 모델을 import 하기 **전에** 해야 한다.
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


def make_client() -> TestClient:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    client = TestClient(app)
    token = create_access_token("admin@test", "super_admin")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def seed_admin() -> None:
    from app.admin.models import Admin
    from app.security.password import hash_password

    db = TestSession()
    db.add(Admin(
        email="admin@test", password_hash=hash_password("pw"),
        name="관리자", org="한이음", status="active", role="super_admin",
    ))
    db.commit()
    db.close()


def main() -> int:
    fails: list[str] = []
    total = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal total
        total += 1
        print(f" {OK if ok else BAD} {label:<50} {detail}")
        if not ok:
            fails.append(label)

    c = make_client()
    seed_admin()

    # ── 내 정보 ──────────────────────────────────────────────
    print("\n── 내 정보 (/api/admin/me) ──")
    r = c.get("/api/admin/me")
    check(r.status_code == 200 and r.json()["email"] == "admin@test",
          "GET 으로 내 정보를 받는다", f"{r.status_code}")

    r = c.patch("/api/admin/me", json={"name": "안준성", "position": "팀장"})
    body = r.json() if r.status_code == 200 else {}
    check(r.status_code == 200 and body.get("name") == "안준성"
          and body.get("position") == "팀장",
          "PATCH 로 이름·직위를 고친다", f"{r.status_code}")
    check(body.get("org") == "한이음", "안 보낸 필드는 그대로 둔다", "org 유지")

    # ── 계정 승인 — 경로가 /{id} 여야 한다 (프론트 기준) ────────────
    print("\n── 계정 승인 (프론트 경로 기준) ──")
    c.post("/api/admin/auth/signup",
           json={"email": "new@test", "password": "pw", "name": "신규", "org": "x"})
    pending = c.get("/api/admin/accounts", params={"status": "pending"}).json()
    check(len(pending) == 1, "가입 신청 목록이 조회된다", f"{len(pending)}건")
    if pending:
        r = c.patch(f"/api/admin/accounts/{pending[0]['id']}", json={"status": "active"})
        check(r.status_code == 200 and r.json()["status"] == "active",
              "PATCH /accounts/{id} 로 승인된다", "/status 없이")

    # ── 건물 · 층 ────────────────────────────────────────────
    print("\n── 건물 · 층 ──")
    b = c.post("/api/buildings", json={"code": "suwon_ict", "name": "ICT융합대학"}).json()
    check("createdAt" in b, "건물 응답에 createdAt 이 있다", str(b.get("createdAt"))[:19])

    f = c.post(f"/api/buildings/{b['id']}/floors", json={"floor": 4}).json()
    check(f["major"] == 104, "층 major = 100 + 층번호", f"4층 → {f['major']}")

    # ── 층 상태가 계산값인가 ─────────────────────────────────
    print("\n── 층 상태 (조회할 때마다 계산) ──")
    fid = f["id"]

    def status() -> str:
        rows = c.get(f"/api/buildings/{b['id']}/floors").json()
        return rows[0]["status"]

    check(status() == "floorplan_missing", "설계도 없음", status())

    c.put(f"/api/floors/{fid}/floorplan", json={"imageUrl": "data:image/png;base64,AA"})
    check(status() == "review_needed", "설계도 올림 → 이동영역 필요", status())

    c.put(f"/api/floors/{fid}/mask", json={"width": 10, "height": 10, "dataUrl": "data:x"})
    check(status() == "scale_missing", "이동영역 칠함 → 축척 필요", status())

    r = c.get(f"/api/floors/{fid}/scale")
    check(r.status_code == 200 and r.json() is None, "축척 전에는 null 을 준다", str(r.json()))

    c.put(f"/api/floors/{fid}/scale", json={"scaleMPerPx": 0.05})
    check(status() == "beacon_missing", "축척 정함 → 비콘 필요", status())
    check(c.get(f"/api/floors/{fid}/scale").json()["scaleMPerPx"] == 0.05,
          "저장한 축척이 그대로 나온다", "0.05")

    c.post(f"/api/floors/{fid}/beacons",
           json={"name": "B1", "minor": 1, "type": "semantic", "x": 10, "y": 20})
    check(status() == "ready", "비콘 등록 → 안내 가능", status())

    # 되돌아가는가 — 예전 방식(bump_status)이 못 하던 것
    c.delete(f"/api/floors/{fid}/floorplan")
    check(status() == "floorplan_missing", "설계도를 지우면 상태가 되돌아간다", status())
    c.put(f"/api/floors/{fid}/floorplan", json={"imageUrl": "data:image/png;base64,AA"})

    # ── 연결자 좌표 ──────────────────────────────────────────
    print("\n── 연결자 좌표 (결손 검수의 근거) ──")
    conn = c.post(f"/api/buildings/{b['id']}/connectors",
                  json={"name": "1번 엘리베이터", "type": "elevator", "floors": [4]}).json()
    check(conn["positions"] == [], "새 연결자는 좌표가 비어 있다")
    check(status() == "connector_missing", "운행층인데 좌표가 없으면 결손", status())

    r = c.put(f"/api/buildings/{b['id']}/connectors/{conn['id']}/positions/{fid}",
              json={"x": 100, "y": 200})
    got = r.json() if r.status_code == 200 else {}
    check(r.status_code == 200 and got.get("positions") == [{"floorId": fid, "x": 100, "y": 200}],
          "좌표를 찍으면 연결자 전체가 돌아온다", f"{r.status_code}")
    check(status() == "ready", "좌표를 찍으면 결손이 풀린다", status())

    # 같은 층에 다시 찍으면 덮어쓴다 (중복 생기면 안 됨)
    r = c.put(f"/api/buildings/{b['id']}/connectors/{conn['id']}/positions/{fid}",
              json={"x": 300, "y": 400})
    check(len(r.json()["positions"]) == 1 and r.json()["positions"][0]["x"] == 300,
          "같은 층에 다시 찍으면 덮어쓴다", "중복 안 생김")

    r = c.delete(f"/api/buildings/{b['id']}/connectors/{conn['id']}/positions/{fid}")
    check(r.status_code == 200 and r.json()["positions"] == [], "좌표를 지운다")

    # ── 비콘 — sourceUid 가 살아남는가 ───────────────────────
    print("\n── 비콘 (map-tool 재가져오기 키) ──")
    r = c.post(f"/api/floors/{fid}/beacons", json={
        "name": "B3", "mac": "44:B1:76:1A:13:B2", "minor": 3, "type": "semantic",
        "x": 30, "y": 40, "sourceUid": "u-b3", "sourceLabel": "B3",
    })
    bc = r.json()
    check(bc.get("sourceUid") == "u-b3", "sourceUid 가 저장된다", str(bc.get("sourceUid")))
    check(bc.get("major") == 104, "major 는 층에서 복사된다", str(bc.get("major")))
    check(bc.get("type") == "semantic", "type 은 semantic/reinforcement", str(bc.get("type")))
    check("connectorId" not in bc and "isAnchor" not in bc,
          "connectorId·isAnchor 는 응답에 없다", "프론트 타입과 일치")

    r = c.patch(f"/api/floors/{fid}/beacons/{bc['id']}", json={"minor": 99})
    check(r.json()["sourceUid"] == "u-b3", "수정해도 sourceUid 는 유지된다")

    # ── 랜드마크 — category 자유 입력 ────────────────────────
    print("\n── 랜드마크 (분류는 자유 입력) ──")
    r = c.post(f"/api/floors/{fid}/landmarks", json={
        "name": "채혈실", "category": "진료지원", "x": 5, "y": 6,
        "sourceUid": "u-l1", "sourceLabel": "L01",
    })
    lm = r.json()
    check(lm.get("category") == "진료지원", "고정 목록에 없는 분류도 저장된다",
          str(lm.get("category")))
    check(lm.get("sourceUid") == "u-l1", "랜드마크도 sourceUid 가 저장된다")
    check("type" not in lm, "type 필드는 없다", "category 로 대체됨")

    # ── 건물 대표 상태 ───────────────────────────────────────
    print("\n── 건물 대표 상태 ──")
    c.post(f"/api/buildings/{b['id']}/floors", json={"floor": 5})   # 새 층 = 설계도 없음
    rows = c.get("/api/buildings").json()
    check(rows[0]["status"] == "floorplan_missing",
          "가장 뒤처진 층을 따라간다", rows[0]["status"])

    print(f"\n{'=' * 62}")
    if fails:
        print(f"실패 {len(fails)} / 전체 {total}")
        for f_ in fails:
            print("  ✗", f_)
        return 1
    print(f"전체 {total}개 통과 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
