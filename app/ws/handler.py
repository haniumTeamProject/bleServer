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


def _process_message(raw: str) -> str:
    try:
        data: dict = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"필터 오류, 원본 전송: {e}")
        return raw

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
  .container { max-width: 760px; margin: 0 auto; }
  .card {
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 20px 22px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  }
  .card h2 {
    margin: 0 0 4px; font-size: 17px; font-weight: 700; display: flex; align-items: center; gap: 8px;
  }
  .hint { color: var(--muted); font-size: 12.5px; margin: 2px 0 14px; line-height: 1.5; }

  .status-pill {
    display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600;
    padding: 4px 10px; border-radius: 999px; background: #f1f2f4; color: var(--muted); margin-bottom: 14px;
  }
  .status-pill .dot { width: 8px; height: 8px; border-radius: 50%; background: #bbb; }
  .status-pill.on { background: #eafaf0; color: #1e8e4a; } .status-pill.on .dot { background: #2ecc71; }
  .status-pill.off { background: #fdecea; color: #c0392b; } .status-pill.off .dot { background: #e74c3c; }

  input[type=text], input[type=number] {
    font-family: inherit; font-size: 14px; padding: 9px 12px; border: 1px solid var(--border);
    border-radius: 8px; background: #fafafa; transition: border-color .15s;
  }
  input[type=text]:focus, input[type=number]:focus { outline: none; border-color: var(--blue); background: #fff; }
  #filterInput, #pathInput { width: 100%; margin-bottom: 6px; }

  table { border-collapse: collapse; width: 100%; margin-top: 4px; }
  th, td { padding: 9px 12px; text-align: left; font-size: 13.5px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .02em; }
  tbody tr:hover { background: #fafbfc; }
  tbody tr:last-child td { border-bottom: none; }

  .controls-row { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; margin: 12px 0 16px; }
  .controls-row label { font-size: 13px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
  .controls-row input[type=number] { width: 64px; }
  button {
    font-family: inherit; font-size: 13.5px; font-weight: 600; padding: 9px 16px; border-radius: 8px;
    border: none; cursor: pointer; transition: opacity .15s;
  }
  button:hover { opacity: .85; }
  #startBtn { background: var(--blue); color: #fff; }
  #resetBtn { background: #eceef1; color: #555; }

  #trackStatus {
    font-size: 14.5px; font-weight: 700; padding: 10px 14px; border-radius: 8px;
    background: #f1f2f4; color: var(--text); margin-bottom: 14px; transition: background .2s, color .2s;
  }
  #trackStatus.advance { background: #eafaf0; color: #1e8e4a; }
  #trackStatus.back { background: #fdecea; color: #c0392b; }
  #trackStatus.warn { background: #fff6e5; color: #b9770e; }

  canvas { display: block; width: 100%; max-width: 100%; height: auto; border: 1px solid var(--border); border-radius: 8px; }

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
    <input id="filterInput" type="text" placeholder="이름 또는 MAC 주소로 필터 (쉼표로 여러 개, 비우면 전체 표시)">
    <div class="hint">서버는 안드로이드가 보낸 값을 전부 받고 있고, 여기서는 화면에 보여줄 것만 걸러줍니다.</div>
    <table>
      <thead><tr><th>비콘 (MAC|이름)</th><th>원본 RSSI</th><th>필터 RSSI</th><th>마지막 수신</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>

  <div class="card">
    <h2>🧭 경로 진행 추적 (테스트)</h2>
    <div class="hint">경로탐색 알고리즘이 정해준 순서대로 비콘 이름/MAC 일부를 쉼표로 입력 (예: beacon1,beacon2,beacon3). 목록상 바로 다음 칸이 "next", 바로 이전 칸이 "prev"입니다.</div>
    <input id="pathInput" type="text" placeholder="beacon1,beacon2,beacon3">
    <div class="controls-row">
      <label>추세 임계값(dB) <input id="threshInput" type="number" value="3"></label>
      <label>다음 비콘 최소 신호(dBm) <input id="minNextInput" type="number" value="-85"></label>
      <button id="startBtn">시작 (자동 시작 노드 탐색)</button>
      <button id="resetBtn">초기화</button>
    </div>
    <div id="trackStatus">경로 미설정</div>
    <canvas id="trackChart" width="700" height="270"></canvas>
    <div id="trackNumbers" class="chips"></div>

    <div class="controls-row" style="margin-top:18px; border-top:1px solid var(--border); padding-top:14px;">
      <button id="measureStartBtn" style="background:#2ecc71; color:#fff;">측정 시작</button>
      <button id="measureEndBtn" style="background:#e74c3c; color:#fff;" disabled>측정 종료</button>
      <span id="measureStatus" class="hint" style="margin:0;">측정 대기 중 — 시작을 누르면 위 필터에 걸리는 비콘만 종료 누를 때까지 따로 기록합니다 (필터가 비어있으면 전체 기록).</span>
    </div>
    <div id="measureSummary"></div>
    <canvas id="measureChart" width="700" height="270" style="margin-top:12px;"></canvas>
    <div class="controls-row" id="measureChartControls" style="display:none; margin-top:8px;">
      <button id="measureImageBtn" style="background:#eceef1; color:#555;">그래프 이미지 저장</button>
    </div>
  </div>

</div>
<script>
  const rows = {};
  const historyByKey = {}; // key -> [{t, filtered}], 추세 계산용 최근 값 버퍼
  const HISTORY_MAX = 40;

  const statusEl = document.getElementById('status');
  const statusLabel = statusEl.querySelector('.label');
  const tbody = document.getElementById('rows');
  const filterInput = document.getElementById('filterInput');
  let filterTerms = [];

  function setStatus(text, kind) {
    statusLabel.textContent = text;
    statusEl.className = 'status-pill' + (kind ? ' ' + kind : '');
  }

  filterInput.addEventListener('input', () => {
    filterTerms = filterInput.value.toLowerCase().split(',').map(s => s.trim()).filter(s => s.length > 0);
    render();
  });

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(proto + '://' + location.host + '/ws');

  ws.onopen = () => setStatus('연결됨 — 데이터 대기 중', 'on');
  ws.onclose = () => setStatus('연결 끊김', 'off');
  ws.onerror = () => setStatus('연결 오류', 'off');

  ws.onmessage = (event) => {
    let data;
    try { data = JSON.parse(event.data); } catch (e) { return; }
    setStatus('연결됨 — 데이터 수신 중', 'on');
    const now = new Date().toLocaleTimeString('ko-KR');
    for (const key in data) {
      if (key === 'timestamp' || key.endsWith('__f')) continue;
      const rawVal = data[key];
      const filteredVal = data[key + '__f'];
      rows[key] = { raw: rawVal, filtered: filteredVal ?? '-', time: now };

      if (typeof filteredVal === 'number' && typeof rawVal === 'number') {
        if (!historyByKey[key]) historyByKey[key] = [];
        historyByKey[key].push({ t: Date.now(), raw: rawVal, filtered: filteredVal });
        while (historyByKey[key].length > HISTORY_MAX) historyByKey[key].shift();

        // 측정 중이면 (히스토리 버퍼와 별개로) 구간 전체를 따로 계속 쌓아둠 — 40개 제한 없이.
        // 단, 주변 ESP 비콘 등 관심 없는 기기까지 다 잡히면 그래프가 지저분해지므로
        // 상단 필터 입력창(filterInput)에 걸리는 비콘만 기록함 (필터를 비워두면 기존처럼 전부 기록).
        if (measuring && matchesFilter(key)) {
          measurementLog.push({ t: Date.now(), key, raw: rawVal, filtered: filteredVal });
        }
      }
    }
    render();
    if (tracking) updateTracking();
  };

  function matchesFilter(key) {
    if (filterTerms.length === 0) return true;
    const lowerKey = key.toLowerCase();
    return filterTerms.some(term => lowerKey.includes(term));
  }

  function render() {
    tbody.innerHTML = '';
    for (const key in rows) {
      if (!matchesFilter(key)) continue;
      const r = rows[key];
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${key}</td><td>${r.raw}</td><td>${r.filtered}</td><td>${r.time}</td>`;
      tbody.appendChild(tr);
    }
  }

  // ---- 측정 구간 저장 ----
  // historyByKey는 최근 40개까지만 남기는 롤링 버퍼라 오래 측정하면 앞부분이 밀려 없어짐.
  // 그래서 측정 중일 때는 별도 배열(measurementLog)에 제한 없이 전부 쌓아뒀다가, 종료 시 요약 + CSV로 뽑음.
  let measuring = false;
  let measureStartTime = null;
  let measurementLog = [];
  let measureTimer = null;
  let measurementCrossovers = []; // 측정 중 currentIndex가 바뀐(비콘이 전환된) 시점들 — { t, label }

  const measureStartBtn = document.getElementById('measureStartBtn');
  const measureEndBtn = document.getElementById('measureEndBtn');
  const measureStatus = document.getElementById('measureStatus');
  const measureSummary = document.getElementById('measureSummary');

  measureStartBtn.onclick = () => {
    measurementLog = [];
    measurementCrossovers = [];
    measuring = true;
    measureStartTime = Date.now();
    measureStartBtn.disabled = true;
    measureEndBtn.disabled = false;
    measureSummary.innerHTML = '';
    measureStatus.textContent = '측정 중... 0초';
    measureTimer = setInterval(() => {
      const sec = Math.floor((Date.now() - measureStartTime) / 1000);
      measureStatus.textContent = `측정 중... ${sec}초 (${measurementLog.length}개 샘플)`;
    }, 1000);
  };

  measureEndBtn.onclick = () => {
    measuring = false;
    clearInterval(measureTimer);
    measureStartBtn.disabled = false;
    measureEndBtn.disabled = true;
    const durationSec = ((Date.now() - measureStartTime) / 1000).toFixed(1);
    measureStatus.textContent = `측정 종료 — ${durationSec}초, ${measurementLog.length}개 샘플`;
    renderMeasureSummary();
  };

  function renderMeasureSummary() {
    const canvas = document.getElementById('measureChart');
    const chartControls = document.getElementById('measureChartControls');

    if (measurementLog.length === 0) {
      measureSummary.innerHTML = '<div class="hint">이 구간엔 기록된 데이터가 없습니다.</div>';
      canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
      chartControls.style.display = 'none';
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
      const raws = rowsForKey.map(r => r.raw);
      const filts = rowsForKey.map(r => r.filtered);
      html += `<tr><td>${key}</td><td>${rowsForKey.length}</td>` +
        `<td>${statText(raws)}</td><td>${statText(filts)}</td></tr>`;
    }
    html += '</tbody></table><button id="downloadBtn" style="margin-top:10px; background:#eceef1; color:#555;">CSV 다운로드</button>';
    measureSummary.innerHTML = html;
    document.getElementById('downloadBtn').onclick = downloadCsv;

    drawMeasureChart(byKey);
    chartControls.style.display = 'flex';
  }

  // 측정 구간 전체를 그래프로: x축 = 측정 시작 기준 경과 시간(delta t), y축 = RSSI.
  // CSV 숫자 나열만으로는 한눈에 안 들어와서, 측정한 전체 비콘을 한 그래프에 겹쳐 그려 추세를 바로 보이게 함.
  const MEASURE_COLORS = ['#2196F3', '#4CAF50', '#e74c3c', '#9b59b6', '#f39c12', '#1abc9c', '#e67e22', '#34495e'];

  function drawMeasureChart(byKey) {
    const canvas = document.getElementById('measureChart');
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const keys = Object.keys(byKey);
    if (keys.length === 0) return;

    const minRssi = -100, maxRssi = -30;
    const leftPad = 34, topPad = 24, bottomPad = 44;
    const w = canvas.width - leftPad - 10;
    const h = canvas.height - topPad - bottomPad;
    const maxT = Math.max(...measurementLog.map(r => r.t - measureStartTime), 1000);

    function xFor(elapsed) { return leftPad + (elapsed / maxT) * w; }
    function yFor(v) { return topPad + h - ((v - minRssi) / (maxRssi - minRssi)) * h; }

    ctx.strokeStyle = '#eee';
    ctx.fillStyle = '#999';
    ctx.font = '10px sans-serif';
    for (let r = minRssi; r <= maxRssi; r += 20) {
      const y = yFor(r);
      ctx.beginPath(); ctx.moveTo(leftPad, y); ctx.lineTo(leftPad + w, y); ctx.stroke();
      ctx.fillText(String(r), 2, y + 3);
    }

    keys.forEach((key, idx) => {
      const color = MEASURE_COLORS[idx % MEASURE_COLORS.length];
      const rowsForKey = byKey[key];

      ctx.setLineDash([4, 3]); ctx.strokeStyle = color; ctx.lineWidth = 1.5;
      ctx.beginPath();
      rowsForKey.forEach((r, i) => {
        const x = xFor(r.t - measureStartTime), y = yFor(r.raw);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();

      ctx.setLineDash([]); ctx.lineWidth = 2;
      ctx.beginPath();
      rowsForKey.forEach((r, i) => {
        const x = xFor(r.t - measureStartTime), y = yFor(r.filtered);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });

    // 측정 중 비콘이 바뀐(currentIndex 전환) 지점을 세로 점선 + 라벨로 표시
    measurementCrossovers.forEach(ev => {
      const elapsed = ev.t - measureStartTime;
      if (elapsed < 0 || elapsed > maxT) return;
      const x = xFor(elapsed);

      ctx.save();
      ctx.setLineDash([2, 2]);
      ctx.strokeStyle = '#e67e22';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(x, topPad);
      ctx.lineTo(x, topPad + h);
      ctx.stroke();
      ctx.restore();

      ctx.save();
      ctx.fillStyle = '#e67e22';
      ctx.font = '10px sans-serif';
      ctx.translate(x + 3, topPad + h - 4);
      ctx.rotate(-Math.PI / 2);
      ctx.fillText(ev.label, 0, 0);
      ctx.restore();
    });

    ctx.strokeStyle = '#ccc';
    ctx.fillStyle = '#999';
    ctx.font = '10px sans-serif';
    const tickCount = 5;
    for (let i = 0; i <= tickCount; i++) {
      const elapsed = maxT * (i / tickCount);
      const x = xFor(elapsed);
      ctx.beginPath(); ctx.moveTo(x, topPad + h); ctx.lineTo(x, topPad + h + 4); ctx.stroke();
      const label = (elapsed / 1000).toFixed(1) + 's';
      const lw = ctx.measureText(label).width;
      const lx = Math.max(leftPad, Math.min(x - lw / 2, leftPad + w - lw));
      ctx.fillText(label, lx, topPad + h + 16);
    }

    let lx = leftPad;
    keys.forEach((key, idx) => {
      const color = MEASURE_COLORS[idx % MEASURE_COLORS.length];
      ctx.fillStyle = color; ctx.fillRect(lx, 4, 10, 10);
      ctx.fillStyle = '#333'; ctx.fillText(key, lx + 14, 13);
      lx += ctx.measureText(key).width + 30;
    });
    ctx.fillStyle = '#666';
    ctx.fillText('실선 = 필터 값, 점선 = 원본 값, 주황 점선 = 비콘 전환 시점 (x축: 측정 시작 기준 경과 시간)', leftPad, canvas.height - 4);
  }

  document.getElementById('measureImageBtn').onclick = () => {
    const canvas = document.getElementById('measureChart');
    const url = canvas.toDataURL('image/png');
    const a = document.createElement('a');
    const ts = new Date(measureStartTime).toISOString().replace(/[:.]/g, '-');
    a.href = url;
    a.download = `rssi_측정_그래프_${ts}.png`;
    a.click();
  };

  function statText(values) {
    if (values.length === 0) return '-';
    const min = Math.min(...values).toFixed(1);
    const max = Math.max(...values).toFixed(1);
    const avg = (values.reduce((a, b) => a + b, 0) / values.length).toFixed(1);
    return `${min} / ${max} / ${avg}`;
  }

  function downloadCsv() {
    let csv = 'timestamp_iso,elapsed_ms,beacon,raw_rssi,filtered_rssi\\n';
    measurementLog.forEach(row => {
      const iso = new Date(row.t).toISOString();
      const elapsed = row.t - measureStartTime;
      csv += `${iso},${elapsed},"${row.key}",${row.raw},${row.filtered}\\n`;
    });
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const ts = new Date(measureStartTime).toISOString().replace(/[:.]/g, '-');
    a.href = url;
    a.download = `rssi_측정_${ts}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // ---- 경로 진행 추적 ----
  // 전제: 경로탐색 알고리즘이 [b0, b1, ..., bn] 순서를 이미 확정해줌.
  // currentIndex = 지금 사용자가 있다고 판단하는 경로상 위치. prev/current/next는 이 순서에서 -1/0/+1.
  let pathTerms = [];
  let currentIndex = 0;
  let backCounter = 0; // "이전 노드로 되돌아가는 중" 의심이 연속으로 몇 번 나왔는지
  let forwardCounter = 0; // "다음 노드로 전진하는 중" 의심이 연속으로 몇 번 나왔는지 (노이즈로 한 번에 안 넘어가게)
  let tracking = false;

  document.getElementById('startBtn').onclick = () => {
    pathTerms = document.getElementById('pathInput').value.split(',').map(s => s.trim().toLowerCase()).filter(s => s.length > 0);
    if (pathTerms.length < 2) { alert('최소 2개 이상 입력해주세요 (현재 + 다음)'); return; }

    // 1단계 규칙 그대로: 경로상 비콘들 중 지금 신호가 가장 센(필터값이 0에 가장 가까운) 걸 시작 노드로
    let bestIdx = 0, bestVal = -Infinity;
    pathTerms.forEach((term, idx) => {
      const key = findKeyForTerm(term);
      const hist = key ? historyByKey[key] : null;
      if (hist && hist.length > 0) {
        const latest = hist[hist.length - 1].filtered;
        if (latest > bestVal) { bestVal = latest; bestIdx = idx; }
      }
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
    const canvas = document.getElementById('trackChart');
    canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
  };

  function findKeyForTerm(term) {
    for (const key in historyByKey) {
      if (key.toLowerCase().includes(term)) return key;
    }
    return null;
  }

  // 추세 = 최근 N개 평균 - 가장 오래된 N개 평균. 양수=신호가 강해짐(접근중), 음수=약해짐(멀어짐)
  // N=2였을 때 실측 중 가만히 서 있어도 순간 RSSI 노이즈로 추세가 6dB 넘게 튀는 경우가 있어서 4로 늘림 (노이즈 완화)
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

  function updateTracking() {
    const thresh = parseFloat(document.getElementById('threshInput').value) || 3;
    const minNext = parseFloat(document.getElementById('minNextInput').value) || -85;

    const prevTerm = currentIndex > 0 ? pathTerms[currentIndex - 1] : null;
    const curTerm = pathTerms[currentIndex];
    const nextTerm = currentIndex < pathTerms.length - 1 ? pathTerms[currentIndex + 1] : null;

    const prevKey = prevTerm ? findKeyForTerm(prevTerm) : null;
    const curKey = findKeyForTerm(curTerm);
    const nextKey = nextTerm ? findKeyForTerm(nextTerm) : null;

    const trendPrev = prevKey ? trendOf(prevKey) : null;
    const trendCur = curKey ? trendOf(curKey) : null;
    const trendNext = nextKey ? trendOf(nextKey) : null;

    const nextHist = nextKey ? historyByKey[nextKey] : null;
    const nextLatest = nextHist && nextHist.length > 0 ? nextHist[nextHist.length - 1].filtered : null;

    // current/prev의 "지금 이 순간" 절대 신호값도 같이 봄 — 추세만으로 판단하면
    // current가 여전히 확실히 더 센(가까운) 상태인데도 노이즈로 튄 추세 때문에 넘어가는 경우가 생김
    const curHist = curKey ? historyByKey[curKey] : null;
    const curLatest = curHist && curHist.length > 0 ? curHist[curHist.length - 1].filtered : null;

    const prevHist = prevKey ? historyByKey[prevKey] : null;
    const prevLatest = prevHist && prevHist.length > 0 ? prevHist[prevHist.length - 1].filtered : null;

    let verdict = '유지';
    let verdictClass = '';

    // 전진 의심: next 신호가 뚜렷이 강해지고(+thresh 이상) 동시에 current는 뚜렷이 약해지고 있으며(-thresh 이하),
    // next 신호 자체가 최소 감지 기준(minNext)을 넘었을 때만 인정 — 노이즈로 뜬 next 값에 낚이지 않기 위함.
    // 추가로 next의 절대 신호값이 실제로 current를 앞질렀을 때만 인정 — 추세가 잠깐 튀어도
    // current가 절대값 기준 여전히 더 가까우면(더 센 값이면) 전진시키지 않음.
    // 후퇴와 마찬가지로 1회성 노이즈로 바로 넘어가지 않도록 2번 연속 조건이 나와야 실제로 전진시킴
    // (실측 중 가만히 서 있어도 추세가 순간적으로 임계값을 넘는 경우가 있어서 넣은 완충 장치)
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
        // 측정 중이면 이 전환 시점을 남겨서 측정 그래프에 표시함
        // (라벨은 "도착/복귀" 구분 없이 그냥 지금 currentIndex로 잡힌 비콘 이름만 — 이 구분은 pathInput에
        //  입력한 순서에 좌우돼서 실제로 어느 쪽이 가까워졌는지와 안 맞을 수 있어 헷갈림)
        if (measuring) {
          measurementCrossovers.push({ t: Date.now(), label: `${pathTerms[currentIndex]}로 전환` });
        }
      } else {
        verdict = `전진 감지 (${forwardCounter}/2, 연속되면 이동)`;
        verdictClass = 'warn';
      }
    }
    // 후퇴 의심: prev 신호가 강해지는 정도가 next보다 크면 카운트. 노이즈 한 번에 바로 되돌리지 않고
    // 3번 연속 같은 판정이 나야 실제로 되돌림 (하이브리드: 트렌드 비교 + 연속성 게이트).
    // 여기도 마찬가지로 prev의 절대 신호값이 실제로 current를 앞질렀을 때만 인정.
    else if (trendPrev !== null && trendPrev > thresh && (trendNext === null || trendPrev > trendNext)
        && prevLatest !== null && curLatest !== null && prevLatest > curLatest) {
      backCounter += 1;
      forwardCounter = 0;
      if (backCounter >= 3) {
        currentIndex = Math.max(0, currentIndex - 1);
        backCounter = 0;
        verdict = '후퇴 → 이전 노드로 되돌림';
        verdictClass = 'back';
        // 측정 중이면 이 전환 시점을 남겨서 측정 그래프에 표시함 (라벨 형식은 전진과 동일하게 통일)
        if (measuring) {
          measurementCrossovers.push({ t: Date.now(), label: `${pathTerms[currentIndex]}로 전환` });
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
    statusDiv.textContent = `현재 위치: ${pathTerms[currentIndex]} (${currentIndex + 1}/${pathTerms.length})  |  판정: ${verdict}`;
    statusDiv.className = verdictClass;

    document.getElementById('trackNumbers').innerHTML = [
      chip('#9e9e9e', 'prev', prevTerm, trendPrev),
      chip('#2196F3', 'current', curTerm, trendCur),
      chip('#4CAF50', 'next', nextTerm, trendNext),
    ].join('');

    drawTrackChart(prevKey, curKey, nextKey);
  }

  function fmt(v) { return v === null || v === undefined ? '-' : v.toFixed(1); }

  function chip(color, label, term, trend) {
    const name = term ?? '-';
    return `<div class="chip"><span class="dot" style="background:${color}"></span>${label}(${name}) 추세 <b>${fmt(trend)}dB</b></div>`;
  }

  function drawTrackChart(prevKey, curKey, nextKey) {
    const canvas = document.getElementById('trackChart');
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const series = [
      { key: prevKey, color: '#9e9e9e', label: 'prev' },
      { key: curKey, color: '#2196F3', label: 'current' },
      { key: nextKey, color: '#4CAF50', label: 'next' },
    ].filter(s => s.key && historyByKey[s.key] && historyByKey[s.key].length > 0);

    if (series.length === 0) return;

    const minRssi = -100, maxRssi = -30;
    const leftPad = 34, topPad = 20, bottomPad = 44;
    const w = canvas.width - leftPad - 10;
    const h = canvas.height - topPad - bottomPad;

    function xFor(t) { return leftPad + ((t - minT) / (maxT - minT)) * w; }
    function yFor(v) { return topPad + h - ((v - minRssi) / (maxRssi - minRssi)) * h; }

    ctx.strokeStyle = '#eee';
    ctx.fillStyle = '#999';
    ctx.font = '10px sans-serif';
    for (let r = minRssi; r <= maxRssi; r += 20) {
      const y = topPad + h - ((r - minRssi) / (maxRssi - minRssi)) * h;
      ctx.beginPath(); ctx.moveTo(leftPad, y); ctx.lineTo(leftPad + w, y); ctx.stroke();
      ctx.fillText(String(r), 2, y + 3);
    }

    let allTimes = [];
    series.forEach(s => historyByKey[s.key].forEach(p => allTimes.push(p.t)));
    const minT = Math.min(...allTimes);
    const maxT = Math.max(...allTimes, minT + 1000);

    function drawLine(hist, field, color, dashed) {
      ctx.setLineDash(dashed ? [4, 3] : []);
      ctx.strokeStyle = color;
      ctx.lineWidth = dashed ? 1.5 : 2;
      ctx.beginPath();
      hist.forEach((p, idx) => {
        const x = xFor(p.t);
        const y = yFor(p[field]);
        if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.setLineDash([]);
    }

    series.forEach(s => {
      const hist = historyByKey[s.key];
      drawLine(hist, 'raw', s.color, true);       // 원본 값 (점선)
      drawLine(hist, 'filtered', s.color, false); // 필터 값 (실선)
    });

    // x축 시간 라벨 (그래프 아래쪽에 실제 시각 표시)
    ctx.strokeStyle = '#ccc';
    ctx.fillStyle = '#999';
    ctx.font = '10px sans-serif';
    const tickCount = 5;
    for (let i = 0; i <= tickCount; i++) {
      const t = minT + (maxT - minT) * (i / tickCount);
      const x = xFor(t);
      ctx.beginPath();
      ctx.moveTo(x, topPad + h);
      ctx.lineTo(x, topPad + h + 4);
      ctx.stroke();
      const label = new Date(t).toLocaleTimeString('ko-KR', { hour12: false });
      const labelWidth = ctx.measureText(label).width;
      const lx = Math.max(leftPad, Math.min(x - labelWidth / 2, leftPad + w - labelWidth));
      ctx.fillText(label, lx, topPad + h + 16);
    }

    let lx = leftPad;
    series.forEach(s => {
      const label = s.label + '(' + s.key + ')';
      ctx.fillStyle = s.color;
      ctx.fillRect(lx, 4, 10, 10);
      ctx.fillStyle = '#333';
      ctx.fillText(label, lx + 14, 13);
      lx += ctx.measureText(label).width + 30;
    });
    ctx.fillStyle = '#666';
    ctx.fillText('실선 = 필터 값, 점선 = 원본 값', leftPad, canvas.height - 4);
  }
</script>
</body>
</html>
"""
