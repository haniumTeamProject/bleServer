import json
import time
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

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


# 지도 편집 도구(map-tool/map_inspection.html)는 별도 저장소로 관리되는 단독 HTML이라,
# 여기로 복사해오지 않고 파일을 그대로 읽어서 서빙한다. /monitor가 iframe으로 이걸 띄운다.
# (같은 출처가 되어야 postMessage로 경로 정보를 주고받기 편하고, 브라우저가 파일을 직접
#  열었을 때 생기는 제약도 피할 수 있음)
_MAP_TOOL_DIR = Path(__file__).resolve().parents[2].parent / "map-tool"
_MAP_TOOL_PATH = _MAP_TOOL_DIR / "map_inspection.html"
_MAP_STATIC_DIR = _MAP_TOOL_DIR / "static"


@router.get("/map-static/{filename}")
async def map_static_file(filename: str):
    """지도 도구가 쓰는 정적 파일(평면도 이미지, 프로젝트 JSON) 제공.

    프로젝트 파일이 13MB가 넘어서 HTML에 끼워 넣지 않고 따로 받아가게 한다.
    """
    # 상위 경로 탈출(../) 방지 — 파일 이름만 받는다
    safe = Path(filename).name
    target = _MAP_STATIC_DIR / safe
    if not target.is_file():
        return JSONResponse({"error": f"파일을 찾을 수 없습니다: {safe}"}, status_code=404)
    return FileResponse(target)


@router.get("/map-static")
async def map_static_list():
    """자동 로드용 — static 폴더에 어떤 파일이 있는지 알려준다."""
    if not _MAP_STATIC_DIR.is_dir():
        return JSONResponse({"files": []})
    return JSONResponse({"files": sorted(p.name for p in _MAP_STATIC_DIR.iterdir() if p.is_file())})


