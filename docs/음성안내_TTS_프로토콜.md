# 음성 안내(TTS) 코드 · 송수신 프로토콜 정리

BLE 비콘 실내 안내에서 "지금 몇 번 지점을 지나고 있는지"를 폰이 음성으로 알려주는 기능의 전체 정리.
서버(`backend-python`)와 안드로이드 앱(`server/bleapp`) 양쪽 코드와, 둘 사이에 오가는 JSON 형태를 다룬다.

---

## 1. 설계 원칙

이 기능은 세 가지 원칙 위에 만들어졌다. 코드를 고칠 때 이 원칙이 깨지지 않는지 먼저 확인할 것.

### 원칙 1 — 판정은 서버만 한다

비콘이 바뀌었는지 판단하는 로직은 **`app/ws/path_tracker.py` 한 곳에만** 있다.

처음에는 `/monitor` 페이지 JS에도 같은 규칙을 두었는데, 한쪽만 고치면 **화면에 보이는 판정과 실제로 폰이 말하는 안내가 서로 달라진다.** 데모에서는 서버가 판단한 것이 곧 사용자가 듣는 안내이므로, 판정 주체를 하나로 못 박았다.

| 역할 | 위치 |
|---|---|
| 판정 (전진/후퇴 결정) | 서버 `path_tracker.py` |
| 음성 출력 | 앱 `SpeechGuide` (서버가 준 문장을 읽기만) |
| 화면 표시 | `/monitor`의 `renderTrackState()` (서버가 준 상태를 그리기만) |

### 원칙 2 — 읽을 문장은 서버가 만든다

앱은 판단도, 문장 조립도 하지 않는다. 서버가 보낸 `speech` 필드를 **그대로** 읽는다.

덕분에 안내 문구를 바꿔도 **앱을 다시 빌드할 필요가 없다.** 실제로 문구를 `"ESP32-Beacon2 지점을 지나고 있습니다"` → `"2"` 로 바꿨을 때 앱 코드는 한 줄도 건드리지 않았다.

### 원칙 3 — 안내는 측정 구간 안에서만

경로를 등록해두는 것(`enabled`)과, 실제로 안내를 내보내는 것(`active`)을 분리했다.
측정을 시작해야 안내가 나가고, 그 순간의 가장 가까운 비콘이 시작 지점이 된다.

---

## 2. 전체 흐름

```
[폰]                        [서버]                          [/monitor]
  |                            |                                |
  |--- BLE RSSI ------------->|                                |
  |                            |-- 칼만 필터 --> PathTracker    |
  |                            |                                |
  |                            |--- RSSI 중계 (+_track) ------->|  (송신자 제외)
  |                            |                                |
  |--- measure start -------->|  start_session()               |
  |                            |   = 가장 센 비콘을 시작으로    |
  |<-- guide sessionStart -----|------------------------------->|  (송신자 포함)
  |    speech: "2"             |                                |
  |  [TTS: "2"]                |                                |
  |                            |                                |
  |--- BLE RSSI ------------->| evaluate() -> 전진 판정         |
  |<-- guide transition -------|------------------------------->|  (송신자 포함)
  |    speech: "3"             |                                |
  |  [TTS: "3"]                |                                |
  |                            |                                |
  |--- measure end ---------->|  end_session()  안내 중지       |
```

핵심은 **브로드캐스트 규칙이 메시지 종류마다 다르다**는 점 (4장 참고).

---

## 3. 메시지 종류와 JSON 형태

한 웹소켓(`/ws`)으로 세 종류가 오간다. 구분은 최상위 `type` 필드로 한다.

| `type` | 방향 | 용도 |
|---|---|---|
| (없음) | 폰 → 서버 | BLE RSSI 데이터 |
| `measure` | 폰/모니터 → 서버 | 측정 구간 시작·종료·지점 표시 |
| `guide` | 양방향 | 경로 등록, 서버의 안내 |

RSSI 메시지에는 `type`이 없으므로 이 한 필드로 완전히 갈린다.

### 3-1. RSSI 데이터 (폰 → 서버)

키는 `"MAC|이름"` 형태이고 값은 RSSI 정수다. 한 비콘만 새로 스캔돼도 그때까지 쌓인 전체가 같이 간다(앱의 `bleRssiMap`이 누적 맵이라서).

