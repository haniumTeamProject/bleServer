# 최종 API 명세서

사용자 앱·관리자웹이 쓰는 API 기준 문서.

> 이 문서는 실제 코드(`backend-python` FastAPI, `WEB-FE` React) 기준으로 작성한다.
> 기존 노션 문서(v3.0 설계서)는 필드명·경로가 실제 구현과 다른 부분이 있어 참고용으로만
> 쓰고, **충돌 시 코드를 기준으로 한다.**
>
> 인증: JWT. 로그인 성공 시 `accessToken` 발급, 이후 `Authorization: Bearer {token}`.
> `/api/admin/auth/*`(로그인·회원가입)와 `/ws`(웹소켓)만 인증 없이 접근 가능.
>
> 응답 필드는 전부 camelCase (`floorCount`, `buildingId`, `connectorId`, `isAnchor` 등).
> 파이썬 쪽은 snake_case로 쓰고 `CamelModel`이 직렬화할 때만 변환한다(`app/common.py`).

---

## 인증 (Auth)

| 내용 | 유형 | URL | 인증 | 상태 |
|---|---|---|---|---|
| 관리자 로그인 | POST | `/api/admin/auth/login` | 불필요 | 구현 완료 |
| 관리자 회원가입 | POST | `/api/admin/auth/signup` | 불필요 | 백엔드 완료 · **프론트 미구현** |

**로그인**

- 요청: `{ email, password }`
- 응답 200: `{ accessToken }`
- 실패: 401(이메일/비번 불일치), 403(승인 대기·거절 계정)

**회원가입**

- 요청: `{ email, password, name, org }` — 공문 파일 첨부 미구현(별도 multipart 엔드포인트 필요)
- 응답 201, 상태는 `pending`으로 생성됨(로그인 불가)

---

## 가입 승인 (Accounts) — super_admin 전용

| 내용 | 유형 | URL | 인증 | 상태 |
|---|---|---|---|---|
| 가입 신청 목록 | GET | `/api/accounts?status=` | super_admin | 백엔드 완료 · **프론트 미구현** |
| 가입 승인·거절 | PATCH | `/api/accounts/{adminId}/status` | super_admin | 백엔드 완료 · **프론트 미구현** |

- 목록 응답: `id, email, name, org, position, phone, building, status, role, officialDocUrl, approvedBy, approvedAt, createdAt`
- 승인·거절 요청: `{ status: "active" | "rejected" }`
- 회원가입으로 만들 수 있는 계정은 전부 `role: admin`. `super_admin`을 만드는 API가 없어
  DB에서 직접 role을 바꿔야 한다.

---

## 건물 (Buildings)

| 내용 | 유형 | URL | 인증 | 상태 |
|---|---|---|---|---|
| 목록 조회 | GET | `/api/buildings` | 필요 | 완료 |
| 등록 | POST | `/api/buildings` | 필요 | 완료 |
| 상세 조회 | GET | `/api/buildings/{buildingId}` | 필요 | 완료 |
| 수정 | PATCH | `/api/buildings/{buildingId}` | 필요 | 완료 |
| 삭제 | DELETE | `/api/buildings/{buildingId}` | 필요 | 완료 |

- 요청/응답 필드: `code, name, address, floorCount`
- 응답에만: `favorite`(bool), `status`(층 세팅 진행 상태 대표값)

---

## 층 (Floors)

| 내용 | 유형 | URL | 인증 | 상태 |
|---|---|---|---|---|
| 목록 조회 | GET | `/api/buildings/{buildingId}/floors` | 필요 | 완료 |
| 등록 | POST | `/api/buildings/{buildingId}/floors` | 필요 | 완료 |
| 삭제 | DELETE | `/api/buildings/{buildingId}/floors/{floorId}` | 필요 | 완료 |

- 요청: `{ floor }` (층 번호)
- 응답: `{ id, buildingId, floor, major, status }` — **`major = 100 + floor`, 서버가 계산**
- `status`: `floorplan_missing → review_needed → beacon_missing → connector_missing → ready`
- 층 수정(PATCH) API 없음 — 등록/삭제만 가능

---

## 연결자 (Connectors)

