"""`/ws/navigation` 이 명세대로 도는지 본다.

    python tests/test_nav_ws.py

규약은 `docs/사용자앱_API_명세.md` 다. 여기서 보는 것은 **앱이 기대하는 모양**이
그대로 나오는가다 — 앱은 `event` 로 분기하지 않고 필드만 보고 동작하므로,
필드가 하나라도 빠지면 조용히 아무 일도 안 일어난다.

LLM 은 부르지 않는다(규칙 엔진 폴백으로도 흐름은 같다).
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

import app.database as database  # noqa: E402
from app.beacon.models import Beacon  # noqa: E402
from app.database import Base  # noqa: E402
from app.floor.models import Floor  # noqa: E402
from app.floorplan.models import Floorplan  # noqa: E402
from app.landmark.models import Landmark  # noqa: E402
from app.mask.models import FloorMask  # noqa: E402
from app.nav.db_map_source import DESIGN_W  # noqa: E402
from tests.pathnode_seed import seed_path_nodes  # noqa: E402
from tests.seed_from_project import gray_alpha_png  # noqa: E402

OK, BAD = "✓", "✗"
FLOOR_ID = "f-nav"
MAJOR = 104
FIRST_MAC = "AA:00:00:00:00:01"

# 명세 §2 — 서버가 보내는 모든 메시지에 있어야 하는 필드
REQUIRED = ("event", "state", "utterance", "listenAfter", "haptic", "screen")
STATES = {"ready", "listening", "navigating", "arrived"}


def find_project():
    for c in (ROOT / "map-tool" / "static" / "mappin_project.json",
              ROOT.parent / "map-tool" / "static" / "mappin_project.json"):
        if c.is_file():
            return c
    return None


def main() -> int:
    project = find_project()
    if project is None:
        print(f"{BAD} 실측 프로젝트 파일이 없어 실행할 수 없습니다.")
        return 1

    fails, total = [], 0

    def check(ok, label, detail=""):
        nonlocal total
        total += 1
        print(f" {OK if ok else BAD} {label:<44} {detail}")
        if not ok:
            fails.append(label)

    # ── DB ────────────────────────────────────────────────────
    eng = sa.create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=sa.pool.StaticPool)
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng)
    database.SessionLocal = S
    import app.nav.db_map_source as dms
    import app.ws.navigation as navmod
    navmod.SessionLocal = S
    dms._MASK_CACHE.clear()

    d = json.loads(project.read_text(encoding="utf-8"))
    w, h = d["workW"], d["workH"]
    k = DESIGN_W / w
    db = S()
    db.add(Floor(id=FLOOR_ID, building_id="b", floor=4, major=MAJOR,
                 scale_m_per_px=d["scale_m_per_px"] * d["origW"] / w))
    db.add(Floorplan(floor_id=FLOOR_ID, image_url="x", extracted=True))
    db.add(FloorMask(floor_id=FLOOR_ID, width=w, height=h,
                     data_url="data:image/png;base64," + base64.b64encode(
                         gray_alpha_png(base64.b64decode(d["corridorMaskB64"]), w, h)).decode()))
    for i, b in enumerate(d["beacons"]):
        db.add(Beacon(id=f"bc{i}", floor_id=FLOOR_ID, name=b["id"], minor=i + 1,
                      major=MAJOR, type="semantic", x=b["x"] * k, y=b["y"] * k,
                      mac=f"AA:00:00:00:00:{i + 1:02X}", source_label=b["id"]))
    for i, lm in enumerate(d["landmarks"]):
        db.add(Landmark(id=f"lm{i}", floor_id=FLOOR_ID, name=lm["name"],
                        x=lm["x"] * k, y=lm["y"] * k))
    db.commit()
    # 서버는 저장된 경로노드만 쓴다. 관리자가 저장해둔 상태를 만들어 준다.
    seed_path_nodes(db, FLOOR_ID)
    db.close()

    from app.ws import navigation_ws
    from app.ws.navigation_ws import NavSession, handle, out

    def send(session, msg):
        return handle(session, json.dumps(msg))

    def beacons(session, pairs, mac=None):
        """pairs: [(minor, rssi), ...] — 스캔된 것 하나씩 보낸다(명세 §1).

        건물은 MAC 으로만 정해지므로, 첫 비콘에는 MAC 을 실어 보낸다.
        """
        msgs = []
        for minor, rssi in pairs:
            one = {"major": MAJOR, "minor": minor, "rssi": rssi}
            if mac:
                one["mac"] = mac
            msgs += send(session, {"event": "beacons", "ts": _ms(), "beacons": [one]})
            msgs += _settle(session, one)
        return msgs

    def _settle(session, one):
        """층 확정 대기(FLOOR_SETTLE_MS)를 건너뛴다.

        서버는 건물이 정해진 뒤 잠깐 신호를 모았다가 **가장 센 비콘**으로 층을
        정한다. 테스트에서 1.5초를 실제로 기다릴 이유가 없으므로 시계만 되감고
        같은 패킷을 한 번 더 준다. 대기 자체는 아래 "층 확정과 전환"에서 잰다.
        """
        if session.building_id is None or session.floor_id is not None:
            return []
        session.building_at -= navigation_ws.FLOOR_SETTLE_MS
        return send(session, {"event": "beacons", "ts": _ms(), "beacons": [one]})

    def _ms():
        return int(time.time() * 1000)

    # ── 메시지 모양 ───────────────────────────────────────────
    print("\n── 서버 → 앱 메시지 모양 (명세 §2) ──")
    s = NavSession()
    m = out("ready", "ready", utterance="목적지를 말씀해 주세요.", listen_after=True)
    check(all(f in m for f in REQUIRED), "필수 필드가 모두 있다", ", ".join(REQUIRED))
    check(out("none", "navigating", utterance="")["utterance"] is None,
          "빈 문자열은 null 로 바뀐다", "앱이 빈 발화를 시도하지 않게")

    # ── 층을 모르면 목적지를 못 찾는다 ────────────────────────
    print("\n── 위치를 모를 때 ──")
    r = send(s, {"event": "destination", "text": "407호"})
    check(r and r[0]["event"] == "error", "비콘 전이면 목적지를 안 찾는다", r[0]["event"] if r else "")
    check(r[0]["listenAfter"] is True, "다시 말하라고 마이크를 연다")

    # ── 비콘 → 층 판정 ────────────────────────────────────────
    print("\n── 비콘으로 층을 안다 ──")
    beacons(s, [(1, -50)])
    check(s.floor_id is None, "MAC 전에는 층을 안 정한다", "다른 건물을 집지 않으려고")
    beacons(s, [(1, -50)], mac=FIRST_MAC)
    check(s.floor_id == FLOOR_ID, "MAC 으로 건물·층을 정한다", f"{s.building_id} / {s.floor_id}")
    check("104-1" in s.filters, "비콘 키가 major-minor 다", list(s.filters)[0])

    # ── 목적지 → 경로 → 안내 시작 ─────────────────────────────
    print("\n── 목적지 지정 ──")
    for minor in range(2, 12):
        beacons(s, [(minor, -70)])
    r = send(s, {"event": "destination", "text": "407호", "requestId": "r1"})
    m = r[0]
    check(m["event"] in ("start", "routeFailed"), "경로를 만든다", m["event"])
    if m["event"] == "start":
        check(m["state"] == "navigating", "state 가 navigating")
        check(bool(m["utterance"]), "안내 시작을 말한다", m["utterance"])
        check(m["haptic"] == "guide", "진동 패턴을 준다")
        check(m["screen"]["totalSteps"] and m["screen"]["totalSteps"] > 1,
              "진행 표시용 총 단계 수", str(m["screen"]["totalSteps"]))
    check(m.get("requestId") == "r1", "requestId 를 그대로 돌려준다")

    # ── 되묻기 ────────────────────────────────────────────────
    print("\n── 되묻기 ──")
    s2 = NavSession()
    beacons(s2, [(1, -50)], mac=FIRST_MAC)
    r = send(s2, {"event": "destination", "text": "계단"})
    m = r[0]
    if m["event"] == "disambiguate":
        check(m["listenAfter"] is True, "되물으면 마이크를 연다")
        check(bool(m["screen"]["items"]), "후보를 화면에도 준다",
              f'{len(m["screen"]["items"])}개')
        check(bool(s2.pending), "후보를 세션이 들고 있다", f"{len(s2.pending)}개")
        r2 = send(s2, {"event": "destination", "text": "두 번째"})
        check(r2[0]["event"] in ("start", "routeFailed", "notFound"),
              "답변이 같은 이벤트로 온다", r2[0]["event"])
    else:
        check(True, "후보가 하나뿐이라 바로 확정", m["event"])

    # ── 목록 · 취소 · 재연결 ──────────────────────────────────
    print("\n── 목록 · 취소 · 재연결 ──")
    r = send(s, {"event": "list"})
    check(r[0]["event"] == "list" and r[0]["utterance"] is None,
          "목록은 말하지 않고 화면으로만", f'{len(r[0]["screen"]["items"])}개')
    r = send(s, {"event": "cancel"})
    check(r[0]["state"] == "ready" and not s.tracker.path, "취소하면 경로를 버린다")
    r = send(s, {"event": "resume", "sessionId": s.id})
    check(r[0]["event"] == "resume", "재연결에 현재 상태를 준다", r[0]["state"])

    # ── 세션이 섞이지 않는가 ──────────────────────────────────
    print("\n── 연결마다 따로 (폰 두 대) ──")
    a, b2 = NavSession(), NavSession()
    beacons(a, [(1, -50)], mac=FIRST_MAC)
    beacons(b2, [(5, -50)], mac=FIRST_MAC)
    check(a.id != b2.id, "세션 id 가 다르다")
    check(list(a.filters) != list(b2.filters), "필터가 섞이지 않는다",
          f"{list(a.filters)} vs {list(b2.filters)}")
    a.pending = ["x"]
    check(not b2.pending, "되묻기 후보가 섞이지 않는다")

    # ── state 값이 명세 안에 있는가 ───────────────────────────
    print("\n── 값 검사 ──")
    seen = set()
    for sess, msg in ((s, {"event": "list"}), (s, {"event": "cancel"}),
                      (s, {"event": "resume"})):
        for x in send(sess, msg):
            seen.add(x["state"])
    check(seen <= STATES, "state 가 명세 값만 쓴다", str(sorted(seen)))

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
