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
from app.connector.models import Connector, ConnectorPosition  # noqa: E402
from app.database import Base  # noqa: E402
from app.floor.models import Floor  # noqa: E402
from app.floorplan.models import Floorplan  # noqa: E402
from app.landmark.models import Landmark  # noqa: E402
from app.mask.models import FloorMask  # noqa: E402
from app.nav.db_map_source import DESIGN_W  # noqa: E402
from app.nav.map_source import MapDataError  # noqa: E402
from app.ws.navigation_ws import FLOOR_SWITCH_DWELL_MS  # noqa: E402
from app.ws.rssi_filter import RssiFilterPipeline  # noqa: E402
from tests.seed_from_project import gray_alpha_png  # noqa: E402

OK, BAD = "✓", "✗"

F1, F3, F4 = "f-1", "f-3", "f-4"
MAJ1, MAJ3, MAJ4 = 101, 103, 104
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
    # 3층은 목적지도 연결자도 없다. **엘리베이터가 지나치거나 잘못 내리는 층**을
    # 재려고 둔다 — 두 층만 있으면 "기다리던 층이 아니다"를 만들 수가 없다.
    for fid, no, major in ((F1, 1, MAJ1), (F3, 3, MAJ3), (F4, 4, MAJ4)):
        db.add(Floor(id=fid, building_id="b", floor=no, major=major,
                     scale_m_per_px=scale))
        db.add(Floorplan(floor_id=fid, image_url="x", extracted=True))
        db.add(FloorMask(floor_id=fid, width=w, height=h, data_url=mask))
        for i, b in enumerate(d["beacons"]):
            db.add(Beacon(id=f"bc{fid}-{i}", floor_id=fid, name=b["id"], minor=i + 1,
                          major=major, type="semantic", x=b["x"] * k, y=b["y"] * k,
                          mac=f"{'AA' if fid == F1 else 'BB'}:0{no}:00:00:00:{i + 1:02X}",
                          source_label=b["id"]))

    # 목적지는 4층에만 둔다 — 1층에서 "407호"라고 말하면 다른 층에서 찾아야 한다.
    for i, lm in enumerate(d["landmarks"]):
        db.add(Landmark(id=f"lm{i}", floor_id=F4, name=lm["name"],
                        x=lm["x"] * k, y=lm["y"] * k))

    # 두 층을 잇는 계단 둘. 하나는 가깝고 하나는 멀다 — 가까운 쪽을 고르는지 본다.
    near = d["beacons"][0]
    far = d["beacons"][-1]
    db.add(Connector(id="cn-near", building_id="b", name="계단1",
                     type="stairs", floors=[1, 3, 4]))
    db.add(Connector(id="cn-far", building_id="b", name="계단2",
                     type="stairs", floors=[1, 3, 4]))
    for cid, pt in (("cn-near", near), ("cn-far", far)):
        for fid in (F1, F3, F4):
            db.add(ConnectorPosition(connector_id=cid, floor_id=fid,
                                     x=pt["x"] * k, y=pt["y"] * k))

    # 1층만 운행하는 연결자 — 후보로 뽑히면 안 된다.
    db.add(Connector(id="cn-solo", building_id="b", name="계단3",
                     type="stairs", floors=[1]))
    db.add(ConnectorPosition(connector_id="cn-solo", floor_id=F1,
                             x=near["x"] * k, y=near["y"] * k))
    db.commit()
    db.close()

    from app.nav import legs as legs_mod
    from app.ws import navigation_ws as nav_ws
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
            if session.building_id is not None and session.floor_id is None:
                # 층 확정 대기(FLOOR_SETTLE_MS)를 시계 되감기로 건너뛴다.
                session.building_at -= nav_ws.FLOOR_SETTLE_MS
                msgs += send(session, {"event": "beacons", "ts": _ms(), "beacons": [one]})
        return msgs

    def _items_here(session):
        """지금 층 목적지만. `_building_items` 와 비교하려고 둔다."""
        return nav_ws._items(nav_ws._load_landmarks(session))

    def hold_floor(session):
        """층 전환 유지시간(FLOOR_SWITCH_DWELL_MS)을 시계 되감기로 채운다.

        서버는 새 층이 **여유를 두고 이기는** 상태가 이만큼 이어져야 층을 바꾼다.
        엘리베이터 앞에 서 있기만 해도 위아래 층 신호가 새는데, 한 패킷만 보고
        바꾸면 거기 서 있는 것만으로 다음 구간이 시작되기 때문이다.
        """
        session.floor_cand_at -= nav_ws.FLOOR_SWITCH_DWELL_MS

    def leave_floor(session, major):
        """그 층 비콘이 더는 안 들리는 상태로 만든다.

        엘리베이터 문이 닫히면 실제로 이렇게 된다. 필터값은 그대로 남아 있으므로
        (칼만은 시간이 지난다고 값을 안 깎는다) **마지막으로 들은 시각**만 되감는다.
        서버가 그 유령값을 걷어내는지 여기서 갈린다.
        """
        for k in session.last_seen:
            if navmod.key_major(k) == major:
                session.last_seen[k] -= nav_ws.BEACON_STALE_MS + 1

    # ── 구간 쪼개기 ───────────────────────────────────────────
    print("\n── 구간 쪼개기 (app/nav/legs.py) ──")
    db = S()
    same = legs_mod.plan_legs(db, F4, "lm10", "407")
    check(len(same) == 1 and same[0].is_final,
          "같은 층이면 구간이 하나", f"{len(same)}개")

    cross = legs_mod.plan_legs(db, F1, "lm10", "407",
                               origin_x=near["x"] * k, origin_y=near["y"] * k)
    check(len(cross) == 2, "다른 층이면 구간이 둘", " → ".join(x.dest_name for x in cross))
    check(cross[0].dest_name == "계단1",
          "가까운 연결자를 고른다", f"고른 것: {cross[0].dest_name}")
    check(cross[0].is_final is False and cross[1].is_final is True,
          "마지막 구간에만 is_final")
    check(cross[0].next_floor_no == 4, "다음 층 번호를 들고 있다",
          f"{cross[0].next_floor_no}층")
    check("계단3" not in [x.dest_name for x in cross],
          "한 층만 운행하는 연결자는 후보에서 빠진다")

    far_first = legs_mod.plan_legs(db, F1, "lm10", "407",
                                   origin_x=far["x"] * k, origin_y=far["y"] * k)
    check(far_first[0].dest_name == "계단2",
          "출발 위치가 바뀌면 고르는 연결자도 바뀐다", far_first[0].dest_name)

    speech = cross[0].handoff_speech()
    check("도착" not in speech and "4층" in speech,
          "경유지 문구가 도착이 아니다", speech)
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
    feed(s, MAJ3, [(26, -70)])       # 3층을 스쳐 지나간다
    check(s.leg_index == before and s.awaiting_floor == F4,
          "지나가는 층에서는 안 움직인다", f"구간 {s.leg_index}")

    # 4층 도착
    print("\n── 4층에 내렸을 때 ──")
    leave_floor(s, MAJ1)             # 문이 닫혀 1층이 안 들린다
    feed(s, MAJ4, [(26, -45)])       # 후보가 선다 — 아직 안 바뀐다
    check(s.floor_id == F1, "한 패킷으로는 층이 안 바뀐다", s.floor_id or "?")
    hold_floor(s)
    r = feed(s, MAJ4, [(26, -45)])   # 계속 이겼으니 전환
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

    # ── 엘리베이터 앞에 서 있기만 할 때 ───────────────────────
    #
    # 문 앞에 서 있어도 위아래 층 신호가 샌다. **들리기만 하면 안 된다** — 앱은
    # -90dB 유령 신호까지 전부 올리므로 그것만으로 층이 바뀌거나, 반대로 그것
    # 때문에 전환이 영영 막힌다. 실측에서 33dB 차이를 두고도 20초가 걸렸다.
    print("\n── 엘리베이터 앞 (약하게 새는 신호) ──")
    e = NavSession()
    feed(e, MAJ1, [(26, -45)], mac=MAC1)
    check(e.floor_id == F1, "1층에서 시작", e.floor_id or "?")

    feed(e, MAJ4, [(26, -80)])       # 위층이 슬래브 너머로 약하게
    check(e.floor_cand is None, "약한 신호로는 후보가 안 선다", str(e.floor_cand))
    feed(e, MAJ4, [(26, -80)])
    feed(e, MAJ4, [(26, -80)])
    check(e.floor_id == F1, "서 있기만 해서는 층이 안 바뀐다", e.floor_id or "?")

    # 여유(FLOOR_WIN_DB)를 못 넘는 차이도 마찬가지다
    feed(e, MAJ4, [(27, -42)])       # -45 를 3dB 이김 — 모자라다
    check(e.floor_cand is None, "여유를 못 넘으면 후보가 안 선다", str(e.floor_cand))

    # 문이 닫히고 1층이 끊긴다 — 겨룰 상대가 없으면 곧바로 후보가 선다
    leave_floor(e, MAJ1)
    feed(e, MAJ4, [(26, -80)])
    check(e.floor_cand == MAJ4, "지금 층이 끊기면 약해도 후보가 선다", str(e.floor_cand))
    check(e.floor_id == F1, "그래도 유지시간은 채워야 한다", e.floor_id or "?")
    hold_floor(e)
    feed(e, MAJ4, [(26, -80)])
    check(e.floor_id == F4, "유지되면 바뀐다", e.floor_id or "?")

    # ── 세게 이기면 유령 신호가 있어도 넘어간다 ────────────────
    #
    # 이게 실측에서 20초 걸리던 자리다. 내려서 나오면 새 층이 같은 방에 있고
    # 옛 층은 슬래브 너머라, 신호가 계속 들어와도 세기로 갈린다.
    print("\n── 유령 신호가 있어도 ──")
    g = NavSession()
    feed(g, MAJ1, [(26, -45)], mac=MAC1)
    feed(g, MAJ1, [(27, -79)])       # 1층이 계속 들린다. 다만 약해졌다
    g.last_seen[f"{MAJ1}-26"] -= nav_ws.BEACON_STALE_MS + 1   # 가까운 것만 끊김
    feed(g, MAJ4, [(26, -46)])       # 새 층이 같은 방 — 33dB 차
    check(g.floor_cand == MAJ4, "세게 이기면 후보가 선다", str(g.floor_cand))
    hold_floor(g)
    feed(g, MAJ4, [(26, -46)])
    check(g.floor_id == F4, "옛 층이 계속 들려도 넘어간다", g.floor_id or "?")

    # ── 층을 정하기 전에는 목적지를 안 묻는다 ─────────────────
    #
    # 예전에는 비콘 하나하나마다 층을 즉시 따라가서, 두 층 신호가 번갈아 잡히면
    # 매 패킷마다 층이 뒤집혔다. 그때마다 "목적지를 말씀해 주세요"가 다시 나가
    # 폰이 마이크를 새로 열었고, **사용자가 말을 시작할 수가 없었다.**
    print("\n── 층 확정 전 ──")
    w = NavSession()
    send(w, {"event": "beacons", "ts": int(time.time() * 1000),
             "beacons": [{"major": MAJ1, "minor": 26, "rssi": -45, "mac": MAC1}]})
    check(w.building_id is not None, "건물은 MAC 으로 바로 정해진다")
    check(w.floor_id is None, "층은 아직 안 정해진다", w.floor_id or "미정")
    r = send(w, {"event": "list"})
    check(r[0]["utterance"] and "확인하고" in r[0]["utterance"],
          "위치를 확인 중이라고 답한다", r[0]["utterance"])

    # 모으는 동안 위층이 약하게 섞여도, 가장 센 것으로 정한다
    send(w, {"event": "beacons", "ts": int(time.time() * 1000),
             "beacons": [{"major": MAJ4, "minor": 26, "rssi": -85}]})
    w.building_at -= nav_ws.FLOOR_SETTLE_MS
    send(w, {"event": "beacons", "ts": int(time.time() * 1000),
             "beacons": [{"major": MAJ1, "minor": 26, "rssi": -45}]})
    check(w.floor_id == F1, "가장 센 비콘의 층으로 정한다", w.floor_id or "?")

    # ── 목적지 목록은 건물 전체 ───────────────────────────────
    #
    # 화면으로 고르는 사용자가 다른 층에 아예 갈 수 없으면 음성이 안 되는 자리에서
    # 갇힌다. 대신 다른 층 것에는 층을 붙인다 — "화장실"이 여러 개 나열되면
    # 화면으로도 소리로도 가릴 수가 없다.
    print("\n── 목적지 목록 ──")
    items = nav_ws._building_items(w)
    names = [x["name"] for x in items]
    check(any("층)" in n for n in names), "다른 층 목적지에는 층을 붙인다",
          next((n for n in names if "층)" in n), "없음"))
    check(any("(" not in n for n in names), "지금 층 것에는 안 붙인다",
          next((n for n in names if "(" not in n), "없음"))
    check(len(items) > len(_items_here(w)), "이 층 것보다 많다",
          f"{len(_items_here(w))} → {len(items)}")

    # ── 다음 구간을 못 만들면 되돌린다 ────────────────────────
    #
    # 층은 바뀌었는데 그 층 비콘이 아직 안 잡히면 _start_leg 이 실패한다. 그때
    # routeFailed(listenAfter=true)를 내보내면 사용자는 엘리베이터에서 내리는
    # 중인데 목적지를 다시 말하라는 소리를 듣는다 — 안내가 처음으로 돌아갔다.
    print("\n── 다음 구간을 못 만들 때 ──")
    b = NavSession()
    feed(b, MAJ1, [(26, -45)], mac=MAC1)
    send(b, {"event": "destination", "id": "lm10"})
    b.awaiting_floor = F4
    b.tracker.active = False
    keep_dest, keep_leg = b.destination, b.leg_index

    # 그 층 경로를 못 만드는 상황을 만든다. 실제로는 4층 비콘이 아직 하나도
    # 안 잡힌 순간이 여기 해당한다.
    real_plan = navmod.plan_route

    def boom(*a, **kw):
        raise MapDataError("지금 잡히는 비콘이 없어 출발점을 알 수 없습니다.")

    navmod.plan_route = boom
    try:
        leave_floor(b, MAJ1)
        feed(b, MAJ4, [(26, -45)])
        hold_floor(b)
        out_msgs = feed(b, MAJ4, [(26, -45)])
    finally:
        navmod.plan_route = real_plan

    check(b.floor_id == F4, "층은 바뀌었다", b.floor_id or "?")
    check(not any(m.get("event") in ("routeFailed", "error") for m in out_msgs),
          "실패를 앱에 흘리지 않는다", str([m.get("event") for m in out_msgs]))
    check(b.leg_index == keep_leg, "구간 번호를 되돌린다", f"구간 {b.leg_index + 1}")
    check(b.destination is keep_dest, "목적지를 그대로 들고 있다",
          "_start_leg 이 지웠을 수 있다")
    check(b.awaiting_floor == F4, "계속 기다린다", b.awaiting_floor or "안 기다림")

    # 신호가 더 잡히면 다음 패킷에서 다시 시도한다
    hold_floor(b)
    again = feed(b, MAJ4, [(27, -45)])
    check(b.leg_index == keep_leg + 1 or any(m.get("event") == "start" for m in again),
          "다시 시도해서 걸린다", f"구간 {b.leg_index + 1}")

    # ── 엉뚱한 층에 내렸을 때 ─────────────────────────────────
    #
    # 기다리던 층이 아닌 곳에 내리면 거기서 다시 계획할 수도 있다. 그런데 지금은
    # **꺼 두었다**(`REPLAN_ON_WRONG_FLOOR`). 엘리베이터가 지나치는 층을 도착으로
    # 읽으면 아직 타고 있는데 경로가 새로 짜이고, 그 층 비콘이 제대로 안 잡혀
    # 대개 실패한다. 꺼도 갇히지 않는다 — 목표 층으로 가면 이어진다.
    print("\n── 엉뚱한 층에 내렸을 때 ──")
    x = NavSession()
    feed(x, MAJ1, [(26, -45)], mac=MAC1)
    send(x, {"event": "destination", "id": "lm10"})
    x.awaiting_floor = F4
    x.tracker.active = False
    x.leg_index = 0

    # 3층에 내렸다 — 기다리던 4층이 아니다
    leave_floor(x, MAJ1)
    feed(x, MAJ3, [(26, -45)])
    hold_floor(x)
    feed(x, MAJ3, [(26, -45)])
    check(x.floor_id == F3, "3층으로 판정은 한다", x.floor_id or "?")

    x.floor_since -= nav_ws.WRONG_FLOOR_DWELL_MS
    r = feed(x, MAJ3, [(26, -45)])
    check(x.awaiting_floor == F4, "그래도 4층을 계속 기다린다",
          x.awaiting_floor or "안 기다림")
    check(x.leg_index == 0, "구간을 새로 짜지 않는다", f"구간 {x.leg_index + 1}")
    check(not any(m.get("event") in ("start", "routeFailed") for m in r),
          "앱에 아무것도 안 나간다", str([m.get("event") for m in r]))

    # 켜면 그 층에서 다시 계획한다
    nav_ws.REPLAN_ON_WRONG_FLOOR = True
    try:
        x.floor_since -= nav_ws.WRONG_FLOOR_DWELL_MS
        r = feed(x, MAJ3, [(26, -45)])
    finally:
        nav_ws.REPLAN_ON_WRONG_FLOOR = False
    check(x.awaiting_floor is None, "켜면 기다리기를 접는다",
          x.awaiting_floor or "접었다")
    check(x.destination is not None and x.destination.name == "407",
          "최종 목적지는 그대로", x.destination.name if x.destination else "없음")
    check(bool(x.legs) and x.legs[0].floor_id == F3,
          "지금 층에서 다시 계획한다",
          " → ".join(l.dest_name for l in x.legs) if x.legs else "없음")

    # ── 실측 로그 재생 (2026-08-26) ───────────────────────────
    #
    # 그날 로그를 그대로 되돌린다. 5초마다 찍힌 필터값이고, 3층(103)에서
    # 엘리베이터로 4층(104)에 내린 구간이다. **그때는 전환에 20초가 걸렸다.**
    #
    #     t=25  103-1:-77  104-1:-62   ← 문 열림. 15dB 차인데도 안 바뀜
    #     t=40  103-1:-79  104-1:-46   ← 33dB 차. 그래도 안 바뀜
    #     t=45+                        ← 여기서야 바뀜
    #
    # 옛 규칙은 103 패킷이 한 번이라도 오면 시계를 0으로 돌려서, 3초짜리 깨끗한
    # 창이 우연히 열릴 때까지 기다린 것이다. 세기로 겨루면 문 열리자마자 갈린다.
    print("\n── 실측 로그 재생 (엘리베이터 5층→4층) ──")

    def at(session, now, levels):
        """그 시점의 필터값을 그대로 심는다. 칼만을 거치지 않고 결과만 본다."""
        for key, v in levels.items():
            pipe = session.filters.setdefault(key, RssiFilterPipeline())
            pipe.x, pipe.initialized = float(v), True
            session.last_seen[key] = now
        return nav_ws._locate_floor(session, now)

    L = NavSession()
    L.building_id, L.floor_id, L.major = "b", F1, MAJ1
    t = 1_000_000

    # 엘리베이터 앞. 지금 층이 훨씬 세다
    at(L, t, {f"{MAJ1}-1": -60, f"{MAJ4}-1": -85, f"{MAJ4}-2": -86})
    check(L.floor_cand is None, "엘베 앞에서는 후보가 안 선다", str(L.floor_cand))

    # 문이 닫힌다. 103 만 약해지고 104 는 그대로 약하다
    at(L, t + 15_000, {f"{MAJ1}-1": -75})
    at(L, t + 20_000, {f"{MAJ1}-1": -77})
    check(L.floor_id == F1, "타고 가는 동안은 그대로", L.floor_id or "?")

    # 문이 열린다 — 104-1 이 -62. 15dB 차
    moved = at(L, t + 25_000, {f"{MAJ1}-1": -77, f"{MAJ4}-1": -62,
                               f"{MAJ4}-2": -79, f"{MAJ4}-3": -86})
    check(L.floor_cand == MAJ4, "문 열리는 즉시 후보가 선다", str(L.floor_cand))
    check(not moved, "그래도 유지시간은 채운다")

    # 3초 뒤. 103 은 여전히 들리지만 세기로 진다
    moved = at(L, t + 28_500, {f"{MAJ1}-1": -76, f"{MAJ4}-1": -61})
    check(moved and L.floor_id == F4, "3.5초 만에 4층으로", L.floor_id or "?")

    took = 28_500 - 25_000
    check(took <= FLOOR_SWITCH_DWELL_MS + 1_000,
          "옛 규칙의 20초가 유지시간만큼으로 줄었다", f"{took / 1000:.1f}초")

    # ── 안내를 언제 말하나 ────────────────────────────────────
    #
    # 안내는 비콘 하나를 통째로 소유한다. 그래서 비콘에 닿는 순간 그 칸의 말이 전부
    # 나가고, 정작 회전은 20m 뒤일 수 있다. **어느 비콘이 무엇을 말할지는 그대로
    # 두고, 그 안에서 언제 입을 여는지만** 옮기는 것이 늦춰 말하기다.
    print("\n── 안내 발화 시점 ──")
    from app.ws import handler as h

    turn20 = nav_ws.SpokenCue(text="조금 뒤 오른쪽으로 꺾으세요.",
                              base="오른쪽으로 꺾으세요.", lead_m=20.0, kind="turn")
    turn3 = nav_ws.SpokenCue(text="왼쪽으로 꺾으세요.",
                             base="왼쪽으로 꺾으세요.", lead_m=3.0, kind="turn")
    straight = nav_ws.SpokenCue(text="계속 직진하세요.", base="계속 직진하세요.",
                                lead_m=18.0, kind="straight")

    keep = h._cue_pacing
    try:
        # 기본 모드 — 지금과 완전히 같아야 한다
        h._cue_pacing = {"enabled": False}
        say, later = nav_ws._split_cues(NavSession(), [turn20, turn3, straight], 0)
        check(say == [turn20.text, turn3.text, straight.text] and not later,
              "기본 모드는 전부 그 자리에서 말한다", f"{len(say)}개 · 미룸 {len(later)}개")

        # 늦춤 모드
        h._cue_pacing = {"enabled": True, "speed_mps": 1.0, "speak_at_m": 5.0}
        say, later = nav_ws._split_cues(NavSession(), [turn20, turn3, straight], 0)
        check(turn3.text in say, "이미 가까운 것은 안 미룬다", str(say))
        check(straight.text in say, "직진은 안 미룬다 — 지점을 가리키는 말이 아니다")
        check(turn20.text not in say, "먼 회전은 그 자리에서 말하지 않는다")
        check(len(later) == 1, "미룬 것이 하나", f"{len(later)}개")

        at, text = later[0]
        check(at == 15_000, "20m 를 1m/s 로 걸어 5m 남을 때까지", f"{at / 1000:.0f}초 뒤")
        check(text == "오른쪽으로 꺾으세요.",
              "거리 표현을 그 시점 것으로 다시 만든다", text)
        check("조금 뒤" not in text,
              "5m 앞에서는 '조금 뒤'가 아니다 — 지금 할 일이다", text)

        # 속도를 빠르게 잡으면 일찍 말한다 (안전한 쪽)
        h._cue_pacing = {"enabled": True, "speed_mps": 2.0, "speak_at_m": 5.0}
        _, later2 = nav_ws._split_cues(NavSession(), [turn20], 0)
        check(later2[0][0] == 7_500, "속도를 빠르게 잡으면 일찍 말한다",
              f"{later2[0][0] / 1000:.1f}초 뒤")

        # 남은 거리를 크게 잡으면 거리 표현이 다시 붙는다
        h._cue_pacing = {"enabled": True, "speed_mps": 1.0, "speak_at_m": 10.0}
        _, later3 = nav_ws._split_cues(NavSession(), [turn20], 0)
        check(later3[0][1] == "조금 뒤 오른쪽으로 꺾으세요.",
              "남은 거리에 맞는 표현이 붙는다", later3[0][1])

        # 상한
        h._cue_pacing = {"enabled": True, "speed_mps": 0.1, "speak_at_m": 5.0}
        _, later4 = nav_ws._split_cues(NavSession(), [turn20], 0)
        check(later4[0][0] == nav_ws.CUE_MAX_HOLD_MS,
              "아무리 길어도 상한까지", f"{later4[0][0] / 1000:.0f}초")

        # 때가 돼야 나온다
        q = NavSession()
        q.due = [(1_000, "오른쪽으로 꺾으세요."), (5_000, "왼쪽으로 꺾으세요.")]
        check(nav_ws._take_due(q, 500) == [], "때가 안 되면 안 나온다")
        check(nav_ws._take_due(q, 1_000) == ["오른쪽으로 꺾으세요."],
              "때가 된 것만 나온다")
        check(len(q.due) == 1, "나머지는 남는다", f"{len(q.due)}개")

        # 다음 비콘에 먼저 닿으면 남은 것을 그때 다 내보낸다 — 버리지 않는다.
        # 예상보다 빨리 걸었을 뿐이고 그 회전은 여전히 앞에 있다.
        check(nav_ws._take_due(q, 0, force=True) == ["왼쪽으로 꺾으세요."],
              "다음 비콘에서 남은 것을 같이 내보낸다")
        check(not q.due, "그러고 나면 비어 있다")
    finally:
        h._cue_pacing = keep

    # 실제 세션에서 한 바퀴 — 미룬 것이 다음 전이에 실려 나가는지
    print("\n── 늦춰 말하기 (세션) ──")
    keep = h._cue_pacing
    try:
        h._cue_pacing = {"enabled": True, "speed_mps": 1.0, "speak_at_m": 5.0}
        n = NavSession()
        feed(n, MAJ4, [(26, -45)], mac="BB:04:00:00:00:1B")
        r = send(n, {"event": "destination", "id": "lm10"})
        started = [m for m in r if m.get("event") == "start"]
        check(bool(started), "4층에서 바로 안내가 걸린다", str([m.get("event") for m in r]))

        # 미뤄둔 것이 있으면 다음 전이에 실려 나가야 한다
        n.due = [(nav_ws._now_ms() + 60_000, "오른쪽으로 꺾으세요.")]
        msgs = nav_ws._transition_message(n, {"number": 2, "total": 5,
                                              "direction": "forward", "isLast": False})
        said = msgs[0].get("utterance") or ""
        check("오른쪽으로 꺾으세요." in said,
              "때가 안 됐어도 다음 비콘에서 같이 나간다", said[:40])
        # 큐에는 **그 칸에서 새로 미룬 것**만 남는다. 내보낸 것이 다시 들어가면 안 된다.
        check(all("오른쪽으로 꺾으세요." != t for _, t in n.due),
              "내보낸 것은 큐에 안 남는다",
              " / ".join(t for _, t in n.due) or "비었음")

        # 경로를 벗어나면 버린다 — 안 가는 길의 회전이다
        n.due = [(nav_ws._now_ms() + 60_000, "오른쪽으로 꺾으세요.")]
        nav_ws._transition_message(n, {"number": 1, "total": 5,
                                       "direction": "back", "isLast": False})
        check(not n.due, "경로를 벗어나면 미룬 것을 버린다", f"{len(n.due)}개")
    finally:
        h._cue_pacing = keep

    print(f"\n{'전체' if not fails else '실패'} "
          f"{total - len(fails)}/{total}개 통과 {OK if not fails else BAD}")
    if fails:
        for f in fails:
            print(f"   - {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