| 내용 | 유형 | URL | 인증 | 상태 |
|---|---|---|---|---|
| 목록 조회 | GET | `/api/buildings/{buildingId}/connectors` | 필요 | 완료 |
| 등록 | POST | `/api/buildings/{buildingId}/connectors` | 필요 | 완료 |
| 삭제 | DELETE | `/api/buildings/{buildingId}/connectors/{connectorId}` | 필요 | 완료 |

- 요청/응답: `{ name, type(elevator|stairs), floors: number[] }`
- 연결자 수정(PATCH) API 없음

---

## 설계도 (Floorplan)

| 내용 | 유형 | URL | 인증 | 상태 |
|---|---|---|---|---|
| 조회 | GET | `/api/floors/{floorId}/floorplan` | 필요 | 완료 |
| 업로드 | PUT | `/api/floors/{floorId}/floorplan` | 필요 | 완료 |
| 삭제 | DELETE | `/api/floors/{floorId}/floorplan` | 필요 | 완료 |

- 요청: `{ imageUrl }` — 실제로는 이미지 base64 data URL을 그대로 저장(파일 스토리지 아님)
- 응답에 `extracted`(bool, 벽·이동영역 자동 추출 완료 여부)

---

## 이동영역 마스크 (Mask)

| 내용 | 유형 | URL | 인증 | 상태 |
|---|---|---|---|---|
| 조회 | GET | `/api/floors/{floorId}/mask` | 필요 | 완료 |
| 저장 | PUT | `/api/floors/{floorId}/mask` | 필요 | 완료 |

- 요청/응답: `{ width, height, dataUrl }` — 채운 영역을 투명 배경 PNG(data URL)로 통째 저장
- **v3.0 설계서와 차이**: `scale_m_per_px`(픽셀→미터 축척) 없음.
  위치판정에 실측 축척이 필요하면 이 필드부터 추가해야 한다.

---

## 비콘 (Beacons)

| 내용 | 유형 | URL | 인증 | 상태 |
|---|---|---|---|---|
| 목록 조회 | GET | `/api/floors/{floorId}/beacons` | 필요 | 완료 |
| 등록 | POST | `/api/floors/{floorId}/beacons` | 필요 | 완료 |
| 수정 | PATCH | `/api/floors/{floorId}/beacons/{beaconId}` | 필요 | 완료 |
| 삭제 | DELETE | `/api/floors/{floorId}/beacons/{beaconId}` | 필요 | 완료 |

- 요청: `{ name, mac?, minor, type(anchor|checkpoint|connector), connectorId?, x?, y? }`
- 응답에만: `major`(층 major 복사값), `isAnchor`(= `type === "anchor"`, 서버가 계산해 저장)
- `type`은 현재 등록 폼에만 쓰이고 실시간 위치판정(`/ws`)은 참조하지 않음 — 분류용 메타데이터
- **v3.0 설계서와 차이**: 식별자는 uuid. `checkpoint`는 별도 엔티티가 아니라 `type` 값 중
  하나라 `checkpointId` 같은 연결 필드 없음. `rssiThreshold`/`txPower` 없음.

---

## 랜드마크 (Landmarks)

| 내용 | 유형 | URL | 인증 | 상태 |
|---|---|---|---|---|
| 목록 조회 | GET | `/api/floors/{floorId}/landmarks` | 필요 | 완료 |
| 등록 | POST | `/api/floors/{floorId}/landmarks` | 필요 | 완료 |
| 수정 | PATCH | `/api/floors/{floorId}/landmarks/{landmarkId}` | 필요 | 완료 |
| 삭제 | DELETE | `/api/floors/{floorId}/landmarks/{landmarkId}` | 필요 | 완료 |

- 요청: `{ name, type(room|restroom|facility|entrance), x?, y? }`
- 응답에만: `visualTagId`(시각태그 연결, 현재 항상 null — 미구현)

---

## 실시간 통신 (WebSocket `/ws`)

인증 없음. 안드로이드 앱(RSSI 송신)과 `/monitor` 페이지가 같은 엔드포인트에 붙는다.

**1) RSSI 측정값 중계**