```json
{
  "timestamp": 1754400000000,
  "9C:CC:01:67:44:52|ESP32-Beacon1": -72,
  "44:B1:76:19:46:32|ESP32-Beacon2": -81
}
```

**서버가 중계할 때**는 원본값과 필터값을 함께 실어 보내고, 추적 중이면 판정 상태(`_track`)를 얹는다.

```json
{
  "timestamp": 1754400000000,
  "9C:CC:01:67:44:52|ESP32-Beacon1": -72.0,
  "9C:CC:01:67:44:52|ESP32-Beacon1__f": -71.4,
  "_track": {
    "enabled": true,
    "active": true,
    "index": 1,
    "number": 2,
    "total": 3,
    "prev": "9C:CC:01:67:44:52|ESP32-Beacon1",
    "current": "44:B1:76:19:46:32|ESP32-Beacon2",
    "next": "CC:03:...|ESP32-Beacon3",
    "trendPrev": -4.2,
    "trendCur": 1.1,
    "trendNext": 0.3,
    "verdict": "유지",
    "verdictKind": ""
  }
}
```

`__f` 접미사가 칼만 필터를 통과한 값이다.
`_track`을 **별도 메시지로 보내지 않고 여기 얹는 이유**: 메시지 수를 늘리지 않으면서, 화면에 그려지는 RSSI와 판정 상태가 항상 같은 시점의 것이 되도록 보장하기 위함.

`verdictKind`는 `""` / `warn` / `advance` / `back` 중 하나로, **화면 색상까지 서버가 정해준다** — 표시 규칙조차 클라이언트가 다시 판단하지 않도록.

### 3-2. 측정 제어 (폰/모니터 → 서버)

```json
{"type":"measure","event":"start","sessionId":"20260805-143012-a1b2",
 "label":"3층 복도 A→B","timestamp":1754400000000,"device":"SM-S911N"}

{"type":"measure","event":"mark","sessionId":"20260805-143012-a1b2",
 "label":"beacon2 통과","timestamp":1754400012000}

{"type":"measure","event":"end","sessionId":"20260805-143012-a1b2",
 "label":"3층 복도 A→B","timestamp":1754400030000,"markCount":2}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `event` | string | `start` / `mark` / `end` |
| `sessionId` | string | 폰이 발급(`날짜-시각-난수`). 여러 폰이 붙어도 구간이 섞이지 않게 구분 |
| `label` | string | 측정 이름. 앱 입력창 값. 비어도 됨 |
| `timestamp` | int | 폰 기준 epoch ms. 없으면 서버가 수신 시각으로 채움 |
| `device` | string | `Build.MODEL`. 어느 폰이 보냈는지 로그에서 구분 |
| `markCount` | int | `end`에만. 그 구간에서 표시한 지점 개수 |

**`start`/`end`는 음성 안내의 켜짐/꺼짐도 함께 제어한다.** 측정 제어가 곧 안내 제어다.

### 3-3. 경로 등록 (모니터 → 서버)

```json
{"type":"guide","event":"setPath",
 "path":["9C:CC:...|ESP32-Beacon1","44:B1:...|ESP32-Beacon2"],
 "threshold":3,"minNext":-85}

{"type":"guide","event":"stop"}
```

`path` 배열의 **순서가 곧 안내 번호**다. 0번째가 1번, 1번째가 2번.

### 3-4. 서버의 안내 (서버 → 전체, 폰 포함)

**측정 시작 — 시작 지점 확정**

```json
{"type":"guide","event":"sessionStart","index":1,"number":2,"total":3,
 "beacon":"44:B1:...|ESP32-Beacon2","name":"ESP32-Beacon2",
 "speech":"2","timestamp":1754400000000}
```

**비콘 전환**

```json
{"type":"guide","event":"transition","direction":"forward",
 "index":2,"number":3,"total":3,
 "beacon":"CC:03:...|ESP32-Beacon3","name":"ESP32-Beacon3",
 "isLast":true,"speech":"3","timestamp":1754400020000}
