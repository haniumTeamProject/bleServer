# 사용자 앱 ↔ 서버 규약 (WebSocket)

목적지 탐색 · 되묻기 · 실시간 안내를 한 소켓에서 처리한다.

> 대상: APP-FE(`org.mcsmtp.wayfinder`) ↔ backend-python
> 원안(`POST /api/route` + `WS /ws/navigation`)에서 **바뀐 부분**을 담는다.
> 바꾼 근거를 각 절에 적어뒀다. 동의가 안 되는 항목은 그 근거부터 반박하면 된다.

---

## 0. 설계 원칙 — 앱은 도구다

**앱은 판단하지 않는다.** 서버가 "무엇을 말하고, 언제 들을지"를 정하고,
앱은 그대로 실행한다.

앱이 하는 일은 네 가지뿐이다.

```
1. 말한다        utterance 를 읽는다
2. 듣는다        listenAfter 면 발화가 끝난 뒤 마이크를 연다
3. 떤다          haptic 대로 진동한다
4. 보낸다        받아적은 말과 스캔된 비콘을 올린다
```

이 네 줄이 앱 로직의 전부다. 경로도, 매칭 규칙도, 상태 전이도 앱에 두지 않는다.

### 왜 이렇게 하나

**고칠 곳이 하나여야 한다.** 안내 문구를 다듬거나 매칭 규칙을 손볼 때마다
앱을 새로 빌드해서 배포하고 사용자가 업데이트하기를 기다려야 한다면, 실측 한 번에
하루가 간다. 서버만 고치면 즉시 반영된다.

**앱에 둔 판단은 조용히 틀린다.** 현재 앱은 후보가 여럿일 때
`matched.first()`로 말없이 첫 번째를 고른다(§7-1). 화면을 볼 수 없는 사용자는
잘못 간 것을 알 방법이 없다. 판단을 서버로 모으면 이런 종류의 버그가
**구조적으로 생기지 않는다.**

---

## 1. 왜 REST가 아니라 WebSocket인가

목적지 탐색은 한 번의 요청-응답이 아니다. 되묻기가 들어가면 대화가 된다.

```
사용자: "화장실"
서버:   "화장실 1번, 화장실 2번 중에서 말씀해 주세요."
사용자: "두 번째"
서버:   "화장실로 안내합니다."
```

REST로 하면 이 문맥(무엇을 물어놨는지)을 요청마다 다시 실어 보내야 한다.
WebSocket은 **연결 자체가 문맥**이라 그냥 된다.

그리고 목적지가 정해지는 순간 곧바로 안내가 시작되는데 안내는 어차피 WebSocket이다.
같은 대화를 두 채널로 쪼갤 이유가 없다.

### `POST /api/route`를 없앤 근거

원안은 경로를 REST로 받았다. 그런데 앱이 그 응답을 쓰는 곳이 한 줄뿐이다.

```kotlin
// NavigationFragment.kt:96
totalSteps = route.steps.size.takeIf { it > 0 } ?: events.maxOfOrNull { it.currentStep } ?: 0
```

`steps.size` — 진행 표시 점 개수. 그게 전부다.
`steps[i].instruction`은 어디서도 읽지 않는다. 발화는 전부 WS의 `utterance`에서 온다.

```kotlin
// NavigationFragment.kt:120
e.utterance?.let { instructionView?.text = TextFormat.guidance(it) }
```

48m짜리 경로 대본을 통째로 내려보내고 배열 길이 하나만 쓰는 셈이다.
게다가 이미 폴백이 있다 — `?: events.maxOfOrNull { it.currentStep }`.
route가 없어도 WS 이벤트에서 최대 step을 세서 알아낸다.

그래서 **경로 응답을 아예 없앤다.** 진행 상황은 매 이벤트에 정수 두 개
(`step`, `totalSteps`)로 실어 보내면 된다.

### `GET .../destinations` 도 없앤다 — 사용자앱의 REST 는 0개다

목적지 목록은 앱이 화면에 나열하고 터치로 고를 때 쓴다(음성이 안 될 때의 대비).
그런데 그 목록도 WS 로 보내면 된다 — 되묻기 후보를 띄우려고 만든 `screen.items` 에
그대로 실으면 되고, `/ws` 에는 이미 같은 일을 하는 `event:"list"` 가 구현되어 있다.

