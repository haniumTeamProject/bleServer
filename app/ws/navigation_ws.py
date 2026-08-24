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
from dataclasses import dataclass

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.nav import legs as legs_mod
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

# 판정이 이만큼 안 움직이면 "아직 가고 있나" 하고 한 번 묻는다 (표 17번).
#
# 서버가 판단하는 이유는 **비콘은 오는데 자리가 안 바뀌는** 상태이기 때문이다.
# 신호가 끊기는 것(표 15·16번)은 앱이 본다 — 서버는 자기가 안 닿는 것을 알릴 수 없다.
#
# 30초는 실측으로 정한 값이 아니다. 짧으면 신호가 잠깐 흔들릴 때마다 묻고,
# 길면 정말 헤매는 사람을 오래 방치한다.
IDLE_ASK_MS = 30_000

# 기다리던 층이 아닌 곳에서 이만큼 머물러야 "여기 내렸다"고 본다.
#
# 계단통을 오르는 동안 지나가는 층의 비콘이 잠깐씩 잡힌다. 그걸 곧바로 도착으로
# 읽으면 2층·3층에서 안내를 새로 만들며 계속 말한다. 목표 층은 이 대기가 없다 —
# 거기서 멈출 것이 확실하므로 기다릴 이유가 없다.
WRONG_FLOOR_DWELL_MS = 5_000


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
        # 칸 번호별로 말할 문장. cues[i] = i+1 번째 칸.
        self.cues: list[list[str]] = []

        # -- 층 이동 -------------------------------------------------------
        #
        # 목적지가 다른 층이면 안내를 한 층짜리 구간 여러 개로 쪼갠다(app/nav/legs.py).
        # **이 셋은 서버 안에만 있다.** 앱으로 나가지 않고, 오히려 나가지 않게
        # 하려고 둔다 — 추적기는 경유지에 닿아도 최종 목적지에 닿은 것과 똑같이
        # `isLast` 를 올리므로, 둘을 가릴 근거가 세션에 없으면 경유지에서
        # `arrived` 가 새어 나간다.
        self.legs: list[legs_mod.Leg] = []
        self.leg_index = 0
        # 경유지에 닿아 층이 바뀌기를 기다리는 중인가. 이 동안에는 판정을 멈춘다.
        self.awaiting_floor: str | None = None
        # 지금 층으로 바뀐 시각. 계단통에서 지나가는 층 신호가 잠깐 잡히는 것과
        # 실제로 그 층에 내린 것을 가리는 데 쓴다.
        self.floor_since: int = 0

        # 마지막으로 칸이 바뀐 시각. 표 17번(정지 지속)이 이걸 본다.
        self.last_advance_at: int = 0
        # 이번 정지 구간에서 이미 물어봤는가. 30초마다 되묻지 않으려고 둔다.
        self.idle_asked = False

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
        self.cues = []
        self.legs = []
        self.leg_index = 0
        self.awaiting_floor = None
        self.last_advance_at = 0
        self.idle_asked = False
        self.tracker.set_path([])

    # -- 층 이동 ----------------------------------------------------------
    @property
    def leg(self) -> "legs_mod.Leg | None":
        """지금 안내 중인 구간."""
        if 0 <= self.leg_index < len(self.legs):
            return self.legs[self.leg_index]
        return None

    @property
    def on_final_leg(self) -> bool:
        """지금 구간이 마지막인가. 구간을 안 쓰면(같은 층) 항상 참."""
        leg = self.leg
        return leg is None or leg.is_final


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
def building_from_mac(mac: str) -> str | None:
    """MAC 하나로 **건물**을 찾는다. 못 찾으면 None.

    ── MAC 은 여기서만 쓴다 ──────────────────────────────────────

    판정은 major/minor 로 한다. 펌웨어에 `major = 100 + 층`, `minor = 그 층의
    번호` 가 새겨져 있으므로 그것만으로 층과 비콘이 정해진다.

    **딱 하나, 건물을 못 가린다.** A동 4층과 B동 4층이 둘 다 `major=104` 다.
    major 는 층 번호일 뿐 건물을 담지 않기 때문이다.

    그래서 MAC 은 **앱이 켜지고 첫 비콘을 잡았을 때 건물을 확정하는 용도로만**
    쓴다. 한 번 정해지면 그 뒤로는 안 본다 — 같은 건물 안에서 major/minor 는
    유일하므로 모호할 일이 없다.

    ── 층은 안 돌려준다 ──────────────────────────────────────────

    예전에는 `(건물, 층)` 을 같이 돌려주고 첫 층만 이 값으로 정했다. 그러면
    **층을 정하는 길이 두 개**가 된다 — 첫 층은 MAC, 그 뒤로는 major. 실제로
    그 탓에 `session.major` 가 출발 층에서 끝까지 비어 있었다(§`_locate`).
    층은 언제나 major 하나로 정한다.

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
            row = (db.query(Floor.building_id)
                   .join(Beacon, Beacon.floor_id == Floor.id)
                   .filter(sa.func.upper(Beacon.mac) == str(mac).upper())
                   .first())
            return row[0] if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[nav] MAC 조회 실패: {e}")
        return None


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


def _load_other_floor_landmarks(session: NavSession) -> list[landmark_matcher.Landmark]:
    """**다른 층**의 목적지들. 이 층에서 못 찾았을 때만 본다.

    ── 왜 처음부터 건물 전체를 보지 않나 ──────────────────────────

    화장실은 층마다 있다. 건물 전체를 후보로 두면 "화장실"이라고 말할 때마다
    다섯 개가 나와서 "몇 층 화장실이요?"를 되묻게 된다. 사용자가 원한 것은
    거의 언제나 **지금 층의 화장실**이다.

    그래서 순서를 둔다 — 이 층에서 먼저 찾고, 없을 때만 다른 층을 본다.
    "407호"는 이 층에 없으니 자연히 다른 층에서 찾히고, "화장실"은 이 층에서
    끝나서 다른 층 것이 끼어들지 않는다.

    ── 연결자는 뺀다 ──────────────────────────────────────────────

    계단·엘리베이터는 층마다 같은 이름으로 있다. 다른 층 것까지 후보에 넣으면
    "계단1"이 여러 개가 되는데, 다른 층 계단을 목적지로 삼는 것은 뜻이 없다 —
    거기 가려면 어차피 이 층 계단을 타야 한다.
    """
    if session.building_id is None or session.floor_id is None:
        return []
    try:
        from app.database import SessionLocal
        from app.floor.models import Floor
        from app.landmark.models import Landmark as LandmarkRow

        db = SessionLocal()
        try:
            floor_ids = [
                r[0] for r in db.query(Floor.id)
                .filter(Floor.building_id == session.building_id,
                        Floor.id != session.floor_id).all()
            ]
            if not floor_ids:
                return []
            raw = [
                {"id": lm.id, "name": lm.name, "x": lm.x, "y": lm.y}
                for lm in db.query(LandmarkRow)
                .filter(LandmarkRow.floor_id.in_(floor_ids)).all()
                if lm.name
            ]
        finally:
            db.close()
    except Exception as e:
        print(f"[nav] 다른 층 목적지 조회 실패: {e}")
        return []
    return landmark_matcher.load_landmarks(raw)


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
    # 층 이동이 끝났나. **목적지 목록보다 먼저 본다** — 층이 바뀐 그 순간에
    # `destination` 은 아직 살아 있으므로 아래 목록 안내와 부딪히지 않지만,
    # 순서를 명확히 해두는 편이 읽기 쉽다.
    if located and session.awaiting_floor is not None:
        out_msgs.extend(_maybe_resume(session))

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
        # 판정 시점을 `/monitor` 그래프에 세로선으로 남긴다. **메시지를 만들기
        # 전에** 남긴다 — 경유지면 `_transition_message` 안에서 기록이 닫히므로,
        # 뒤에 두면 그 구간의 마지막 전환이 파일에 안 들어간다.
        session.mirror_events.append(monitor_mirror.transition_msg(session, transition))
        final = session.on_final_leg
        # 자리가 바뀌었다 = 사용자가 움직이고 있다. 표 17번 타이머를 되감는다.
        session.last_advance_at = _now_ms()
        session.idle_asked = False
        out_msgs.extend(_transition_message(session, transition))
        if transition.get("isLast") and final:
            session.stop_recording("도착")
    _log_track(session)

    session.mirror = monitor_mirror.beacon_payload(session, samples)
    return out_msgs


def _locate(session: NavSession, major, mac) -> bool:
    """지금 어느 건물 몇 층인지 정한다. **층이 실제로 바뀌었을 때만 True.**

        건물   MAC 으로 한 번만 (major 가 건물을 안 담으므로)
        층     그 건물 안에서 major 로 — 첫 층도 포함해서 언제나

    건물이 정해지기 전에는 층도 정하지 않는다. major 만으로 층을 고르면 다른
    건물의 같은 층을 집을 수 있고, 그러면 목적지 목록과 경로가 통째로 남의 건물
    것이 된다 — 사용자가 알아챌 방법이 없다.

    ── 층을 정하는 길은 하나여야 한다 ────────────────────────────

    예전에는 **첫 층만 MAC 으로** 정하고 그 뒤로는 major 로 따라갔다. 길이 둘이라
    `session.major` 가 첫 분기에서 안 채워졌고, 두 번째 분기는 층이 같으면 저장
    전에 빠져나가서 **출발 층에 있는 내내 `session.major` 가 None 이었다.**

        첫 비콘 뒤 :  floor_id = f-1   major = None
        같은 층 계속:  floor_id = f-1   major = None

    출발점을 고를 때 층을 거르는 데 이 값을 쓰므로(`strongest_beacon_key`),
    None 이면 거르지 않고 지나간다. 계단 근처에서 위층 신호가 새어 들어오면
    그것이 출발 비콘으로 뽑힌다.

    지금은 MAC 과 major 가 같은 메시지에 함께 오므로(`NavClient.sendBeacon` —
    major/minor 는 필수, mac 만 nullable) 한 번에 둘 다 정해진다.
    """
    if session.building_id is None:
        if not mac:
            return False
        building_id = building_from_mac(mac)
        if building_id is None:
            return False
        session.building_id = building_id
        print(f"[nav] {session.id} 건물 확정 — {building_id} (MAC {mac})")

    if not isinstance(major, int) or major == session.major:
        return False
    floor_id = floor_in_building(session.building_id, major)
    if floor_id is None:
        return False

    # **층이 그대로여도 major 는 저장한다.** 아래에서 빠져나가기 전에 한다 —
    # 이 값이 비어 있으면 출발점을 고를 때 층을 못 거른다.
    session.major = major
    if floor_id == session.floor_id:
        return False

    where = session.floor_id or "?"
    print(f"[nav] {session.id} 층 {where} → {floor_id} (major {major})")
    session.floor_id = floor_id
    session.floor_since = _now_ms()
    session.landmarks = []      # 목적지 목록을 새 층 것으로 다시 읽는다
    return True


def _transition_message(session: NavSession, t: dict) -> list[dict]:
    """추적기 판정을 앱이 읽을 문장으로 바꾼다.

    **문구는 `app/nav/cues.py` 가 만든 것을 그대로 읽는다.** 여기서 따로 짓지 않는다.
    예전에는 `"3번. 왼쪽으로 꺾으세요."` 처럼 이 함수가 직접 만들었는데, 그러면
    `/monitor` 에 보이는 안내와 폰이 듣는 안내가 서로 다른 코드에서 나와 갈라진다.
    실제로 화면에는 횡단 안내가 뜨는데 폰은 번호만 읽는 상태였다.
    """
    step = t.get("number")
    total = t.get("total")
    is_last = bool(t.get("isLast"))
    forward = t.get("direction") == "forward"

    if not forward:
        return [out("back", "navigating", utterance="멈추세요. 경로를 벗어났습니다.",
                    haptic="warn", screen=screen_of(None, None, step, total))]

    # 이 칸에 배정된 안내. seq 는 1부터라 -1 한다.
    said = _cues_for_step(session, step)

    if is_last:
        # **여기가 갈리는 지점이다.**
        #
        # 추적기는 경유지(계단)에 닿아도 최종 목적지에 닿은 것과 똑같이 `isLast`
        # 를 올린다. 경로 마지막 칸이라는 사실만 알지 그 경로가 왜 거기서 끝나는지는
        # 모르기 때문이다. 세션이 들고 있는 구간 목록만이 둘을 가릴 수 있다.
        leg = session.leg
        if leg is not None and not leg.is_final:
            # 경유지 — `arrived` 를 내보내지 않는다. 층 이동 안내로 바꿔 단다.
            # cue 로 만들어진 "계단1입니다."는 버린다. `handoff_speech()` 가
            # 같은 말을 층 이동 지시까지 붙여서 다시 한다.
            return _handoff(session, leg)

        name = session.destination.name if session.destination else "목적지"
        # 도착 안내는 cue 로도 만들어지지만(마지막 비콘 고정), 그것이 없거나
        # 다른 칸에 있을 수 있으므로 여기서 반드시 도착을 말한다.
        text = " ".join(said) if said else f"{name}입니다."
        return [out("arrive", "arrived", utterance=text,
                    haptic="arrive", screen=screen_of(name, None, step, total))]

    if not said:
        # 이 비콘에 할 말이 없다 — 표 2번 "일반 직진"은 무음이다.
        # 화면의 진행 표시만 갱신한다.
        return [out("advance", "navigating", utterance=None,
                    screen=screen_of(None, None, step, total))]

    return [out("advance", "navigating", utterance=" ".join(said),
                haptic="guide", screen=screen_of(None, None, step, total))]


def _build_cues(session: NavSession, plan, destination: str) -> list[list[str]]:
    """경로 노드에서 안내 문구를 뽑아 **칸 번호대로** 늘어놓는다.

    돌려주는 것은 `cues[i] = i+1 번째 칸에서 말할 문장들` 이다. 추적기는 칸 번호로
    움직이므로 이 모양이면 조회가 한 번에 끝난다.

    ── 왜 소유 방식인가 ──────────────────────────────────────────

    세 가지 배정 방식이 있지만(`docs/경로안내_생성과_진행판정.md`), 실제 안내에는
    소유 방식을 쓴다. 비콘 간격이 넓은 자리에서도 **한 칸은 반드시 확보**되어,
    그 비콘의 판정이 늦어도 말할 기회를 놓치지 않기 때문이다. 얼마나 앞선
    이야기인지는 문장에 거리로 담긴다("조금 뒤", "약 20미터 뒤").

    실패해도 안내는 계속돼야 하므로 예외를 삼킨다 — 문구가 없으면 무음일 뿐,
    진행 판정과 도착은 그대로 돈다.
    """
    try:
        from app.database import SessionLocal
        from app.nav import cues as cue_mod
        from app.nav.db_map_source import DbMapSource

        db = SessionLocal()
        try:
            source = DbMapSource(db)
            beacons = source.beacons(plan.floor_id)
            result = cue_mod.build(
                source.graph(plan.floor_id), plan.route.node_ids, beacons,
                source.beacon_match_radius_m(plan.floor_id),
                source.meters_per_px(plan.floor_id), destination)
        finally:
            db.close()
    except Exception as e:
        print(f"[nav {session.id}] 안내 문구를 만들지 못함: {e!r}")
        return []

    by_step = [[c.text for c in st.cues_by_owner] for st in result.steps]
    spoken = sum(len(x) for x in by_step)
    print(f"[nav {session.id}] 안내 {spoken}개 / {len(by_step)}칸"
          + (f" · 미배정 {len(result.orphan_owner)}개" if result.orphan_owner else ""))
    return by_step


def _cues_for_step(session: NavSession, step: int | None) -> list[str]:
    """그 칸에서 말할 문장들. 없으면 빈 목록."""
    if not step or not session.cues:
        return []
    index = step - 1
    if 0 <= index < len(session.cues):
        return session.cues[index]
    return []


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
            # 화면 목록은 이 층 것만 보여주지만, 다른 층 id 가 올 수도 있다.
            lm = next((x for x in _load_other_floor_landmarks(session)
                       if x.id == picked_id), None)
        if lm is None:
            return [out("notFound", "listening", utterance="그 목적지를 찾지 못했습니다.",
                        listen_after=True)]
        session.pending = []
        return _start_route(session, lm)

    text = str(data.get("text") or "")
    session.heard = text or session.heard
    pending = session.take_pending()
    result = llm_matcher.choose(text, pending) if pending else llm_matcher.resolve(text, landmarks)

    # 이 층에 없으면 다른 층을 본다. **순서가 중요하다** — 자세한 이유는
    # `_load_other_floor_landmarks` 참고("화장실"은 이 층 것이어야 한다).
    if result.status == "notFound" and not pending:
        others = _load_other_floor_landmarks(session)
        if others:
            elsewhere = llm_matcher.resolve(text, others)
            if elsewhere.status != "notFound":
                result = elsewhere

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
    """사용자가 말한 **최종** 목적지가 정해졌다.

    같은 층이면 구간이 하나라 예전과 똑같이 돈다. 다른 층이면 구간을 쪼개고
    첫 구간(가까운 연결자까지)만 건다. 나머지는 층이 바뀐 뒤에 이어서 한다.
    """
    session.destination = lm
    session.awaiting_floor = None
    session.leg_index = 0
    try:
        session.legs = _plan_legs(session, lm)
    except MapDataError as e:
        session.destination = None
        session.legs = []
        return [out("routeFailed", "ready", utterance=str(e).splitlines()[0],
                    listen_after=True)]
    if len(session.legs) > 1:
        print(f"[nav] {session.id} 층 이동 — "
              + " → ".join(leg.dest_name for leg in session.legs))
    return _start_leg(session)


def _plan_legs(session: NavSession, lm: landmark_matcher.Landmark) -> list[legs_mod.Leg]:
    """구간을 쪼갠다. 층을 모르면 쪼갤 수 없으니 한 구간으로 둔다."""
    if session.floor_id is None:
        return [legs_mod.Leg(floor_id="", dest_id=lm.id, dest_name=lm.name, is_final=True)]
    from app.database import SessionLocal

    xy = navigation.origin_point(session.floor_id, session.filters,
                                 session.beacon_ids, session.major)
    db = SessionLocal()
    try:
        return legs_mod.plan_legs(db, session.floor_id, lm.id, lm.name,
                                  origin_x=xy[0] if xy else None,
                                  origin_y=xy[1] if xy else None)
    finally:
        db.close()


def _start_leg(session: NavSession) -> list[dict]:
    """구간 하나를 건다. **층을 넘는 것을 여기서는 모른다.**

    첫 구간이든 층을 옮기고 난 두 번째 구간이든 하는 일이 완전히 같다 —
    한 층, 출발 비콘 하나, 목적지 하나. 그래서 코드가 하나뿐이다.
    """
    leg = session.leg
    if leg is None:
        session.destination = None
        return [out("error", "ready", utterance="경로를 만들지 못했습니다. 다시 말씀해 주세요.",
                    listen_after=True)]
    # 이 구간에서 "목적지"라고 부를 것. 경유지면 연결자 이름이다.
    name = leg.dest_name
    first_leg = session.leg_index == 0
    try:
        plan = navigation.plan_route(leg.dest_id, list(session.filters.keys()),
                                     session.filters, beacon_ids=session.beacon_ids,
                                     floor_id=leg.floor_id or None,
                                     major=session.major)
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
    session.cues = _build_cues(session, plan, name)

    # `/monitor` 가 지도에 그릴 수 있게 서버에 둔다. **추적이 걸리든 안 걸리든
    # 둔다** — 경로를 만드는 것과 추적을 거는 것은 다른 일이고, 한 칸짜리 경로도
    # 어디로 가려 했는지는 화면에서 봐야 한다.
    #
    # 경유 구간이면 최종 목적지가 아니라 **이 구간의 목적지**를 그린다. 화면에
    # 407호를 띄워놓고 실제로는 계단까지의 경로를 그리면 검수할 수가 없다.
    monitor_mirror.set_route(plan, _LegTarget(leg.dest_id, name),
                             session.heard or name)

    total = len(plan.keys)
    if total < 2:
        # 경로가 한 칸이면 진행을 판정할 수가 없다 — 다음 비콘이 없으니 "옮겨갔다"를
        # 볼 기준이 없다. 목적지가 바로 옆이라 실제로 안내할 것도 없다.
        #
        # 예전에는 "비콘 신호를 기다리는 중입니다"라고 했는데 **거짓말이었다.**
        # 신호 문제가 아니라 경로가 짧은 것이고, 그렇게 말하면 사용자는 오지 않을
        # 신호를 영원히 기다린다. listenAfter 도 없어서 다시 말할 수조차 없었다.
        if not leg.is_final:
            # 연결자가 바로 옆이다 — 걸을 것도 없이 곧장 층 이동으로 넘긴다.
            return _handoff(session, leg)
        session.destination = None
        return [out("arrive", "arrived",
                    utterance=f"{name}은 바로 근처입니다.",
                    haptic="arrive", listen_after=True,
                    screen=screen_of(name, None, 1, 1))]

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
    # 표 17번은 "출발하고도 안 움직인다" 부터 세야 한다.
    session.last_advance_at = _now_ms()
    session.idle_asked = False

    # 목적지가 정해지는 순간이 곧 구간 측정 시작이다 — 버튼을 누를 사람이 없어도
    # 실제 출발 시점과 어긋나지 않는다.
    session.stop_recording("새 목적지")
    session.measuring = True
    session.mirror_events.append(
        monitor_mirror.measure_control("start", session.id, name,
                                       origin=plan.from_beacon))
    session.recorder = nav_recorder.start(session.id, plan.from_beacon, name, {
        "목적지": name,
        "최종목적지": session.destination.name if session.destination else name,
        "구간": f"{session.leg_index + 1}/{len(session.legs)}",
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
    print(f"[nav] {session.id} {plan.from_beacon} → {name} "
          f"{plan.distance_m:.0f}m / {total}칸 · {session.tracker.index + 1}번에서 시작"
          + (f" (구간 {session.leg_index + 1}/{len(session.legs)})"
             if len(session.legs) > 1 else ""))
    # 출발 안내(표 1번)에 **시작 칸의 안내를 이어붙인다.**
    #
    # 첫 비콘이 소유한 사건은 한 칸 앞이 없어서 자기 자신에 남는다(전수 검사에서
    # 39건). 그것을 안 실으면 출발하자마자 해야 할 일 — 대개 바로 앞의 횡단이나
    # 회전 — 을 아무도 말해주지 않는다.
    #
    # 두 번째 구간부터는 "손이 닿는 벽을 짚고 걸어주세요"를 다시 말하지 않는다.
    # 이미 그러고 있는 사람에게 반복하면 새 지시로 들린다. 대신 **최종 목적지를
    # 다시 짚어준다** — 층을 옮기는 동안 어디로 가는 중이었는지 잊기 쉽다.
    if first_leg:
        opening = [f"{name}로 안내합니다. 손이 닿는 벽을 짚고 걸어주세요."]
    else:
        opening = [f"{name}로 계속 안내합니다."]
    opening += _cues_for_step(session, session.tracker.index + 1)

    # 화면에 띄우는 이름은 **최종 목적지**다. 경유지 이름을 띄우면 사용자가
    # 목적지가 바뀐 줄 안다 — 서버 안에서 구간을 쪼갠 것은 앱이 알 바가 아니다.
    shown = session.destination.name if session.destination else name
    return [out("start", "navigating", utterance=" ".join(opening),
                haptic="guide",
                screen=screen_of(shown, None, session.tracker.index + 1, total))]


@dataclass(frozen=True)
class _LegTarget:
    """`monitor_mirror.set_route` 가 원하는 최소한의 모양(id·name)만 갖춘 껍데기.

    경유 구간의 목적지는 연결자라 `landmark_matcher.Landmark` 가 아니다.
    지도에 그릴 때 필요한 것은 id 와 이름 둘뿐이라 그것만 담아 넘긴다.
    """

    id: str
    name: str


def _handoff(session: NavSession, leg: legs_mod.Leg, opening: str = "") -> list[dict]:
    """경유지에 닿았다. **도착이 아니라 층 이동이다.**

    여기서 하는 일이 셋이다.

        판정을 멈춘다      층을 옮기는 동안 신호가 끊기는데, 켜둔 채로 두면
                          그 구간이 "경로를 벗어났습니다"로 읽힌다
        기다릴 층을 적는다  major 가 그 층 것으로 바뀌면 이동이 끝난 줄 안다
        말한다             `arrived` 가 아니라 `advance`. 앱은 안내 화면에 머문다
    """
    # `end_session()` 은 측정만 끈다(active=False). 경로는 그대로 둔다 —
    # `/monitor` 가 1층 경로를 계속 그릴 수 있어야 층 이동 직전 상태를 볼 수 있다.
    session.tracker.end_session()
    session.stop_recording("층 이동")
    session.awaiting_floor = leg.next_floor_id
    print(f"[nav] {session.id} {leg.dest_name} 도달 — {leg.next_floor_no}층 신호를 기다린다")

    text = (opening + " " + leg.handoff_speech()) if opening else leg.handoff_speech()
    shown = session.destination.name if session.destination else leg.dest_name
    return [out("advance", "navigating", utterance=text.strip(),
                haptic="guide", screen=screen_of(shown, None, None, None))]


def _maybe_resume(session: NavSession) -> list[dict]:
    """층 이동이 끝났으면 다음 구간을 건다.

    ── 무엇을 보고 "끝났다"고 하나 ────────────────────────────────

    **새 층의 비콘이 잡힌 것** 하나만 본다. 시간을 재지 않는다 — 계단을 오르는
    속도는 사람마다 다르고, 엘리베이터는 몇 층을 더 돌기도 한다. 목표 층 신호가
    들어온 것보다 확실한 증거는 없다.

    `_locate()` 가 major 로 층을 이미 따라가고 있어서(펌웨어에 `major = 100 + 층`
    이 새겨져 있다) 여기서는 그 결과가 기다리던 층인지만 확인하면 된다.

    ── 다른 층에 내렸으면 ────────────────────────────────────────

    엘리베이터에서 층을 잘못 눌렀거나 계단을 지나쳤을 수 있다. 그때는 다시
    구간을 쪼갠다 — 지금 층에서 최종 목적지까지 새로 계획하는 것이라, 3층에
    내렸으면 3층에서 다시 4층으로 가는 안내가 나온다.
    """
    want = session.awaiting_floor
    if want is None or session.floor_id is None:
        return []

    if session.floor_id != want:
        # 지나가는 중일 수 있다. 잠깐 머물러 보고 판단한다.
        if _now_ms() - session.floor_since < WRONG_FLOOR_DWELL_MS:
            return []
        # 엉뚱한 층이다. 최종 목적지는 그대로 두고 여기서 다시 계획한다.
        dest = session.destination
        if dest is None:
            session.awaiting_floor = None
            return []
        print(f"[nav] {session.id} 기다리던 층이 아님({want} 아닌 {session.floor_id}) — 다시 계획")
        session.awaiting_floor = None
        return _start_route(session, dest)

    session.awaiting_floor = None
    session.leg_index += 1
    leg = session.leg
    if leg is None:
        return []
    print(f"[nav] {session.id} 층 이동 완료 — {leg.dest_name} 구간 시작")
    return _start_leg(session)


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

    # 시간이 조건인 안내(표 17번)는 따로 세는 쪽이 있어야 한다 — 아래 루프는
    # 폰이 보낸 것이 있을 때만 깨어나므로 "아무 일도 없는 상태"를 못 본다.
    idle = asyncio.create_task(_watch_idle(websocket, session, lock))
    tasks.add(idle)
    idle.add_done_callback(tasks.discard)

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


async def _watch_idle(websocket: WebSocket, session: NavSession,
                      lock: asyncio.Lock) -> None:
    """표 17번 — 비콘은 오는데 **자리가 안 바뀌면** 한 번 물어본다.

    ── 왜 따로 도는 태스크인가 ──────────────────────────────────

    소켓 루프는 `await websocket.receive_text()` 로만 깨어난다. 폰이 보낸 것을
    처리하는 구조라, **아무 일도 안 일어나는 상태**는 감지할 수가 없다.
    시간이 조건인 안내는 시간을 세는 쪽이 따로 있어야 한다.

    ── 15·16번은 여기 없다 ─────────────────────────────────────

    신호가 끊기는 것은 **앱이 판단한다.** 서버는 자기 메시지가 안 닿는 것을
    알릴 방법이 없다 — 파이프가 끊겼는데 파이프로 알릴 수는 없다.
    그래서 연결이 끊겼다는 안내만은 앱에 문장을 둔다(`NavCoordinator`).

    ── 한 번만 묻는다 ──────────────────────────────────────────

    30초마다 되물으면 잠깐 서서 쉬는 사람에게 계속 말을 건다. 자리가 한 칸이라도
    바뀌면 플래그가 풀려서 다음 정지 때 다시 물을 수 있다.
    """
    try:
        while True:
            await asyncio.sleep(1)
            if session.destination is None or not session.tracker.active:
                continue
            # 층을 옮기는 중에는 원래 안 움직인다. 물으면 안 된다.
            if session.awaiting_floor is not None or session.idle_asked:
                continue
            if not session.last_advance_at:
                continue
            if _now_ms() - session.last_advance_at < IDLE_ASK_MS:
                continue

            session.idle_asked = True
            print(f"[nav {session.id}] {IDLE_ASK_MS // 1000}초째 자리가 그대로 — 확인")
            msg = out("idle", "navigating",
                      utterance="안내를 계속할까요? 화면을 두 번 두드려주세요.",
                      haptic="guide")
            _log_out(session, msg)
            async with lock:
                await _send(websocket, msg)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[nav {session.id}] 정지 감시 오류: {e!r}")


async def _send(websocket: WebSocket, msg: dict) -> None:
    try:
        await websocket.send_text(json.dumps(msg, ensure_ascii=False))
    except Exception:
        pass