- 앱 → 서버: `{ timestamp, "<비콘키>": rssi, ... }` (비콘키 = `MAC|이름`)
- 서버 → 나머지(보낸 사람 제외): `{ timestamp, "<키>": 원본, "<키>__f": 칼만값, _track?: 스냅샷 }`
- 서버가 중앙값 + 칼만 + 히스테리시스 (`ws/rssi_filter.py`)

**2) 측정 제어 (`type: "measure"`)**

- `{ type:"measure", event:"start"|"mark"|"end", sessionId, label, timestamp, device?, markCount? }`
- 측정 시작/종료가 경로 안내 켜짐/꺼짐도 같이 제어한다

**3) 경로 안내 (`type: "guide"`)**

- 앱 → 서버: `{ type:"guide", event:"setPath", path:[비콘키...], threshold, minNext, mode?, windowMs?, segments? }` / `{ event:"stop" }`
- 서버 → 전체: `{ type:"guide", event:"transition", direction, index, total, beacon, name, isLast, speech, timestamp }`

**4) 음성 목적지 (`type: "destination"`)** — 이 문서 원본에는 없으나 구현되어 있음

- 앱 → 서버: `{ type:"destination", event:"resolve"|"choose"|"cancel"|"list", text, requestId }`
- 서버 → **요청한 폰에게만**: `{ event:"resolved"|"ambiguous"|"notFound", landmark|candidates, speech, source }`
- 해석은 로컬 LLM(Ollama), 실패 시 규칙 엔진으로 폴백 (`ws/llm_matcher.py`)

**알려진 제약**: 필터 상태(`_filters`)와 경로 추적기(`_tracker`)가 모든 연결에서 전역 공유됨.
동시에 여러 명 접속 시 섞일 수 있음(Java 원본부터 있던 이슈, 미해결).
되묻기 상태(`session`)와 목적지 응답 전달은 연결별로 분리 완료.

---

## 사용자 앱이 요구하는 API — **아직 없음**

APP-FE(`org.mcsmtp.wayfinder`)가 `net/model/Models.kt`에서 전제하는 것들이다.
현재 백엔드 엔드포인트 28개는 **전부 관리자웹용**이고 사용자앱용은 0개다.

원안은 아래와 같았으나, **일부는 채택하지 않았다.**
확정된 규약은 `docs/API_목적지_되묻기_WS.md` 에 있고 아래는 그 요약이다.

| 우선순위 | Method | 경로 | 상태 |
|---|---|---|---|
| 필수 | WS | `/ws/navigation` | **채택.** 목적지·되묻기·목록·안내를 전부 여기서 |
| 필수 | GET | `/api/floors/{floorId}/destinations` | **폐기** — WS `event:"list"` 로 대체 |
| 필수 | POST | `/api/route` | **폐기** — 앱이 응답을 `steps.size` 만 썼다 |
| 2차 | POST | `/api/locate` | 보류. 1차는 제일 센 비콘으로 대체 |
| 2차 | POST | `/api/route/reroute` | 보류 |
| 여유 | GET | `/api/config` | 보류 |

**사용자앱이 쓰는 REST 는 0개다.** 전부 `/ws/navigation` 하나로 처리한다.

### 왜 `GET .../destinations` 를 폐기했나

목록은 음성이 안 될 때 화면에서 고르는 용도인데, 그것도 WS 로 보내면 된다
(`screen.items`). 그리고 **앱이 `floorId` 를 알 방법이 없다** — 층은 비콘의
`major`(= 100 + 층)로 정해지므로 스캔을 시작해야 알 수 있고, REST 를 부르려면
WS 를 먼저 열어야 하는 순서 역전이 생긴다.

### 왜 `POST /api/route` 를 폐기했나

앱이 그 응답을 쓰는 곳이 한 줄뿐이었다.

```kotlin
// NavigationFragment.kt:96
totalSteps = route.steps.size.takeIf { it > 0 } ?: events.maxOfOrNull { it.currentStep } ?: 0
```

`steps[i].instruction` 은 어디서도 읽지 않는다. 발화는 전부 WS 의 `utterance` 에서 온다.
48m 짜리 경로 대본을 통째로 내려보내고 배열 길이 하나만 쓰는 셈이라,
진행 상황을 매 이벤트에 정수 두 개(`step`, `totalSteps`)로 실어 보내는 것으로 대체했다.