**결정적인 이유는 따로 있다. 앱이 `floorId` 를 알 방법이 없다.**

```
GET /api/floors/{floorId}/destinations
                 ↑ 이 값을 앱이 어디서 얻나?
```

층은 비콘의 `major`(= 100 + 층)로 정해지는데, 그건 스캔을 시작해야 알 수 있다.
REST 를 부르려면 먼저 WS 로 비콘을 올려 층을 알아낸 뒤 REST 를 불러야 하는데,
그러면 순서가 뒤집힌다. WS 하나로 하면 이 문제 자체가 생기지 않는다.

---

## 2. 엔드포인트

```
ws://<서버>/ws/navigation
```

인증 없음(기존 `/ws`와 동일). 연결 하나 = 사용자 한 명의 안내 세션.

### `/ws` 와 `/monitor` 는 그대로 둔다

| | 쓰는 곳 | 성격 |
|---|---|---|
| `/ws` | `/monitor`, 실측앱(`bleapp`) | 붙어 있는 전부에게 뿌린다 |
| `/ws/navigation` | 사용자앱 | 그 연결에만 보낸다 |

`/ws` 는 **연결 목록을 돌면서 모두에게 보내는** 구조다. 모니터가 폰의 RSSI를 봐야
하므로 일부러 그렇게 만들었다.

```python
async def _broadcast(payload, exclude=None):
    for conn in list(_connections):     # ← 붙어 있는 전부에게
        await conn.send_text(payload)
```

사용자앱을 여기 붙이면 남의 RSSI를 초당 수십 개씩 받아서 버려야 하고,
반대로 사용자앱 메시지가 모니터로 샌다. 성격이 반대라 같은 문에 둘 수 없다.

`/monitor` 는 실측·검수 도구로 계속 쓴다. WEB-FE 로 옮기지 않는다 —
옮기는 대신 WEB-FE 에 필요한 기능을 **추가**하는 방식으로 간다.
지도 도구가 양쪽에 있는 상태가 되지만, 지금 도는 것을 깨지 않는 편이 낫다.

---

## 3. 서버 → 앱 : 메시지는 한 가지 모양이다

**모든 서버 메시지가 같은 필드를 쓴다.** 앱은 `event` 값을 몰라도 동작한다.

```jsonc
{
  "event": "disambiguate",       // 참고용. 앱은 분기하지 않아도 된다
  "state": "listening",          // 앱이 보여줄 화면
  "utterance": "화장실 1번, 화장실 2번 중에서 말씀해 주세요.",
  "listenAfter": true,
  "haptic": null,
  "screen": {                    // 화면 표시용. 없으면 이전 것을 유지
    "title": "어디로 갈까요?",
    "items": ["화장실 1", "화장실 2"],
    "step": null, "totalSteps": null
  }
}
```

### 필드

| 필드 | 뜻 | 앱이 할 일 |
|---|---|---|
| `utterance` | 읽을 문장. `null`이면 없음 | 그대로 읽는다 |
| `listenAfter` | 답을 기다리는가 | `true`면 **발화가 끝난 뒤** 마이크를 연다 |
| `haptic` | `guide` / `warn` / `arrive` / `null` | 정해진 패턴으로 진동 |
| `state` | `ready` / `listening` / `navigating` / `arrived` | 해당 화면으로 전환 |
| `screen` | 화면에 띄울 것 | 있으면 갱신, 없으면 유지 |
| `event` | 무슨 일이 일어났는지 | 로그·디버깅용. 분기하지 않아도 된다 |

### `utterance`가 발화 신호다

| 값 | 앱이 할 일 |
|---|---|
| 문자열 | 읽는다 |
| `null` 또는 없음 | **아무 말도 하지 않는다** |

빈 문자열(`""`)은 쓰지 않는다. `null`과 구분이 안 되면 앱이 빈 발화를 시도한다.

같은 문장을 반복해 들려주면 사용자가 매우 괴로우므로,
**무엇을 말할지가 아니라 말할지 말지를 서버가 정한다.**
비콘을 지날 때마다 떠들지 않도록, 무음 구간은 `utterance: null`로 내려간다.

### `listenAfter` — 마이크를 여는 시점

