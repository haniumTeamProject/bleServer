"""사용자 앱과의 WebSocket — `/ws/navigation`.

규약은 `docs/사용자앱_API_명세.md` 다. 여기서는 그것을 그대로 구현한다.

── 앱은 도구다 ────────────────────────────────────────────────────

앱은 판단하지 않는다. 서버가 "무엇을 말하고, 언제 들을지"를 정하고 앱은 실행한다.
그래서 **서버가 보내는 메시지는 모양이 하나**다. 앱은 `event` 로 분기하지 않아도
동작한다.

    utterance     읽을 문장. null 이면 아무 말도 하지 않는다
    listenAfter   true 면 발화가 끝난 뒤 마이크를 연다
    haptic        진동 패턴
    state         보여줄 화면
    screen        화면에 띄울 것

이렇게 두는 이유는 고칠 곳을 하나로 모으기 위해서다. 안내 문구나 매칭 규칙을
앱에 두면 손볼 때마다 앱을 다시 빌드해 배포하고 사용자가 업데이트하기를 기다려야
한다. 실측 한 번에 하루가 간다.

── `/ws` 와 무엇이 다른가 ────────────────────────────────────────

    /ws              붙어 있는 전부에게 뿌린다.  전역 필터·추적기 하나.
    /ws/navigation   그 연결에만 보낸다.        연결마다 필터·추적기.

`/ws` 는 `/monitor` 가 폰의 RSSI 를 봐야 해서 일부러 브로드캐스트로 만들었다.
사용자앱을 거기 붙이면 남의 RSSI 를 초당 수십 개씩 받아 버려야 하고, 반대로
사용자앱 메시지가 모니터로 샌다.

**연결마다 상태를 두는 것이 이 엔드포인트의 핵심이다.** 전역 하나면 폰 두 대가
붙는 순간 서로의 되묻기 후보와 경로가 섞인다.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.nav.map_source import MapDataError
from app.ws import landmark_matcher, llm_matcher, monitor_mirror, nav_recorder, navigation
from app.ws.path_tracker import PathTracker
from app.ws.rssi_filter import RssiFilterPipeline

router = APIRouter()

# 되묻고 나서 이 시간이 지나면 후보를 버린다. 답을 안 하고 한참 뒤에 다른 말을 하면
# 그건 되묻기 답변이 아니라 새 요청이다.
PENDING_TTL_MS = 120_000

# 목적지 목록을 잠깐 들고 있는다. 관리자가 목적지를 추가하면 이 시간 안에 반영된다.
LANDMARK_TTL_MS = 10_000


class NavSession:
    """연결 하나 = 사용자 한 명의 안내 세션."""

    def __init__(self) -> None:
        self.id = "s-" + uuid.uuid4().hex[:8]
        # 비콘 키는 "major-minor" 다. 앱이 major/minor 를 그대로 보내주므로
        # `/ws` 처럼 "MAC|이름" 을 쓸 이유가 없다.
        self.filters: dict[str, RssiFilterPipeline] = {}
        self.beacon_ids: dict[str, dict] = {}
        self.tracker = PathTracker()

        # 건물은 MAC 으로 한 번만 정하고, 층은 그 건물 안에서 major 로 따라간다.
        self.building_id: str | None = None
        self.floor_id: str | None = None
        self.major: int | None = None
        self.landmarks: list[landmark_matcher.Landmark] = []
        self.landmarks_at: float = 0.0

        self.pending: list[landmark_matcher.Landmark] = []
        self.pending_at: int = 0

        self.destination: landmark_matcher.Landmark | None = None
        self.plan: navigation.RoutePlan | None = None

        # 로그 조절용 — 비콘은 초당 열 번씩 오므로 요약만 남긴다
        self.beacon_count = 0
        self.beacon_logged_at = 0.0
        self.track_logged_at = 0.0

        # 사용자가 말한 문장. `/monitor` 가 "폰이 말함: ..." 으로 보여준다.
        self.heard = ""

        # `/monitor` 로 흘려보낼 것. handle() 이 채우고 소켓 루프가 비운다.
        #
        # 반환값에 섞지 않는 이유는 **받는 쪽이 다르기 때문**이다. 반환값은 이
        # 연결(폰)로 가고 이건 `/ws` 전체로 간다. 한 목록에 담으면 폰이 자기가
        # 못 알아듣는 메시지를 받는다.
        self.mirror: dict | None = None

        # `/ws` 로 그때그때 뿌릴 사건들(측정 시작·종료, 전환). RSSI 와 달리 모아
        # 보낼 수 없어서 따로 큐에 담고 소켓 루프가 비운다.
        self.mirror_events: list[dict] = []

        # 실측 기록. 목적지가 정해지면 열리고 도착·취소·끊김에 닫힌다.
        self.recorder: "nav_recorder.NavRecorder | None" = None
        self.measuring = False

    def stop_recording(self, reason: str) -> None:
        if self.recorder is not None:
            self.recorder.close(reason)
            self.recorder = None
        # `/monitor` 의 측정도 같이 끝낸다 — 서버가 버튼을 대신 눌러주는 셈이다.
        if self.measuring:
            self.measuring = False
            self.mirror_events.append(monitor_mirror.measure_control("end", self.id))

    # -- 되묻기 후보 ------------------------------------------------------
    def take_pending(self) -> list[landmark_matcher.Landmark]:
        pending = self.pending
        if not pending:
            return []
        if _now_ms() - self.pending_at > PENDING_TTL_MS:
            self.pending = []
            return []
        return pending

    def clear(self) -> None:
        self.pending = []
        self.destination = None
        self.plan = None
        self.tracker.set_path([])


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# 로그 — 폰과 주고받는 것을 전부 남긴다
#
# 현장에서 폰을 들고 걸어다니며 확인해야 하는 일이라, 무엇이 오갔는지 눈으로 볼 수
# 없으면 어디가 막혔는지 좁힐 방법이 없다. 그래서 **비콘만 빼고 전부** 찍는다.
#
# 비콘은 초당 열 번씩 들어와서 그대로 찍으면 로그가 그것만으로 가득 찬다.
# 대신 몇 초에 한 번 요약을 남겨서 "들어오고는 있다"는 것만 확인되게 한다.
# ---------------------------------------------------------------------------
import os

# 비콘 RSSI 요약 로그. 0 이면 아예 안 찍는다.
#
#     LOG_BEACONS=0     끄기
#     LOG_BEACONS=5     5초마다 요약 (기본)
BEACON_LOG_EVERY_S = float(os.environ.get("LOG_BEACONS", "5"))

# 판정 근거 로그. 그래프에서는 교차했는데 안내가 안 나갈 때 어느 조건이 막는지 본다.
#
#     LOG_TRACK=0       끄기
#     LOG_TRACK=1       1초마다 (기본)
TRACK_LOG_EVERY_S = float(os.environ.get("LOG_TRACK", "1"))


def _log_in(session: "NavSession", data: dict) -> None:
    """비콘이 아닌 메시지를 그대로 남긴다."""
    event = str(data.get("event") or "?")
    detail = {k: v for k, v in data.items() if k != "event"}
    print(f"[nav {session.id}] ← {event}  {json.dumps(detail, ensure_ascii=False)}")


def _log_track(session: "NavSession") -> None:
    """판정이 왜 안 넘어가는지 숫자로 남긴다.

    `/monitor` 그래프에서는 두 비콘이 분명히 교차하는데 안내가 안 나가는 일이 있다.
    전진 조건이 넷이라(추세·최소세기·값존재·신호차) 화면만 봐서는 어느 것이 막는지
    알 수 없어서, 판정에 쓴 값과 실패한 조건을 그대로 찍는다.
    """
    if TRACK_LOG_EVERY_S <= 0:
        return
    now = time.time()
    if now - session.track_logged_at < TRACK_LOG_EVERY_S:
        return
    n = session.tracker.last_numbers
    if not n:
        return
    session.track_logged_at = now

    def v(x):
        return "-" if x is None else f"{x:+.1f}"

    print(f"[nav {session.id}] 판정 {n['cur']}→{n['next'] or '끝'} "
          f"| 값 {v(n['vCur'])}→{v(n['vNext'])} 차 {v(n['gapNext'])}(요구 {n['minGap']:.1f}) "
          f"| 추세 {v(n['tCur'])}/{v(n['tNext'])}(요구 ±{n['threshold']:.1f}) "
          f"| {session.tracker.last_verdict}"
          + (f" | 막힘: {','.join(n['blockers'])}" if n["blockers"] else ""))


def _log_beacons(session: "NavSession", data: dict) -> None:
    """비콘은 요약만. **처리가 끝난 뒤에 부른다** — 필터가 채워져야 값이 나온다."""
    if BEACON_LOG_EVERY_S <= 0:
        return
    session.beacon_count += len(data.get("beacons") or [])
    now = time.time()
    if now - session.beacon_logged_at < BEACON_LOG_EVERY_S:
        return
    session.beacon_logged_at = now
    seen = sorted(session.filters)
    rssi = " ".join(f"{k}:{session.filters[k].x:.0f}" for k in seen[:6])
    where = f"{session.building_id or '?'}/{session.floor_id or '?'}"
    print(f"[nav {session.id}] ← beacons  {session.beacon_count}개 · {where} · "
          f"{len(seen)}종  {rssi}{' …' if len(seen) > 6 else ''}")
    session.beacon_count = 0


def _log_out(session: "NavSession", msg: dict) -> None:
    bits = [f"state={msg.get('state')}"]
    if msg.get("utterance"):
        bits.append(f'말="{msg["utterance"]}"')
    else:
        bits.append("말=없음")
    if msg.get("listenAfter"):
        bits.append("마이크엶")
    if msg.get("haptic"):
        bits.append(f"진동={msg['haptic']}")
    screen = msg.get("screen") or {}
    if screen.get("items"):
        bits.append(f"목록 {len(screen['items'])}개")
    if screen.get("totalSteps"):
        bits.append(f"{screen.get('step')}/{screen['totalSteps']}단계")
    print(f"[nav {session.id}] → {msg.get('event')}  {' · '.join(bits)}")


# ---------------------------------------------------------------------------
# 서버 → 앱 메시지
# ---------------------------------------------------------------------------
def out(event: str, state: str, *, utterance: str | None = None,
        listen_after: bool = False, haptic: str | None = None,
        screen: dict | None = None, **extra) -> dict:
    """앱에 보낼 메시지 하나. **모양이 항상 같다.**

    `utterance` 에 빈 문자열은 넣지 않는다 — null 과 구분이 안 되면 앱이 빈 발화를
    시도한다. 그래서 여기서 걸러 null 로 바꾼다.
    """
    return {
        "event": event,
        "state": state,
        "utterance": utterance or None,
        "listenAfter": listen_after,
        "haptic": haptic,
        "screen": screen,
        **extra,
    }


def screen_of(title: str | None = None, items: list | None = None,
              step: int | None = None, total: int | None = None) -> dict:
    return {"title": title, "items": items, "step": step, "totalSteps": total}


def _items(landmarks) -> list[dict]:
    return [{"id": lm.id, "name": lm.name} for lm in landmarks]


# ---------------------------------------------------------------------------
# 층·목적지
# ---------------------------------------------------------------------------
def building_from_mac(mac: str) -> tuple[str | None, str | None]:
    """MAC 하나로 (건물 id, 층 id)를 찾는다. 못 찾으면 (None, None).

    ── MAC 은 여기서만 쓴다 ──────────────────────────────────────

    판정은 major/minor 로 한다. 펌웨어에 `major = 100 + 층`, `minor = 그 층의
    번호` 가 새겨져 있으므로 그것만으로 층과 비콘이 정해진다.

    **딱 하나, 건물을 못 가린다.** A동 4층과 B동 4층이 둘 다 `major=104` 다.
    major 는 층 번호일 뿐 건물을 담지 않기 때문이다.

    그래서 MAC 은 **앱이 켜지고 첫 비콘을 잡았을 때 건물을 확정하는 용도로만**
    쓴다. 한 번 정해지면 그 뒤로는 안 본다 — 같은 건물 안에서 major/minor 는
    유일하므로 모호할 일이 없다.

    (건물까지 신호에 담으려면 iBeacon UUID 를 건물마다 다르게 구우면 된다.
     그러면 MAC 이 아예 필요 없어진다. 지금은 UUID 가 전 비콘 공통이라 MAC 을 쓴다)
    """
    try:
        import sqlalchemy as sa

        from app.beacon.models import Beacon
        from app.database import SessionLocal
        from app.floor.models import Floor

        db = SessionLocal()
        try:
            row = (db.query(Beacon.floor_id, Floor.building_id)
                   .join(Floor, Floor.id == Beacon.floor_id)
                   .filter(sa.func.upper(Beacon.mac) == str(mac).upper())
                   .first())
            return (row[1], row[0]) if row else (None, None)
        finally:
            db.close()
    except Exception as e:
        print(f"[nav] MAC 조회 실패: {e}")
        return (None, None)


def floor_in_building(building_id: str, major: int) -> str | None:
    """건물이 정해진 뒤 층을 찾는다. 층이 바뀌면 이걸로 따라간다."""
    try:
        from app.database import SessionLocal
        from app.floor.models import Floor

        db = SessionLocal()
        try:
            row = (db.query(Floor.id)
                   .filter(Floor.building_id == building_id, Floor.major == major)
                   .first())
            return row[0] if row else None
        finally:
            db.close()
    except Exception:
        return None


def _load_landmarks(session: NavSession) -> list[landmark_matcher.Landmark]:
    """지금 있는 층의 목적지 목록.

    **층을 모르면 빈 목록을 준다.** 아무 층이나 골라 답하는 것보다 "모르겠다"가
    낫다 — 잘못 안내하면 화면을 볼 수 없는 사용자가 알아챌 방법이 없다.
    """
    if session.floor_id is None:
        return []
    now = time.time() * 1000
    if session.landmarks and now - session.landmarks_at < LANDMARK_TTL_MS:
        return session.landmarks
    try:
        from app.database import SessionLocal
        from app.nav.db_map_source import DbMapSource

        db = SessionLocal()
        try:
            raw = [{"id": lm.id, "name": lm.name, "x": lm.x, "y": lm.y}
                   for lm in DbMapSource(db).landmarks(session.floor_id)]
        finally:
            db.close()
    except Exception as e:
        print(f"[nav] 목적지 조회 실패: {e}")
        return []
    session.landmarks = landmark_matcher.load_landmarks(raw)
    session.landmarks_at = now
    return session.landmarks


# ---------------------------------------------------------------------------
# 앱 → 서버 이벤트
# ---------------------------------------------------------------------------
def on_beacons(session: NavSession, data: dict) -> list[dict]:
    """비콘 관측. 필터를 먹이고 진행을 판정한다.

    **스캔될 때마다 하나씩 온다.** 묶어 보내지 않는 이유는 실측 간격이 87ms 라
    2.5초 판정 창에 29개가 들어오는데, 1초로 묶으면 2~3개로 줄어 판정이 무너지기
    때문이다. 게다가 누적 맵을 반복 전송하면 칼만 필터가 그 반복값을 새 측정으로
    받아들여 톱니 파형을 만든다.
    """
    located = False
    samples: list[tuple[str, float, float]] = []
    for b in data.get("beacons") or []:
        if not isinstance(b, dict):
            continue
        rssi = b.get("rssi")
        if not isinstance(rssi, (int, float)) or rssi >= 0 or rssi == 127:
            continue
        major, minor = b.get("major"), b.get("minor")
        key = navigation.tracking_key(major, minor)
        if key is None:
            continue

        if _locate(session, major, b.get("mac")):
            located = True
        session.beacon_ids[key] = {"major": major, "minor": minor}
        pipe = session.filters.setdefault(key, RssiFilterPipeline())
        filtered = pipe.filter(float(rssi))
        session.tracker.feed(key, filtered)
        samples.append((key, float(rssi), filtered))
        if session.recorder is not None:
            session.recorder.sample(
                monitor_mirror.display_key(session.floor_id, key), float(rssi), filtered)

    # `/monitor` 로 넘길 것. **판정(evaluate)보다 먼저 만들지 않는다** — 아래에서
    # 인덱스가 옮겨갈 수 있고, 그 전 상태를 그리면 화면이 한 박자 늦는다.
    out_msgs: list[dict] = []

    # 위치가 막 정해졌으면 목적지 목록을 **먼저 보내준다.**
    #
    # 앱은 켜지자마자 목록을 청하는데 그때는 아직 비콘이 안 잡혀 있다. 그래서
    # "아직 위치를 확인하지 못했습니다"만 받고 끝났고, 그 뒤로 다시 물어보지 않아
    # 목록이 영영 비어 있었다. 언제 다시 물을지를 앱이 판단하게 두면 이런 구멍이
    # 생기므로, 준비되는 쪽이 알려준다.
    if located and session.destination is None:
        landmarks = _load_landmarks(session)
        if landmarks:
            # **여기서 마이크를 연다.**
            #
            # 연결 직후의 "목적지를 말씀해 주세요"(listenAfter=true)는 화면이 아직
            # 안 붙어 있어 허공으로 날아간다. 그 뒤로 듣겠다는 신호를 다시 안 주면
            # 사용자는 "듣고 있어요" 화면만 보고 아무것도 할 수 없다.
            #
            # 위치가 정해진 지금이 실제로 들을 수 있게 된 첫 순간이다.
            out_msgs.append(out("list", "listening",
                                utterance="목적지를 말씀해 주세요.",
                                listen_after=True,
                                screen=screen_of("목적지", _items(landmarks))))

    transition = session.tracker.evaluate()
    if transition is not None:
        if session.recorder is not None:
            session.recorder.transition(transition, session.tracker.last_numbers,
                                        session.tracker.last_verdict)
        out_msgs.append(_transition_message(session, transition))
        # 판정 시점을 `/monitor` 그래프에 세로선으로 남긴다.
        session.mirror_events.append(monitor_mirror.transition_msg(session, transition))
        if transition.get("isLast"):
            session.stop_recording("도착")
    _log_track(session)

    session.mirror = monitor_mirror.beacon_payload(session, samples)
    return out_msgs


def _locate(session: NavSession, major, mac) -> bool:
    """지금 어느 건물 몇 층인지 정한다.

        건물   MAC 으로 한 번만 (major 가 건물을 안 담으므로)
        층     그 건물 안에서 major 로 (층이 바뀌면 따라간다)

    건물이 정해지기 전에는 층도 정하지 않는다. major 만으로 층을 고르면 다른
    건물의 같은 층을 집을 수 있고, 그러면 목적지 목록과 경로가 통째로 남의 건물
    것이 된다 — 사용자가 알아챌 방법이 없다.
    """
    if session.building_id is None:
        if not mac:
            return False
        building_id, floor_id = building_from_mac(mac)
        if building_id is None:
            return False
        session.building_id = building_id
        session.floor_id = floor_id
        print(f"[nav] {session.id} 위치 확정 — 건물 {building_id} / 층 {floor_id} (MAC {mac})")
        return True

    if not isinstance(major, int) or major == session.major:
        return False
    floor_id = floor_in_building(session.building_id, major)
    if floor_id is None or floor_id == session.floor_id:
        return False
    print(f"[nav] {session.id} 층 바뀜 {session.floor_id} → {floor_id} (major {major})")
    session.floor_id = floor_id
    session.major = major
    session.landmarks = []      # 목적지 목록을 새 층 것으로 다시 읽는다
    return True


def _transition_message(session: NavSession, t: dict) -> dict:
    """추적기 판정을 앱이 읽을 문장으로 바꾼다."""
    step = t.get("number")
    total = t.get("total")
    is_last = bool(t.get("isLast"))
    forward = t.get("direction") == "forward"

    turn = None
    if session.plan:
        for s in session.plan.route.steps:
            if s.seq == step:
                turn = s.turn
                break

    if is_last:
        name = session.destination.name if session.destination else "목적지"
        return out("arrive", "arrived", utterance=f"{name}입니다. 도착했습니다.",
                   haptic="arrive", screen=screen_of(name, None, step, total))

    if not forward:
        return out("back", "navigating", utterance="경로를 벗어났습니다. 뒤로 돌아가세요.",
                   haptic="warn", screen=screen_of(None, None, step, total))

    turn_text = {"left": " 왼쪽으로 꺾으세요.", "right": " 오른쪽으로 꺾으세요."}.get(turn, "")
    return out("advance", "navigating", utterance=f"{step}번.{turn_text}",
               haptic="guide", screen=screen_of(None, None, step, total))


def on_destination(session: NavSession, data: dict) -> list[dict]:
    """목적지 지정. 첫 발화와 되묻기 답변이 같은 이벤트다."""
    landmarks = _load_landmarks(session)
    if not landmarks:
        return [out("error", "ready",
                    utterance="아직 위치를 확인하지 못했습니다. 잠시 후 다시 말씀해 주세요.",
                    listen_after=True)]

    picked_id = str(data.get("id") or "")
    if picked_id:
        # 목록에서 터치로 고른 것 — 해석을 건너뛴다.
        lm = next((x for x in landmarks if x.id == picked_id), None)
        if lm is None:
            return [out("notFound", "listening", utterance="그 목적지를 찾지 못했습니다.",
                        listen_after=True)]
        session.pending = []
        return _start_route(session, lm)

    text = str(data.get("text") or "")
    session.heard = text or session.heard
    pending = session.take_pending()
    result = llm_matcher.choose(text, pending) if pending else llm_matcher.resolve(text, landmarks)

    if result.status == "resolved" and result.landmark:
        session.pending = []
        return _start_route(session, result.landmark)

    if result.status == "ambiguous":
        session.pending = list(result.candidates)
        session.pending_at = _now_ms()
        return [out("disambiguate", "listening", utterance=result.speech,
                    listen_after=True,
                    screen=screen_of("어디로 갈까요?", _items(result.candidates)))]

    return [out("notFound", "listening", utterance=result.speech, listen_after=True,
                screen=screen_of("찾지 못했습니다", _items(result.candidates)))]


def _start_route(session: NavSession, lm: landmark_matcher.Landmark) -> list[dict]:
    """목적지가 정해졌으니 경로를 만들어 추적을 건다."""
    session.destination = lm
    try:
        plan = navigation.plan_route(lm.id, list(session.filters.keys()),
                                     session.filters, beacon_ids=session.beacon_ids)
    except MapDataError as e:
        # 못 만든 이유를 그대로 읽어주고 다시 듣는다. 여기서 마이크를 안 열면
        # 사용자는 그 자리에서 막힌다.
        session.destination = None
        return [out("routeFailed", "ready", utterance=str(e).splitlines()[0],
                    listen_after=True)]
    except Exception as e:
        print(f"[nav] 경로 생성 오류: {e!r}")
        session.destination = None
        return [out("error", "ready", utterance="경로를 만들지 못했습니다. 다시 말씀해 주세요.",
                    listen_after=True)]

    session.plan = plan

    # `/monitor` 가 지도에 그릴 수 있게 서버에 둔다. **추적이 걸리든 안 걸리든
    # 둔다** — 경로를 만드는 것과 추적을 거는 것은 다른 일이고, 한 칸짜리 경로도
    # 어디로 가려 했는지는 화면에서 봐야 한다.
    monitor_mirror.set_route(plan, lm, session.heard or lm.name)

    total = len(plan.keys)
    if total < 2:
        # 경로가 한 칸이면 진행을 판정할 수가 없다 — 다음 비콘이 없으니 "옮겨갔다"를
        # 볼 기준이 없다. 목적지가 바로 옆이라 실제로 안내할 것도 없다.
        #
        # 예전에는 "비콘 신호를 기다리는 중입니다"라고 했는데 **거짓말이었다.**
        # 신호 문제가 아니라 경로가 짧은 것이고, 그렇게 말하면 사용자는 오지 않을
        # 신호를 영원히 기다린다. listenAfter 도 없어서 다시 말할 수조차 없었다.
        session.destination = None
        return [out("arrive", "arrived",
                    utterance=f"{lm.name}은 바로 근처입니다.",
                    haptic="arrive", listen_after=True,
                    screen=screen_of(lm.name, None, 1, 1))]

    # `/monitor` 판정 설정 창에서 고른 값을 그대로 쓴다.
    #
    # 예전에는 인자 없이 불러서 **화면에서 임계값을 아무리 바꿔도 폰 안내는 기본값
    # 그대로였다.** 판정기가 두 개(전역 `handler._tracker` / 연결별 이 tracker)인데
    # 설정이 앞쪽에만 걸려서, 화면에 보이는 판정과 폰이 받는 안내가 다른 기준으로
    # 돌고 있었다. bleapp 시절에는 둘이 같은 판정기라 드러나지 않던 문제다.
    #
    # 전역 하나라 폰이 여러 대면 다 같은 설정을 쓴다 — 실측 도구 수준의 한계이고,
    # 오히려 실측 중에는 그게 편하다.
    from app.ws.handler import _track_tuning

    session.tracker.set_path(plan.keys, **_track_tuning)
    _seed_index(session, plan.keys)
    session.tracker.start_session()

    # 목적지가 정해지는 순간이 곧 구간 측정 시작이다 — 버튼을 누를 사람이 없어도
    # 실제 출발 시점과 어긋나지 않는다.
    session.stop_recording("새 목적지")
    session.measuring = True
    session.mirror_events.append(
        monitor_mirror.measure_control("start", session.id, lm.name))
    session.recorder = nav_recorder.start(session.id, lm.name, {
        "목적지": lm.name,
        "출발비콘": plan.from_beacon,
        "거리m": round(plan.distance_m, 1),
        "경로비콘": [s.beacon_id for s in plan.route.steps],
        "추적키": list(plan.keys),
        "안잡힌비콘": list(plan.missing),
        "시작칸": session.tracker.index + 1,
        "판정설정": {
            "mode": session.tracker.mode,
            "threshold": session.tracker.threshold,
            "minNext": session.tracker.min_next,
            "minGap": session.tracker.min_gap,
            "requireTrend": session.tracker.require_trend,
            "windowMs": session.tracker.window_ms,
            "segments": session.tracker.segments,
            "confirmDelayMs": session.tracker.confirm_delay_ms,
            "confirmGap": session.tracker.confirm_gap,
            "forwardStreakNeed": session.tracker.forward_streak_need,
            "backStreakNeed": session.tracker.back_streak_need,
        },
    })
    print(f"[nav] {session.id} {plan.from_beacon} → {lm.name} "
          f"{plan.distance_m:.0f}m / {total}칸 · {session.tracker.index + 1}번에서 시작")
    return [out("start", "navigating", utterance=f"{lm.name}로 안내합니다.",
                haptic="guide",
                screen=screen_of(lm.name, None, session.tracker.index + 1, total))]


def _seed_index(session: NavSession, keys: list[str]) -> None:
    """지금 가장 세게 잡히는 비콘을 시작 위치로.

    `start_session()` 만 부르면 항상 0번에서 시작한다 — 바로 앞에서 `set_path` 가
    이력을 지웠는데 그 함수는 지워진 이력에서 위치를 고르기 때문이다.
    경로 중간에서 목적지를 말하면(흔한 일) 안내 번호가 계속 어긋난다.
    """
    best_idx, best_val = None, float("-inf")
    for idx, key in enumerate(keys):
        pipe = session.filters.get(key)
        if pipe is None or not getattr(pipe, "initialized", False):
            continue
        if float(pipe.x) > best_val:
            best_idx, best_val = idx, float(pipe.x)
    if best_idx is None:
        return
    session.tracker.index = best_idx
    for key in keys:
        pipe = session.filters.get(key)
        if pipe is not None and getattr(pipe, "initialized", False):
            session.tracker.feed(key, float(pipe.x))


def on_list(session: NavSession, _data: dict) -> list[dict]:
    landmarks = _load_landmarks(session)
    if not landmarks:
        # 위치를 모른다고 그냥 끝내면 그대로 멈춘다. 비콘이 잡히면 서버가
        # 목록과 함께 다시 들을 기회를 준다(on_beacons 참고).
        return [out("error", "ready",
                    utterance="아직 위치를 확인하고 있습니다. 잠시만 기다려 주세요.",
                    listen_after=False)]
    return [out("list", "listening", utterance=None,
                screen=screen_of("목적지", _items(landmarks)))]


def on_cancel(session: NavSession, _data: dict) -> list[dict]:
    session.stop_recording("취소")
    session.clear()
    monitor_mirror.clear_route()
    return [out("ready", "ready", utterance="안내를 취소했습니다.", listen_after=True,
                screen=screen_of(None, None, None, None))]


def on_resume(session: NavSession, _data: dict) -> list[dict]:
    """재연결. 지금 상태를 다시 알려준다."""
    if session.destination and session.plan:
        snap = session.tracker.snapshot() or {}
        return [out("resume", "navigating",
                    utterance=f"{session.destination.name}로 안내 중입니다.",
                    screen=screen_of(session.destination.name, None,
                                     snap.get("number"), snap.get("total")))]
    return [out("resume", "ready", utterance="목적지를 말씀해 주세요.", listen_after=True)]


HANDLERS = {
    "destination": on_destination,
    "beacons": on_beacons,
    "list": on_list,
    "cancel": on_cancel,
    "resume": on_resume,
}


def handle(session: NavSession, raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[nav {session.id}] ← (JSON 아님) {raw[:200]}")
        return [out("error", "ready", utterance="다시 시도해 주세요.")]
    if not isinstance(data, dict):
        print(f"[nav {session.id}] ← (객체 아님) {raw[:200]}")
        return [out("error", "ready", utterance="다시 시도해 주세요.")]

    event = str(data.get("event") or "")
    if event != "beacons":
        _log_in(session, data)

    fn = HANDLERS.get(event)
    if fn is None:
        # 모르는 이벤트도 남긴다. 조용히 버리면 앱이 보냈는데 아무 일도 안 일어나는
        # 상황에서 원인을 찾을 수 없다.
        print(f"[nav {session.id}] ! 모르는 이벤트: {data.get('event')!r}")
        return []
    msgs = fn(session, data)
    if event == "beacons":
        _log_beacons(session, data)
    request_id = data.get("requestId")
    for m in msgs:
        if request_id:
            m["requestId"] = request_id
        _log_out(session, m)
    return msgs


# ---------------------------------------------------------------------------
@router.websocket("/ws/navigation")
async def navigation_endpoint(websocket: WebSocket):
    await websocket.accept()
    session = NavSession()
    client = getattr(websocket, "client", None)
    print(f"[nav {session.id}] ◆ 연결됨  {client.host if client else '?'}")

    hello = out("ready", "ready", utterance="목적지를 말씀해 주세요.", listen_after=True,
                sessionId=session.id)
    _log_out(session, hello)
    await websocket.send_text(json.dumps(hello, ensure_ascii=False))

    # 목적지 해석은 LLM 응답을 기다리느라 몇 초가 걸린다. 읽기 루프 안에서 기다리면
    # 그동안 receive_text() 를 안 부르므로 폰이 올리는 비콘이 소켓에 쌓였다가
    # 한꺼번에 몰려 들어온다. 판정기는 그걸 "같은 순간에 온 수십 개"로 보게 되어
    # 시간축이 무너진다. 그래서 따로 떼어 보내고 루프는 곧장 다음 메시지를 읽는다.
    lock = asyncio.Lock()
    tasks: set[asyncio.Task] = set()

    try:
        while True:
            raw = await websocket.receive_text()

            if '"destination"' in raw or '"list"' in raw:
                task = asyncio.create_task(_slow(websocket, session, raw, lock))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                continue

            for m in handle(session, raw):
                await _send(websocket, m)

            # 폰이 올린 것을 `/monitor` 로도 흘려보낸다. 여기서 하는 이유는
            # handle() 이 동기 함수라 브로드캐스트(await)를 할 수 없어서다.
            if session.mirror is not None:
                payload, session.mirror = session.mirror, None
                await monitor_mirror.publish(payload)
            while session.mirror_events:
                await monitor_mirror.publish(session.mirror_events.pop(0))
    except WebSocketDisconnect:
        pass
    finally:
        for t in list(tasks):
            t.cancel()
        session.stop_recording("연결 끊김")
        # stop_recording 이 "측정 종료" 를 큐에 넣는다. 루프가 끝난 뒤라 여기서 비운다.
        while session.mirror_events:
            await monitor_mirror.publish(session.mirror_events.pop(0))
        print(f"[nav {session.id}] ◆ 끊김  "
              f"건물={session.building_id} 층={session.floor_id} "
              f"목적지={session.destination.name if session.destination else '-'}")


async def _slow(websocket: WebSocket, session: NavSession, raw: str,
                lock: asyncio.Lock) -> None:
    try:
        async with lock:
            msgs = await asyncio.to_thread(handle, session, raw)
        for m in msgs:
            await _send(websocket, m)
        # 목적지 확정은 이 경로로 처리되므로 "측정 시작"도 여기서 나간다.
        while session.mirror_events:
            await monitor_mirror.publish(session.mirror_events.pop(0))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[nav] 처리 오류: {e!r}")


async def _send(websocket: WebSocket, msg: dict) -> None:
    try:
        await websocket.send_text(json.dumps(msg, ensure_ascii=False))
    except Exception:
        pass
