import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from app.nav.map_source import MapDataError
from app.ws import landmark_matcher, llm_matcher, navigation
from app.ws.path_tracker import PathTracker
from app.ws.rssi_filter import RssiFilterPipeline

router = APIRouter()

# Java WebSocketHandler와 동일하게 포팅.
# 주의(원본 그대로 유지된 한계): 연결된 모든 세션이 _filters를 공유함 —
# 사용자가 여러 명 동시 접속하면 같은 비콘 키의 필터 상태가 섞일 수 있음.
# (예전에 얘기했던 "세션별로 필터 분리 안 됨" 이슈, 실사용 단계에선 손봐야 함)
_connections: set[WebSocket] = set()
_filters: dict[str, RssiFilterPipeline] = {}

# 지금 안내 중인 경로. `/monitor` 가 화면에 그리려고 물어본다.
#
# **목적지 응답을 모니터에도 뿌리지 않는다.** 그 응답에는 되묻기 후보가 들어 있어서,
# 뿌리면 폰 A 에게 물어놓은 후보가 다른 연결로 샌다. 서버가 이미 경로를 들고 있으니
# 필요한 쪽이 물어보면 된다.
#
# `_route_seq` 는 "바뀌었는지"를 알리는 번호다. 이게 있어야 모니터가 매번 물어보지
# 않고 바뀐 순간에만 한 번 가져간다.
_current_route: dict | None = None
_route_seq: int = 0

# 추적 키("major-minor")별 최근 필터값. 경로를 걸 때 시작 위치를 고르는 데 쓴다.
# `_filters` 는 원본 키로 되어 있어서 추적 키로는 못 찾는다.
_track_values: dict[str, float] = {}

# 화면에서 고른 판정 설정. 목적지로 거는 경로에도 이걸 그대로 쓴다.
_TUNING_FIELDS = ("threshold", "min_next", "mode", "window_ms", "segments",
                  "min_gap", "gap_window_ms", "min_hold_ms", "require_trend",
                  "trigger_gap", "confirm_delay_ms", "confirm_gap", "confirm_trend")
_track_tuning: dict = {}

# 경로 진행 추적 — 비콘이 바뀌는 시점을 서버가 판단해서 폰에 음성 안내를 내려보내기 위한 것.
# _filters와 마찬가지로 전역 하나라서 동시에 여러 명을 안내하지는 못함 (실측 도구 수준의 한계).
_tracker = PathTracker()

# 음성 목적지 매칭용 랜드마크 목록. 지도 프로젝트 파일에서 읽어온다.
# 파일이 13MB라 매번 파싱하면 느려서 수정 시각으로 캐시한다.
_landmarks: list[landmark_matcher.Landmark] = []
_landmarks_mtime: float | None = None

# 되물었을 때 사용자가 고를 후보.
#
# 이건 **연결마다 따로** 들고 있어야 한다. _filters·_tracker 는 실측 도구 수준의
# 한계로 전역 하나지만, 되묻기는 성격이 다르다. 폰 A 에게 "계단 1번, 2번, 3번
# 중에서" 하고 물어놓은 상태에서 폰 B 가 "두 번째"라고 하면, 전역이면 A 의 후보를
# B 가 집어간다. 시연 때 폰 두 대만 붙여도 바로 터진다.
#
# 그래서 연결별 세션 dict 에 담고, _process_message 가 그걸 받아서 쓴다.
# 세션을 안 넘기면(테스트 등) 그 호출만 쓰고 버리는 임시 세션을 만든다.
_PENDING_KEY = "pending"
_PENDING_AT_KEY = "pending_at"

# 되묻고 나서 이 시간이 지나면 후보를 버린다. 사용자가 답을 안 하고 그냥 둔 채
# 한참 뒤에 다른 말을 하면, 그건 되묻기 답변이 아니라 새 요청으로 봐야 한다.
_PENDING_TTL_MS = 120_000


_APP_DIR = Path(__file__).resolve().parent          # backend-python/app/ws
_PROJECT_DIR = _APP_DIR.parents[1]                  # backend-python
_MONITOR_HTML_PATH = _APP_DIR / "monitor.html"

# 지도 도구와 평면도 파일 위치 찾기.
#
# backend-python / map-tool 은 서로 다른 git 저장소라, 어떻게 내려받았는지에 따라 위치가 달라진다.
# 그래서 한 경로에 고정하지 않고 후보를 순서대로 찾는다. 아래 어느 배치로 clone해도 동작한다:
#
#   (A) 형제 저장소로 나란히 둔 경우          (B) backend-python 안에 둔 경우
#       hanieum_project/                         backend-python/
#         backend-python/                          map-tool/
#         map-tool/                                  map_inspection.html
#           map_inspection.html                      static/
#           static/
#
# 환경변수 MAP_TOOL_DIR 로 직접 지정할 수도 있다 (위 후보에 없는 곳에 둘 때).
_MAP_TOOL_CANDIDATES = [
    _PROJECT_DIR / "map-tool",           # (B) 저장소 안
    _PROJECT_DIR.parent / "map-tool",    # (A) 형제 저장소
    _APP_DIR / "map-tool",               # app/ws 아래에 둔 경우
]


def _resolve_map_tool_dir() -> Path:
    """지도 도구 폴더를 찾는다. 못 찾으면 첫 후보를 돌려줘서 오류 메시지에 경로가 남게 한다."""
    import os

    env = os.environ.get("MAP_TOOL_DIR")
    if env:
        return Path(env).expanduser().resolve()
    for cand in _MAP_TOOL_CANDIDATES:
        if (cand / "map_inspection.html").is_file():
            return cand
    return _MAP_TOOL_CANDIDATES[0]


def _map_tool_dir() -> Path:
    # 매번 찾는다. 서버를 켠 뒤에 파일을 배치해도 재시작 없이 잡히도록.
    return _resolve_map_tool_dir()


# ── 지도 도구(map-tool)는 걷어냈다 ────────────────────────────────
#
# `/map`(단독 도구), `/map-static`(정적 파일), `_extract_map_tool_parts()`(monitor 에
# 통째로 합쳐 넣던 코드)를 모두 지웠다. 편집은 관리자웹이, 노드 생성은 서버가
# 하므로(app/nav/path_nodes.py) 남겨둘 이유가 없었다.
#
# `_map_tool_dir()` 만 남긴다 — DB 가 없을 때 랜드마크 목록을 지도 프로젝트 파일에서
# 읽는 폴백이 아직 그 경로를 쓴다(_load_landmark_list).