**반드시 발화가 끝난 뒤에 열어야 한다.** 읽는 도중에 열면 자기 TTS를 그대로
받아적어서 무한 루프가 된다. TTS 완료 콜백을 써야 하고 타이머로 때려맞히면 안 된다.
(실측앱에서 `speak(text, done)` 콜백으로 처리했다)

이 필드를 서버가 정하는 이유: 앱이 `event` 종류마다 "답을 기다려야 하나"를
판단하게 하면, 서버가 이벤트를 추가할 때마다 앱도 고쳐야 한다.

### `event` 값 목록 (참고)

앱은 분기하지 않아도 되지만, 로그와 사람의 이해를 위해 값은 정해둔다.

| `event` | 언제 |
|---|---|
| `disambiguate` | 후보가 여럿이라 되묻는다 |
| `notFound` | 목록에 없는 곳을 말했다 |
| `routeFailed` | 목적지는 알아들었지만 갈 수 있는 길이 없다 |
| `start` | 경로가 정해져 안내를 시작한다 |
| `advance` `back` | 다음/이전 비콘으로 이동했다 |
| `deviate` | 경로를 벗어났다 |
| `arrive` | 도착했다 |
| `none` | 아무 일도 없음(무음 구간) |
| `resume` | 재연결 후 현재 상태를 다시 알려준다 |

---

## 4. 앱 → 서버

### 4-1. 목적지를 말했다

```jsonc
{ "event": "destination", "text": "화장실 가고 싶어" }
```

**첫 발화와 되묻기 답변이 같은 이벤트다.** 앱은 자기가 지금 되묻기에 답하는
중인지 몰라도 된다. 문맥은 서버가 들고 있다(§5).

### 4-2. 취소

```jsonc
{ "event": "cancel" }
```

"그만"이라고 말했거나, 취소 버튼을 눌렀거나, **폰을 흔들었을 때**
(앱에 `util/ShakeDetector.kt` 가 있다).

서버는 되묻기 후보와 진행 중인 경로를 모두 버리고 `state: "ready"` 로 돌려보낸다.

### 4-3. 비콘 관측

```jsonc
{
  "event": "beacons",
  "ts": 1786500000000,
  "beacons": [ { "major": 104, "minor": 3, "rssi": -63 } ]
}
```

**판정 기준은 major/minor 다.** MAC 이 아니다.

| | 뜻 |
|---|---|
| `major` | 층. **`100 + 층번호`** — 4층이면 104 (`floor/service.py:29`) |
| `minor` | 그 층의 비콘 번호 |
| `rssi` | 신호 세기(dBm) |

MAC 을 안 쓰는 이유: MAC 은 기기를 바꾸면 달라진다. 비콘이 고장나 교체하면
관리자가 DB 의 MAC 을 다시 입력해야 하고, 그 사이 안내가 죽는다.
minor 는 **펌웨어에 새겨 넣는 논리 번호**라 같은 자리에 새 기기를 달아도 그대로다.

major 로 층이 바로 나오는 것도 크다. 여러 층을 다룰 때 "이 신호가 몇 층 것인가"를
따로 조회하지 않아도 된다.

> **과도기 처리.** 지금 펌웨어는 전 비콘이 `major=1, minor=1` 이고(§7-5),
> 앱은 제조사 데이터를 파싱하지 않는다. 재플래시가 끝날 때까지는 `mac` 이나
> `name` 을 같이 실어 보내면 서버가 그걸로도 매칭한다.
>
> ```jsonc
> { "major": 104, "minor": 3, "mac": "44:B1:...", "name": "ESP32-Beacon3-tx", "rssi": -63 }
> ```
>
> 서버는 **minor → MAC → 이름** 순으로 찾는다(`map_source.resolve_beacon`).
> 셋 중 무엇이 채워져 있든 동작하고, 전환이 끝나면 minor 만 남는다.

**스캔될 때마다 즉시 보낸다.** 원안의 "1초 주기로 묶어 보내기"는 채택하지 않았다.

> 실측 13개 데이터셋에서 비콘당 표본 간격 중앙값이 **87ms(11.5Hz)**였고,
> 위치 판정(중앙값3 → 칼만 → 2.5초 구간 최소제곱)이 그 밀도에 맞춰 튜닝되어 있다.
>
> | | 2.5초 판정 창의 표본 수 |
> |---|---|
> | 스캔마다 즉시 | 약 29개 |
> | 1초 배치 | 2~3개 |
>
> 또한 1초마다 누적 맵을 통째로 보내면 아직 재스캔되지 않은 비콘의 옛 값이
> 반복 전송된다. 칼만 필터가 그 반복값을 새 측정으로 받아들여 톱니 파형을 만든다
> (폰을 가만히 둬도 나타난다). 실측앱에서 이미 겪고 고친 문제다.