```

**측정 종료 / 경로 등록 확인 / 해제** — `speech`가 빈 문자열이라 폰은 아무 말도 하지 않는다.

```json
{"type":"guide","event":"sessionEnd","speech":"","timestamp":...}
{"type":"guide","event":"pathSet","enabled":true,"path":[...],"numbers":{...},"speech":""}
{"type":"guide","event":"stopped","enabled":false,"speech":""}
```

| 필드 | 설명 |
|---|---|
| `speech` | **폰이 그대로 읽는 문자열.** 비어 있으면 읽지 않음 |
| `number` | 경로 순서 번호(1부터). 음성으로 읽히는 값 |
| `name` | 비콘 이름(`MAC\|이름`의 뒷부분). 화면 표시용 |
| `direction` | `forward` / `backward` |
| `isLast` | 마지막 노드인지 |

**왜 번호만 읽는가**: `"ESP32-Beacon2 지점을 지나고 있습니다"` 같은 문장은 다 읽기 전에 다음 지점으로 이동해버려 안내가 서로 겹쳤다. 그래서 `speech`는 `"2"` 하나로 줄이고, 화면에 필요한 `name`·`number`는 따로 실어 보낸다. **폰은 짧게 듣고, `/monitor`는 자세히 본다.**

---

## 4. 브로드캐스트 규칙 (가장 중요한 함정)

기존 중계는 **"보낸 쪽 제외"** 였다(원본 Java `WebSocketHandler` 로직을 그대로 유지). 그런데 안내는 그러면 안 된다 — **RSSI를 보내는 폰이 곧 안내를 들어야 할 대상**이라, 송신자를 빼면 정작 폰이 못 받는다.

그래서 `_process_message()`가 반환값을 둘로 나눈다.

```python
def _process_message(raw: str) -> tuple[str, list[dict]]:
    """(중계할 JSON 문자열, 전체에 보낼 안내 메시지 목록)"""
```

```python
payload, guides = _process_message(raw)

# 중계: 보낸 쪽 제외 (기존 동작 유지)
await _broadcast(payload, exclude=websocket)

# 안내: 보낸 쪽 포함 — 폰이 받아야 하므로
for guide in guides:
    await _broadcast(json.dumps(guide, ensure_ascii=False))
```

```python
async def _broadcast(payload: str, exclude: WebSocket | None = None) -> None:
    for conn in list(_connections):
        if exclude is not None and conn is exclude:
            continue
        try:
            await conn.send_text(payload)
        except Exception:
            pass  # 끊긴 연결은 무시하고 계속
```

**검증됨**: 폰이 40번 RSSI를 보내는 동안 폰은 안내 1건 + 자기 RSSI 중계 0건, 모니터는 안내 1건 + RSSI 중계 40건을 받는 것을 테스트로 확인.

---

## 5. 서버 코드

### 5-1. `app/ws/path_tracker.py` — 판정

상수는 `/monitor`에 있던 값을 그대로 옮겼다.

```python
TREND_WINDOW = 4      # 추세 계산에 쓰는 앞/뒤 표본 수
HISTORY_MAX = 40      # 비콘별 이력 버퍼
FORWARD_STREAK = 2    # 전진 확정에 필요한 연속 횟수
BACK_STREAK = 3       # 후퇴 확정에 필요한 연속 횟수
DEFAULT_THRESHOLD = 3.0    # 추세 임계값(dB)
DEFAULT_MIN_NEXT = -85.0   # 다음 비콘 최소 감지 신호(dBm)
```

**상태 두 개를 구분한다.**

```python
self.enabled = False   # 경로가 등록되어 있는가
self.active  = False   # 측정이 시작되어 실제로 안내를 내보내는 중인가
```

**추세** = 최근 N개 평균 − 가장 오래된 N개 평균. 양수면 접근 중, 음수면 멀어지는 중.

```python
def _trend(self, key: str):
    buf = self.history.get(key)
    if not buf or len(buf) < TREND_WINDOW * 2:
        return None
    recent = sum(buf[-TREND_WINDOW:]) / TREND_WINDOW
    old = sum(buf[:TREND_WINDOW]) / TREND_WINDOW
    return recent - old
