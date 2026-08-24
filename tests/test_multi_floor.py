"""층을 넘는 안내가 **한 층짜리 구간 두 개**로 도는지 본다.

    python tests/test_multi_floor.py

여기서 확인하는 것은 하나다 — **사용자에게는 안내가 한 번이어야 한다.**
서버 안에서 구간을 쪼갠 것은 앱이 알 바가 아니므로, 경유지(계단)에 닿아도
`arrived` 가 나가면 안 되고 `state` 도 `navigating` 을 유지해야 한다.

실측 도면 하나를 1층·4층 두 층에 똑같이 깔고 그 사이를 계단으로 잇는다.
도면이 같아도 검사하려는 것(구간 분할·경유지 처리·층 이동 복귀)에는 지장이 없다.
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
    """sqlite 에는 ARRAY 가 없다. JSON 으로 대신한다."""

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

F1, F4 = "f-1", "f-4"
MAJ1, MAJ4 = 101, 104
MAC1 = "AA:01:00:00:00:01"


def find_project():
    for c in (ROOT / "map-tool" / "static" / "mappin_project.json",
              ROOT.parent / "map-tool" / "static" / "mappin_project.json"):
        if c.is_file():
            return c
    return None


def _ms():
    return int(time.time() * 1000)


def main() -> int:  # noqa: C901
    project = find_project()
    if project is None:
        print(f"{BAD} 실측 프로젝트 파일이 없어 실행할 수 없습니다.")
        return 1

    fails, total = [], 0

    def check(ok, label, detail=""):
        nonlocal total
        total += 1
        print(f" {OK if ok else BAD} {label:<46} {detail}")
        if not ok:
            fails.append(label)

    # ── 두 층짜리 건물 ────────────────────────────────────────
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
    dms._GRAPH_CACHE.clear()

    d = json.loads(project.read_text(encoding="utf-8"))
    w, h = d["workW"], d["workH"]
    k = DESIGN_W / w
    scale = d["scale_m_per_px"] * d["origW"] / w
    mask = ("data:image/png;base64," + base64.b64encode(
        gray_alpha_png(base64.b64decode(d["corridorMaskB64"]), w, h)).decode())

    db = S()
    for fid, no, major in ((F1, 1, MAJ1), (F4, 4, MAJ4)):
        db.add(Floor(id=fid, building_id="b", floor=no, major=major,
                     scale_m_per_px=scale))
        db.add(Floorplan(floor_id=fid, image_url="x", extracted=True))
        db.add(FloorMask(floor_id=fid, width=w, height=h, data_url=mask))
        for i, b in enumerate(d["beacons"]):
            db.add(Beacon(id=f"bc{fid}-{i}", floor_id=fid, name=b["id"], minor=i + 1,
                          major=major, type="semantic", x=b["x"] * k, y=b["y"] * k,
                          mac=f"{'AA' if fid == F1 else 'BB'}:01:00:00:00:{i + 1:02X}",
                          source_label=b["id"]))

    # 목적지는 4층에만 둔다 — 1층에서 "407"이라고 말하면 다른 층에서 찾아야 한다.
    for i, lm in enumerate(d["landmarks"]):
        db.add(Landmark(id=f"lm{i}", floor_id=F4, name=lm["name"],
                        x=lm["x"] * k, y=lm["y"] * k))

    # **계단·엘베도 그냥 목적지다.** 연결자 테이블은 없앴다.
    #
    # 1층에 셋을 둔다 — 가까운 계단, 먼 계단, 가까운 계단 바로 옆 엘베.
    # 거리로 고르는지, 엘리베이터를 밀어주는지, 출발 위치에 따라 바뀌는지를 본다.
    near = d["beacons"][0]
    far = d["beacons"][-1]
    db.add(Landmark(id="f1-stair-near", floor_id=F1, name="계단1",
                    x=near["x"] * k, y=near["y"] * k))
    db.add(Landmark(id="f1-stair-far", floor_id=F1, name="계단2",
                    x=far["x"] * k, y=far["y"] * k))
    # 가까운 계단에서 조금 떨어뜨린다(설계도 좌표 60px < ELEVATOR_BONUS_PX 100).
    db.add(Landmark(id="f1-lift", floor_id=F1, name="엘베1",
                    x=near["x"] * k + 60, y=near["y"] * k))
    # 층을 잇지 않는 목적지도 하나 — 후보에 끼면 안 된다.
    db.add(Landmark(id="f1-room", floor_id=F1, name="101",
                    x=near["x"] * k, y=near["y"] * k + 5))
    db.commit()
    db.close()

    from app.nav import legs as legs_mod
    from app.ws.navigation_ws import NavSession, handle

    def send(session, msg):
        return handle(session, json.dumps(msg))

    def feed(session, major, pairs, mac=None):
        msgs = []
        for minor, rssi in pairs:
            one = {"major": major, "minor": minor, "rssi": rssi}
            if mac:
                one["mac"] = mac
            msgs += send(session, {"event": "beacons", "ts": _ms(), "beacons": [one]})
        return msgs

    # ── 구간 쪼개기 ───────────────────────────────────────────
    print("\n── 구간 쪼개기 (app/nav/legs.py) ──")
    db = S()
    same = legs_mod.plan_legs(db, F4, "lm10", "407")
    check(len(same) == 1 and same[0].is_final,
          "같은 층이면 구간이 하나", f"{len(same)}개")

    cross = legs_mod.plan_legs(db, F1, "lm10", "407",
                               origin_x=near["x"] * k, origin_y=near["y"] * k)
    check(len(cross) == 2, "다른 층이면 구간이 둘", " → ".join(x.dest_name for x in cross))
    check(cross[0].is_final is False and cross[1].is_final is True,
          "마지막 구간에만 is_final")
    check(cross[0].next_floor_no == 4, "다음 층 번호를 들고 있다",
          f"{cross[0].next_floor_no}층")
    check("101" not in [x.dest_name for x in cross],
          "층을 안 잇는 목적지는 후보에서 빠진다")

    # 계단이 더 가깝지만 엘베가 ELEVATOR_BONUS_PX 안이면 엘베로 바꾼다.
    check(cross[0].dest_name == "엘베1" and cross[0].connector_type == "elevator",
          "가까운 계단보다 조금 먼 엘베를 고른다", cross[0].dest_name)

    far_first = legs_mod.plan_legs(db, F1, "lm10", "407",
                                   origin_x=far["x"] * k, origin_y=far["y"] * k)
    check(far_first[0].dest_name == "계단2",
          "출발 위치가 바뀌면 고르는 것도 바뀐다", far_first[0].dest_name)
    check(far_first[0].connector_type == "stairs",
          "멀리 있는 엘베까지 끌려가지 않는다", far_first[0].connector_type)

    speech = cross[0].handoff_speech()
    check("도착" not in speech and "4층" in speech and "엘리베이터" in speech,
          "경유지 문구가 도착이 아니고 종류를 말한다", speech)

    # 이름·분류로 종류를 가린다 (map_source.connector_kind)
    from app.nav.map_source import connector_kind
    check(connector_kind("계단1") == "stairs", "이름으로 계단을 가린다")
    check(connector_kind("엘베2") == "elevator", "이름으로 엘베를 가린다")
    check(connector_kind("엘리베이터 1호기") == "elevator", "긴 이름도 가린다")
    check(connector_kind("407") is None, "방 번호는 층을 안 잇는다")
    check(connector_kind("1번 출입구", "계단") == "stairs", "분류로도 가린다")
    check(connector_kind("엘리베이터 옆 계단") == "elevator",
          "둘 다 걸리면 엘리베이터", "계단으로 안내했다가 못 오르는 쪽이 위험")

    # 층을 잇는 목적지가 하나도 없는 층에서는 이유를 말한다.
    # 5층은 목적지를 하나도 안 넣었다.
    db.add(Floor(id="f-5", building_id="b", floor=5, major=105,
                 scale_m_per_px=scale))
    db.commit()
    try:
        legs_mod.plan_legs(db, "f-5", "lm10", "407")
        err = ""
    except Exception as e:
        err = str(e)
    check("계단이나 엘리베이터" in err,
          "층을 잇는 목적지가 없으면 이유를 말한다", err.splitlines()[0] if err else "예외 없음")
    db.close()

    # ── 실제 세션 ─────────────────────────────────────────────
    print("\n── 1층에서 407호까지 ──")
    s = NavSession()
    feed(s, MAJ1, [(26, -45)], mac=MAC1)
    check(s.floor_id == F1, "1층으로 판정", s.floor_id or "?")
    # 예전에는 첫 층만 MAC 으로 정하느라 여기서 major 가 None 이었고, 그 탓에
    # 출발점을 고를 때 층을 못 걸렀다.
    check(s.major == MAJ1, "출발 층에서도 major 를 들고 있다", str(s.major))

    r = send(s, {"event": "destination", "id": "lm10"})
    check(len(s.legs) == 2, "구간 두 개가 걸렸다", f"{len(s.legs)}개")
    check(s.leg_index == 0 and s.legs[0].floor_id == F1,
          "첫 구간은 1층")
    start = r[-1] if r else {}
    check(start.get("state") == "navigating", "안내 시작", start.get("state"))
    shown = (start.get("screen") or {}).get("title")
    check(shown == s.destination.name,
          "화면에는 최종 목적지를 띄운다", f"{shown} (경유지 {s.legs[0].dest_name} 아님)")

    # 경유지 도달 — 마지막 칸으로 강제로 밀어 판정을 일으킨다.
    print("\n── 계단에 닿았을 때 ──")
    keys = list(s.tracker.path)
    check(len(keys) >= 2, "1층 경로가 두 칸 이상", f"{len(keys)}칸")
    # 마지막 두 칸만 남기고 앞으로 밀어둔 뒤, 신호가 교차하도록 먹인다.
    # **시각을 직접 준다** — 추세도 확인 대기(500ms)도 데이터 시각으로 재기 때문에
    # 같은 순간에 몰아 넣으면 아무 판정도 안 난다.
    s.tracker.index = len(keys) - 2
    from app.ws.navigation_ws import _transition_message

    msgs = []
    t0 = _ms()
    for i in range(120):
        t = t0 + i * 100
        # 앞 칸은 멀어지고 다음 칸은 가까워진다
        s.tracker.feed(keys[-2], -55.0 - i * 0.4, now_ms=t)
        s.tracker.feed(keys[-1], -80.0 + i * 0.4, now_ms=t)
        tr = s.tracker.evaluate()
        if tr is not None:
            msgs += _transition_message(s, tr)
            if tr.get("isLast"):
                break

    check(bool(msgs), "판정이 났다", f"{len(msgs)}건")
    last = msgs[-1] if msgs else {}
    check(last.get("state") == "navigating",
          "경유지에서 state 는 navigating", last.get("state"))
    check(last.get("event") != "arrive",
          "경유지에서 arrive 를 안 보낸다", last.get("event"))
    check("도착했습니다" not in (last.get("utterance") or ""),
          "도착했다고 말하지 않는다", last.get("utterance"))
    check(s.awaiting_floor == F4, "4층 신호를 기다리는 중",
          s.awaiting_floor or "안 기다림")
    check(s.tracker.active is False, "판정을 멈췄다",
          "층 이동 구간이 이탈로 읽히지 않게")

    # 층 이동 중 — 지나가는 층 신호
    print("\n── 층을 옮기는 동안 ──")
    before = s.leg_index
    feed(s, MAJ4 - 1, [(26, -70)])   # 3층을 스쳐 지나간다
    check(s.leg_index == before and s.awaiting_floor == F4,
          "지나가는 층에서는 안 움직인다", f"구간 {s.leg_index}")

    # 4층 도착
    print("\n── 4층에 내렸을 때 ──")
    r = feed(s, MAJ4, [(26, -45)])
    check(s.floor_id == F4, "4층으로 판정", s.floor_id or "?")
    check(s.leg_index == 1, "다음 구간으로 넘어갔다", f"구간 {s.leg_index + 1}")
    check(s.awaiting_floor is None, "더 기다리지 않는다")
    started = [m for m in r if m.get("event") == "start"]
    check(bool(started), "4층 안내가 시작됐다", f"{len(started)}건")
    if started:
        u = started[-1].get("utterance") or ""
        check("407" in u or (s.destination and s.destination.name in u),
              "최종 목적지를 다시 짚어준다", u[:40])
        check("손이 닿는 벽" not in u,
              "이미 하고 있는 것은 다시 말하지 않는다")
    check(s.tracker.active is True, "판정을 다시 켰다")
    check(all(navmod.key_major(x) == MAJ4 for x in s.tracker.path),
          "경로가 4층 비콘으로만 채워졌다",
          f"{len(s.tracker.path)}칸")

    # ── 떠나온 층 필터가 출발점을 가로채지 않는가 ─────────────
    print("\n── 떠나온 층의 필터 ──")
    strongest_any = navmod.strongest_beacon_key(s.filters)
    strongest_f4 = navmod.strongest_beacon_key(s.filters, MAJ4)
    check(navmod.key_major(strongest_any) == MAJ1,
          "필터만 보면 1층 비콘이 아직 제일 세다", strongest_any)
    check(navmod.key_major(strongest_f4) == MAJ4,
          "major 를 주면 4층 것만 고른다", strongest_f4)

    print(f"\n{'전체' if not fails else '실패'} "
          f"{total - len(fails)}/{total}개 통과 {OK if not fails else BAD}")
    if fails:
        for f in fails:
            print(f"   - {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
