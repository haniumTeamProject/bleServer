"""말한 문장 하나로 추적까지 이어지는지 본다.

    python tests/test_destination_to_route.py

    STT 텍스트 → 목적지 매칭 → 경로 생성 → 비콘 순서 → 추적기 등록

**이 사슬의 가운데가 끊겨 있었다.** `route_engine` 은 만들어져 있었는데
`handler.py` 가 import 조차 하지 않아서, 목적지를 알아들어도 경로를 만들지 않았다.
경로는 `/monitor` 에서 사람이 비콘 순서를 손으로 등록해야 돌았다.

LLM 은 부르지 않는다(느리고 환경을 탄다). 규칙 엔진 결과로도 사슬은 똑같이 돈다 —
여기서 보려는 것은 해석 품질이 아니라 **배선이 이어져 있는가**다.
"""

import base64
import json
import sys
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

import app.database as database  # noqa: E402
from app.beacon.models import Beacon  # noqa: E402
from app.database import Base  # noqa: E402
from app.floor.models import Floor  # noqa: E402
from app.floorplan.models import Floorplan  # noqa: E402
from app.landmark.models import Landmark  # noqa: E402
from app.mask.models import FloorMask  # noqa: E402
from app.nav.db_map_source import DESIGN_W  # noqa: E402
from tests.seed_from_project import gray_alpha_png  # noqa: E402

OK, BAD = "✓", "✗"
FLOOR_ID = "f-chain-4"


def find_project() -> Path | None:
    for cand in (ROOT / "map-tool" / "static" / "mappin_project.json",
                 ROOT.parent / "map-tool" / "static" / "mappin_project.json"):
        if cand.is_file():
            return cand
    return None