```

**시작 지점 확정** — 측정을 시작하는 순간 가장 신호가 센(가장 가까운) 비콘을 잡는다.

```python
def start_session(self) -> dict | None:
    best_idx, best_val = 0, None
    for idx, key in enumerate(self.path):
        latest = self._latest(key)
        if latest is not None and (best_val is None or latest > best_val):
            best_val, best_idx = latest, idx

    self.index = best_idx
    self.forward_streak = 0
    self.back_streak = 0
    self.active = True
    return {..., "number": self.index + 1, "speech": str(self.index + 1)}
```

**전진 판정** — 조건이 네 개나 걸려 있는 이유가 각각 있다.

```python
if (
    trend_next > self.threshold          # ① next가 뚜렷이 강해지는 중
    and trend_cur < -self.threshold      # ② 동시에 current는 뚜렷이 약해지는 중
    and next_latest > self.min_next      # ③ next가 최소 감지 기준은 넘김
    and next_latest > cur_latest         # ④ 절대값으로도 next가 current를 앞지름
):
    self.forward_streak += 1
    if self.forward_streak >= FORWARD_STREAK:   # ⑤ 2회 연속이어야 확정
        self.index += 1
        return self._transition("forward")
```

- ①만 보면 노이즈로 next가 잠깐 튄 것과 실제 접근을 구분 못 한다 → ②로 교차 확인
- ③은 아직 거의 안 잡히는 next를 성급히 인정하지 않기 위한 하한
- ④가 없으면 **current가 절대값으로 여전히 더 가까운데도** 추세만 튀어서 넘어가는 문제가 실측에서 실제로 발생했다
- ⑤는 1회성 노이즈로 위치가 흔들리지 않게 하는 완충

후퇴는 같은 구조에 `BACK_STREAK = 3`(더 보수적)으로, prev 추세가 next 추세보다 클 때만 센다.

**표시용 스냅샷** — 판정 결과의 사본. `/monitor`는 이걸 그리기만 한다.

```python
def snapshot(self) -> dict | None:
    """현재 추적 상태 — /monitor가 화면에 그대로 표시하기 위한 것"""
```

### 5-2. `app/ws/handler.py` — 라우팅

```python
# RSSI가 아닌 제어 메시지는 필터 경로를 타지 않고 따로 처리
if data.get("type") == _MEASURE_TYPE:
    return _process_control(data)      # (payload, guides)
if data.get("type") == _GUIDE_TYPE:
    return _process_guide(data), []
```

제어 메시지가 **`_filters`(칼만 필터 상태)를 오염시키지 않도록** 경로를 완전히 분리했다.

RSSI 처리 중에는 필터값을 추적기에도 먹인다.

```python
pipeline = _filters.setdefault(key, RssiFilterPipeline())
filtered_rssi = pipeline.filter(rssi)

filtered[key] = rssi                    # 원본값
filtered[f"{key}__f"] = round(filtered_rssi, 1)   # 칼만 필터값

_tracker.feed(key, filtered_rssi)       # 판정용
```

측정 제어가 안내를 켜고 끈다.

```python
if event == "start":
    started = _tracker.start_session()
    if started:
        guides.append(started)
elif event == "end":
    ended = _tracker.end_session()
    if ended:
        guides.append(ended)
```

스펙에 없는 `event`가 오면 그대로 통과시키지 않고 `{"event":"error"}`로 바꿔 보낸다 — 클라이언트가 모르는 이벤트를 시작/종료로 착각하지 않도록.

---

## 6. 앱 코드 (`server/bleapp`)

### 6-1. `WebSocketManager` — 수신 통로 열기

원래 `onMessage`는 로그만 찍고 버렸다. 서버 메시지를 밖으로 전달하는 콜백을 추가했다.

```java
/** 서버에서 내려온 메시지를 받아보는 콜백. OkHttp 백그라운드 스레드에서 호출된다. */
public interface MessageListener {
    void onServerMessage(String text);
}

