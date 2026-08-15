import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.ws import landmark_matcher, llm_matcher
from app.ws.path_tracker import PathTracker
from app.ws.rssi_filter import RssiFilterPipeline

router = APIRouter()

# Java WebSocketHandler와 동일하게 포팅.
# 주의(원본 그대로 유지된 한계): 연결된 모든 세션이 _filters를 공유함 —
# 사용자가 여러 명 동시 접속하면 같은 비콘 키의 필터 상태가 섞일 수 있음.
# (예전에 얘기했던 "세션별로 필터 분리 안 됨" 이슈, 실사용 단계에선 손봐야 함)
_connections: set[WebSocket] = set()
_filters: dict[str, RssiFilterPipeline] = {}

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


@router.get("/map-static/{filename}")
async def map_static_file(filename: str):
    """지도 도구가 쓰는 정적 파일(평면도 이미지, 프로젝트 JSON) 제공.

    프로젝트 파일이 13MB가 넘어서 HTML에 끼워 넣지 않고 따로 받아가게 한다.
    """
    # 상위 경로 탈출(../) 방지 — 파일 이름만 받는다
    safe = Path(filename).name
    target = _map_tool_dir() / "static" / safe
    if not target.is_file():
        return JSONResponse({"error": f"파일을 찾을 수 없습니다: {safe}"}, status_code=404)
    return FileResponse(target)


@router.get("/map-static")
async def map_static_list():
    """자동 로드용 — static 폴더에 어떤 파일이 있는지 알려준다."""
    static_dir = _map_tool_dir() / "static"
    if not static_dir.is_dir():
        return JSONResponse({"files": []})
    return JSONResponse({"files": sorted(p.name for p in static_dir.iterdir() if p.is_file())})


@router.get("/map", response_class=HTMLResponse)
async def map_tool_page() -> HTMLResponse:
    try:
        return HTMLResponse((_map_tool_dir() / "map_inspection.html").read_text(encoding="utf-8"))
    except FileNotFoundError:
        # 경로가 어긋났을 때 빈 화면 대신 원인을 알려준다 (실측 현장에서 디버깅하기 쉽게)
        return HTMLResponse(
            "<h3>지도 도구 파일을 찾지 못했습니다</h3>"
            f"<p>찾은 위치: <code>{_map_tool_dir() / 'map_inspection.html'}</code></p>"
            "<p>MAP_TOOL_DIR 환경변수로 폴더를 직접 지정할 수도 있습니다.</p>"
            "<p>map-tool 폴더가 backend-python과 같은 상위 폴더 안에 있는지 확인해주세요.</p>",
            status_code=404,
        )


def _extract_map_tool_parts() -> tuple[str, str, str]:
    """지도 도구 HTML에서 (스타일, 본문, 스크립트)를 뽑아 monitor에 합칠 수 있게 만든다.

    지도 도구는 WEB-FE 화면을 흉내낸 앱 셸(사이드바·상단바·로고)을 갖고 있는데,
    monitor 안에서는 껍데기일 뿐이라 떼어낸다. 나중에 WEB-FE로 옮겨갈 때를 대비해
    map_inspection.html은 단독 실행 가능한 원본 그대로 두고, 합치는 건 여기서만 한다.
    """
    import re

    raw = (_map_tool_dir() / "map_inspection.html").read_text(encoding="utf-8")

    style = re.search(r"<style>(.*?)</style>", raw, re.S)
    script = re.search(r"<script>(.*?)</script>", raw, re.S)
    body = re.search(r"<body>(.*?)</body>", raw, re.S)
    if not (style and script and body):
        raise ValueError("지도 도구 HTML 구조가 예상과 다릅니다 (style/script/body를 못 찾음)")

    css = style.group(1)
    # 전역 선택자는 monitor 쪽 스타일을 덮어쓰므로 제거한다.
    # (겹치는 클래스는 .on 하나뿐인데 양쪽 다 복합 선택자라 충돌하지 않음)
    css = re.sub(r"^\s*\*\s*\{[^}]*\}", "", css, flags=re.M)
    css = re.sub(r"^\s*body\s*\{[^}]*\}", "", css, flags=re.M)
    # 셸이 화면 전체 높이를 차지하려 하는 것도 막는다
    css = css.replace(".shell{display:flex;min-height:100vh;}", ".shell{display:block;}")
    css = css.replace(".content{padding:28px 32px;flex:1;}", ".content{padding:0;}")

    markup = body.group(1)
    markup = markup[: markup.rfind("<script>")] if "<script>" in markup else markup
    # 앱 셸 껍데기 제거 — 사이드바와 상단바
    markup = re.sub(r'<aside class="sidebar">.*?</aside>', "", markup, flags=re.S)
    markup = re.sub(r'<div class="topbar">.*?\n    </div>\n', "", markup, count=1, flags=re.S)

    return css, markup, script.group(1)


@router.get("/monitor", response_class=HTMLResponse)
async def monitor_page() -> HTMLResponse:
    # 디버그용 실시간 RSSI 모니터 — /ws에 리스너로 붙어서 브로드캐스트되는 값을 표로 보여줌.
    # 데이터를 보내는 쪽(안드로이드 등)이 아니라 "구경만 하는" 별도 연결이라 기존 브로드캐스트 로직 그대로 씀.
    # 지도 도구는 iframe이 아니라 같은 페이지로 합쳐서 내려준다 (창 간 통신 없이 직접 호출).
    try:
        map_css, map_markup, map_script = _extract_map_tool_parts()
    except (FileNotFoundError, ValueError) as e:
        map_css, map_script = "", ""
        map_markup = f'<div class="hint">지도 도구를 불러오지 못했습니다: {e}</div>'

    html = (
        _load_monitor_html()
        .replace("/*__MAP_TOOL_CSS__*/", map_css)
        .replace("<!--__MAP_TOOL_MARKUP__-->", map_markup)
        .replace("/*__MAP_TOOL_SCRIPT__*/", map_script)
    )
    return HTMLResponse(html)


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

    try:
        while True:
            raw = await websocket.receive_text()

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


def _load_landmark_list() -> list[landmark_matcher.Landmark]:
    """지도 프로젝트 파일에서 랜드마크를 읽는다. 파일이 바뀌었을 때만 다시 파싱한다."""
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
    landmarks = _load_landmark_list()

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

            # 추적기에도 같은 필터값을 먹여서 서버가 직접 전진/후퇴를 판정하게 함
            _tracker.feed(key, filtered_rssi)
            got_rssi = True

            print(f"비콘 {key} | 원본: {rssi:.1f} | 필터: {rounded:.1f} | 상태: {pipeline.state.value}")

        guides: list[dict] = []
        if got_rssi:
            transition = _tracker.evaluate()
            if transition:
                print(f"[안내] {transition['speech']}")
                guides.append(transition)

            # 판정 결과를 중계 payload에 실어 보냄. /monitor는 이걸 화면에 표시만 하고
            # 자체 판정은 하지 않는다 — 판정 주체를 서버 하나로 유지하기 위함.
            # (별도 메시지로 안 보내고 여기 얹는 이유: 메시지 수를 안 늘리면서 RSSI와 항상 같은 시점의 상태가 되도록)
            snapshot = _tracker.snapshot()
            if snapshot:
                filtered["_track"] = snapshot

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