@router.get("/monitor", response_class=HTMLResponse)
async def monitor_page() -> HTMLResponse:
    """실측용 모니터 — RSSI, 서버 판정, 지도.

    **지도는 서버가 주는 그래프를 그대로 그린다.** 예전에는 지도 편집 도구
    (map_inspection.html) 3,200줄을 이 페이지에 통째로 합쳐 넣었는데, 그 도구가
    자기 알고리즘으로 만든 그래프는 관리자웹이 만든 것과 눈에 띄게 달랐다.
    이제 노드 생성기가 서버에 있으므로(app/nav/path_nodes.py) `/map-db` 로 받아
    그리면 **화면에 뜨는 것과 안내에 쓰는 것이 같은 값**이 된다.

    편집 기능(마스크 칠하기·비콘 배치·이름 붙이기)은 전부 관리자웹으로 넘어갔다.
    """
    return HTMLResponse(_load_monitor_html())


def _is_slow_message(raw: str) -> bool:
    """LLM 호출로 오래 걸릴 수 있는 메시지인지 싸게 판별한다.

    문자열에 "destination" 이 없으면 파싱조차 안 한다. RSSI 는 초당 열 번씩
    들어오므로 여기서 매번 json 을 두 번 파싱하면 그게 더 손해다.
    """
    if _DESTINATION_TYPE not in raw:
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and data.get("type") == _DESTINATION_TYPE


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _connections.add(websocket)
    # 이 연결만의 상태 (되묻기 후보 등). 연결이 끊기면 같이 사라진다.
    session: dict = {}
    print(f"Connected: {id(websocket)}")

    # 목적지 요청을 순서대로 처리하기 위한 잠금. 해석이 백그라운드로 도는 동안
    # 다음 요청이 들어오면 되묻기 상태가 엉키므로, 이 연결 안에서는 한 번에 하나만.
    lock = asyncio.Lock()
    tasks: set[asyncio.Task] = set()

    # 이 연결의 첫 메시지는 원문 그대로 찍는다.
    #
    # 평소 로그는 RSSI 한 줄씩만 나오는데, 그건 숫자 값을 도는 루프 안에서 찍기
    # 때문이다. 그래서 폰이 **무엇을 보냈는지**는 볼 방법이 없었다 — `_ids` 처럼
    # 새 필드를 붙여도 온 건지 안 온 건지 구분이 안 됐다.
    #
    # 초당 열 번씩 오므로 전부 찍을 수는 없다. 첫 한 번이면 형식을 확인하기에 충분하다.
    first = True

    try:
        while True:
            raw = await websocket.receive_text()

            if first:
                first = False
                head = raw if len(raw) <= 600 else raw[:600] + f"... (총 {len(raw)}자)"
                print(f"[첫 메시지] {head}")

            # 목적지 해석은 Ollama 응답을 기다리느라 몇 초가 걸린다.
            #
            # 이걸 읽기 루프 안에서 기다리면 — await 이든 아니든 — **그동안
            # receive_text() 를 안 부른다.** 그러면 폰이 계속 올려보내는 RSSI 가
            # 소켓에 쌓였다가, 해석이 끝나는 순간 한꺼번에 몰려 들어온다.
            # 판정기는 그걸 "같은 순간에 온 수십 개"로 보게 되어 시간축이 무너진다.
            #
            # (실제로 이 자리에 `await asyncio.to_thread(...)` 만 뒀다가
            #  check_pipeline.py --load 에서 2.6초 끊김이 그대로 잡혔다.
            #  await 은 다른 연결에게만 양보할 뿐, 이 연결의 읽기는 멈춘다.)
            #
            # 그래서 아예 **따로 떼어 보내고 루프는 곧장 다음 메시지를 읽는다.**
            if _is_slow_message(raw):
                task = asyncio.create_task(_handle_destination(websocket, raw, session, lock))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                continue

            # RSSI·측정 메시지는 빠르고 순서가 중요하므로 루프에서 그대로 처리한다.
            payload, guides = _process_message(raw, session)

            # 기존 동작 유지: RSSI/측정 메시지는 "보낸 쪽 제외" 브로드캐스트
            await _broadcast(payload, exclude=websocket)

            # 안내 메시지는 반대로 "보낸 쪽 포함" 전체에 보내야 함.
            # RSSI를 보내는 폰이 곧 안내를 들어야 할 대상이라, 송신자를 빼면 정작 폰이 못 받음.
            for guide in guides:
                await _broadcast(json.dumps(guide, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    finally:
        for task in list(tasks):
            task.cancel()
        _connections.discard(websocket)
        print(f"Disconnected: {id(websocket)}")


async def _handle_destination(websocket: WebSocket, raw: str, session: dict,
                              lock: asyncio.Lock) -> None:
    """목적지 해석을 읽기 루프 밖에서 처리한다.

    LLM 호출은 동기 함수라 스레드로 넘긴다. 그동안 읽기 루프는 계속 돌면서
    RSSI 를 받아 중계하므로, 사용자가 걸어가며 말해도 위치 판정이 끊기지 않는다.
    """
    try:
        async with lock:
            _payload, guides = await asyncio.to_thread(_process_message, raw, session)

        # 목적지 응답은 **물어본 폰에게만** 보낸다.
        #
        # 다른 안내(guide)는 전체에 뿌리는 게 맞지만 이건 다르다. 전체로 뿌리면
        # 폰 A 에게 되물은 후보 목록을 폰 B 도 받아서, B 가 자기 질문의 답인 줄
        # 알고 그 목록으로 되묻기 화면에 들어간다. 실제로 폰 두 대로 재보니
        # B 가 "엘베"라고 물었는데 A 의 계단 후보를 받았다.
        #
        # 안내 음성도 마찬가지다 — 옆 사람 목적지가 내 이어폰에서 들린다.
        for guide in guides:
            await _send(websocket, json.dumps(guide, ensure_ascii=False))
    except asyncio.CancelledError:
        raise
    except Exception as e:          # 여기서 죽으면 조용히 사라지므로 반드시 남긴다
        print(f"[목적지] 처리 중 오류: {e!r}")


async def _send(conn: WebSocket, payload: str) -> None:
    """한 연결에만 보낸다. 끊긴 연결이면 조용히 넘어간다."""
    try:
        await conn.send_text(payload)
    except Exception:
        pass


async def _broadcast(payload: str, exclude: WebSocket | None = None) -> None:
    for conn in list(_connections):
        if exclude is not None and conn is exclude:
            continue
        try:
            await conn.send_text(payload)
        except Exception:
            pass  # 끊긴 연결에 보내다 실패하는 경우 무시하고 계속 진행


# 측정 제어 메시지 스펙
#   { "type": "measure", "event": "start"|"mark"|"end", "sessionId": str,
#     "label": str, "timestamp": int(ms), "device": str, "markCount": int(end에만) }
# 안드로이드 앱에서 측정 시작/종료를 지정하고, 서버는 RSSI 필터를 태우지 않고 그대로 중계한다.
_MEASURE_TYPE = "measure"
_MEASURE_EVENTS = ("start", "mark", "end")


def _process_control(data: dict) -> tuple[str, list[dict]]:
    """측정 제어 메시지를 정규화해서 (중계용 JSON, 안내 메시지 목록)으로 만든다.

    RSSI 경로와 완전히 분리되어 있어서 필터(_filters)를 건드리지 않는다.
    event 값이 스펙에 없으면 그대로 통과시키지 않고 오류로 표시해서, /monitor가
    모르는 이벤트를 측정 시작/종료로 착각하지 않게 한다.

    측정 시작/종료는 경로 안내의 켜짐/꺼짐도 함께 제어한다 — 안내는 측정 구간
    안에서만 나가야 하고, 시작 지점은 측정을 시작하는 순간 확정되어야 하므로.
    """
    event = data.get("event")
    if event not in _MEASURE_EVENTS:
        print(f"알 수 없는 측정 이벤트 무시: {event!r}")
        return json.dumps(
            {"type": _MEASURE_TYPE, "event": "error", "reason": f"unknown event: {event}"},
            ensure_ascii=False,
        ), []

    payload = {
        "type": _MEASURE_TYPE,
        "event": event,
        "sessionId": str(data.get("sessionId") or ""),
        "label": str(data.get("label") or ""),
        "timestamp": data.get("timestamp") or int(time.time() * 1000),
    }

    device = data.get("device")
    if device:
        payload["device"] = str(device)

    if event == "end" and isinstance(data.get("markCount"), int):
        payload["markCount"] = data["markCount"]

    label_text = payload["label"] or "(이름 없음)"
    print(f"[측정 {event}] {label_text} | session={payload['sessionId']} | device={payload.get('device', '-')}")

    # 측정 시작 = 안내 켜기(+ 시작 지점 확정), 측정 종료 = 안내 끄기
    guides: list[dict] = []
    if event == "start":
        started = _tracker.start_session()
        if started:
            print(f"[안내] 시작 지점 {started['number']}번 ({started['name']})")
            guides.append(started)
    elif event == "end":
        ended = _tracker.end_session()
        if ended:
            print("[안내] 측정 종료 — 안내 중지")
            guides.append(ended)

    return json.dumps(payload, ensure_ascii=False), guides


# 경로 안내 메시지 스펙
#   보내는 쪽 → 서버:
#     { "type":"guide", "event":"setPath", "path":[키...], "threshold":3, "minNext":-85 }
#     { "type":"guide", "event":"stop" }
#   서버 → 전체(폰 포함):
#     { "type":"guide", "event":"transition", "direction":"forward"|"backward",
#       "index":int, "total":int, "beacon":str, "name":str, "isLast":bool,
#       "speech":str, "timestamp":int }
# speech는 폰이 그대로 읽어주는 문장 — 문구를 바꿔도 앱을 다시 빌드할 필요가 없게 서버가 만든다.
_GUIDE_TYPE = "guide"


def _process_guide(data: dict) -> str:
    event = data.get("event")

    if event == "setPath":
        path = data.get("path")
        if not isinstance(path, list):
            path = []
        result = _tracker.set_path(
            [str(p) for p in path],
            threshold=data.get("threshold"),
            min_next=data.get("minNext"),
            mode=data.get("mode"),
            window_ms=data.get("windowMs"),
            segments=data.get("segments"),
            min_gap=data.get("minGap"),
            gap_window_ms=data.get("gapWindowMs"),
            min_hold_ms=data.get("minHoldMs"),
            require_trend=data.get("requireTrend"),
            trigger_gap=data.get("triggerGap"),
            confirm_delay_ms=data.get("confirmDelayMs"),
            confirm_gap=data.get("confirmGap"),
            confirm_trend=data.get("confirmTrend"),
        )
        print(f"[안내] 경로 설정: {result.get('path')} (활성={result.get('enabled')})")
        return json.dumps(result, ensure_ascii=False)

    if event == "stop":
        result = _tracker.stop()
        print("[안내] 경로 안내 중지")
        return json.dumps(result, ensure_ascii=False)

    if event == "setTuning":
        # 화면에서 고른 판정 설정을 기억해둔다.
        #
        # 목적지로 거는 경로는 `set_path(keys)` 를 인자 없이 부르므로, 이 값이
        # 없으면 서버 기본값(confirm)으로만 돌았다. 화면은 "구간 분할"을 가리키는데
        # 서버는 confirm 으로 도는 상태가 되어, 구간 분할이 깐깐해진 것처럼 보였다.
        global _track_tuning
        # 화면은 camelCase(minNext) 로, set_path 는 snake_case(min_next) 로 쓴다.
        import re as _re
        _track_tuning = {}
        for k, v in data.items():
            snake = _re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower()
            if snake in _TUNING_FIELDS and v is not None:
                _track_tuning[snake] = v
        print(f"[안내] 판정 설정: {_track_tuning}")
        return json.dumps(
            {"type": _GUIDE_TYPE, "event": "tuningSet", **_track_tuning}, ensure_ascii=False)

    if event == "setFloor":
        # `/monitor` 에서 고른 층을 서버 전체에 걸어둔다.
        #
        # 이걸 모니터가 보내는 메시지에만 적용하면 소용이 없다. 정작 확인하려는 것은
        # **폰이 말한 목적지**가 그 층에서 찾아지는가인데, 폰은 자기 메시지에
        # floorId 를 안 싣기 때문이다.
        global _floor_override, _db_landmarks, _db_landmarks_floor
        floor_id = str(data.get("floorId") or "") or None
        _floor_override = floor_id
        _db_landmarks = []          # 층이 바뀌었으니 목적지 목록을 다시 읽는다
        _db_landmarks_floor = None
        label = _floor_label(floor_id) if floor_id else "해제 (비콘으로 판정)"
        print(f"[층] 고정: {label}")
        return json.dumps(
            {"type": _GUIDE_TYPE, "event": "floorSet", "floorId": floor_id, "label": label},
            ensure_ascii=False)

    print(f"알 수 없는 안내 이벤트 무시: {event!r}")
    return json.dumps(
        {"type": _GUIDE_TYPE, "event": "error", "reason": f"unknown event: {event}"},
        ensure_ascii=False,
    )


# 음성 목적지 메시지 스펙
#   폰 → 서버:
#     { "type":"destination", "event":"resolve", "text":"화장실", "requestId":"..." }
#     { "type":"destination", "event":"choose",  "text":"두 번째", "requestId":"..." }
#     { "type":"destination", "event":"cancel" }
#     { "type":"destination", "event":"list" }        ← 등록된 랜드마크 확인용
#   서버 → 전체(폰 포함):
#     { "type":"destination", "event":"resolved",  "landmark":{...},   "speech":"..." }
#     { "type":"destination", "event":"ambiguous", "candidates":[...], "speech":"..." }
#     { "type":"destination", "event":"notFound",  "suggestions":[...], "speech":"..." }
#
# 폰은 STT 결과 문자열만 보내고, 매칭은 전부 서버가 한다.
# 별칭 사전이나 임계값을 고쳐도 앱을 다시 빌드할 필요가 없다.
_DESTINATION_TYPE = "destination"


_last_state_log: float = 0.0


def _log_tracking_state() -> None:
    """왜 안내가 안 나가는지 3초에 한 번씩 알려준다.

    RSSI 는 초당 열 번씩 들어오므로 매번 찍으면 로그가 그것만으로 가득 찬다.
    그렇다고 아무것도 안 찍으면 "판정이 도는 중"과 "아예 멈춤"이 구분되지 않는다.
    """
    global _last_state_log
    now = time.time()
    if now - _last_state_log < 3.0:
        return
    _last_state_log = now

    t = _tracker
    if not t.enabled:
        return
    if not t.active:
        print("[추적] 경로는 있는데 시작되지 않음 (active=False)")
        return

    cur = t.path[t.index] if t.index < len(t.path) else "?"
    nxt = t.path[t.index + 1] if t.index + 1 < len(t.path) else None
    have = sum(1 for k in t.path if t._latest(k) is not None)
    print(f"[추적] {t.index + 1}/{len(t.path)}번 ({cur}) · 다음 {nxt} · "
          f"데이터 있는 칸 {have}개 · {t.last_verdict}")


def _seed_tracker_index(keys: list[str]) -> None:
    """경로 중 지금 가장 세게 잡히는 비콘을 시작 위치로 삼는다.

    추적기 자신의 이력은 방금 지워졌으므로 쓸 수 없다. 대신 필터가 들고 있는
    현재 추정값(`RssiFilterPipeline.x`)을 본다 — 같은 값을 먹여 온 것이라
    시작 위치를 정하기에 충분하다.
    """
    def value_of(key: str) -> float | None:
        if key in _track_values:                    # 추적 키 ("major-minor")
            return _track_values[key]
        pipe = _filters.get(key)                    # 원본 키 ("MAC|이름")
        if pipe is not None and getattr(pipe, "initialized", False):
            return float(pipe.x)
        return None

    best_idx, best_val = None, float("-inf")
    for idx, key in enumerate(keys):
        v = value_of(key)
        if v is not None and v > best_val:
            best_idx, best_val = idx, v
    if best_idx is None:
        # 경로에 있는 비콘이 하나도 안 잡히는 상태. 0번에서 시작한다 —
        # 걸어가다 신호가 잡히면 그때 판정이 따라온다.
        return
    _tracker.index = best_idx
    # start_session 이 이력을 보고 다시 고르지 않도록 그 자리에 값을 심어둔다.
    for key in keys:
        v = value_of(key)
        if v is not None:
            _tracker.feed(key, v)
    print(f"[경로] 시작 위치 {best_idx + 1}/{len(keys)}번 "
          f"({keys[best_idx]}, {best_val:.0f}dBm)")


def _attach_route(msg: dict, landmark: landmark_matcher.Landmark,
                  from_beacon: str | None = None) -> None:
    """목적지까지의 경로를 만들어 추적기에 얹고, 응답에 실어 보낸다.

    **실패해도 목적지 응답 자체는 살린다.** 경로를 못 만드는 이유는 여러 가지인데
    (축척 미설정, 비콘 미등록, 길 없음), 그때 "목적지를 못 알아들었다"로 보이면
    엉뚱한 곳을 고치게 된다. 그래서 이유를 따로 실어 보낸다.

    `from_beacon` 은 출발점을 손으로 지정할 때만 쓴다(`/monitor` 에서 폰 없이
    시험할 때). 실제 안내에서는 안 온다 — 폰이 올린 RSSI 로 정한다.
    """
    try:
        plan = navigation.plan_route(landmark.id, list(_filters.keys()), _filters,
                                     from_beacon_id=from_beacon,
                                     beacon_ids=_beacon_ids)
    except MapDataError as e:
        msg["routeError"] = str(e)
        print(f"[경로] 만들지 못함: {str(e).splitlines()[0]}")
        return
    except Exception as e:                     # DB 가 없거나 예상 못한 오류
        msg["routeError"] = f"경로 생성 중 오류: {e}"
        print(f"[경로] 오류: {e}")
        return

    # **경로는 언제나 실어 보낸다.**
    #
    # 경로를 만드는 것과 추적을 거는 것은 다른 일이다. 앞은 지도만 있으면 되고,
    # 뒤는 폰이 비콘을 올리고 있어야 한다. 예전엔 이 둘을 한 덩어리로 봐서, 폰이
    # 안 붙어 있으면 **이미 계산해 둔 경로를 통째로 버렸다.** 그러면 지도로 경로를
    # 확인하는 것조차 못 한다.
    msg["route"] = {
        "from": plan.from_beacon,
        "floorId": plan.floor_id,
        "distanceM": round(plan.distance_m, 1),
        "seconds": plan.seconds,
        "crossings": plan.route.crossings,
        "beacons": [s.beacon_id for s in plan.route.steps],
        "keys": plan.keys,
        "missing": plan.missing,
    }

    # 서버가 들고 있는다. 누가 물어본 것이든(폰이든 /monitor 든) 화면에 그리려면
    # 이 값이 필요하다. 응답을 뿌리는 대신 여기 두는 이유는 위 주석 참고.
    global _current_route, _route_seq
    _route_seq += 1
    _current_route = {
        "seq": _route_seq,
        "floorId": plan.floor_id,
        "from": plan.from_beacon,
        "destinationId": landmark.id,
        "destinationName": landmark.name,
        "heard": msg.get("heard", ""),
        "distanceM": round(plan.distance_m, 1),
        "seconds": plan.seconds,
        "crossings": plan.route.crossings,
        "nodeIds": plan.route.node_ids,
        "beacons": [s.beacon_id for s in plan.route.steps],
        "missing": plan.missing,
    }
    tracking = len(plan.keys) >= 2
    msg["speech"] = plan.speech(landmark.name, tracking)

    if tracking:
        # 화면에서 고른 판정 설정을 그대로 쓴다. 안 넘기면 서버 기본값(confirm)으로만
        # 돌아서, 화면에 표시된 모드와 실제 판정이 갈라진다.
        _tracker.set_path(plan.keys, **_track_tuning)
        # 시작 위치를 지금 신호로 잡아준다.
        #
        # `start_session()` 만 부르면 **항상 0번에서 시작한다.** 바로 앞에서
        # `set_path` 가 이력을 지웠는데, 그 함수는 지워진 이력에서 시작 위치를
        # 고르기 때문이다. `/monitor` 에서는 경로를 등록하고 사람이 "추적 시작"을
        # 누를 때까지 시간이 흘러 이력이 차 있어서 드러나지 않았다.
        #
        # 경로 중간에서 목적지를 말하면(흔한 일이다) 서버는 출발점에 있다고 여기고,
        # 사용자가 걸어가도 안내 번호가 계속 어긋난다.
        _seed_tracker_index(plan.keys)
        _tracker.start_session()
        msg["tracking"] = True
        # 경로를 걸자마자 "이 중 몇 개가 실제로 들리는가"를 찍는다.
        # 0개면 추적 키가 안 맞는 것이고(폰이 _ids 를 안 보내거나 minor 가 다름),
        # 그러면 안내가 영영 안 나간다 — 로그가 없으면 원인을 좁힐 수 없다.
        heard = [k for k in plan.keys if k in _track_values]
        print(f"[경로] 추적 {len(plan.keys)}칸 · 지금 들리는 것 {len(heard)}개 "
              f"{heard[:6]}  시작 {_tracker.index + 1}번")
        if not heard:
            print("       ↳ 들리는 게 없다. 폰이 _ids 를 보내는지, "
                  "그 major/minor 가 DB 와 같은지 확인 필요")
        print(f"[경로] {plan.from_beacon} → {landmark.name}  "
              f"{plan.distance_m:.0f}m / {plan.seconds}초 / 비콘 {len(plan.keys)}개 · 추적 시작")
    else:
        msg["tracking"] = False
        msg["trackingNote"] = (
            f"경로는 만들었지만 지금 잡히는 비콘이 {len(plan.keys)}개뿐이라 추적은 걸지 않았습니다"
            + (f" (안 잡히는 비콘: {', '.join(plan.missing[:6])})" if plan.missing else "")
        )
        print(f"[경로] {plan.from_beacon} → {landmark.name}  "
              f"{plan.distance_m:.0f}m / 비콘 {len(plan.route.steps)}개 · "
              f"추적 안 검(잡히는 비콘 {len(plan.keys)}개)")


def _load_landmark_list(floor_id: str | None = None) -> list[landmark_matcher.Landmark]:
    """목적지 후보 목록.

    **DB 를 먼저 본다.** 관리자웹이 넣은 것이 정본이고, 경로 생성도 DB 의
    랜드마크 id 로 하므로 매칭에 쓰는 목록이 다르면 id 가 안 맞는다.
    DB 가 비어 있거나 붙지 않으면 예전처럼 지도 프로젝트 파일로 떨어진다
    (서버만 띄우고 지도 도구로 실측하던 흐름을 깨지 않기 위해).

    `floor_id` 는 목적지를 어느 층에서 찾을지다. 안 주면 지금 잡히는 비콘으로
    알아낸다 — 층을 안 좁히면 다른 층·다른 건물의 같은 이름이 후보로 섞인다.
    """
    from_db = _load_landmarks_from_db(floor_id or _current_floor_id())
    if from_db:
        return from_db

    global _landmarks, _landmarks_mtime

    path = _map_tool_dir() / "static" / "mappin_project.json"
    if not path.is_file():
        return _landmarks

    mtime = path.stat().st_mtime
    if _landmarks and _landmarks_mtime == mtime:
        return _landmarks

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"랜드마크 파일을 읽지 못했습니다: {e}")
        return _landmarks

    _landmarks = landmark_matcher.load_landmarks(data.get("landmarks") or [])
    _landmarks_mtime = mtime
    print(f"[목적지] 랜드마크 {len(_landmarks)}개 로드: {path}")
    return _landmarks


_DB_LANDMARK_TTL_MS = 10_000
_db_landmarks: list[landmark_matcher.Landmark] = []
_db_landmarks_at: float = 0.0
_db_landmarks_floor: str | None = None      # 캐시가 어느 층 것인지 — 층이 바뀌면 다시 읽는다


# 폰이 올려준 비콘 식별자. {비콘키: {"major":104, "minor":3}}
#
# 앱이 iBeacon 광고에서 뽑아 `_ids` 로 실어 보낸다. 없을 수도 있다 —
# 펌웨어를 다시 굽기 전이거나 iBeacon 이 아닌 비콘이면 비어 있다.
_beacon_ids: dict[str, dict] = {}


# major/minor 없이 들어온 비콘 — 한 번만 알려준다
_no_id_warned: set[str] = set()

# 실측용 층 고정.
#
# 원래 층은 폰이 잡은 비콘으로 정해진다. 그런데 펌웨어를 아직 다 안 구워서 major 가
# 실제 층과 다르거나, 폰 없이 확인하고 싶을 때가 있다. 그럴 때 `/monitor` 에서
# 고른 층을 여기 박아두고 **폰이 보낸 요청에도 그 층을 쓴다.**
#
# 전역 하나다 — 실측 도구 수준의 한계이고, `_filters`·`_tracker` 와 같은 성격이다.
# 서비스에서는 폰마다 다른 층에 있으므로 이런 고정을 쓰면 안 된다.
_floor_override: str | None = None


def _floor_label(floor_id: str) -> str:
    """로그에 쓸 층 이름. 못 찾으면 id 를 그대로."""
    try:
        from app.building.models import Building
        from app.database import SessionLocal
        from app.floor.models import Floor

        db = SessionLocal()
        try:
            f = db.get(Floor, floor_id)
            if f is None:
                return f"{floor_id} (DB 에 없음)"
            b = db.get(Building, f.building_id)
            return f"{b.name if b else '?'} {f.floor}층"
        finally:
            db.close()
    except Exception:
        return floor_id


def _report_beacon_id(key: str, ids: dict) -> None:
    """폰이 올린 major/minor 를 받았다고 알린다. **어느 층으로 읽혔는지까지 찍는다.**

    받았다는 것만으로는 부족하다. major 가 와도 그 값이 DB 의 층과 안 맞으면
    안내가 안 되는데, 그때 로그가 "받았음"만 말하면 다음에 어디를 볼지 알 수 없다.
    """
    name = navigation._ble_name(key)
    major, minor = ids.get("major"), ids.get("minor")
    where = ""
    try:
        from app.beacon.models import Beacon
        from app.database import SessionLocal
        from app.floor.models import Floor

        db = SessionLocal()
        try:
            if isinstance(major, int) and major >= 100:
                row = db.query(Floor.floor).filter(Floor.major == major).first()
                where = f" → {row[0]}층" if row else f" → major {major} 인 층이 DB 에 없음"
            elif isinstance(major, int):
                where = f" → 층 아님(major 는 100+층번호, 지금 {major})"
            hit = db.query(Beacon.id).filter(Beacon.name == name).first()
            if hit is None:
                where += " · 이 이름의 비콘이 DB 에 없음"
        finally:
            db.close()
    except Exception:
        pass
    print(f"[식별자] {name} major={major} minor={minor}{where}")


def _note_missing_id(key: str) -> None:
    """major/minor 없이 RSSI 만 온 비콘. 비콘당 한 번만 알린다.

    이게 없으면 **아무 일도 안 일어나는 것**과 구분이 안 된다. 앱이 못 읽은 건지,
    안 보낸 건지, 애초에 안 온 건지 로그가 조용하면 알 방법이 없다.
    """
    if key in _no_id_warned or key in _beacon_ids:
        return
    _no_id_warned.add(key)
    print(f"[식별자] {navigation._ble_name(key)} — major/minor 없이 RSSI 만 옴 "
          f"(앱이 iBeacon 을 못 읽었거나 옛 버전)")


def _current_floor_id() -> str | None:
    """지금 있는 층. 알 수 없으면 None.

    `/monitor` 에서 층을 고정해두면 그것이 가장 먼저다 — 실측 중에는 화면에서
    고른 층이 곧 "지금 있는 층"이다.

    고정이 없으면 두 가지로 찾는다. **major 가 먼저다.**

        major   = 100 + 층번호 — 신호 자체에 층이 들어 있다. 조회가 필요 없다.
        이름     폰이 major 를 안 보낼 때. 비콘 이름으로 DB 를 뒤진다.

    펌웨어가 아직 전부 `major=1` 이라 지금은 이름 쪽으로 떨어지는 경우가 많다.
    비콘별로 다시 구우면 자연히 major 쪽으로 넘어간다.
    """
    if _floor_override:
        return _floor_override

    key = navigation.strongest_beacon_key(_filters)
    if key is None:
        return None

    try:
        from app.beacon.models import Beacon
        from app.database import SessionLocal
        from app.floor.models import Floor

        db = SessionLocal()
        try:
            ids = _beacon_ids.get(key) or {}
            major = ids.get("major")
            # major=1 은 "아직 안 구운 펌웨어"의 기본값이라 층으로 쓸 수 없다.
            # 층은 100 부터 시작한다(100+층번호).
            if isinstance(major, int) and major >= 100:
                row = db.query(Floor.id).filter(Floor.major == major).first()
                if row:
                    return row[0]

            name = navigation._ble_name(key)
            row = db.query(Beacon.floor_id).filter(Beacon.name == name).first()
            return row[0] if row else None
        finally:
            db.close()
    except Exception:
        return None


def _load_landmarks_from_db(floor_id: str | None = None) -> list[landmark_matcher.Landmark]:
    """목적지 후보를 DB 에서 읽는다.

    **층을 반드시 좁힌다.** 예전에는 등록된 모든 층의 목적지를 한 통에 담아서,
    4층에서 "화장실"이라고 말하면 1층 화장실이나 아예 다른 건물의 화장실이 후보로
    올라왔다. 이름이 겹치는 것이 정상인 값들이라(화장실·계단·엘리베이터는 층마다
    있다) 되묻기가 무의미해지고, 운 나쁘면 갈 수 없는 곳으로 안내한다.

    층을 모르면 **빈 목록을 준다.** 아무 층이나 골라 답하는 것보다 "어디 있는지
    모르겠다"가 낫다 — 잘못 안내하면 사용자가 알아챌 방법이 없다.

    매 발화마다 DB 를 치지 않도록 잠깐 들고 있는다. 관리자가 목적지를 추가하면
    10초 안에 반영된다 — 실측 중에 고치고 바로 말해보는 흐름을 막지 않을 만큼 짧고,
    LLM 호출 한 번보다는 훨씬 싸다.
    """
    global _db_landmarks, _db_landmarks_at, _db_landmarks_floor

    if floor_id is None:
        return []

    now = time.time() * 1000
    if (_db_landmarks and _db_landmarks_floor == floor_id
            and now - _db_landmarks_at < _DB_LANDMARK_TTL_MS):
        return _db_landmarks

    try:
        from app.database import SessionLocal
        from app.nav.db_map_source import DbMapSource

        db = SessionLocal()
        try:
            raw = [{"id": lm.id, "name": lm.name, "x": lm.x, "y": lm.y}
                   for lm in DbMapSource(db).landmarks(floor_id)]
        finally:
            db.close()
    except Exception as e:
        # DB 없이 파일로만 돌리는 흐름을 막지 않는다. 조용히 파일로 떨어진다.
        print(f"[목적지] DB 조회 건너뜀: {e}")
        return []

    if not raw:
        return []
    _db_landmarks = landmark_matcher.load_landmarks(raw)
    _db_landmarks_at = now
    _db_landmarks_floor = floor_id
    print(f"[목적지] {floor_id} 층의 랜드마크 {len(_db_landmarks)}개 로드")
    return _db_landmarks


def _landmark_json(lm: landmark_matcher.Landmark) -> dict:
    return {"id": lm.id, "name": lm.name, "x": lm.x, "y": lm.y}


def _take_pending(session: dict) -> list[landmark_matcher.Landmark]:
    """이 연결이 답을 기다리고 있는 후보. 오래됐으면 없는 것으로 친다."""
    pending = session.get(_PENDING_KEY) or []
    if not pending:
        return []
    age = int(time.time() * 1000) - int(session.get(_PENDING_AT_KEY) or 0)
    if age > _PENDING_TTL_MS:
        session[_PENDING_KEY] = []
        print(f"[목적지] 되묻기 만료 ({age / 1000:.0f}초 무응답) — 새 요청으로 처리")
        return []
    return pending


def _process_destination(data: dict, session: dict) -> tuple[str, list[dict]]:
    """음성으로 말한 목적지를 랜드마크에 잇는다.

    응답은 중계용이 아니라 안내용으로 돌려준다 — 요청을 보낸 폰이 바로
    그 응답을 들어야 하는 대상이라, "보낸 쪽 제외" 브로드캐스트를 타면 안 된다.

    **이 함수는 LLM 호출 때문에 오래 걸릴 수 있다.** 그래서 이벤트 루프가 아니라
    별도 스레드에서 불린다(websocket_endpoint 참고). 여기서 만지는 상태는
    인자로 받은 session 뿐이라 다른 연결과 섞이지 않는다.
    """
    event = data.get("event")
    request_id = str(data.get("requestId") or "")
    text = str(data.get("text") or "")
    # 목적지는 **지금 있는 층**에서만 찾는다. /monitor 는 고른 층을 실어 보내고,
    # 폰은 안 보내므로 잡히는 비콘으로 알아낸다.
    floor_id = str(data.get("floorId") or "") or None
    landmarks = _load_landmark_list(floor_id)

    def reply(result: landmark_matcher.MatchResult) -> tuple[str, list[dict]]:
        msg: dict = {
            "type": _DESTINATION_TYPE,
            "event": result.status,          # resolved | ambiguous | notFound
            "requestId": request_id,
            "heard": text,
            "speech": result.speech,
            "source": result.source,         # llm | rule | llm→rule(사유)
            "timestamp": int(time.time() * 1000),
        }
        if result.status == "resolved" and result.landmark:
            msg["landmark"] = _landmark_json(result.landmark)
            session[_PENDING_KEY] = []
            print(f"[목적지] \"{text}\" → {result.landmark.name}  [{result.source}]")
            # 목적지가 정해졌으면 곧바로 경로를 만들어 추적기에 얹는다.
            # 예전에는 여기서 끝나서, /monitor 에서 사람이 비콘 순서를 손으로
            # 등록해야 안내가 돌았다.
            _attach_route(msg, result.landmark, str(data.get("fromBeacon") or "") or None)
        elif result.status == "ambiguous":
            msg["candidates"] = [_landmark_json(c) for c in result.candidates]
            session[_PENDING_KEY] = list(result.candidates)
            session[_PENDING_AT_KEY] = int(time.time() * 1000)
            names = ", ".join(c.name for c in result.candidates)
            print(f"[목적지] \"{text}\" → 후보 여러 개: {names}  [{result.source}]")
        else:
            msg["suggestions"] = [_landmark_json(c) for c in result.candidates]
            session[_PENDING_KEY] = []
            print(f"[목적지] \"{text}\" → 매칭 실패  [{result.source}]")
        return json.dumps(msg, ensure_ascii=False), [msg]

    if event == "resolve":
        # 새 목적지를 말하면 이전 되묻기는 버린다
        session[_PENDING_KEY] = []
        # LLM으로 해석한다. 모델이 안 떠 있거나 응답이 이상하면
        # llm_matcher가 알아서 규칙 엔진 결과로 넘어간다.
        return reply(llm_matcher.resolve(text, landmarks))

    if event == "choose":
        pending = _take_pending(session)
        if not pending:
            # 되묻지도 않았는데 선택이 왔다 — 그냥 새 요청으로 처리한다
            return reply(llm_matcher.resolve(text, landmarks))
        # 대답 해석도 모델이 한다. 후보 목록과 대답을 주고 고르게 한다.
        return reply(llm_matcher.choose(text, pending))

    if event == "cancel":
        session[_PENDING_KEY] = []
        msg = {"type": _DESTINATION_TYPE, "event": "cancelled", "requestId": request_id,
               "speech": "", "timestamp": int(time.time() * 1000)}
        return json.dumps(msg, ensure_ascii=False), [msg]

    if event == "list":
        msg = {"type": _DESTINATION_TYPE, "event": "list", "requestId": request_id,
               "landmarks": [_landmark_json(lm) for lm in landmarks],
               "speech": "", "timestamp": int(time.time() * 1000)}
        return json.dumps(msg, ensure_ascii=False), [msg]

    print(f"알 수 없는 목적지 이벤트 무시: {event!r}")
    return json.dumps(
        {"type": _DESTINATION_TYPE, "event": "error", "reason": f"unknown event: {event}"},
        ensure_ascii=False,
    ), []


def _process_message(raw: str, session: dict | None = None) -> tuple[str, list[dict]]:
    """수신 메시지를 처리해서 (중계할 JSON 문자열, 전체에 보낼 안내 메시지 목록)을 돌려준다.

    안내 메시지를 따로 돌려주는 이유: 일반 중계는 "보낸 쪽 제외"인데,
    안내는 RSSI를 보낸 폰이 받아야 하므로 송신자를 포함해 보내야 한다.
    """
    try:
        data: dict = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"필터 오류, 원본 전송: {e}")
        return raw, []

    # RSSI 데이터가 아닌 제어 메시지는 필터 경로를 타지 않고 따로 처리
    if isinstance(data, dict) and data.get("type") == _MEASURE_TYPE:
        return _process_control(data)

    if isinstance(data, dict) and data.get("type") == _GUIDE_TYPE:
        return _process_guide(data), []

    if isinstance(data, dict) and data.get("type") == _DESTINATION_TYPE:
        return _process_destination(data, session if session is not None else {})

    try:
        filtered: dict = {"timestamp": data.get("timestamp", int(time.time() * 1000))}
        got_rssi = False

        # 앱이 iBeacon 에서 뽑아 보낸 major/minor. 있으면 갱신해 둔다.
        # RSSI 처럼 매번 오지만 값은 잘 안 바뀌므로 그냥 덮어쓴다.
        ids = data.get("_ids")
        if isinstance(ids, dict):
            for key, one in ids.items():
                if isinstance(one, dict):
                    # 값이 바뀔 때만 찍는다. RSSI 처럼 초당 열 번씩 오는데 매번 찍으면
                    # 로그가 그것만으로 가득 차서 정작 볼 게 안 보인다.
                    if _beacon_ids.get(key) != one:
                        _beacon_ids[key] = one
                        _report_beacon_id(key, one)
                    _beacon_ids[key] = one

        for key, value in data.items():
            if key == "timestamp":
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue

            rssi = float(value)
            if rssi >= 0 or rssi == 127:
                continue

            pipeline = _filters.setdefault(key, RssiFilterPipeline())
            filtered_rssi = pipeline.filter(rssi)
            rounded = round(filtered_rssi, 1)

            filtered[key] = rssi  # 원본값
            filtered[f"{key}__f"] = rounded  # 칼만 필터값

            # 추적기에도 같은 필터값을 먹인다.
            #
            # **두 가지 키로 먹인다.**
            #   원본 키 "MAC|이름"   — /monitor 에서 손으로 짠 경로가 이걸 쓴다
            #   추적 키 "major-minor" — 목적지로 만든 경로가 이걸 쓴다
            #
            # 목적지 경로를 minor 기반으로 바꾼 이유는, 원본 키는 폰이 그 비콘을
            # 한 번이라도 봐야 생겨서 아직 안 잡힌 앞쪽 비콘을 경로에 못 세우기
            # 때문이다(navigation.tracking_key 참고). 둘 다 먹여야 기존 실측 흐름을
            # 깨지 않으면서 새 방식이 돈다 — 경로에는 둘 중 하나만 들어간다.
            _tracker.feed(key, filtered_rssi)
            ids = _beacon_ids.get(key)
            if ids:
                tkey = navigation.tracking_key(ids.get("major"), ids.get("minor"))
                if tkey:
                    _tracker.feed(tkey, filtered_rssi)
                    _track_values[tkey] = filtered_rssi
            got_rssi = True

            _note_missing_id(key)
            #print(f"비콘 {key} | 원본: {rssi:.1f} | 필터: {rounded:.1f} | 상태: {pipeline.state.value}")

        guides: list[dict] = []
        if got_rssi:
            transition = _tracker.evaluate()
            if transition:
                print(f"[안내] {transition['speech']}")
                guides.append(transition)
            else:
                _log_tracking_state()

            # 판정 결과를 중계 payload에 실어 보냄. /monitor는 이걸 화면에 표시만 하고
            # 자체 판정은 하지 않는다 — 판정 주체를 서버 하나로 유지하기 위함.
            # (별도 메시지로 안 보내고 여기 얹는 이유: 메시지 수를 안 늘리면서 RSSI와 항상 같은 시점의 상태가 되도록)
            snapshot = _tracker.snapshot()
            if snapshot:
                filtered["_track"] = snapshot

            # 경로가 바뀐 순간을 알리는 번호. 경로 자체(노드 수백 개)를 여기 실으면
            # 초당 열 번씩 같은 값을 보내게 되므로 번호만 얹는다.
            #
            # **`_track` 안에 넣으면 안 된다.** `_track` 은 추적이 걸렸을 때만
            # 나오는데(비콘 2개 이상 필요), 경로가 생긴 것과 추적이 걸린 것은
            # 별개다. 비콘 하나로 시험하면 경로는 나오는데 화면에 안 뜬다.
            #
            # **경로가 없을 때도 번호를 보낸다.** 예전엔 `_current_route` 가 있을
            # 때만 실었는데, 그러면 안내를 취소해도 모니터는 번호가 안 바뀐 줄 알고
            # 지워진 경로를 계속 그린다.
            filtered["_routeSeq"] = _route_seq

        return json.dumps(filtered, ensure_ascii=False), guides
    except Exception as e:
        print(f"필터 오류, 원본 전송: {e}")
        return raw, []


# monitor 페이지는 app/ws/monitor.html 에 있다.
# 예전에는 이 파일 안의 파이썬 문자열이었는데, HTML/CSS/JS 1179행이 문자열로 들어가 있어
# 에디터가 문법을 인식하지 못했고 백슬래시를 두 번 써야 했다(실제로 CSV 줄바꿈을 \n 으로
# 쓴 탓에 페이지 전체가 죽는 버그가 있었다). 별도 파일로 빼서 그 문제를 없앴다.
# 매 요청마다 읽으므로 개발 중에는 서버 재시작 없이 파일만 고쳐도 반영된다.
def _load_monitor_html() -> str:
    return _MONITOR_HTML_PATH.read_text(encoding="utf-8")