@Override
public void onMessage(@NonNull WebSocket webSocket, @NonNull String text) {
    Log.d(TAG, "서버 메시지 : " + text);
    MessageListener listener = messageListener;   // 지역 변수로 복사 (도중에 null이 되는 경우 방지)
    if (listener != null) listener.onServerMessage(text);
}
```

제어 JSON 전송은 RSSI 전송과 분리했다.

```java
/**
 * RSSI 데이터가 아닌 제어용 JSON(측정 시작/종료 등)을 그대로 전송한다.
 * RSSI 전송(send)과 달리 bleRssiMap 병합이나 timestamp 자동 추가를 하지 않고,
 * 호출한 쪽이 만든 JSON을 손대지 않고 보낸다.
 */
public boolean sendControl(JSONObject payload) {
    if (webSocket == null || payload == null) {
        Log.w(TAG, "제어 메시지 전송 실패 (연결 없음): " + payload);
        return false;
    }
    Log.d(TAG, "제어 메시지 전송: " + payload);
    return webSocket.send(payload.toString());
}
```

### 6-2. `BleScanner` — 안내만 골라내기

RSSI 중계 등 다른 메시지도 같이 들어오므로 `type`으로 거른다.

```java
private void onServerMessage(String text) {
    if (text == null || text.isEmpty()) return;
    try {
        JSONObject msg = new JSONObject(text);
        if (!"guide".equals(msg.optString("type"))) return;   // RSSI 중계분 등은 무시

        String speech = msg.optString("speech", "");
        if (!speech.isEmpty()) speechGuide.speak(speech);      // 빈 문자열이면 말하지 않음
    } catch (JSONException e) {
        // 안내가 아닌 메시지도 같이 들어오므로 조용히 넘어간다
        Log.v(LOG_TAG, "안내 메시지 아님: " + text);
    }
}
```

**앱이 하는 판단은 이게 전부다** — "guide 타입인가", "읽을 문장이 있는가". 번호를 만들지도, 전진/후퇴를 따지지도 않는다.

측정 제어 전송 쪽:

```java
public String startMeasurement(String label) {
    if (!webSocketManager.isConnected()) return null;
    String sessionId = newSessionId();
    JSONObject payload = baseMeasurePayload("start", sessionId);
    payload.put("label", label == null ? "" : label);
    if (!webSocketManager.sendControl(payload)) return null;
    measureSessionId = sessionId;
    ...
    return sessionId;
}
```

### 6-3. `speech/SpeechGuide.java` — TTS 래퍼

세 가지 문제를 처리한다.

**① 스레드** — 웹소켓 수신은 OkHttp 백그라운드 스레드다. TTS 호출을 메인 스레드로 넘긴다.

```java
private final Handler mainHandler = new Handler(Looper.getMainLooper());

/** 어느 스레드에서 불러도 안전하다. */
public void speak(String text) {
    if (!enabled || text == null || text.trim().isEmpty()) return;
    mainHandler.post(() -> {
        if (!ready) {
            pendingText = text;   // 초기화 중이면 마지막 것만 들고 있다가 준비되면 읽음
            return;
        }
        speakNow(text);
    });
}
```

**② 비동기 초기화** — TTS 엔진은 준비되기까지 시간이 걸린다. 그 사이 들어온 문장은 **마지막 하나만** 보관한다. 큐로 쌓으면 준비된 순간 지나간 안내를 몰아서 읽어 오히려 방해가 된다.

```java
tts = new TextToSpeech(context.getApplicationContext(), status -> {
    if (status != TextToSpeech.SUCCESS) {
        Log.e(TAG, "TTS 초기화 실패: status=" + status);
        return;
    }
    int result = tts.setLanguage(Locale.KOREAN);
    if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
        // 한국어 음성 데이터가 없으면 기기 기본 언어로라도 읽게 둔다
        Log.w(TAG, "한국어 TTS를 쓸 수 없어 기본 언어로 진행합니다");
    }
    ready = true;
    if (pendingText != null) {
        String t = pendingText; pendingText = null; speakNow(t);
    }
});
```

**③ 최신 안내 우선** — `QUEUE_FLUSH`로 이전 발화를 끊고 새 것을 읽는다. 위치 안내는 최신 것만 의미가 있다.

```java
private void speakNow(String text) {
    if (tts == null) return;
    Log.d(TAG, "안내 음성: " + text);
    tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, UTTERANCE_ID);
}
```

**자원 반납** — 앱을 실제로 끝낼 때만 반납한다. 화면 전환마다 `shutdown()`하면 안내가 중간에 죽는다.

```java
// MainActivity.onStop()
if (isFinishing()) {
    bleScanner.stopScan();
    bleScanner.getSpeechGuide().shutdown();
}
```

### 6-4. UI

메인 화면의 "비콘 전환 음성 안내" 체크박스가 `SpeechGuide.setEnabled()`를 토글한다. 끄면 진행 중인 발화도 멈춘다.

```java
CheckBox checkSpeech = findViewById(R.id.checkSpeech);
checkSpeech.setChecked(bleScanner.getSpeechGuide().isEnabled());
checkSpeech.setOnCheckedChangeListener(
        (buttonView, isChecked) -> bleScanner.getSpeechGuide().setEnabled(isChecked));