### 4-4. 목적지 목록을 달라

```jsonc
{ "event": "list" }
```

음성이 안 될 때(시끄러운 곳, 마이크 권한 거부, STT 반복 실패) 화면에서 골라야 한다.
서버가 `screen.items` 에 전체 목록을 실어 보낸다.

```jsonc
서버 → { "event":"list", "state":"listening", "utterance":null,
         "screen":{ "title":"목적지", "items":[
           { "id":"lm_407", "name":"407호" },
           { "id":"lm_wc1", "name":"화장실 1" } ] } }
```

터치로 고르면 그 `id` 를 목적지로 보낸다.

```jsonc
{ "event": "destination", "id": "lm_407" }
```

`text` 대신 `id` 를 보내면 해석을 건너뛴다 — 이미 확정된 선택이므로.

### 4-5. 재연결

```jsonc
{ "event": "resume", "sessionId": "s-abc123" }
```

연결이 끊겼다 붙었을 때. 서버가 현재 상태를 `event: "resume"`으로 다시 내려준다.
`sessionId`는 서버가 첫 연결 때 알려준 값을 그대로 돌려준다.

---

## 5. 되묻기 판정은 서버가 한다

### 서버가 들고 있는 것 — 이미 구현되어 있다

```python
_PENDING_KEY = "pending"          # 후보 목록
_PENDING_AT_KEY = "pending_at"    # 물어본 시각
_PENDING_TTL_MS = 120_000         # 2분
```

전역이 아니라 **연결별**이다. 전역이면 폰 A에게 물어놓은 후보를 폰 B가 집어간다.
2분이 지나면 버린다 — 되묻고 그냥 둔 채 한참 뒤에 다른 말을 하면 그건 답변이 아니다.

### 후보를 이름으로 읽어준다

```
계단 1번, 엘리베이터 1번, 계단 2번, 계단 3번 중에서 말씀해 주세요.
```

"첫 번째, 두 번째, 세 번째 중에"처럼 순서로 뭉뚱그리지 않는다.
후보 종류가 섞이면 사용자가 **무엇 중에서 고르는지 알 수 없기** 때문이다.

> 실제로 났던 버그: 후보가 `['계단1','엘베1','계단2','계단3']`인데
> "계단이 4곳 있습니다"로 읽어서 엘리베이터가 계단이 됐다.
> "두 번째"라고 답하면 계단인 줄 알고 엘리베이터로 간다.

### 받는 대답의 형태

| 대답 | 처리 |
|---|---|
| "화장실 2번", "엘리베이터 1번" | 이름 대조 — **읽어준 말을 따라 한 경우** |
| "두 번째", "둘째", "가운데 거" | 모델이 해석 |
| "3번째", "2" | 숫자만 말한 경우 순서로 |
| "몰라" | 다시 묻는다 |

**이름 대조가 순서 해석보다 먼저다.** 후보를 이름으로 읽어주므로 따라 말하는 것이
가장 흔한 대답인데, 이름 속 숫자가 순서로 읽히면 전부 틀린다.

> 실제로 났던 버그: 후보 `['계단1','엘베1','계단2','계단3']`에서
> "엘베 1번" → 계단1, "계단 2번" → 엘베1, "계단3" → 계단2.
> 읽어준 대로 답했는데 전부 오답이었다.

### 되묻는 중에 새 목적지를 말하면

후보 안에서 못 고르면 **전체 목록에서 다시 해석한다.** 확실히 잡히면 그걸로 간다.

이 규칙이 없으면 사용자가 되묻기에 갇힌다 — 마음이 바뀌어 다른 곳을 말해도
"못 알아들었습니다"만 반복된다.

---

## 6. 대화 예시

### 연결 직후 — 서버가 먼저 말을 건다

```jsonc
(앱이 /ws/navigation 에 연결)

서버 → { "event":"ready", "state":"listening", "sessionId":"s-abc123",
         "utterance":"목적지를 말씀해 주세요.", "listenAfter":true }
```

