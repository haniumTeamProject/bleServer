# 사용자 앱 API 명세

`APP-FE` ↔ `backend-python` 통신 규약.

> 이 문서는 **무엇을 주고받는지**만 적음.
> 왜 그렇게 정했는지는 `docs/API_목적지_되묻기_WS.md` 에 있음.

---

## 요약

**사용자 앱이 쓰는 REST 없음.** WebSocket 하나로 전부 처리함.

```
ws://<서버>/ws/navigation
```

| | 값 |
|---|---|
| 인증 | 없음 |
| 세션 | 연결 1개 = 사용자 1명 |
| 형식 | JSON 텍스트 프레임 |
| 브로드캐스트 | 없음 (그 연결에만 응답) |

---

## 1. 앱 → 서버

`event` 로 구분. 5가지.

### `destination` — 목적지 지정

```jsonc
{ "event": "destination", "text": "화장실 가고 싶어" }
```

```jsonc
{ "event": "destination", "id": "lm_407" }
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `text` | string | △ | STT 결과 1순위 |
| `id` | string | △ | 목록에서 터치로 고른 경우. 해석 건너뜀 |
| `requestId` | string | | 응답에 그대로 실려 옴 |

`text` 또는 `id` 중 하나 필수.

**첫 발화와 되묻기 답변이 같은 이벤트임.** 앱은 구분하지 않음.

### `beacons` — 비콘 관측

```jsonc
{
  "event": "beacons",
  "ts": 1786500000000,
  "beacons": [
    { "uuid": "8ec76ea3-...", "major": 104, "minor": 3, "rssi": -63 }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `ts` | int | | 폰 시각(ms) |
| `beacons[].uuid` | string | | 건물 식별 (§7) |
| `beacons[].major` | int | ○ | 층 = `100 + 층번호` |
| `beacons[].minor` | int | ○ | 그 층의 비콘 번호 |
| `beacons[].rssi` | int | ○ | 신호 세기(dBm) |
| `beacons[].mac` | string | | 과도기 폴백 |
| `beacons[].name` | string | | 과도기 폴백 |

**전송 주기: 스캔될 때마다 즉시.** 묶어 보내지 않음.
비콘 하나가 잡히면 그 하나만 보냄.

> 재플래시 전까지는 전 비콘이 `major=1, minor=1` 임.
> `mac`·`name` 을 같이 보내면 서버가 **minor → MAC → 이름** 순으로 찾음.

### `list` — 목적지 목록 요청

```jsonc
{ "event": "list" }
```

음성이 안 될 때 화면에서 고르기 위한 것. 응답은 `screen.items` 로 옴.

### `cancel` — 취소

```jsonc
{ "event": "cancel" }
```

되묻기·안내를 모두 버리고 `state: "ready"` 로 돌아감.

### `resume` — 재연결

```jsonc
{ "event": "resume", "sessionId": "s-abc123" }
```

`sessionId` 는 서버가 첫 연결 때 준 값.

---

## 2. 서버 → 앱

**메시지 모양이 하나임.** 앱은 `event` 로 분기하지 않음.

```jsonc
{
  "event": "disambiguate",
  "state": "listening",
  "utterance": "화장실 1번, 화장실 2번 중에서 말씀해 주세요.",
  "listenAfter": true,
  "haptic": null,
  "screen": {
    "title": "어디로 갈까요?",
    "items": [
      { "id": "lm_wc1", "name": "화장실 1" },
      { "id": "lm_wc2", "name": "화장실 2" }
    ],
    "step": null,
    "totalSteps": null
  }
}
```

### 필드

| 필드 | 타입 | 앱이 할 일 |
|---|---|---|
| `utterance` | string \| null | 문자열이면 읽음. `null` 이면 **아무 말도 하지 않음** |
| `listenAfter` | bool | `true` 면 **발화가 끝난 뒤** 마이크를 엶 |
| `haptic` | string \| null | `guide` / `warn` / `arrive` / `null` |
| `state` | string | `ready` / `listening` / `navigating` / `arrived` — 해당 화면으로 |
| `screen` | object \| null | 있으면 화면 갱신, 없으면 이전 것 유지 |
| `event` | string | 로그용. 분기하지 않아도 됨 |
| `sessionId` | string | 첫 연결 때만. 재연결에 씀 |

`utterance` 에 빈 문자열(`""`)은 오지 않음. 발화 없음은 항상 `null`.

### `screen`

| 필드 | 타입 | 설명 |
|---|---|---|
| `title` | string \| null | 화면 제목 |
| `items` | array \| null | `{id, name}` 목록. 터치 선택용 |
| `step` | int \| null | 현재 몇 번째 비콘 |
| `totalSteps` | int \| null | 전체 비콘 수. 진행 표시 점 개수 |

### `event` 값

| 값 | 언제 |
|---|---|
| `ready` | 연결 직후. `sessionId` 를 함께 줌 |
| `disambiguate` | 후보가 여럿이라 되물음 |
| `notFound` | 목록에 없는 곳을 말함 |
| `list` | 목적지 목록 응답 |
| `start` | 경로가 정해져 안내 시작 |
| `advance` | 다음 비콘으로 진행 |
| `back` | 이전 비콘으로 되돌아감 |
| `none` | 아무 일 없음 (무음 구간) |
| `deviate` | 경로 이탈 |
| `arrive` | 도착 |
| `routeFailed` | 목적지는 알아들었으나 갈 길이 없음 |
| `resume` | 재연결 후 현재 상태 |
| `error` | 처리 실패 |

---

## 3. 앱 처리 규칙

받은 메시지 하나를 이렇게 처리함. **이게 전부임.**

```kotlin
fun onServerMessage(msg: JSONObject) {
    msg.optString("state").takeIf { it.isNotEmpty() }?.let { showScreen(it) }
    msg.optJSONObject("screen")?.let { updateScreen(it) }
    msg.optString("haptic").takeIf { it.isNotEmpty() }?.let { haptics.play(it) }

    val utterance = msg.optString("utterance", null)
    val listenAfter = msg.optBoolean("listenAfter", false)

    when {
        utterance != null && listenAfter -> speech.speakThen(utterance) { stt.start(...) }
        utterance != null                -> speech.speak(root, utterance)
        listenAfter                      -> stt.start(...)
    }
}
```

### 지켜야 할 것

| # | 규칙 | 안 지키면 |
|---|---|---|
| 1 | 마이크는 **TTS 완료 콜백** 이후에 엶 | 자기 TTS 를 받아적어 무한 루프 |
| 2 | `utterance: null` 이면 아무 말도 안 함 | 반복 발화로 사용자가 괴로움 |
| 3 | 목적지 매칭을 앱에서 하지 않음 | 조용히 틀린 곳으로 안내 |
| 4 | 스캔될 때마다 즉시 보냄 | 위치 판정 정확도 하락 |
| 5 | 끊긴 동안 말하지 않음 | 낡은 안내를 반복 |

---

## 4. 대화 흐름

### 연결 → 목적지 → 안내 → 도착

```jsonc
(연결)
서버 → { "event":"ready", "state":"listening", "sessionId":"s-abc123",
         "utterance":"목적지를 말씀해 주세요.", "listenAfter":true }

앱  → { "event":"destination", "text":"사백칠호로 안내해줘" }

서버 → { "event":"start", "state":"navigating",
         "utterance":"407호로 안내합니다. 손이 닿는 벽을 짚고 걸어주세요.",
         "listenAfter":false, "haptic":"guide",
         "screen":{ "title":"407호", "step":1, "totalSteps":12 } }

앱  → { "event":"beacons", "beacons":[{ "major":104, "minor":3, "rssi":-63 }] }
      (스캔될 때마다 계속)

서버 → { "event":"none", "state":"navigating", "utterance":null,
         "screen":{ "step":3, "totalSteps":12 } }

서버 → { "event":"advance", "state":"navigating",
         "utterance":"벽을 따라 오른쪽으로 꺾으세요.",
         "listenAfter":false, "haptic":"guide",
         "screen":{ "step":4, "totalSteps":12 } }

서버 → { "event":"arrive", "state":"arrived",
         "utterance":"407호입니다. 문은 왼쪽에 있습니다.",
         "listenAfter":false, "haptic":"arrive",
         "screen":{ "title":"도착", "step":12, "totalSteps":12 } }
```

### 되묻기

```jsonc
앱  → { "event":"destination", "text":"화장실 가고 싶어" }

서버 → { "event":"disambiguate", "state":"listening",
         "utterance":"화장실 1번, 화장실 2번 중에서 말씀해 주세요.",
         "listenAfter":true,
         "screen":{ "title":"어디로 갈까요?",
                    "items":[{"id":"lm_wc1","name":"화장실 1"},
                             {"id":"lm_wc2","name":"화장실 2"}] } }

앱  → { "event":"destination", "text":"두 번째" }

서버 → { "event":"start", "state":"navigating", ... }
```

되묻는 중에 **다른 목적지를 말해도 됨.** 후보에 없으면 전체에서 다시 찾음.

```jsonc
앱  → { "event":"destination", "text":"아니 407호로 가줘" }
서버 → { "event":"start", "screen":{ "title":"407호" }, ... }
```

### 목록에서 고르기

```jsonc
앱  → { "event":"list" }

서버 → { "event":"list", "state":"listening", "utterance":null,
         "screen":{ "title":"목적지",
                    "items":[{"id":"lm_401","name":"401호"}, ...] } }

앱  → { "event":"destination", "id":"lm_401" }

서버 → { "event":"start", "state":"navigating", ... }
```

### 못 찾음

```jsonc
앱  → { "event":"destination", "text":"옥상 정원" }
서버 → { "event":"notFound", "state":"listening",
         "utterance":"찾지 못했습니다. 다시 말씀해 주세요.", "listenAfter":true }
```

### 재연결

```jsonc
(끊김 → 다시 붙음)
앱  → { "event":"resume", "sessionId":"s-abc123" }

서버 → { "event":"resume", "state":"navigating", "utterance":null,
         "screen":{ "title":"407호", "step":4, "totalSteps":12 } }
```

세션이 만료됐으면 `state:"ready"` 로 응답함.

---

## 5. 오류

```jsonc
{ "event":"error", "state":"ready", "reason":"badRequest",
  "utterance":"다시 시도해 주세요.", "listenAfter":false }
```

| `reason` | 뜻 |
|---|---|
| `badRequest` | 메시지 형식 오류 |
| `noBeacon` | 비콘이 안 잡혀 현재 위치를 모름 |
| `mapMissing` | 해당 층의 지도 데이터 없음 |
| `internal` | 서버 오류 |

`routeFailed` 는 별도 이벤트임 (§2).

---

## 6. 관리자웹 REST

사용자 앱과 무관. `docs/API명세_최종.md` 참고.

| 대상 | 경로 |
|---|---|
| 인증 | `/api/admin/auth/*` |
| 건물·층·연결자 | `/api/buildings`, `/api/buildings/{id}/floors`, `.../connectors` |
| 설계도·마스크 | `/api/floors/{id}/floorplan`, `.../mask` |
| 비콘·랜드마크 | `/api/floors/{id}/beacons`, `.../landmarks` |

---

## 7. 토의 필요 — 건물을 어떻게 정하나

**아직 정하지 않음.** 대학교일 수도 병원일 수도 있는데, 서버는 어느 건물인지
알아야 랜드마크와 경로를 꺼낼 수 있음.

### 후보 ① 비콘 UUID 로 자동 판별 — 유력

iBeacon 광고에 UUID·major·minor 가 다 실려 있고, **UUID 는 건물 전체가 같은 값**임.

```
UUID   → 건물   (8ec76ea3-... → 수원대 ICT융합대학)
major  → 층     (104 → 4층)
minor  → 비콘   (3 → B3)
```

세 값만으로 건물·층·비콘이 다 나옴. **사용자가 아무것도 고르지 않아도 되고
건물 이름을 알 필요도 없음.** 앱은 이미 보내는 `beacons` 에 `uuid` 만 더하면 됨.

- **필요한 작업**: `buildings` 테이블에 UUID 칸이 없음. 추가해야 함.
- **한계**: 건물에 들어가 비콘이 잡혀야 앎. 밖에서는 모름.

### 후보 ② 건물 목록 + 사용자 선택

밖에서 미리 정하는 경우에 필요함 — 집에서 "○○병원 3층 접수처까지" 같은.
①로는 안 됨.

**시각장애인이 정확한 명칭을 모른다는 문제가 있음.** "수원대학교 ICT융합대학"을
정확히 말해야 하는데 보통은 "수원대 컴퓨터관" 식으로 말함. 결국 LLM 매칭이
필요해지고, 그러면 목적지 매칭과 같은 구조가 됨 — **WS 로 하는 편이 일관됨.**

```jsonc
앱  → { "event":"building", "text":"수원대 컴퓨터 있는 건물" }
서버 → { "event":"disambiguate", "utterance":"ICT융합대학, 공과대학 중에서..." }
```

이렇게 하면 REST 는 여전히 0개임. REST 로 한다면 이것이 **유일한 REST** 가 됨.

```
GET /api/buildings/public
```

### 후보 ③ GPS 로 좁히기

주변 건물만 추려 후보를 줄임. ②의 보조 수단이지 대체는 아님.

### 정해야 할 것

| 질문 | 선택지 |
|---|---|
| 건물 안에서 시작 | ① UUID 자동 (사용자 개입 0) |
| 밖에서 미리 정할 때 | ② 목록 + 음성 매칭. **REST 로 할지 WS 로 할지** |
| 이름을 모를 때 | LLM 매칭 / GPS 보조 |
| DB | `buildings` 에 UUID 칸 추가 여부 |

**1차 범위는 ① 만으로 충분함.** 실측도 건물 안에서 시작하고, 밖에서 미리 정하는
시나리오는 아직 요구사항이 아님. ②는 요구가 생기면 붙이되 WS 쪽이 일관될 듯.

---

## 8. 그 밖의 미확정

| 항목 | 상태 |
|---|---|
| 안내 문장 | 템플릿 18종 미구현. 지금은 단순 문장만 |
| 출발 비콘 | 1차는 제일 센 비콘. `POST /api/locate` 는 2차 |
| 층 이동 | 연결자 통과 후 새 층 안내. 2차 |
| 경로 이탈 재탐색 | `deviate` 이벤트 형식만 정함. 2차 |
| 세션 보관 시간 | 재연결 유예를 몇 분으로 할지 |
| 비콘 재플래시 | minor 를 새겨 다시 굽는 물리 작업. 미완 |

---

## 참고

| 문서 | 내용 |
|---|---|
| `API_목적지_되묻기_WS.md` | 이 규약을 왜 이렇게 정했는지 · 앱 변경 목록 · 펌웨어 |
| `API명세_최종.md` | 관리자웹 REST 전체 |
| `음성_목적지_매칭.md` | 목적지 해석 로직 |
| `개발환경_준비.md` | 서버 띄우기 · DB 재구축 |