@router.get("/map", response_class=HTMLResponse)
async def map_tool_page() -> HTMLResponse:
    try:
        return HTMLResponse(_MAP_TOOL_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # 경로가 어긋났을 때 빈 화면 대신 원인을 알려준다 (실측 현장에서 디버깅하기 쉽게)
        return HTMLResponse(
            "<h3>지도 도구 파일을 찾지 못했습니다</h3>"
            f"<p>찾은 위치: <code>{_MAP_TOOL_PATH}</code></p>"
            "<p>map-tool 폴더가 backend-python과 같은 상위 폴더 안에 있는지 확인해주세요.</p>",
            status_code=404,
        )


@router.get("/monitor", response_class=HTMLResponse)
async def monitor_page() -> str:
    # 디버그용 실시간 RSSI 모니터 — /ws에 리스너로 붙어서 브로드캐스트되는 값을 표로 보여줌.
    # 데이터를 보내는 쪽(안드로이드 등)이 아니라 "구경만 하는" 별도 연결이라 기존 브로드캐스트 로직 그대로 씀.
    return _MONITOR_HTML


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _connections.add(websocket)
    print(f"Connected: {id(websocket)}")

    try:
        while True:
            raw = await websocket.receive_text()
            payload, guides = _process_message(raw)

            # 기존 동작 유지: RSSI/측정 메시지는 "보낸 쪽 제외" 브로드캐스트
            await _broadcast(payload, exclude=websocket)

            # 안내 메시지는 반대로 "보낸 쪽 포함" 전체에 보내야 함.
            # RSSI를 보내는 폰이 곧 안내를 들어야 할 대상이라, 송신자를 빼면 정작 폰이 못 받음.
            for guide in guides:
                await _broadcast(json.dumps(guide, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(websocket)
        print(f"Disconnected: {id(websocket)}")


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


def _process_message(raw: str) -> tuple[str, list[dict]]:
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


_MONITOR_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>RSSI 실시간 모니터</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.min.css">
<script src="https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.iife.min.js"></script>
<style>
  :root {
    --gray: #9e9e9e; --blue: #2196F3; --green: #4CAF50;
    --bg: #f4f5f7; --card: #ffffff; --border: #e5e7eb; --text: #1f2430; --muted: #8a8f9c;
  }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Pretendard, sans-serif;
    background: var(--bg); color: var(--text);
    margin: 0; padding: 28px 16px 60px;
  }
  .container { max-width: 900px; margin: 0 auto; }
  /* 지도 도구는 자체 사이드바+도구패널 때문에 1000px 이상 필요해서 따로 넓게 쓴다 */
  .wide-container { max-width: 1700px; margin: 0 auto; }
  #mapFrame {
    display: block; width: 100%; min-width: 1040px;
    height: 85vh; min-height: 720px;
    border: 1px solid var(--border); border-radius: 10px; background: #fff;
    resize: vertical; overflow: auto;   /* 모서리를 끌어 높이 조절 */
  }
  /* 창이 좁으면 지도 카드만 가로 스크롤되게 해서 레이아웃이 깨지지 않도록 */
  .wide-container .card { overflow-x: auto; }
  .card {
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 20px 22px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  }
  .card h2 { margin: 0 0 4px; font-size: 17px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
  .hint { color: var(--muted); font-size: 12.5px; margin: 2px 0 14px; line-height: 1.5; }

  .status-pill {
    display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600;
    padding: 4px 10px; border-radius: 999px; background: #f1f2f4; color: var(--muted); margin-bottom: 14px;
  }
  .status-pill .dot { width: 8px; height: 8px; border-radius: 50%; background: #bbb; }
  .status-pill.on { background: #eafaf0; color: #1e8e4a; } .status-pill.on .dot { background: #2ecc71; }
  .status-pill.off { background: #fdecea; color: #c0392b; } .status-pill.off .dot { background: #e74c3c; }

  input[type=number], select {
    font-family: inherit; font-size: 14px; padding: 8px 10px; border: 1px solid var(--border);
    border-radius: 8px; background: #fafafa;
  }
  input[type=number]:focus, select:focus { outline: none; border-color: var(--blue); background: #fff; }

  table { border-collapse: collapse; width: 100%; margin-top: 4px; }
  th, td { padding: 9px 12px; text-align: left; font-size: 13.5px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .02em; }
  tbody tr:hover { background: #fafbfc; }
  tbody tr:last-child td { border-bottom: none; }

  .controls-row { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin: 12px 0 16px; }
  .controls-row label { font-size: 13px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
  .controls-row input[type=number] { width: 68px; }
  button {
    font-family: inherit; font-size: 13.5px; font-weight: 600; padding: 9px 16px; border-radius: 8px;
    border: none; cursor: pointer; transition: opacity .15s;
  }
  button:hover { opacity: .85; }
  button:disabled { opacity: .45; cursor: default; }
  .btn-primary { background: var(--blue); color: #fff; }
  .btn-plain { background: #eceef1; color: #555; }
  .btn-go { background: #2ecc71; color: #fff; }
  .btn-stop { background: #e74c3c; color: #fff; }

  #trackStatus {
    font-size: 14.5px; font-weight: 700; padding: 10px 14px; border-radius: 8px;
    background: #f1f2f4; color: var(--text); margin-bottom: 14px;
  }
  #trackStatus.advance { background: #eafaf0; color: #1e8e4a; }
  #trackStatus.back { background: #fdecea; color: #c0392b; }
  #trackStatus.warn { background: #fff6e5; color: #b9770e; }

  .chart-host { width: 100%; margin-top: 8px; }
  .uplot { font-family: inherit !important; }
  .u-legend { font-size: 12px; }

  .beacon-picker { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0 14px; }
  .beacon-toggle {
    display: flex; align-items: center; gap: 7px; font-size: 12.5px; padding: 6px 11px;
    border-radius: 999px; background: #f7f8fa; border: 1px solid var(--border); cursor: pointer; user-select: none;
  }
  .beacon-toggle input { margin: 0; cursor: pointer; }
  .beacon-toggle .swatch { width: 10px; height: 10px; border-radius: 50%; }
  .beacon-toggle.off { opacity: .4; }
  .picker-actions { display: flex; gap: 8px; margin-bottom: 12px; }
  .picker-actions button { padding: 5px 12px; font-size: 12px; }

  .path-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .path-row .idx { font-size: 12px; color: var(--muted); width: 54px; }
  .path-row select { flex: 1; min-width: 0; }
  .path-row button { padding: 6px 11px; font-size: 12px; }

  .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .chip {
    display: flex; align-items: center; gap: 6px; font-size: 12.5px; padding: 6px 10px;
    border-radius: 8px; background: #f7f8fa; border: 1px solid var(--border);
  }
  .chip .dot { width: 8px; height: 8px; border-radius: 50%; }
  .chip b { font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
<div class="container">

  <div class="card">
    <h2>📡 RSSI 실시간 모니터</h2>
    <div id="status" class="status-pill"><span class="dot"></span><span class="label">연결 중...</span></div>
    <div class="hint">들어오는 비콘이 아래에 자동으로 추가됩니다. 기본은 전체 선택이고, 체크를 풀면 표·그래프·측정 기록에서 빠집니다.</div>
    <div class="picker-actions">
      <button class="btn-plain" id="checkAllBtn">전체 선택</button>
      <button class="btn-plain" id="uncheckAllBtn">전체 해제</button>
      <button class="btn-plain" id="clearDataBtn">그래프 기록 지우기</button>
      <button class="btn-plain" id="clearAllBtn">비콘 목록까지 초기화</button>
    </div>
    <div id="beaconPicker" class="beacon-picker"></div>
    <div class="controls-row">
      <label>Y축 최소(dBm) <input id="yMinInput" type="number" value="-100"></label>
      <label>Y축 최대(dBm) <input id="yMaxInput" type="number" value="-30"></label>
      <label>X축 표시 폭(초) <input id="windowInput" type="number" value="30"></label>
    </div>
    <div class="chart-host" id="liveChartHost"></div>
    <table>
      <thead><tr><th>비콘 (MAC|이름)</th><th>원본 RSSI</th><th>필터 RSSI</th><th>마지막 수신</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>

  <div class="card">
    <h2>🧭 경로 진행 추적 (테스트)</h2>
    <div class="hint">경로탐색 알고리즘이 정해준 순서대로 비콘을 위에서부터 골라주세요. 실제로 잡힌 비콘 중에서만 고르므로 오타·매칭 오류가 없습니다.</div>
    <div id="pathBuilder"></div>
    <div class="controls-row">
      <button class="btn-plain" id="addNodeBtn">+ 경로 노드 추가</button>
      <label>추세 임계값(dB) <input id="threshInput" type="number" value="3"></label>
      <label>다음 비콘 최소 신호(dBm) <input id="minNextInput" type="number" value="-85"></label>
      <button class="btn-primary" id="startBtn">추적 시작 (폰 음성 안내 포함)</button>
      <button class="btn-plain" id="resetBtn">추적 중지</button>
    </div>
    <div id="trackStatus">경로 미설정 — 경로를 고르고 "추적 시작"을 누르세요</div>
    <div id="trackNumbers" class="chips"></div>
    <div id="guideStatus" class="hint" style="margin-top:10px;">판정은 서버가 합니다. 시작하면 이 페이지를 닫아도 폰 음성 안내가 계속 동작합니다.</div>
  </div>

  <div class="card">
    <h2>⏱️ 구간 측정</h2>
    <div class="controls-row">
      <button class="btn-go" id="measureStartBtn">측정 시작</button>
      <button class="btn-stop" id="measureEndBtn" disabled>측정 종료</button>
      <span id="measureStatus" class="hint" style="margin:0;">측정 대기 중 — 안드로이드 앱에서 시작해도 자동으로 여기서 기록이 시작됩니다.</span>
    </div>
    <div id="measureSummary"></div>
    <div class="chart-host" id="measureChartHost"></div>
    <div class="controls-row" id="measureChartControls" style="display:none;">
      <button class="btn-plain" id="measureImageBtn">그래프 이미지 저장</button>
      <button class="btn-plain" id="downloadBtn">CSV 다운로드</button>
    </div>
  </div>

</div>

<!-- 지도 도구는 자체 사이드바(210px)+도구패널(220px)이 있어서 900px 안에서는 화면이 깨진다.
     그래서 이 카드만 위쪽 컨테이너 밖으로 빼서 넓게 쓴다. -->
<div class="wide-container">
  <div class="card">
    <h2>🗺️ 지도 · 경로 탐색</h2>
    <div class="hint">
      지도에서 경로를 탐색한 뒤 "이 순서를 모니터 경로로 보내기"를 누르면, 경로상 비콘의 BLE 이름을
      위 비콘 목록과 대조해서 <b>거쳐가는 순서대로</b> 경로에 자동 등록합니다.
      지도 비콘에 BLE 이름(예: ESP32-Beacon1)을 먼저 지정해두셔야 합니다.
    </div>
    <div id="mapMatchStatus" class="hint" style="margin-bottom:10px;">지도에서 경로를 탐색해주세요.</div>
    <iframe id="mapFrame" src="/map" title="지도 경로 탐색"></iframe>
    <div class="hint" style="margin:8px 0 0;">
      지도 화면이 좁으면 아래 모서리를 끌어서 높이를 늘리거나, <a href="/map" target="_blank" style="color:var(--blue);">새 탭에서 열기</a>를 눌러 크게 볼 수 있습니다.
    </div>
  </div>
</div>
<script>
  const PALETTE = ['#2196F3', '#4CAF50', '#e74c3c', '#9b59b6', '#f39c12', '#1abc9c', '#e67e22', '#34495e'];
  const CHART_HEIGHT = 300;

  const rows = {};
  const historyByKey = {}; // key -> [{t, raw, filtered}], 추세 계산 전용 버퍼 (40개 고정)
  const HISTORY_MAX = 40;

  // 그래프 표시용 버퍼는 추세용과 분리함. 추세는 40개 기준으로 계산 규칙이 정해져 있어서 늘리면 의미가 달라지는데,
  // 그래프는 X축 표시 폭(초)만큼 보여줘야 해서 40개로는 부족한 경우가 많음.
  const displayByKey = {};
  const DISPLAY_MAX_AGE_MS = 180000; // 3분치까지만 들고 있음
  const DISPLAY_MAX = 1500;

  const checkedByKey = {};   // 비콘별 표시 여부 (새로 발견되면 기본 true)
  const colorByKey = {};     // 비콘별 고정 색상 — 순서가 바뀌어도 색이 안 흔들리게 최초 등록 시 배정
  let knownKeys = [];        // 발견된 순서를 유지하는 비콘 키 목록
  const pageStartTime = Date.now();

  const statusEl = document.getElementById('status');
  const statusLabel = statusEl.querySelector('.label');
  const tbody = document.getElementById('rows');
  const beaconPicker = document.getElementById('beaconPicker');

  function setStatus(text, kind) {
    statusLabel.textContent = text;
    statusEl.className = 'status-pill' + (kind ? ' ' + kind : '');
  }

  function isChecked(key) { return checkedByKey[key] !== false; }

  // 비콘 키는 "MAC|이름" 형태라 그대로 쓰면 그래프 라벨이 너무 길어짐. 이름 부분만 뽑아 씀.
  function shortName(key) {
    if (!key) return '';
    const idx = key.indexOf('|');
    return idx >= 0 ? key.slice(idx + 1) : key;
  }

  // 새 비콘이 처음 잡히면 등록 — 색상은 이때 한 번만 배정해서 이후로 안 바뀜
  function registerKey(key) {
    if (knownKeys.indexOf(key) !== -1) return false;
    knownKeys.push(key);
    colorByKey[key] = PALETTE[(knownKeys.length - 1) % PALETTE.length];
    checkedByKey[key] = true;
    return true;
  }

  function renderPicker() {
    beaconPicker.innerHTML = '';
    knownKeys.forEach(key => {
      const label = document.createElement('label');
      label.className = 'beacon-toggle' + (isChecked(key) ? '' : ' off');

      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = isChecked(key);
      box.onchange = () => {
        checkedByKey[key] = box.checked;
        renderPicker();
        renderTable();
        syncLiveChart();
        renderPathBuilder();
      };

      const swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.background = colorByKey[key];

      const text = document.createElement('span');
      text.textContent = key;

      label.appendChild(box);
      label.appendChild(swatch);
      label.appendChild(text);
      beaconPicker.appendChild(label);
    });
  }

  document.getElementById('checkAllBtn').onclick = () => {
    knownKeys.forEach(k => { checkedByKey[k] = true; });
    renderPicker(); renderTable(); syncLiveChart(); renderPathBuilder();
  };
  document.getElementById('uncheckAllBtn').onclick = () => {
    knownKeys.forEach(k => { checkedByKey[k] = false; });
    renderPicker(); renderTable(); syncLiveChart(); renderPathBuilder();
  };

  // 쌓인 값만 비움 — 비콘 목록/체크 상태/색상/경로 설정은 그대로 두고 그래프와 표만 초기화.
  // 자리를 옮겨서 다시 재고 싶을 때 설정을 새로 하지 않아도 되게 하기 위함.
  document.getElementById('clearDataBtn').onclick = () => {
    Object.keys(historyByKey).forEach(k => { historyByKey[k].length = 0; });
    Object.keys(displayByKey).forEach(k => { displayByKey[k].length = 0; });
    Object.keys(rows).forEach(k => { delete rows[k]; });
    // 서버 쪽 추세 이력은 여기서 못 비움 — 필요하면 "추적 시작"을 다시 눌러
    // 서버의 PathTracker를 초기화해야 함 (판정 상태는 서버가 들고 있으므로)
    renderTable();
    syncLiveChart();
  };

  // 비콘 목록 자체를 리셋 — 이제 안 잡히는 옛 비콘이 목록에 계속 남아있을 때 사용.
  // 경로 설정은 지워진 비콘을 가리킬 수 있으므로 추적도 같이 중지시킴.
  document.getElementById('clearAllBtn').onclick = () => {
    if (!confirm('비콘 목록과 쌓인 값을 모두 지웁니다. 경로 추적도 중지됩니다. 계속할까요?')) return;
    Object.keys(historyByKey).forEach(k => { delete historyByKey[k]; });
    Object.keys(displayByKey).forEach(k => { delete displayByKey[k]; });
    Object.keys(rows).forEach(k => { delete rows[k]; });
    Object.keys(checkedByKey).forEach(k => { delete checkedByKey[k]; });
    Object.keys(colorByKey).forEach(k => { delete colorByKey[k]; });
    knownKeys = [];

    // 지워진 비콘을 서버가 계속 추적하지 않도록 서버 쪽 추적도 중지시킨다
    pathSlots = ['', ''];
    ws.send(JSON.stringify({ type: 'guide', event: 'stop' }));
    renderTrackState(null);

    renderPicker(); renderTable(); renderPathBuilder(); syncLiveChart();
  };

  // ---- uPlot 공통 ----
  // uPlot은 모든 시리즈가 하나의 x축 배열을 공유하는 구조라, 비콘마다 수신 시각이 제각각인 데이터를
  // 그대로 넣을 수 없음. 그래서 전체 타임스탬프의 합집합을 x축으로 만들고, 각 비콘은 자기 값이 없는
  // 시각에 null을 채워 넣는다 (uPlot의 spanGaps가 null 구간을 이어서 그려줌).
  // 이 정렬 과정 덕분에 시리즈별 x 범위가 달라서 그래프 앞뒤가 잘려 보이던 문제도 같이 해결됨.
  function buildAligned(sourceByKey, keys, t0, sinceT) {
    const tsSet = new Set();
    keys.forEach(k => (sourceByKey[k] || []).forEach(p => {
      if (sinceT === undefined || p.t >= sinceT) tsSet.add(p.t);
    }));
    const ts = Array.from(tsSet).sort((a, b) => a - b);

    const cols = [ts.map(t => (t - t0) / 1000)];
    keys.forEach(k => {
      const byT = new Map();
      (sourceByKey[k] || []).forEach(p => byT.set(p.t, p));
      cols.push(ts.map(t => { const p = byT.get(t); return p ? p.filtered : null; }));
      cols.push(ts.map(t => { const p = byT.get(t); return p ? p.raw : null; }));
    });
    return cols;
  }

  function seriesConfigFor(keys) {
    const series = [{ label: '경과(초)' }];
    keys.forEach(k => {
      const color = colorByKey[k] || PALETTE[0];
      series.push({ label: k, stroke: color, width: 2, spanGaps: true, points: { show: false } });
      series.push({ label: k + ' (원본)', stroke: color, width: 1, dash: [4, 3], spanGaps: true, points: { show: false } });
    });
    return series;
  }

  // 축 범위는 데이터에 따라 자동으로 늘었다 줄었다 하지 않고 고정값을 씀.
  // (자동 범위면 값이 들어올 때마다 눈금이 계속 바뀌어서 그래프가 출렁이는 것처럼 보임)
  function yRange() {
    const lo = parseFloat(document.getElementById('yMinInput').value);
    const hi = parseFloat(document.getElementById('yMaxInput').value);
    const min = isNaN(lo) ? -100 : lo;
    const max = isNaN(hi) ? -30 : hi;
    return min < max ? [min, max] : [-100, -30];
  }

  function windowSec() {
    const v = parseFloat(document.getElementById('windowInput').value);
    return isNaN(v) || v <= 0 ? 30 : v;
  }

  // 실시간 차트의 x축: 항상 실제 데이터의 양 끝에 정확히 맞춰서 좌우 여백이 안 생기게 함.
  // 데이터가 표시 폭보다 길게 쌓이면 그때부터는 폭을 고정한 채 최신 값을 따라 옆으로 흐름.
  // (예전엔 end를 max(마지막값, 표시폭)으로 잡아서, 데이터가 표시폭보다 짧은 초반에
  //  오른쪽이 비고 왼쪽도 데이터 없는 구간이 남아 양끝에 여백처럼 보였음)
  function liveXRange(u) {
    const win = windowSec();
    const xs = u.data[0];
    if (!xs || xs.length === 0) return [0, win];
    const first = xs[0];
    const last = xs[xs.length - 1];
    if (last <= first) return [first, first + 1];       // 점이 하나뿐일 때 0폭 방지
    if (last - first <= win) return [first, last];      // 아직 표시 폭을 못 채웠으면 데이터에 딱 맞춤
    return [last - win, last];                          // 다 채운 뒤에는 고정 폭으로 스크롤
  }

  function baseOpts(keys, xLabel, xRange, extraPlugins) {
    return {
      width: 100, height: CHART_HEIGHT,
      series: seriesConfigFor(keys),
      scales: {
        x: { time: false, range: xRange },
        y: { range: () => yRange() }
      },
      axes: [
        { label: xLabel, grid: { stroke: '#eee' } },
        { label: 'RSSI (dBm)', grid: { stroke: '#eee' } }
      ],
      cursor: { drag: { x: true, y: false } },
      legend: { live: true },
      plugins: extraPlugins || []
    };
  }

  function fitWidth(chart, host) {
    if (chart && host.clientWidth > 0) chart.setSize({ width: host.clientWidth, height: CHART_HEIGHT });
  }

  // ---- 실시간 차트 ----
  const liveHost = document.getElementById('liveChartHost');
  let liveChart = null;
  let liveKeysSig = '';

  function syncLiveChart() {
    const keys = knownKeys.filter(isChecked);
    const sig = keys.join('|');
    // 표시 폭 밖의 오래된 점은 아예 넘기지 않음 — 배열이 작아져 갱신이 가벼워지고,
    // x 범위를 데이터 양 끝에 딱 맞출 수 있어 좌우 여백이 안 생김
    const data = buildAligned(displayByKey, keys, pageStartTime, Date.now() - windowSec() * 1000);

    // 보이는 비콘 구성이 바뀔 때만 차트를 다시 만들고, 평소엔 setData로 값만 갱신 (매번 재생성하면 느림)
    if (sig !== liveKeysSig || !liveChart) {
      if (liveChart) { liveChart.destroy(); liveChart = null; }
      liveKeysSig = sig;
      if (keys.length === 0) { liveHost.innerHTML = '<div class="hint">표시할 비콘이 없습니다.</div>'; return; }
      liveHost.innerHTML = '';
      liveChart = new uPlot(baseOpts(keys, '경과 시간 (초)', liveXRange), data, liveHost);
      fitWidth(liveChart, liveHost);
    } else {
      liveChart.setData(data);
    }
  }

  // 축 설정을 바꾸면 즉시 반영. setData로 다시 넣어야 scales의 range 함수가 재평가됨
  // (redraw만 하면 이미 계산된 스케일을 그대로 다시 그려서 값이 안 바뀜)
  ['yMinInput', 'yMaxInput', 'windowInput'].forEach(id => {
    document.getElementById(id).addEventListener('change', () => {
      if (liveChart) liveChart.setData(liveChart.data);
      if (measureChart) measureChart.setData(measureChart.data);
    });
  });

  function renderTable() {
    tbody.innerHTML = '';
    knownKeys.forEach(key => {
      if (!isChecked(key) || !rows[key]) return;
      const r = rows[key];
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${key}</td><td>${r.raw}</td><td>${r.filtered}</td><td>${r.time}</td>`;
      tbody.appendChild(tr);
    });
  }

  window.addEventListener('resize', () => {
    fitWidth(liveChart, liveHost);
    fitWidth(measureChart, measureHost);
  });

  // ---- 웹소켓 수신 ----
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(proto + '://' + location.host + '/ws');

  ws.onopen = () => setStatus('연결됨 — 데이터 대기 중', 'on');
  ws.onclose = () => setStatus('연결 끊김', 'off');
  ws.onerror = () => setStatus('연결 오류', 'off');

  ws.onmessage = (event) => {
    let data;
    try { data = JSON.parse(event.data); } catch (e) { return; }
    setStatus('연결됨 — 데이터 수신 중', 'on');

    // 안드로이드 앱이 보낸 측정 제어 메시지 (RSSI 데이터가 아님)
    if (data && data.type === 'measure') { handleMeasureControl(data); return; }
    // 서버가 판단한 경로 안내 메시지
    if (data && data.type === 'guide') { handleGuideMessage(data); return; }

    const now = new Date().toLocaleTimeString('ko-KR');
    let pickerDirty = false;

    for (const key in data) {
      if (key === 'timestamp' || key.endsWith('__f')) continue;
      const rawVal = data[key];
      const filteredVal = data[key + '__f'];
      rows[key] = { raw: rawVal, filtered: filteredVal ?? '-', time: now };

      if (typeof filteredVal === 'number' && typeof rawVal === 'number') {
        if (registerKey(key)) pickerDirty = true;

        const nowMs = Date.now();

        if (!historyByKey[key]) historyByKey[key] = [];
        historyByKey[key].push({ t: nowMs, raw: rawVal, filtered: filteredVal });
        while (historyByKey[key].length > HISTORY_MAX) historyByKey[key].shift();

        if (!displayByKey[key]) displayByKey[key] = [];
        displayByKey[key].push({ t: nowMs, raw: rawVal, filtered: filteredVal });
        const buf = displayByKey[key];
        while (buf.length > DISPLAY_MAX || (buf.length > 0 && nowMs - buf[0].t > DISPLAY_MAX_AGE_MS)) buf.shift();

        // 측정 중이면 (히스토리 버퍼와 별개로) 구간 전체를 따로 계속 쌓아둠 — 40개 제한 없이.
        // 체크가 풀린 비콘은 기록하지 않음.
        if (measuring && isChecked(key)) {
          measurementLog.push({ t: Date.now(), key, raw: rawVal, filtered: filteredVal });
        }
      }
    }

    if (pickerDirty) { renderPicker(); renderPathBuilder(); pushBeaconListToMap(); }
    renderTable();
    syncLiveChart();
    // 서버가 실어 보낸 판정 결과를 그대로 표시 (판정은 서버에서만 함)
    renderTrackState(data._track);
    pushCurrentBeaconToMap(data._track);   // 지도에도 현재 위치 표시
  };

  // 폰에서 보낸 측정 제어 메시지 처리.
  // start/end는 이 페이지의 버튼과 같은 경로(beginMeasurement/finishMeasurement)를 타고,
  // mark는 측정 그래프의 세로 표시선(measurementCrossovers)으로 그대로 들어간다.
  function handleMeasureControl(msg) {
    if (msg.event === 'start') {
      measureSessionId = msg.sessionId || '';
      beginMeasurement(msg.label || '', 'phone');
      return;
    }

    if (msg.event === 'mark') {
      // 측정 중이 아닐 때 온 mark는 버릴 수밖에 없음 (담아둘 구간이 없음)
      if (!measuring) return;
      measurementCrossovers.push({ t: Date.now(), label: msg.label || '지점' });
      return;
    }

    if (msg.event === 'end') {
      // 다른 세션(다른 폰)의 종료 신호로 내 측정이 끊기지 않게 세션 ID를 대조.
      // 단, monitor에서 직접 시작한 경우엔 세션 ID가 없으므로 그때는 그냥 종료시킨다.
      if (measureSessionId && msg.sessionId && msg.sessionId !== measureSessionId) return;
      finishMeasurement('phone');
      measureSessionId = '';
      return;
    }

    if (msg.event === 'error') {
      console.warn('측정 제어 메시지 오류:', msg.reason);
    }
  }

  // ---- 경로 노드 선택 (드롭다운) ----
  // 예전엔 이름 일부를 타이핑해서 substring 매칭으로 비콘을 찾았는데, 오타나 부분 일치 때문에
  // 엉뚱한 비콘이 잡히는 오류가 잦았음. 실제 잡힌 비콘 목록에서만 고르게 해서 매칭 단계를 아예 없앰.
  const pathBuilder = document.getElementById('pathBuilder');
  let pathSlots = ['', ''];

  function renderPathBuilder() {
    pathBuilder.innerHTML = '';
    pathSlots.forEach((selected, idx) => {
      const row = document.createElement('div');
      row.className = 'path-row';

      const label = document.createElement('span');
      label.className = 'idx';
      label.textContent = (idx + 1) + '번째';

      const select = document.createElement('select');
      const blank = document.createElement('option');
      blank.value = '';
      blank.textContent = '— 비콘 선택 —';
      select.appendChild(blank);
      knownKeys.filter(isChecked).forEach(key => {
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = key;
        if (key === selected) opt.selected = true;
        select.appendChild(opt);
      });
      select.onchange = () => { pathSlots[idx] = select.value; };

      const del = document.createElement('button');
      del.className = 'btn-plain';
      del.textContent = '삭제';
      del.onclick = () => { pathSlots.splice(idx, 1); renderPathBuilder(); };

      row.appendChild(label);
      row.appendChild(select);
      row.appendChild(del);
      pathBuilder.appendChild(row);
    });
  }

  document.getElementById('addNodeBtn').onclick = () => { pathSlots.push(''); renderPathBuilder(); };
  renderPathBuilder();

  // ---- 지도 도구 연동 ----
  // 지도(iframe)에서 경로를 탐색하면 경로상 비콘의 BLE 이름을 순서대로 보내온다.
  // 그 이름을 지금 실제로 잡히고 있는 비콘 키("MAC|이름")와 대조해서 경로 슬롯을 채운다.
  const mapMatchStatus = document.getElementById('mapMatchStatus');

  // 이름에서 "Beacon" 뒤에 오는 번호를 뽑는다. ESP32-Beacon3-tx -> 3
  // "Beacon" 뒤라는 조건이 중요함: 그냥 첫 숫자를 쓰면 "ESP32"의 32가 잡힘.
  function beaconNumberFromName(name) {
    if (!name) return null;
    const m = String(name).match(/beacon[^0-9]*(\d+)/i);
    return m ? parseInt(m[1], 10) : null;
  }

  // 지도의 BLE 이름 하나에 대응하는 실제 비콘 키를 찾는다 (키는 "MAC|이름" 형태).
  // 1) 이름이 정확히 같은 것
  // 2) 없으면 Beacon 뒤 번호가 같은 것 — 실제 이름이 ESP32-Beacon3-tx처럼
  //    뒤에 접미사가 붙는 경우가 있어서 번호로 맞춘다.
  // 단순 포함(includes) 비교는 쓰지 않는다. ESP32-Beacon1이 ESP32-Beacon12-tx에
  // 잘못 걸리기 때문.
  function findKeyByBleName(bleName) {
    const target = String(bleName || '').trim().toLowerCase();
    if (!target) return null;

    const exact = knownKeys.find(k => shortName(k).toLowerCase() === target);
    if (exact) return exact;

    const num = beaconNumberFromName(target);
    if (num !== null) {
      const byNum = knownKeys.find(k => beaconNumberFromName(shortName(k)) === num);
      if (byNum) return byNum;
    }
    return null;
  }

  function applyMapSequence(seq) {
    const matched = [];
    const missing = [];

    seq.forEach(item => {
      const key = findKeyByBleName(item.bleName);
      if (key) matched.push({ ...item, key });
      else missing.push(item);
    });

    if (matched.length < 2) {
      mapMatchStatus.innerHTML =
        `<span style="color:#c0392b;">경로에 등록할 비콘이 부족합니다 — 일치 ${matched.length}개.</span> ` +
        (missing.length ? `못 찾은 이름: ${missing.map(m => m.bleName).join(', ')}` : '') +
        ' (지금 잡히고 있는 비콘의 이름과 지도에 적은 이름이 같아야 합니다)';
      return;
    }

    // 매칭된 것만 지도상의 통과 순서 그대로 경로 슬롯에 채운다.
    // 이 차례가 곧 안내 번호(1, 2, 3...)가 된다.
    pathSlots = matched.map(m => m.key);
    renderPathBuilder();

    const chain = matched.map((m, i) => `<b>${i + 1}번</b> ${shortName(m.key)}`).join(' → ');
    mapMatchStatus.innerHTML =
      `<span style="color:#1e8e4a;">경로 자동 반영됨 (${matched.length}개):</span> ${chain}`
      + (missing.length
          ? `<br><span style="color:#b9770e;">못 찾아서 뺀 비콘: ${missing.map(m => m.bleName).join(', ')}</span>`
          : '')
      + '<br>위의 "추적 시작"을 누르면 이 순서로 안내가 시작됩니다.';
  }

  window.addEventListener('message', (e) => {
    const msg = e.data;
    if (!msg || msg.source !== 'mapTool') return;
    if (msg.event === 'beaconSequence' && Array.isArray(msg.beacons)) {
      applyMapSequence(msg.beacons);
    }
  });

  // 지도 쪽에서 이름을 직접 타이핑하면 오타가 나도 매칭 실패로만 나타나 원인을 찾기 어렵다.
  // 지금 잡히고 있는 비콘 이름을 지도에 계속 알려줘서 목록에서 고를 수 있게 한다.
  const mapFrame = document.getElementById('mapFrame');
  let sentBeaconSig = '';

  function pushBeaconListToMap() {
    if (!mapFrame || !mapFrame.contentWindow) return;
    const names = knownKeys.map(shortName);
    const sig = names.join('|');
    if (sig === sentBeaconSig) return;   // 바뀐 게 없으면 보내지 않음
    sentBeaconSig = sig;
    mapFrame.contentWindow.postMessage({ source: 'monitor', event: 'beaconList', names }, '*');
  }

  // iframe이 늦게 뜨므로 로드 시점에 한 번, 이후에는 비콘이 새로 잡힐 때마다 보냄
  if (mapFrame) mapFrame.addEventListener('load', () => {
    sentBeaconSig = '';
    sentCurrentBeacon = null;
    pushBeaconListToMap();
  });

  // 서버가 판단한 현재 위치를 지도에 표시. 바뀔 때만 보내서 불필요한 다시 그리기를 줄인다.
  let sentCurrentBeacon = null;

  function pushCurrentBeaconToMap(track) {
    if (!mapFrame || !mapFrame.contentWindow) return;

    // 측정 중이 아니어도(추적 경로만 등록된 상태) 서버가 보는 현재 노드를 지도에 보여준다.
    // 측정 중일 때만 보내면 "추적 시작만 누른" 상태에서 아무 표시도 안 떠서 고장처럼 보임.
    // 대신 active 여부를 같이 보내서 지도가 "대기 중"으로 구분해 표시하게 한다.
    const name = (track && track.enabled && track.current) ? shortName(track.current) : null;
    const active = !!(track && track.active);
    const sig = name === null ? '' : `${name}#${track.number}#${active}`;
    if (sig === sentCurrentBeacon) return;
    sentCurrentBeacon = sig;
    mapFrame.contentWindow.postMessage({
      source: 'monitor', event: 'currentBeacon',
      name, number: (track && track.number) || null, active,
    }, '*');
  }

  // ---- 서버 음성 안내 제어 ----
  // 판정을 서버가 하게 경로를 넘겨준다. 이렇게 해야 이 페이지를 닫아도 폰 안내가 계속 동작함.
  const guideStatus = document.getElementById('guideStatus');

  // 추적 시작/중지는 곧 "서버에 경로를 등록/해제"하는 것. 화면에는 별도 추적 상태가 없다.
  document.getElementById('startBtn').onclick = () => {
    const path = pathSlots.filter(k => k);
    if (path.length < 2) { alert('경로 노드를 2개 이상 선택해주세요 (현재 + 다음)'); return; }
    ws.send(JSON.stringify({
      type: 'guide',
      event: 'setPath',
      path,
      threshold: parseFloat(document.getElementById('threshInput').value) || 3,
      minNext: parseFloat(document.getElementById('minNextInput').value) || -85,
    }));
  };

  document.getElementById('resetBtn').onclick = () => {
    ws.send(JSON.stringify({ type: 'guide', event: 'stop' }));
  };

  // 서버가 보내주는 안내 관련 응답 처리 (setPath 확인, 실제 전환 안내 등)
  function handleGuideMessage(msg) {
    if (msg.event === 'pathSet') {
      guideStatus.textContent = msg.enabled
        ? `경로 등록됨 — ${msg.path.map((k, i) => `${i + 1}.${shortName(k)}`).join(' → ')} · 측정을 시작하면 안내가 나갑니다`
        : '경로 노드가 부족해서 등록하지 못했습니다 (2개 이상 필요)';
      return;
    }
    if (msg.event === 'stopped') {
      guideStatus.textContent = '경로 등록 해제됨';
      return;
    }
    if (msg.event === 'sessionStart') {
      guideStatus.textContent = `📢 측정 시작 — 시작 지점 ${msg.number}번 (${msg.name})`;
      if (measuring) {
        measurementCrossovers.push({ t: Date.now(), label: `시작 ${msg.number}번` });
      }
      return;
    }
    if (msg.event === 'sessionEnd') {
      guideStatus.textContent = '측정 종료 — 안내 중지됨';
      return;
    }
    if (msg.event === 'transition') {
      guideStatus.textContent = `📢 "${msg.speech}" — ${msg.number}번 ${msg.name} (${msg.number}/${msg.total})`;
      // 서버가 판단한 전환 시점도 측정 그래프에 남겨둔다
      if (measuring) {
        measurementCrossovers.push({ t: Date.now(), label: `${msg.number}번 ${msg.name}` });
      }
      return;
    }
    if (msg.event === 'error') {
      console.warn('안내 메시지 오류:', msg.reason);
    }
  }

  // ---- 경로 진행 추적 (표시 전용) ----
  // 판정은 전부 서버(app/ws/path_tracker.py)가 한다. 이 페이지는 서버가 RSSI 중계에 실어 보낸
  // _track 스냅샷을 그대로 그리기만 함 — 같은 규칙을 두 곳에 두면 화면과 음성 안내가 어긋나므로.
  function renderTrackState(track) {
    const statusDiv = document.getElementById('trackStatus');

    if (!track || !track.enabled) {
      statusDiv.textContent = '경로 미설정 — 아래에서 경로를 고르고 "추적 시작"을 누르세요';
      statusDiv.className = '';
      document.getElementById('trackNumbers').innerHTML = '';
      return;
    }

    // 안내는 측정 중에만 나가므로, 대기 상태인지 한눈에 보이게 표시
    const head = track.active
      ? `현재 위치: ${track.number}번 (${shortName(track.current)})`
      : `대기 중 — 측정을 시작하면 가장 가까운 지점부터 안내합니다`;

    statusDiv.textContent = `${head}  |  판정: ${track.verdict}`;
    statusDiv.className = track.active ? (track.verdictKind || '') : '';

    document.getElementById('trackNumbers').innerHTML = [
      chip('#9e9e9e', track.index, track.prev, track.trendPrev, -1),
      chip('#2196F3', track.index, track.current, track.trendCur, 0),
      chip('#4CAF50', track.index, track.next, track.trendNext, 1),
    ].join('');
  }

  function fmt(v) { return v === null || v === undefined ? '-' : Number(v).toFixed(1); }

  // 비콘은 경로 순서대로 1, 2, 3... 번호로 부른다 (음성 안내도 이 번호만 읽음)
  function chip(color, curIndex, key, trend, offset) {
    if (!key) return '';
    const number = curIndex + offset + 1;
    return `<div class="chip"><span class="dot" style="background:${color}"></span>`
      + `<b>${number}번</b> ${shortName(key)} · 추세 <b>${fmt(trend)}dB</b></div>`;
  }

  // ---- 구간 측정 ----
  // historyByKey는 최근 40개까지만 남기는 롤링 버퍼라 오래 측정하면 앞부분이 밀려 없어짐.
  // 그래서 측정 중일 때는 별도 배열(measurementLog)에 제한 없이 전부 쌓아뒀다가, 종료 시 요약 + 그래프로 뽑음.
  let measuring = false;
  let measureStartTime = null;
  let measurementLog = [];
  let measurementCrossovers = []; // 측정 중 비콘이 전환된 시점들 — { t, label }
  let measureTimer = null;
  let measureChart = null;
  let measureLabel = '';      // 안드로이드에서 보낸 측정 이름 (없으면 빈 문자열)
  let measureSessionId = '';  // 폰이 발급한 세션 ID — 다른 세션의 end를 잘못 받아 끝나지 않게 대조용

  const measureHost = document.getElementById('measureChartHost');
  const measureStartBtn = document.getElementById('measureStartBtn');
  const measureEndBtn = document.getElementById('measureEndBtn');
  const measureStatus = document.getElementById('measureStatus');
  const measureSummary = document.getElementById('measureSummary');

  // 측정 시작/종료는 이 페이지의 버튼으로도, 안드로이드 앱이 보낸 제어 메시지로도 들어올 수 있어서
  // 공통 함수로 빼둠 (origin은 화면에 누가 시작했는지 표시하는 용도)
  function beginMeasurement(label, origin) {
    measurementLog = [];
    measurementCrossovers = [];
    measuring = true;
    measureStartTime = Date.now();
    measureLabel = label || '';
    measureStartBtn.disabled = true;
    measureEndBtn.disabled = false;
    measureSummary.innerHTML = '';
    measureHost.innerHTML = '';
    if (measureChart) { measureChart.destroy(); measureChart = null; }
    document.getElementById('measureChartControls').style.display = 'none';

    clearInterval(measureTimer);
    measureTimer = setInterval(() => {
      const sec = Math.floor((Date.now() - measureStartTime) / 1000);
      measureStatus.textContent = `${measureTitle(origin)} 측정 중... ${sec}초 (${measurementLog.length}개 샘플)`;
    }, 1000);
    measureStatus.textContent = `${measureTitle(origin)} 측정 중... 0초`;
  }

  function finishMeasurement(origin) {
    if (!measuring) return;
    measuring = false;
    clearInterval(measureTimer);
    measureStartBtn.disabled = false;
    measureEndBtn.disabled = true;
    const durationSec = ((Date.now() - measureStartTime) / 1000).toFixed(1);
    measureStatus.textContent =
      `${measureTitle(origin)} 측정 종료 — ${durationSec}초, ${measurementLog.length}개 샘플`;
    renderMeasureSummary();
  }

  function measureTitle(origin) {
    const name = measureLabel ? `"${measureLabel}"` : '(이름 없음)';
    return origin === 'phone' ? `📱 ${name}` : `🖥️ ${name}`;
  }

  // 이 페이지에서 시작해도 서버가 알아야 안내가 켜지고 시작 지점이 확정됨.
  // (서버 중계는 '보낸 쪽 제외'라 이 메시지가 되돌아오지는 않으므로 중복 시작 걱정 없음)
  measureStartBtn.onclick = () => {
    measureSessionId = 'monitor-' + Date.now();
    ws.send(JSON.stringify({
      type: 'measure', event: 'start', sessionId: measureSessionId,
      label: '', timestamp: Date.now(), device: 'monitor',
    }));
    beginMeasurement('', 'monitor');
  };

  measureEndBtn.onclick = () => {
    ws.send(JSON.stringify({
      type: 'measure', event: 'end', sessionId: measureSessionId,
      label: measureLabel, timestamp: Date.now(), device: 'monitor',
    }));
    finishMeasurement('monitor');
    measureSessionId = '';
  };

  function renderMeasureSummary() {
    const controls = document.getElementById('measureChartControls');

    if (measurementLog.length === 0) {
      measureSummary.innerHTML = '<div class="hint">이 구간엔 기록된 데이터가 없습니다.</div>';
      controls.style.display = 'none';
      return;
    }

    const byKey = {};
    measurementLog.forEach(row => {
      if (!byKey[row.key]) byKey[row.key] = [];
      byKey[row.key].push(row);
    });

    let html = '<table style="margin-top:10px;"><thead><tr>' +
      '<th>비콘</th><th>샘플 수</th><th>원본 min/max/평균</th><th>필터 min/max/평균</th></tr></thead><tbody>';
    for (const key in byKey) {
      const rowsForKey = byKey[key];
      html += `<tr><td>${key}</td><td>${rowsForKey.length}</td>` +
        `<td>${statText(rowsForKey.map(r => r.raw))}</td>` +
        `<td>${statText(rowsForKey.map(r => r.filtered))}</td></tr>`;
    }
    html += '</tbody></table>';
    measureSummary.innerHTML = html;

    controls.style.display = 'flex';
    drawMeasureChart(byKey);
  }

  function statText(values) {
    if (values.length === 0) return '-';
    const min = Math.min(...values).toFixed(1);
    const max = Math.max(...values).toFixed(1);
    const avg = (values.reduce((a, b) => a + b, 0) / values.length).toFixed(1);
    return `${min} / ${max} / ${avg}`;
  }

  // 비콘 전환 시점을 세로 점선으로 그리는 uPlot 플러그인.
  // uPlot 캔버스는 devicePixelRatio가 곱해진 좌표계라, valToPos(..., true)로 캔버스 픽셀을 직접 받아 그림.
  function crossoverPlugin(getEvents) {
    return {
      hooks: {
        draw: u => {
          const events = getEvents();
          if (!events.length) return;
          const ctx = u.ctx;
          const dpr = devicePixelRatio || 1;
          const left = u.bbox.left, top = u.bbox.top, width = u.bbox.width, height = u.bbox.height;

          ctx.save();
          ctx.font = (11 * dpr) + 'px sans-serif';
          ctx.textBaseline = 'top';

          const gap = 4 * dpr;
          const lineHeight = 14 * dpr;
          // 각 줄에서 글자가 이미 차지한 오른쪽 끝 x. 전환 시점이 서로 가까우면
          // 같은 줄에 겹쳐 찍히므로, 빈 줄을 찾아 아래로 한 칸씩 내려 그린다.
          const rowEnds = [];

          // 왼쪽부터 순서대로 배치해야 줄 배정이 자연스러움
          const sorted = events.slice().sort((a, b) => a.sec - b.sec);

          sorted.forEach(ev => {
            const x = u.valToPos(ev.sec, 'x', true);
            if (x < left || x > left + width) return;

            ctx.setLineDash([3 * dpr, 3 * dpr]);
            ctx.strokeStyle = '#e67e22';
            ctx.lineWidth = 1.5 * dpr;
            ctx.beginPath();
            ctx.moveTo(x, top);
            ctx.lineTo(x, top + height);
            ctx.stroke();
            ctx.setLineDash([]);

            // 라벨은 x축과 평행하게(가로로). 선 오른쪽에 두되, 오른쪽 끝에 붙어
            // 글자가 잘릴 것 같으면 선 왼쪽으로 넘겨서 그린다.
            const textWidth = ctx.measureText(ev.label).width;
            const fitsRight = x + gap + textWidth <= left + width;
            const startX = fitsRight ? x + gap : x - gap - textWidth;
            const endX = startX + textWidth;

            // 이 라벨이 들어갈 수 있는 첫 번째 줄 찾기 (없으면 새 줄 추가)
            let row = 0;
            while (row < rowEnds.length && startX < rowEnds[row]) row += 1;
            rowEnds[row] = endX + gap;

            ctx.fillStyle = '#e67e22';
            ctx.textAlign = 'left';
            ctx.fillText(ev.label, startX, top + 4 * dpr + row * lineHeight);
          });
          ctx.restore();
        }
      }
    };
  }

  function drawMeasureChart(byKey) {
    const keys = Object.keys(byKey);
    if (keys.length === 0) return;

    const sourceByKey = {};
    keys.forEach(k => { sourceByKey[k] = byKey[k]; });
    const data = buildAligned(sourceByKey, keys, measureStartTime);

    const events = measurementCrossovers.map(ev => ({
      sec: (ev.t - measureStartTime) / 1000, label: ev.label
    }));

    if (measureChart) { measureChart.destroy(); measureChart = null; }
    measureHost.innerHTML = '';

    // 측정 그래프는 구간 전체를 한눈에 봐야 하므로 x축을 0 ~ 측정 길이로 고정 (스크롤 창 아님)
    const totalSec = Math.max(...data[0], 1);
    measureChart = new uPlot(
      baseOpts(keys, '측정 시작 기준 경과 시간 (초)', () => [0, totalSec], [crossoverPlugin(() => events)]),
      data, measureHost
    );
    fitWidth(measureChart, measureHost);
  }

  document.getElementById('measureImageBtn').onclick = () => {
    if (!measureChart) return;
    // uPlot 캔버스는 배경이 투명이라, 흰 배경을 깐 임시 캔버스에 옮겨 그린 뒤 저장
    const src = measureChart.ctx.canvas;
    const tmp = document.createElement('canvas');
    tmp.width = src.width;
    tmp.height = src.height;
    const tctx = tmp.getContext('2d');
    tctx.fillStyle = '#ffffff';
    tctx.fillRect(0, 0, tmp.width, tmp.height);
    tctx.drawImage(src, 0, 0);

    const a = document.createElement('a');
    a.href = tmp.toDataURL('image/png');
    a.download = `rssi_측정_그래프_${new Date(measureStartTime).toISOString().replace(/[:.]/g, '-')}.png`;
    a.click();
  };

  document.getElementById('downloadBtn').onclick = () => {
    let csv = 'timestamp_iso,elapsed_ms,beacon,raw_rssi,filtered_rssi\\n';
    measurementLog.forEach(row => {
      csv += `${new Date(row.t).toISOString()},${row.t - measureStartTime},"${row.key}",${row.raw},${row.filtered}\\n`;
    });
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `rssi_측정_${new Date(measureStartTime).toISOString().replace(/[:.]/g, '-')}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };
</script>
</body>
</html>
"""