앱은 연결만 하면 된다. 무엇을 먼저 말할지도 서버가 정한다.
`sessionId` 는 재연결(§4-4) 때 돌려보낼 값이다.

### 한 번에 확정

```jsonc
앱 → { "event":"destination", "text":"사백칠호로 안내해줘" }

서버 → { "event":"start", "state":"navigating",
         "utterance":"407호로 안내합니다. 손이 닿는 벽을 짚고 걸어주세요.",
         "listenAfter":false, "haptic":"guide",
         "screen":{ "title":"407호", "step":1, "totalSteps":12 } }
```

### 되묻기

```jsonc
앱 → { "event":"destination", "text":"화장실 가고 싶어" }

서버 → { "event":"disambiguate", "state":"listening",
         "utterance":"화장실 1번, 화장실 2번 중에서 말씀해 주세요.",
         "listenAfter":true, "haptic":null,
         "screen":{ "title":"어디로 갈까요?", "items":["화장실 1","화장실 2"] } }

앱 → { "event":"destination", "text":"두 번째" }

서버 → { "event":"start", "state":"navigating", "utterance":"화장실로 안내합니다. ...",
         "listenAfter":false, "screen":{ "title":"화장실 2", "step":1, "totalSteps":9 } }
```

### 안내 중 — 대부분은 무음

```jsonc
서버 → { "event":"none", "state":"navigating", "utterance":null,
         "listenAfter":false, "screen":{ "step":3, "totalSteps":12 } }

서버 → { "event":"advance", "state":"navigating",
         "utterance":"벽을 따라 오른쪽으로 꺾으세요.",
         "listenAfter":false, "haptic":"guide",
         "screen":{ "step":4, "totalSteps":12 } }

서버 → { "event":"arrive", "state":"arrived",
         "utterance":"407호입니다. 문은 왼쪽에 있습니다.",
         "listenAfter":false, "haptic":"arrive",
         "screen":{ "title":"도착", "step":12, "totalSteps":12 } }
```

### 되묻는 중에 마음이 바뀜

```jsonc
앱 → { "event":"destination", "text":"화장실" }
서버 → { "event":"disambiguate", "listenAfter":true, ... }
앱 → { "event":"destination", "text":"아니 407호로 가줘" }
서버 → { "event":"start", "state":"navigating", "screen":{ "title":"407호" }, ... }
```

### 없는 곳

```jsonc
앱 → { "event":"destination", "text":"옥상 정원" }
서버 → { "event":"notFound", "state":"listening",
         "utterance":"찾지 못했습니다. 다시 말씀해 주세요.", "listenAfter":true }
```

---

## 7. 앱에 필요한 변경

### 7-1. 매칭 코드를 없앤다 — 가장 급하다

```kotlin
// DestinationFragment.kt:115  — 현재
matched.size > 1 -> {
    select(matched.first()); return
}
```

**후보가 여럿일 때 말없이 첫 번째를 고른다.** "화장실"이라고 하면 화장실 1로
조용히 간다. 화면을 볼 수 없는 사용자는 잘못 간 것을 알 방법이 없다.

`DestinationMatcher.kt`(64줄)와 `MockApi.match()`를 지우면
**앱이 고를 일이 없어져서 이 버그가 구조적으로 사라진다.**

### 7-2. `aliases`를 없앤다

원안에서 `aliases`는 "STT가 409를 「사백구」로 돌려주니 별칭 표가 없으면 매칭이
불가능하다"는 근거로 있었다. 그 전제는 **매칭을 앱이 할 때만** 맞다.

지금은 매칭이 서버에 있고, 한국어 수사는 `korean_numbers()`가 기계적으로 푼다
("사백칠"→407, "사공칠"→407). 동의어는 로컬 LLM이 판단한다.

별칭 표는 이미 한 번 넣었다가 **이름이 바뀐 건물에서 매칭을 망가뜨리는 것을
확인하고 걷어냈다**(`docs/음성_목적지_매칭.md` 5장). 앱에 같은 표를 다시 만들면
같은 문제가 되돌아온다.

목록이 필요하면 WS 의 `event:"list"` 로 받는다(§4-4). REST 는 쓰지 않는다.

### 7-3. 상태를 서버가 지시한다

현재 전이 표에는 되묻기가 들어갈 자리가 없다.

