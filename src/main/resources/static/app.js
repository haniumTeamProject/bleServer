// ── 설정 ─────────────────────────────────────────────
const MAX_POINTS    = 2000;
const SMOOTH_WINDOW = 20;
let   windowSec     = 20;

// ── 상태 ─────────────────────────────────────────────
const devices = new Map();
let deviceOrder = 0;
let selectedKey = null;
let chart = null;
let following = true;

let measurements = [];
let measuring = false;

// ── DOM ──────────────────────────────────────────────
const $status     = document.getElementById("conn-status");
const $list       = document.getElementById("device-list");
const $count      = document.getElementById("device-count");
const $chartTitle = document.getElementById("chart-title");
const $chartSub   = document.getElementById("chart-sub");
const $measureBtn = document.getElementById("measure-btn");
const $smoothTgl  = document.getElementById("smooth-toggle");
const $windowRange= document.getElementById("window-range");
const $windowValue= document.getElementById("window-value");
const $followTgl  = document.getElementById("follow-toggle");
const $resetView  = document.getElementById("reset-view");

// ── 웹소켓 ────────────────────────────────────────────
const proto = location.protocol === "https:" ? "wss://" : "ws://";
const ws = new WebSocket(proto + location.host + "/ws");
ws.onopen  = () => { setStatus("live", "● live"); };
ws.onerror = (e) => { console.log("에러:", e); setStatus("dead", "● error"); };
ws.onclose = (e) => { setStatus("dead", "● disconnected"); };
ws.onmessage = (event) => {
    let data;
    try { data = JSON.parse(event.data); }
    catch (err) { return; }
    handleData(data);
};
function setStatus(cls, text){ $status.className = "status "+cls; $status.textContent = text; }

// ── 데이터 처리 ───────────────────────────────────────
function handleData(data) {
    const ts = typeof data.timestamp === "number" ? data.timestamp : Date.now();
    for (const key in data) {
        if (key === "timestamp") continue;
        if (key.endsWith("__f")) continue;  // 칼만 필터값은 따로 처리

        const rssi = data[key];
        if (typeof rssi !== "number") continue;
        if (rssi === 127 || rssi >= 0) continue;

        const filteredRssi = data[key + "__f"];  // 칼만 필터값

        let dev = devices.get(key);
        if (!dev) {
            const [mac, name] = splitKey(key);
            dev = { mac, name, order: deviceOrder++, series: [], filteredSeries: [], lastRssi: rssi };
            devices.set(key, dev);
        }
        dev.lastRssi = rssi;
        dev.series.push({ t: ts, rssi });
        if (filteredRssi !== undefined) {
            dev.filteredSeries.push({ t: ts, rssi: filteredRssi });
        }
        if (dev.series.length > MAX_POINTS) dev.series.shift();
        if (dev.filteredSeries.length > MAX_POINTS) dev.filteredSeries.shift();
    }
    renderList();
    if (selectedKey && devices.has(selectedKey)) refreshData();
}
function splitKey(key){ const i=key.indexOf("|"); return i===-1?[key,"unknown"]:[key.slice(0,i),key.slice(i+1)]; }