### 1. 목적지 목록

```json
{ "floorId": "suwon_ict-4", "floorName": "수원대학교 ICT융합대학 4층",
  "destinations": [
    { "id": "lm_409a", "name": "409호 앞문",
      "type": "room", "doorSide": "left" } ] }
```

`doorSide`(`left`/`right`/`null`)는 도착 안내 「문은 오른쪽에 있습니다」에 쓴다.

> **`aliases`는 넣지 않는다.** APP-FE 초안에는 `aliases: ["409","사백구","409호실"]`가
> 있었고, "STT가 409를 「사백구」로 돌려주니 별칭 표가 없으면 매칭이 불가능하다"는 것이
> 근거였다. 그 전제는 **매칭을 앱이 한다**고 봤을 때만 맞다.
>
> 지금은 매칭이 서버에 있고, 한글 수사는 `landmark_matcher.korean_numbers()`가
> 기계적으로 푼다("사백칠"→407, "사공칠"→407, "사백십오"→415). 별칭 표는
> 이미 한 번 넣었다가 **이름이 바뀐 건물에서 매칭을 망가뜨리는 것을 확인하고 걷어냈다**
> (`docs/음성_목적지_매칭.md` 5장). 같은 표를 앱에 다시 만들면 같은 문제가 되돌아온다.

### 2. 경로 요청 — **폐기됨 (참고용)**

> 아래는 원안이다. 지금은 `/ws/navigation` 의 `event:"destination"` 하나로 대체됐다.
> 목적지가 정해지면 서버가 경로를 만들어 들고 있고, 앱에는 안내 문장만 흘려보낸다.

`POST /api/route` → `{ floorId, fromBeaconId, toDestinationId }`

```json
{ "routeId": "rt_0001", "totalDistanceM": 48.5, "estimatedSeconds": 70,
  "steps": [
    { "seq": 1, "beaconId": "B1", "turn": null,
      "template": "start", "instruction": "409호로 안내합니다. ..." },
    { "seq": 3, "beaconId": "B4", "turn": null,
      "template": "silent", "instruction": null } ] }
```

**추적 단위는 경로노드가 아니라 비콘이다.** 경로노드는 서버 계산에만 쓰고 응답에 넣지
않는다. 앱은 `steps.size`로 진행 표시 점 개수를 그린다.
`instruction: null`은 **무음 구간**이라는 뜻이다.

### 3. 실시간 안내 (WebSocket `/ws/navigation`)

**앱 → 서버**

```json
{ "event": "beacons", "ts": 1786500000000,
  "beacons": [ { "major": 104, "minor": 3, "rssi": -63 } ] }
```

**판정 기준은 major/minor 다.** 원안은 MAC 이었으나 바꿨다 — MAC 은 기기를 교체하면
달라져서 그때마다 DB 를 고쳐야 한다. `major = 100 + 층`, `minor = 그 층의 비콘 번호`.

비콘 ID(`B1`, `B2`) ↔ major/minor 매핑은 서버가 한다 — 앱은 ID 체계를 모른다.
재플래시가 끝날 때까지는 MAC·이름도 같이 실어 보내고 서버가 폴백한다.

> **전송 주기: 스캔될 때마다 즉시.** 원안은 "1초 주기"였으나 채택하지 않는다.
> 실측 13개 데이터셋에서 비콘당 표본 간격 중앙값이 **87ms(11.5Hz)**였고, 판정 로직
> (중앙값3 → 칼만 → 2.5초 구간 최소제곱)은 그 밀도에 맞춰 튜닝되어 있다.
>
> | | 2.5초 판정 창의 표본 수 |
> |---|---|
> | 스캔마다 즉시 (현재) | 약 29개 |
> | 1초 배치 | 2~3개 |
>
> 또한 1초마다 누적 맵을 통째로 보내면, 아직 재스캔되지 않은 비콘의 옛 값이 반복 전송된다.
> 칼만 필터는 그 반복값을 매번 새 측정으로 받아들여 톱니 파형을 만든다(폰을 가만히 둬도
> 나타남). 실측앱에서 이미 겪고 고친 문제다 — `BleScanner.java`의 해당 주석 참조.