```kotlin
NavState.LISTENING to setOf(NavState.ROUTING, NavState.READY),
```

`DISAMBIGUATE`는 2차로 빠져 있고, `LISTENING → LISTENING` 자기 전이도
`transition()`이 `next == current`면 false를 돌려주므로 막혀 있다.

`state` 필드를 서버가 내려보내므로 **앱의 전이 표 자체가 필요 없어진다.**
받은 `state`로 화면을 바꾸면 된다. `NavStateMachine`은 남겨도 되지만
전이 검사는 서버 쪽 관심사가 된다.

> 이 앱의 원칙("화면이 아니라 상태로 설계한다")과 충돌하지 않는다.
> 상태로 설계하는 것은 그대로이고, 그 상태를 누가 정하느냐만 서버로 옮긴 것이다.

### 7-4. iBeacon 제조사 데이터를 파싱한다

major/minor 를 보내려면 앱이 광고 패킷의 제조사 데이터를 읽어야 한다.
**지금은 두 앱 모두 파싱하지 않는다** — MAC 과 광고 이름만 쓴다.

```java
byte[] d = result.getScanRecord().getManufacturerSpecificData(0x004C);  // Apple
// d[0..1]   = 0x02 0x15   (iBeacon 표시)
// d[2..17]  = UUID
// d[18..19] = major   (big-endian)
// d[20..21] = minor
// d[22]     = txPower
int major = ((d[18] & 0xFF) << 8) | (d[19] & 0xFF);
int minor = ((d[20] & 0xFF) << 8) | (d[21] & 0xFF);
```

`getManufacturerSpecificData(0x004C)` 는 **회사 ID 다음부터** 돌려주므로
위 인덱스가 맞는다. 길이가 23이 아니면 iBeacon 이 아니니 건너뛴다.

### 7-5. 서버 주소를 빌드 설정으로

```java
// BleScanner.java:80
webSocketManager = new WebSocketManager("wss://nearby-ideal-handhelds-marsh.trycloudflare.com/ws");
```

cloudflare 임시 터널이라 재시작하면 주소가 바뀐다.
README에 "BASE_URL은 빌드 설정으로 분리한다"고 적혀 있는데 아직 코드에 박혀 있다.

---

## 7-A. 펌웨어 — 비콘마다 다른 minor 를 새긴다

**major/minor 로 판정하려면 이게 선행되어야 한다.** 지금은 전 비콘이 같은 값이다.

```cpp
// firmware/beacon/beacon.ino  — 현재
beacon.setMajor(1);
beacon.setMinor(1);        // ← 파일 하나로 32개를 전부 구웠다
```

이 상태로는 major/minor 를 보내봐야 전부 `1/1` 이라 구분이 안 된다.

### 정할 값

| | 규칙 | 예 |
|---|---|---|
| UUID | 건물 전체가 같은 값 | 지금 것 유지 |
| `major` | **`100 + 층번호`** | 4층 → `104` |
| `minor` | 그 층의 비콘 번호 (1부터) | B1 → `1`, B12 → `12` |

major 규칙은 DB 가 이미 그렇게 쓰고 있다.

```python
major=100 + req.floor,      # floor/service.py:29
```

### 굽는 방법

32개를 각각 다른 소스로 관리하면 반드시 어긋난다. 값만 바꿔 굽도록 한 줄로 모은다.

```cpp
#define BEACON_MAJOR 104     // ← 층
#define BEACON_MINOR 3       // ← 이 줄만 바꿔서 다시 굽는다

beacon.setMajor(BEACON_MAJOR);
beacon.setMinor(BEACON_MINOR);
```

굽고 나면 어느 기기가 몇 번인지 **몸체에 적어둔다.** 안 적으면 나중에 스캔해서
찾아야 하고, 벽 높은 곳에 붙은 뒤에는 그것도 어렵다.

### 지도와 맞추기

map-tool 의 비콘(`B1`, `B2`…)에 minor 를 적을 자리가 아직 없다.
지금은 `bleName` 만 있다. 비콘 편집 패널에 minor 입력을 추가해야 한다.

**WEB-FE 는 건드리지 않는다** — 비콘 CRUD 에 `minor` 필드가 이미 있어서
관리자가 거기서 입력할 수 있다(`features/beacons/api.ts`).
DB 로 넘어가면 그 경로를 쓰면 되고, 그때까지는 map-tool 파일이 출처다.