// ── 기기 목록 ─────────────────────────────────────────
function renderList() {
    $count.textContent = devices.size;
    const entries = [...devices.entries()].sort((a,b)=>a[1].order-b[1].order);
    $list.innerHTML = "";
    for (const [key, dev] of entries) {
        const li = document.createElement("li");
        li.className = "device-item" + (key===selectedKey?" active":"");
        const nameCls = dev.name==="unknown"?"device-name unknown":"device-name";
        li.innerHTML =
            '<div class="device-meta">'+
            '<div class="'+nameCls+'">'+escapeHtml(dev.name)+'</div>'+
            '<div class="device-mac">'+dev.mac+'</div>'+
            '</div>'+
            '<div class="device-rssi '+rssiClass(dev.lastRssi)+'">'+dev.lastRssi+'</div>';
        li.addEventListener("click", ()=>selectDevice(key));
        $list.appendChild(li);
    }
}
function rssiClass(r){ return r>=-70?"rssi-strong":r>=-85?"rssi-mid":"rssi-weak"; }
function escapeHtml(s){ return String(s).replace(/[&<>"']/g,c=>({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

// ── 기기 선택 ────────────────────────────────────────
function selectDevice(key) {
    if (selectedKey !== key) { measurements=[]; measuring=false; updateMeasureBtn(); }
    selectedKey = key;
    const dev = devices.get(key);
    $chartTitle.textContent = dev.name==="unknown"?dev.mac:dev.name;
    $chartSub.textContent = dev.mac + "  ·  실시간 RSSI (dBm)";
    $measureBtn.disabled = false;
    following = true; $followTgl.checked = true;
    renderList();
    ensureChart();
    updateChart();
}

// ── 스무딩 ────────────────────────────────────────────
function smoothSeries(series, win) {
    const out = [];
    for (let i=0;i<series.length;i++){
        let sum=0,cnt=0;
        for (let j=Math.max(0,i-win+1);j<=i;j++){ sum+=series[j].rssi; cnt++; }
        out.push({ x: series[i].t, y: sum/cnt });
    }
    return out;
}

// ── 측정 버튼 ────────────────────────────────────────
$measureBtn.addEventListener("click", ()=>{
    if (!selectedKey) return;
    if (!measuring){ measurements.push({start:Date.now(),end:null}); measuring=true; }
    else { const c=measurements[measurements.length-1]; if(c) c.end=Date.now(); measuring=false; }
    updateMeasureBtn(); updateChart();
});
function updateMeasureBtn(){
    if (measuring){ $measureBtn.textContent="측정 종료"; $measureBtn.classList.add("recording"); }
    else { $measureBtn.textContent="측정 시작"; $measureBtn.classList.remove("recording"); }
}

// ── 컨트롤 이벤트 ─────────────────────────────────────
$smoothTgl.addEventListener("change", updateChart);
$windowRange.addEventListener("input", ()=>{
    windowSec = parseInt($windowRange.value,10);
    $windowValue.textContent = windowSec + "초";
    applyWindowSec();
});
$followTgl.addEventListener("change", ()=>{
    following = $followTgl.checked;
    if (following) updateChart();
});
$resetView.addEventListener("click", ()=>{
    following = true; $followTgl.checked = true;
    updateChart();
});

// ── 측정 구간 배경 플러그인 ──────────────────────────
const measureBandPlugin = {
    id: "measureBand",
    beforeDatasetsDraw(c) {
        const { ctx, chartArea, scales } = c;
        if (!chartArea) return;
        const x = scales.x;
        ctx.save();
        measurements.forEach((m,i)=>{
            const sX = x.getPixelForValue(m.start);
            const eX = m.end!=null ? x.getPixelForValue(m.end) : x.getPixelForValue(Date.now());
            const left = Math.max(chartArea.left, Math.min(sX,eX));
            const right= Math.min(chartArea.right, Math.max(sX,eX));
            if (right<=chartArea.left || left>=chartArea.right) return;
            const active = (m.end==null);
            ctx.fillStyle = active ? "rgba(184,114,255,0.22)" : "rgba(184,114,255,0.12)";
            ctx.fillRect(left, chartArea.top, right-left, chartArea.bottom-chartArea.top);
            ctx.strokeStyle = "rgba(184,114,255,0.6)"; ctx.lineWidth = 1;
            if (sX>=chartArea.left && sX<=chartArea.right){ ctx.beginPath(); ctx.moveTo(sX,chartArea.top); ctx.lineTo(sX,chartArea.bottom); ctx.stroke(); }
            if (m.end!=null && eX>=chartArea.left && eX<=chartArea.right){ ctx.beginPath(); ctx.moveTo(eX,chartArea.top); ctx.lineTo(eX,chartArea.bottom); ctx.stroke(); }
            ctx.fillStyle = "rgba(184,114,255,0.9)";
            ctx.font = "11px 'JetBrains Mono', monospace";
            ctx.fillText("#"+(i+1), left+4, chartArea.top+13);
        });
        ctx.restore();
    }
};

// ── 차트 생성 ─────────────────────────────────────────
function ensureChart() {
    if (chart) return;
    const ctx = document.getElementById("rssi-chart").getContext("2d");
    chart = new Chart(ctx, {
        type: "line",
        data: { datasets: [
                { label:"RSSI (raw)", data:[], borderColor:"#4a9eff", borderWidth:1.5, pointRadius:0, tension:0.35, cubicInterpolationMode:"monotone", fill:false },
                { label:"RSSI (kalman)", data:[], borderColor:"#f0a020", borderWidth:2.5, pointRadius:0, tension:0.4, cubicInterpolationMode:"monotone", fill:false },
            ]},
        options: {
            responsive:true, maintainAspectRatio:false,
            animation:false,
            parsing:false,
            interaction:{ intersect:false, mode:"index" },
            scales: {
                x: {
                    type:"time",
                    time:{ displayFormats:{ millisecond:"HH:mm:ss", second:"HH:mm:ss" } },
                    ticks:{
                        color:"#8b949e", maxRotation:0, autoSkip:false,
                        source:"data",
                        callback:(v)=>luxon.DateTime.fromMillis(v).toFormat("HH:mm:ss"),
                    },
                    grid:{ color:"#2a323d" },
                    afterBuildTicks: (axis) => {
                        const min = axis.min, max = axis.max;
                        if (min == null || max == null || !isFinite(min) || !isFinite(max)) return;
                        const N = 6;
                        const ticks = [];
                        for (let i=0;i<=N;i++){
                            ticks.push({ value: min + (max-min)*i/N });
                        }
                        axis.ticks = ticks;
                    },
                },
                y: {
                    min:-100, max:-30,
                    ticks:{ color:"#8b949e", stepSize:10 },
                    grid:{ color:"#2a323d" },
                    title:{ display:true, text:"RSSI (dBm)", color:"#8b949e" },
                },
            },
            plugins: {
                legend:{ display:true, labels:{ color:"#8b949e", boxWidth:14, font:{size:12} } },
                tooltip:{ callbacks:{
                        title:(items)=>luxon.DateTime.fromMillis(items[0].parsed.x).toFormat("HH:mm:ss.SSS"),
                        label:(item)=>item.dataset.label+": "+item.parsed.y.toFixed(1)+" dBm",
                    }},
                zoom: {
                    pan:{ enabled:true, mode:"x",
                        onPanStart:()=>{ following=false; $followTgl.checked=false; } },
                    zoom:{ wheel:{enabled:true}, pinch:{enabled:true}, mode:"x",
                        onZoomStart:()=>{ following=false; $followTgl.checked=false; } },
                    limits:{ x:{ minRange: 2000 } },
                },
            },
        },
        plugins: [measureBandPlugin],
    });
}

// ── 데이터를 차트에 반영 ──────────────────────────────
function refreshData() {
    const dev = devices.get(selectedKey);
    if (!dev || !chart) return;
    // 파란선: 원본 RSSI
    chart.data.datasets[0].data = dev.series.map(p=>({x:p.t,y:p.rssi}));
    // 주황선: 칼만 필터값 (스무딩 토글 체크 시)
    if ($smoothTgl.checked) {
        chart.data.datasets[1].hidden = false;
        chart.data.datasets[1].data = dev.filteredSeries.map(p=>({x:p.t,y:p.rssi}));
    } else {
        chart.data.datasets[1].hidden = true;
    }
}
function updateChart(){ refreshData(); }

// ── 애니메이션 루프 ───────────────────────────────────
function animate() {
    if (chart && selectedKey) {
        if (following) {
            const now = Date.now();
            chart.options.scales.x.min = now - windowSec*1000;
            chart.options.scales.x.max = now;
        }
        chart.update("none");
    }
    requestAnimationFrame(animate);
}
requestAnimationFrame(animate);

function applyWindowSec() {
    if (!chart) return;
    if (!following && chart.options.scales.x.min != null) {
        const cur = chart.options.scales.x;
        const center = (cur.min + cur.max)/2;
        cur.min = center - windowSec*1000/2;
        cur.max = center + windowSec*1000/2;
        chart.update("none");
    }
}

// ── 초기 표시 ─────────────────────────────────────────
$windowValue.textContent = windowSec + "초";