```

---

## 7. 사용 순서

1. 앱에서 웹소켓 주소를 넣고 **연결** → RSSI가 서버로 흐르기 시작
2. `/monitor`에서 잡힌 비콘 중 경로 순서대로 골라 **추적 시작** → 서버에 경로 등록 (아직 안내 없음)
3. 앱에서 측정 이름을 넣고 **측정 시작** → 그 순간 가장 가까운 비콘이 시작 지점으로 확정되고, 번호를 읽어줌
4. 걸어다니는 동안 비콘이 바뀔 때마다 번호를 읽어줌
5. 앱에서 **측정 종료** → 안내 중지, `/monitor`에 구간 그래프·통계·CSV 생성

`/monitor`를 닫아도 3~4는 계속 동작한다(판정이 서버에 있으므로).

---

## 8. 바꾸고 싶을 때

| 바꿀 것 | 어디를 고치나 | 앱 재빌드 |
|---|---|---|
| 안내 문구 | `path_tracker.py`의 `speech` 값 | 불필요 |
| 판정 민감도 | `/monitor`의 추세 임계값·최소 신호 입력창 | 불필요 |
| 연속 확정 횟수 | `path_tracker.py`의 `FORWARD_STREAK`/`BACK_STREAK` | 불필요 |
| 추세 윈도우 | `path_tracker.py`의 `TREND_WINDOW` | 불필요 |
| TTS 속도·음높이 | `SpeechGuide`에 `setSpeechRate()`/`setPitch()` 추가 | 필요 |

문구와 판정이 전부 서버 쪽에 있어서, 실측 중 조정이 필요한 것들은 대부분 앱을 다시 설치하지 않아도 된다.

---

## 9. 남은 한계

- `PathTracker`가 서버에 **전역 하나**라서 동시에 여러 명을 각각 안내하지 못한다 (`_filters`와 같은 한계). 실사용 단계에서는 세션별로 분리해야 함.
- 측정 제어 메시지를 서버가 저장하지 않고 중계만 한다. 측정 도중 `/monitor`를 새로고침하면 그 구간 기록이 사라진다.
- 폰이 `mark`를 보냈는데 `/monitor`가 측정 중이 아니면 그 표시는 버려진다.
- 안드로이드 빌드 검증은 아직 안 됨 — 서버 로직은 테스트로 검증했지만 앱은 Android Studio에서 빌드·실행 확인이 필요하다.

---

## 10. 검증 현황

서버 로직은 가상 RSSI 시퀀스로 테스트했다.

| 확인한 것 | 결과 |
|---|---|
| 정지 상태에서 오작동 없음 | 통과 |
| 정상 전진 감지 | 통과 |
| 노이즈로 추세만 튈 때 전진 안 함 (절대값 검증) | 통과 |
| 경로 미설정 시 무동작 | 통과 |
| `stop` 후 안내 중지 | 통과 |
| 측정 전에는 안내 0건 | 통과 |
| 측정 시작 시 가장 센 비콘이 시작 지점으로 확정 | 통과 (2번이 가장 셀 때 시작 2번) |
| 전환 발화가 숫자만 | 통과 |
| 측정 종료 후 안내 0건 | 통과 |
| 폰이 안내는 받고 자기 RSSI 중계는 안 받음 | 통과 |
| 기존 측정 제어·RSSI 경로 무손상 | 통과 |
| JS에 판정 로직 잔재 없음 | 통과 |