---

## 8. 연결이 끊기면

앱이 상태를 안 들고 있으므로, 소켓이 끊기면 앱은 아무것도 모른다.
이건 이 설계의 **대가**이고, 그래서 재연결 규약이 필요하다.

| 상황 | 앱 | 서버 |
|---|---|---|
| 끊김 감지 | 화면 유지, 재연결 시도 | 세션을 일정 시간 보관 |
| 재연결 성공 | `resume` + `sessionId` 전송 | 현재 상태를 `event:"resume"`으로 재송신 |
| 세션 만료 | — | `state:"ready"` 로 초기화해 응답 |

**끊긴 동안 앱은 말하지 않는다.** 마지막 안내를 반복하는 것보다 조용한 편이 낫다.
오래 끊기면(15초) 서버가 아니라 앱이 판단해 "연결이 끊겼습니다"를 알린다 —
서버가 못 알려주는 유일한 경우라서다.

---

## 9. 기존 구현과의 대응

서버 쪽 해석·되묻기 로직은 **이미 동작하고 검증되어 있다**(`/ws`의 `destination`).
바뀌는 것은 껍데기(엔드포인트와 필드 이름)뿐이다.

| 지금 (`/ws`) | 새로 (`/ws/navigation`) |
|---|---|
| `{type:"destination", event:"resolve", text}` | `{event:"destination", text}` |
| `{type:"destination", event:"choose", text}` | `{event:"destination", text}` (같은 이벤트) |
| `event:"ambiguous"` + `speech` | `event:"disambiguate"` + `utterance` + `listenAfter` |
| `event:"resolved"` + `landmark` | `event:"start"` + `state:"navigating"` + `screen` |
| `event:"notFound"` + `speech` | 그대로 + `utterance` + `listenAfter` |
| `type:"guide"` + `speech` | `event:"advance"` 등 + `utterance` |
| `source` (llm / rule / llm→rule) | 유지 — 디버깅용 |

---

## 10. 아직 안 정한 것

| 항목 | 상태 |
|---|---|
| 안내 문장 생성 | 템플릿 18종을 서버가 만든다. 미구현 — 지금은 단순 문장만 |
| 출발 비콘 판정 | 1차는 앱이 보낸 것 중 제일 센 비콘. `POST /api/locate`는 2차 |
| 층 이동 | 연결자 통과 후 새 층 안내. 2차 |
| 경로 이탈 | `deviate` 이벤트 형식만 정함. 재탐색은 2차 |
| 여러 층 | 지금은 파일 기반이라 층 하나 고정. DB 전환 시 해소 |
| 세션 보관 시간 | 재연결 유예를 몇 분으로 할지 |
| 비콘 재플래시 | minor 를 새겨 다시 굽는 물리 작업(§7-A). 아직 안 함 |
| map-tool minor 입력 | 비콘 편집 패널에 칸 추가 필요 |

### 정해진 것 (이 문서에서 바뀐 것)

| 항목 | 결정 |
|---|---|
| `POST /api/route` | **없앤다** — 앱이 `steps.size` 만 쓴다 |
| 판정 식별자 | **major/minor** (MAC 아님). 과도기엔 MAC·이름 폴백 |
| RSSI 전송 주기 | **스캔마다 즉시** (1Hz 배치 아님) |
| `aliases` | **없앤다** — 매칭이 서버로 갔으므로 |
| 되묻기 | 같은 `destination` 이벤트로 처리, 문맥은 서버가 보관 |
| 앱 상태 전이 | **서버가 `state` 로 지시** |
| `/ws`, `/monitor` | **그대로 둔다.** WEB-FE 로 옮기지 않고 기능만 추가 |

---

## 10-A. 토의 필요 — 건물을 어떻게 정하나

**아직 정하지 않았다.** 대학교일 수도 병원일 수도 있는데, 서버는 어느 건물인지
알아야 랜드마크와 경로를 꺼낼 수 있다.

### 후보 ①: 비콘 UUID 로 자동 판별 (추천)

iBeacon 광고에는 UUID·major·minor 가 다 실려 있고, **UUID 는 건물 전체가 같은 값**이다.

```cpp
// firmware/beacon/beacon.ino:5
#define BEACON_UUID "8ec76ea3-6668-48da-9866-75be8bc86f4d"
```