**서버 → 앱**

```json
{ "currentStep": 2, "currentBeaconId": "B7", "nextBeaconId": "B10",
  "progress": 0.35, "event": "advance",
  "utterance": "오른쪽으로 꺾으세요", "haptic": "guide" }
```

`event`: `none` `advance` `back` `deviate` `arrive`

**`utterance: null`이 발화 억제 신호다.** 같은 문장을 반복해 들려주면 사용자가 매우
괴로우므로, **무엇을 말할지가 아니라 말할지 말지를 서버가 정한다.** 빈 문자열 대신 `null`.

---

## 코드끼리 어긋나 있는 곳 (확인됨)

통합 전에 정리해야 하는 것들. 전부 실제로 실행해 확인했다.

### 1. 비콘 `type` 값이 서로 다르다

| | 값 |
|---|---|
| 백엔드 (`beacon/service.py`) | `anchor` \| `checkpoint` \| `connector` |
| WEB-FE (`types/domain.ts`) | `semantic` \| `reinforcement` |

백엔드는 `type: str | None`이라 아무 문자열이나 받는다. 그래서 **에러 없이 통과하고**
`is_anchor = (type == "anchor")`가 **영원히 false**가 된다.

```python
is_anchor=(req.type == "anchor")     # beacon/service.py:24
beacon.is_anchor = beacon.type == "anchor"   # :56
```

### 2. WEB-FE가 보내는 `sourceUid`/`sourceLabel`이 조용히 버려진다

WEB-FE의 `CreateBeaconInput`은 `sourceUid`, `sourceLabel`을 보내지만 `BeaconRequest`에
그 필드가 없다. pydantic 기본값이 `extra="ignore"`라 **422도 안 나고 그냥 사라진다.**

```
WEB-FE 페이로드 → {'name': 'B3', 'mac': None, 'minor': 3, 'type': 'semantic',
                  'connector_id': None, 'x': None, 'y': None}
sourceUid 가 남았나: False
```

이건 단순 누락이 아니다. `lib/mapImport.ts`의 재가져오기 diff가 `sourceUid`로 매칭하므로,
저장이 안 되면 **map-tool에서 다시 가져올 때마다 관리자가 입력한 MAC·이름이 날아간다.**

### 3. DB에 없는 필드

| 필드 | 어디서 필요한가 |
|---|---|
| `Beacon.source_uid`, `source_label` | WEB-FE 재가져오기 diff |
| `Landmark.source_uid`, `source_label` | 〃 |
| `Landmark.door_side` | 안내 템플릿 13·14번 「문은 {문방향}에 있습니다」 |
| **경로노드 테이블 자체** | 방향(좌/우) 판정의 근거. 현재 없음 |

### 4. 펌웨어가 major/minor를 구분하지 않는다

```
firmware/beacon/beacon.ino:  setMajor(1)  setMinor(1)     ← 파일 하나, 전 비콘 동일
앱:                          제조사 데이터를 파싱하지 않음 (MAC과 이름만 사용)
map-tool 프로젝트 파일:        비콘 필드가 {id, x, y} 뿐
```

DB와 WEB-FE는 `major`/`minor`를 갖고 있지만, **지금 보내봐야 전부 1/1이다.**
비콘별로 다른 minor를 넣어 재플래시해야 실제로 쓸 수 있다.

**결정: 판정 기준은 major/minor 로 간다.** MAC 은 기기를 바꾸면 달라지므로
비콘을 교체할 때마다 DB 를 고쳐야 한다. minor 는 펌웨어에 새기는 논리 번호라
같은 자리에 새 기기를 달아도 그대로다. major(`100 + 층`)로 층이 바로 나오는 것도 크다.

재플래시가 끝날 때까지는 앱이 MAC·이름을 같이 실어 보내고, 서버가
**minor → MAC → 이름** 순으로 찾는다. 자세한 것은 `docs/API_목적지_되묻기_WS.md` §7-A.

---

## 참고

- 음성 목적지 매칭: `docs/음성_목적지_매칭.md`
- 위치 판정 로직: `docs/위치판정_코드해설.md`
- 파이프라인 점검: `python tests/check_pipeline.py`
