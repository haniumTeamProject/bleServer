import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from app.ws.rssi_filter import RssiFilterPipeline

router = APIRouter()

# Java WebSocketHandler와 동일하게 포팅.
# 주의(원본 그대로 유지된 한계): 연결된 모든 세션이 _filters를 공유함 —
# 사용자가 여러 명 동시 접속하면 같은 비콘 키의 필터 상태가 섞일 수 있음.
# (예전에 얘기했던 "세션별로 필터 분리 안 됨" 이슈, 실사용 단계에선 손봐야 함)
_connections: set[WebSocket] = set()
_filters: dict[str, RssiFilterPipeline] = {}


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
            payload = _process_message(raw)

            for conn in list(_connections):
                if conn is websocket:
                    continue
                try:
                    await conn.send_text(payload)
                except Exception:
                    pass  # 끊긴 연결에 보내다 실패하는 경우 무시하고 계속 진행
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(websocket)
        print(f"Disconnected: {id(websocket)}")


# 측정 제어 메시지 스펙
#   { "type": "measure", "event": "start"|"mark"|"end", "sessionId": str,
#     "label": str, "timestamp": int(ms), "device": str, "markCount": int(end에만) }
# 안드로이드 앱에서 측정 시작/종료를 지정하고, 서버는 RSSI 필터를 태우지 않고 그대로 중계한다.
_MEASURE_TYPE = "measure"
_MEASURE_EVENTS = ("start", "mark", "end")


def _process_control(data: dict) -> str:
    """측정 제어 메시지를 정규화해서 중계용 JSON으로 만든다.

    RSSI 경로와 완전히 분리되어 있어서 필터(_filters)를 건드리지 않는다.
    event 값이 스펙에 없으면 그대로 통과시키지 않고 오류로 표시해서, /monitor가
    모르는 이벤트를 측정 시작/종료로 착각하지 않게 한다.
    """
    event = data.get("event")
    if event not in _MEASURE_EVENTS:
        print(f"알 수 없는 측정 이벤트 무시: {event!r}")
        return json.dumps(
            {"type": _MEASURE_TYPE, "event": "error", "reason": f"unknown event: {event}"},
            ensure_ascii=False,
        )

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

    return json.dumps(payload, ensure_ascii=False)