def main() -> int:
    project = find_project()
    if project is None:
        print(f"{BAD} 실측 프로젝트 파일이 없어 실행할 수 없습니다.")
        return 1

    fails: list[str] = []
    total = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal total
        total += 1
        print(f" {OK if ok else BAD} {label:<42} {detail}")
        if not ok:
            fails.append(label)

    # ── SQLite 를 진짜 DB 인 것처럼 끼워 넣는다 ──────────────
    # navigation.plan_route 가 SessionLocal 을 직접 부르므로 그걸 갈아끼운다.
    engine = sa.create_engine("sqlite+pysqlite:///:memory:",
                              connect_args={"check_same_thread": False},
                              poolclass=sa.pool.StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    database.SessionLocal = TestSession

    import app.nav.db_map_source as dms
    import app.ws.navigation as navmod
    navmod.SessionLocal = TestSession

    d = json.loads(project.read_text(encoding="utf-8"))
    w, h = d["workW"], d["workH"]
    k = DESIGN_W / w
    scale_db = d["scale_m_per_px"] * d["origW"] / w

    db = TestSession()
    db.add(Floor(id=FLOOR_ID, building_id="b", floor=4, major=104, scale_m_per_px=scale_db))
    db.add(Floorplan(floor_id=FLOOR_ID, image_url="data:image/png;base64,AA", extracted=True))
    png = gray_alpha_png(base64.b64decode(d["corridorMaskB64"]), w, h)
    db.add(FloorMask(floor_id=FLOOR_ID, width=w, height=h,
                     data_url="data:image/png;base64," + base64.b64encode(png).decode()))
    for i, b in enumerate(d["beacons"]):
        db.add(Beacon(id=f"bc{i}", floor_id=FLOOR_ID, name=b.get("bleName") or b["id"],
                      minor=i + 1, major=104, type="semantic",
                      x=b["x"] * k, y=b["y"] * k, source_label=b["id"]))
    for i, lm in enumerate(d["landmarks"]):
        db.add(Landmark(id=f"lm{i}", floor_id=FLOOR_ID, name=lm["name"],
                        category="미분류", x=lm["x"] * k, y=lm["y"] * k,
                        source_label=lm["id"]))
    db.commit()
    db.close()
    dms._MASK_CACHE.clear()
    dms._GRAPH_CACHE.clear()

    # ── 폰이 비콘을 올리고 있는 상황을 만든다 ────────────────
    import app.ws.handler as handler
    from app.ws.rssi_filter import RssiFilterPipeline

    handler._db_landmarks = []
    handler._db_landmarks_at = 0.0
    handler._filters.clear()

    names = [b.get("bleName") or b["id"] for b in d["beacons"]]
    print(f"\n── 비콘 {len(names)}개가 잡히는 중 ──")
    # 실제 스트림처럼 키는 "MAC|이름". B1 이 제일 세게 잡히게 둔다.
    for i, name in enumerate(names):
        key = f"AA:BB:CC:00:00:{i:02X}|{name}"
        pipe = RssiFilterPipeline()
        for _ in range(5):
            pipe.filter(-55.0 if i == 0 else -80.0 - i)
        handler._filters[key] = pipe

    strongest = handler.navigation.strongest_beacon_key(handler._filters)
    check(strongest is not None and strongest.endswith(names[0]),
          "가장 센 비콘을 출발점으로 고른다", handler.navigation._ble_name(strongest or ""))

    # ── 목적지 목록이 DB 에서 오는가 ─────────────────────────
    print("\n── 목적지 목록 ──")
    lms = handler._load_landmark_list(FLOOR_ID)
    check(len(lms) == len(d["landmarks"]),
          "DB 에서 읽는다", f"{len(lms)}개")
    check(all(x.id.startswith("lm") for x in lms),
          "id 가 DB 의 것이다 (파일 id 아님)", lms[0].id if lms else "")

    # ── 말한 문장 하나로 끝까지 ──────────────────────────────
    print("\n── STT 텍스트 → 추적 등록 ──")
    for text in ["사백칠호", "410", "엘베2"]:
        handler._tracker.set_path([])
        session: dict = {}
        payload, guides = handler._process_destination(
            {"event": "resolve", "text": text, "requestId": "t", "floorId": FLOOR_ID}, session)
        msg = json.loads(payload)

        if msg["event"] != "resolved":
            check(False, f'"{text}"', f'{msg["event"]} — {msg.get("speech","")[:30]}')
            continue
        if msg.get("routeError"):
            check(False, f'"{text}" → {msg["landmark"]["name"]}',
                  msg["routeError"].split("\n")[0])
            continue

        r = msg["route"]
        registered = list(handler._tracker.path)
        ok = len(registered) >= 2 and registered == r["keys"]
        check(ok, f'"{text}" → {msg["landmark"]["name"]}',
              f'{r["distanceM"]}m / {r["seconds"]}초 / 비콘 {len(r["keys"])}개 → 추적 {len(registered)}개')

    # ── 폰이 안 붙어 있어도 경로는 나와야 한다 ───────────────
    #
    # 경로를 만드는 것과 추적을 거는 것은 다른 일이다. 앞은 지도만 있으면 되고,
    # 뒤는 폰이 비콘을 올리고 있어야 한다. 예전엔 이 둘을 한 덩어리로 봐서,
    # 폰이 없으면 이미 계산해 둔 경로를 통째로 버렸다.
    # ── 아직 안 잡힌 비콘도 경로에 미리 선다 ─────────────────
    #
    # 예전에는 추적 키가 RSSI 스트림의 키("MAC|이름")였다. 그 키는 폰이 그 비콘을
    # 한 번이라도 봐야 생기므로, 목적지를 말하는 순간에는 앞쪽 비콘이 전부 빠져
    # 20개짜리 경로가 두어 개로 쪼그라들었다.
    #
    # 지금은 minor 로 키를 만든다. DB 만으로 만들 수 있어 미리 세워둘 수 있고,
    # 걸어가다 신호가 잡히면 그때부터 그 자리가 채워진다.
    print("\n── 아직 안 잡힌 비콘도 경로에 선다 ──")
    handler._filters.clear()
    handler._track_values.clear()
    handler._tracker.set_path([])
    payload, _ = handler._process_destination(
        {"event": "resolve", "text": "404", "requestId": "t", "floorId": FLOOR_ID, "fromBeacon": "B6"}, {})
    msg = json.loads(payload)
    r = msg.get("route", {})
    check(msg["event"] == "resolved", "목적지 해석은 성공한다", msg["event"])
    check(bool(r.get("beacons")), "경로가 내려온다", f'비콘 {len(r.get("beacons", []))}개')
    check(msg.get("tracking") is True,
          "잡힌 비콘이 없어도 추적이 걸린다", f'경로 {len(handler._tracker.path)}개')
    check(handler._tracker.path == r["keys"], "추적기 경로가 응답과 같다")
    check(all("-" in k for k in r["keys"]),
          "추적 키가 major-minor 형식이다", r["keys"][0] if r["keys"] else "")
    check(len(r.get("missing", [])) == len(r["beacons"]),
          "지금 안 잡히는 비콘을 전부 표시한다", f'{len(r.get("missing", []))}개')

    # ── 경로 자체를 못 만들 때 ───────────────────────────────
    print("\n── 경로 자체를 못 만들 때 ──")
    payload, _ = handler._process_destination(
        {"event": "resolve", "text": "404", "requestId": "t", "floorId": FLOOR_ID}, {})
    msg = json.loads(payload)
    check(msg["event"] == "resolved", "목적지 해석은 그대로 성공한다", msg["event"])
    check("routeError" in msg, "경로 실패 이유를 따로 알려준다",
          msg.get("routeError", "").split("\n")[0][:36])
    check("route" not in msg, "실패했는데 경로가 실린 것처럼 보이지 않는다")

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