```
UUID   → 건물      (8ec76ea3-... → 수원대 ICT융합대학)
major  → 층        (104 → 4층)
minor  → 비콘      (3 → B3)
```

**세 값만으로 건물·층·비콘이 다 나온다.** 사용자가 아무것도 고르지 않아도 되고,
건물 이름을 알 필요도 없다. 시각장애인에게는 이게 가장 나은 경험이다 —
"여기가 어디냐"를 사람이 아니라 기계가 답한다.

앱은 이미 보낼 것에 UUID 만 더하면 된다.

```jsonc
{ "event":"beacons",
  "beacons":[ { "uuid":"8ec76ea3-...", "major":104, "minor":3, "rssi":-63 } ] }
```

**필요한 작업**: `buildings` 테이블에 UUID 칸이 없다(`code`, `name`, `address`,
`floor_count`, `favorite`, `status` 뿐). 추가해야 한다 → DB 재구축 목록에 포함.

**한계**: 건물 안에 들어가서 비콘이 잡혀야만 안다. 밖에서는 모른다.

### 후보 ②: 건물 목록 REST + 사용자 선택

```
GET /api/buildings/public     서비스 중인 건물 목록
```

**이게 유일한 REST 가 될 가능성이 있다.**

밖에서 미리 정하는 경우에 필요하다 — 집에서 "○○병원 3층 접수처까지 안내" 같은.
①은 그걸 못 한다.

**문제는 시각장애인이 정확한 명칭을 모른다는 것이다.** "수원대학교 ICT융합대학"을
정확히 말할 수 있어야 하는데, 보통은 "수원대 컴퓨터관" 같은 식으로 말한다.
결국 여기도 LLM 매칭이 필요해지고, 그러면 목적지 매칭과 같은 구조가 된다
— 즉 **WS 로 하는 게 일관된다.**

```jsonc
앱 → { "event":"building", "text":"수원대 컴퓨터 있는 건물" }
서버 → { "event":"disambiguate", "utterance":"ICT융합대학, 공과대학 중에서..." }
```

이러면 REST 는 또 0개가 된다.

### 후보 ③: GPS 로 좁히기

주변 건물만 추려서 후보를 줄인다. ②의 보조 수단이지 대체는 아니다.
실내 진입 전에 대략 위치를 알 수 있으므로 "지금 근처에 ○○병원이 있습니다"가 가능하다.

### 정리 — 무엇을 정해야 하나

| 질문 | 선택지 |
|---|---|
| 건물 안에서 시작할 때 | ① UUID 자동 (사용자 개입 0) |
| 밖에서 미리 정할 때 | ② 목록 + 음성 매칭. **REST 로 할지 WS 로 할지** |
| 건물 이름을 모를 때 | LLM 매칭 (목적지와 같은 방식) / GPS 보조 |
| DB | `buildings` 에 UUID 칸 추가할지 |

**1차 범위에서는 ① 만으로 충분하다.** 실측도 건물 안에서 시작하고,
밖에서 미리 정하는 시나리오는 아직 요구사항이 아니다.
②는 요구가 생길 때 붙이되, 붙인다면 WS 쪽이 일관될 것 같다.

---

## 11. 검증 방법

```bash
# 폰 없이 목적지 대화를 왕복시킨다
python tests/check_pipeline.py --say "화장실 가고 싶어"
python tests/check_pipeline.py --say "층계"
python tests/check_pipeline.py --say "사백칠호로 안내해줘"
python tests/check_pipeline.py --say "옥상정원"        # 거절해야 정상

# 걸어가며 말하는 상황 — 목적지 해석이 위치 판정을 막지 않는지
python tests/check_pipeline.py --load
```

`source=llm`이면 모델이 판단한 것이고, `llm→rule(...)`이면 모델을 못 불러
규칙 엔진이 받은 것이다.

프롬프트에 시험 표현이 들어 있으면 확인이 무의미해지므로 직접 볼 수 있게 해뒀다.

```bash
python tests/try_destination.py --prompt
python tests/try_destination.py --trace "층계"
```

---

## 참고

- 음성 목적지 매칭 상세: `docs/음성_목적지_매칭.md`
- 전체 API 현황: `docs/API명세_최종.md`
- 개발 환경 · DB 재구축: `docs/개발환경_준비.md`