def _process_message(raw: str) -> str:
    try:
        data: dict = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"필터 오류, 원본 전송: {e}")
        return raw

    # RSSI 데이터가 아닌 제어 메시지는 필터 경로를 타지 않고 따로 처리
    if isinstance(data, dict) and data.get("type") == _MEASURE_TYPE:
        return _process_control(data)

    try:
        filtered: dict = {"timestamp": data.get("timestamp", int(time.time() * 1000))}

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

            print(f"비콘 {key} | 원본: {rssi:.1f} | 필터: {rounded:.1f} | 상태: {pipeline.state.value}")

        return json.dumps(filtered, ensure_ascii=False)
    except Exception as e:
        print(f"필터 오류, 원본 전송: {e}")
        return raw


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
      <button class="btn-primary" id="startBtn">추적 시작</button>
      <button class="btn-plain" id="resetBtn">초기화</button>
    </div>
    <div id="trackStatus">경로 미설정</div>
    <div id="trackNumbers" class="chips"></div>
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
    // 추세 판정 상태도 같이 초기화 — 비운 직후엔 비교할 이력이 없으므로
    backCounter = 0;
    forwardCounter = 0;
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

    tracking = false;
    pathKeys = [];
    pathSlots = ['', ''];
    currentIndex = 0;
    backCounter = 0;
    forwardCounter = 0;
    const statusDiv = document.getElementById('trackStatus');
    statusDiv.textContent = '경로 미설정';
    statusDiv.className = '';
    document.getElementById('trackNumbers').innerHTML = '';

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

    if (pickerDirty) { renderPicker(); renderPathBuilder(); }
    renderTable();
    syncLiveChart();
    if (tracking) updateTracking();
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

  // ---- 경로 진행 추적 ----
  // 전제: 경로탐색 알고리즘이 [b0, b1, ..., bn] 순서를 이미 확정해줌.
  // currentIndex = 지금 사용자가 있다고 판단하는 경로상 위치. prev/current/next는 이 순서에서 -1/0/+1.
  let pathKeys = [];
  let currentIndex = 0;
  let backCounter = 0;    // "이전 노드로 되돌아가는 중" 의심이 연속으로 몇 번 나왔는지
  let forwardCounter = 0; // "다음 노드로 전진하는 중" 의심이 연속으로 몇 번 나왔는지
  let tracking = false;

  document.getElementById('startBtn').onclick = () => {
    pathKeys = pathSlots.filter(k => k);
    if (pathKeys.length < 2) { alert('경로 노드를 2개 이상 선택해주세요 (현재 + 다음)'); return; }

    // 경로상 비콘들 중 지금 신호가 가장 센(필터값이 0에 가장 가까운) 걸 시작 노드로
    let bestIdx = 0, bestVal = -Infinity;
    pathKeys.forEach((key, idx) => {
      const latest = latestOf(key);
      if (latest !== null && latest > bestVal) { bestVal = latest; bestIdx = idx; }
    });
    currentIndex = bestIdx;
    backCounter = 0;
    forwardCounter = 0;
    tracking = true;
    updateTracking();
  };

  document.getElementById('resetBtn').onclick = () => {
    tracking = false;
    currentIndex = 0;
    backCounter = 0;
    forwardCounter = 0;
    const statusDiv = document.getElementById('trackStatus');
    statusDiv.textContent = '경로 미설정';
    statusDiv.className = '';
    document.getElementById('trackNumbers').innerHTML = '';
  };

  // 추세 = 최근 N개 평균 - 가장 오래된 N개 평균. 양수=신호가 강해짐(접근중), 음수=약해짐(멀어짐)
  // N=2였을 때 실측 중 가만히 서 있어도 순간 RSSI 노이즈로 추세가 6dB 넘게 튀는 경우가 있어서 4로 늘림
  const TREND_WINDOW = 4;
  function trendOf(key) {
    const hist = historyByKey[key];
    if (!hist || hist.length < TREND_WINDOW * 2) return null;
    const n = hist.length;
    let recentSum = 0, oldSum = 0;
    for (let i = 0; i < TREND_WINDOW; i++) {
      recentSum += hist[n - 1 - i].filtered;
      oldSum += hist[i].filtered;
    }
    return (recentSum / TREND_WINDOW) - (oldSum / TREND_WINDOW);
  }

  function latestOf(key) {
    const hist = key ? historyByKey[key] : null;
    return hist && hist.length > 0 ? hist[hist.length - 1].filtered : null;
  }

  function updateTracking() {
    const thresh = parseFloat(document.getElementById('threshInput').value) || 3;
    const minNext = parseFloat(document.getElementById('minNextInput').value) || -85;

    const prevKey = currentIndex > 0 ? pathKeys[currentIndex - 1] : null;
    const curKey = pathKeys[currentIndex];
    const nextKey = currentIndex < pathKeys.length - 1 ? pathKeys[currentIndex + 1] : null;

    const trendPrev = prevKey ? trendOf(prevKey) : null;
    const trendCur = curKey ? trendOf(curKey) : null;
    const trendNext = nextKey ? trendOf(nextKey) : null;

    // 추세만으로 판단하면 current가 여전히 확실히 더 센(가까운) 상태인데도
    // 노이즈로 튄 추세 때문에 넘어가는 경우가 생겨서, 절대 신호값도 같이 확인함
    const nextLatest = latestOf(nextKey);
    const curLatest = latestOf(curKey);
    const prevLatest = latestOf(prevKey);

    let verdict = '유지';
    let verdictClass = '';

    // 전진 의심: next 신호가 뚜렷이 강해지고(+thresh 이상) 동시에 current는 뚜렷이 약해지고 있으며(-thresh 이하),
    // next 신호 자체가 최소 감지 기준(minNext)을 넘었고, 절대값도 current를 앞질렀을 때만 인정.
    // 1회성 노이즈로 바로 넘어가지 않도록 2번 연속 조건이 나와야 실제로 전진시킴
    if (trendNext !== null && trendCur !== null && trendNext > thresh && trendCur < -thresh
        && nextLatest !== null && nextLatest > minNext
        && curLatest !== null && nextLatest > curLatest) {
      forwardCounter += 1;
      backCounter = 0;
      if (forwardCounter >= 2) {
        currentIndex += 1;
        forwardCounter = 0;
        verdict = '전진 → 다음 노드로 이동';
        verdictClass = 'advance';
        if (measuring) {
          measurementCrossovers.push({ t: Date.now(), label: `${shortName(pathKeys[currentIndex])}로 전환` });
        }
      } else {
        verdict = `전진 감지 (${forwardCounter}/2, 연속되면 이동)`;
        verdictClass = 'warn';
      }
    }
    // 후퇴 의심: prev 신호가 강해지는 정도가 next보다 크고 절대값도 current를 앞질렀을 때 카운트.
    // 3번 연속 같은 판정이 나야 실제로 되돌림
    else if (trendPrev !== null && trendPrev > thresh && (trendNext === null || trendPrev > trendNext)
        && prevLatest !== null && curLatest !== null && prevLatest > curLatest) {
      backCounter += 1;
      forwardCounter = 0;
      if (backCounter >= 3) {
        currentIndex = Math.max(0, currentIndex - 1);
        backCounter = 0;
        verdict = '후퇴 → 이전 노드로 되돌림';
        verdictClass = 'back';
        if (measuring) {
          measurementCrossovers.push({ t: Date.now(), label: `${shortName(pathKeys[currentIndex])}로 전환` });
        }
      } else {
        verdict = `이탈 의심 (${backCounter}/3, 연속되면 되돌림)`;
        verdictClass = 'warn';
      }
    } else {
      backCounter = 0;
      forwardCounter = 0;
    }

    const statusDiv = document.getElementById('trackStatus');
    statusDiv.textContent = `현재 위치: ${pathKeys[currentIndex]} (${currentIndex + 1}/${pathKeys.length})  |  판정: ${verdict}`;
    statusDiv.className = verdictClass;

    document.getElementById('trackNumbers').innerHTML = [
      chip('#9e9e9e', 'prev', prevKey, trendPrev),
      chip('#2196F3', 'current', curKey, trendCur),
      chip('#4CAF50', 'next', nextKey, trendNext),
    ].join('');
  }

  function fmt(v) { return v === null || v === undefined ? '-' : v.toFixed(1); }

  function chip(color, label, key, trend) {
    return `<div class="chip"><span class="dot" style="background:${color}"></span>${label}(${key ?? '-'}) 추세 <b>${fmt(trend)}dB</b></div>`;
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

  measureStartBtn.onclick = () => beginMeasurement('', 'monitor');
  measureEndBtn.onclick = () => finishMeasurement('monitor');

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
