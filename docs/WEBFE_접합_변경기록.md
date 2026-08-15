# WEB-FE 접합 변경 기록

관리자웹(WEB-FE)과 백엔드를 붙이면서 **백엔드를 고친 내용**과 그 근거.

> 기준: 프론트가 이미 구현해 둔 쪽에 맞춤.
> 서로 다른 항목은 임의로 정하지 않고 물어본 뒤 진행했고, 아래에 결정을 남긴다.

---

## 대전제

작업 전에 34개 엔드포인트를 전수 대조했다. WEB-FE 의 mock 핸들러
(`src/mocks/handlers.ts`)를 **계약의 정본**으로 삼았다 — 화면들이 그 mock 을
상대로 개발되어 실제로 동작하므로, 거기 적힌 경로·필드가 곧 명세다.

문서보다 코드가 정확했던 사례가 있다. 기존 명세서에는 계정 API 가
`/api/accounts` 로 적혀 있었으나 실제 구현은 `/api/admin/accounts` 였다.

---

## 1. 프론트 기준으로 맞춘 것 (물어본 뒤 결정)

### 1-1. 계정 승인 경로에서 `/status` 를 뺐다

```
전    PATCH /api/admin/accounts/{id}/status
후    PATCH /api/admin/accounts/{id}
```

**원래는 백엔드 쪽이 REST 관례에 더 맞았다.** 하위 리소스(`status`)를 경로에
드러내는 편이 명확하다. 그럼에도 프론트에 맞춘 이유는 WEB-FE 를 되도록 건드리지
않기로 했기 때문이고, 요청 본문이 `{status}` 하나뿐이라 경로에 없어도 뜻이 분명하다.

이 상태 그대로 두면 프론트 호출이 **404** 였다.

- 고친 곳: `app/admin/router.py`

### 1-2. 랜드마크 분류를 자유 입력으로 (`type` → `category`)

```
전    type: room | restroom | facility | entrance     (고정 4종)
후    category: str                                   (자유 입력)
```

고정 4종으로는 건물 종류마다 다른 분류를 담을 수 없다. 병원의 "채혈실",
"진료지원" 같은 것이 갈 자리가 없다. 프론트 주석이 그 판단을 적어두고 있다.

```typescript
category?: string // 자유 입력 분류(예: 강의실, 화장실) — 고정 목록 아님
```

**대가**: 분류별로 안내 문구를 다르게 만들려면 분기할 값이 없어진다.
필요해지면 그때 별도 필드를 두는 편이 낫다(자유 입력을 다시 고정 목록으로
되돌리면 이미 입력된 값이 깨진다).

- 고친 곳: `app/landmark/models.py`, `schemas.py`, `service.py`

### 1-3. 비콘 타입 값을 바꿨다

```
전    anchor | checkpoint | connector   + is_anchor(파생값)
후    semantic | reinforcement
```

두 체계는 **축이 다르다.**

| | 뜻 |
|---|---|
| 전 (`anchor`/`checkpoint`/`connector`) | 이 비콘이 무슨 **역할**인가 |
| 후 (`semantic`/`reinforcement`) | **사람이 찍었나, 자동으로 채웠나** |

프론트에는 후자를 전제로 한 기능이 이미 돌고 있다 — 의미비콘 사이 간격이
D_max(6m)를 넘으면 보강비콘을 자동 배치한다(`lib/reinforcementBeacons.ts`).
`semantic` 만 골라 간격을 재야 하므로 이 구분 없이는 동작하지 않는다.

`is_anchor` 는 같이 지웠다. 새 값 체계에 `anchor` 가 없고, 프론트 `Beacon` 타입에도
그 필드가 없다.

> **바뀌기 전 상태가 조용히 틀리고 있었다.** 백엔드가 `type: str` 이라 프론트가
> 보낸 `"semantic"` 이 **에러 없이 통과**했고, `is_anchor = (type == "anchor")` 가
> 영원히 false 로 저장됐다. 422 도 안 났으므로 아무도 몰랐다.

- 고친 곳: `app/beacon/models.py`, `schemas.py`, `service.py`

### 1-4. 연결자 위치를 비콘에서 연결자로 옮겼다

```
전    Beacon.connector_id                     비콘이 "나는 엘베 A 입구다"
후    ConnectorPosition(connector_id, floor_id, x, y)
      연결자가 "나는 4층에서 (x,y)에 있다"
```

담는 정보는 같은데 **주인이 반대다.**

연결자는 여러 층에 걸쳐 있다. 엘리베이터 하나가 1~5층을 운행하면 층마다 입구가
있어야 한다. 비콘 쪽에 두면 "어느 층 입구가 빠졌는지" 보려고 **전 층의 비콘을 뒤져야**
한다. 연결자 쪽에 모아두면 그 연결자만 보면 결손이 바로 드러난다.

프론트의 결손 검수 화면이 정확히 그렇게 동작한다.

```typescript
// ConnectorReviewPage.tsx:22 — 정책 3.3
// 운행층인데 좌표(positions)가 없는 칸이 결손
return floor && !c.positions?.some((p) => p.floorId === floor.id)
```

`Beacon.connector_id` 는 지웠다. 두 곳에 같은 정보를 두면 동기화가 어긋난다.

- 고친 곳: `app/connector/models.py`(테이블 신설), `schemas.py`, `service.py`, `router.py`
- 새 엔드포인트: `PUT`/`DELETE /api/buildings/{bid}/connectors/{cid}/positions/{floorId}`

---

## 2. 버그였던 것 (결정 불필요)

### 2-1. `sourceUid` / `sourceLabel` 이 조용히 버려지고 있었다

map-tool 과 관리자웹을 잇는 **재가져오기 매칭 키**인데 저장되지 않았다.

정상 흐름은 이렇다.

```
1. map-tool 에서 비콘 32개를 찍음          → 각각 uid 부여
2. JSON 내보내 관리자웹으로 가져옴          → 비콘 32개 생성, sourceUid 저장
3. 관리자가 MAC·minor·이름을 손으로 입력     ← 32번 타이핑
4. map-tool 에서 위치를 조정하고 다시 내보냄
5. 관리자웹 재가져오기
```

5단계에서 `diffImport` 가 uid 로 갈라낸다.

```typescript
const target = byUid.get(source.uid)
if (target) toUpdate.push(...)      // 좌표만 갱신 — MAC·minor 보존
else        toCreate.push(...)      // 새로 생성
```

2단계에서 uid 가 버려지면 `byUid` 가 비어 **전부 새로 생성**된다. 게다가 기존 항목은
`toDelete` 에도 안 들어간다(`filter(e => e.sourceUid ...)` 에서 걸러짐).
결과는 **비콘 64개, 관리자가 입력한 MAC 은 옛 32개에만 남음.**

에러 없이 일어난다 — pydantic 기본값이 `extra="ignore"` 라 422 도 안 난다.

```
WEB-FE 페이로드 → {'name':'B3', 'minor':3, 'type':'semantic', ...}
sourceUid 가 남았나: False
```

- 고친 곳: `Beacon`·`Landmark` 에 `source_uid`, `source_label` 컬럼 추가

### 2-2. 층 상태가 되돌아가지 못했다

```
전    floors.status 컬럼에 저장 + 단계마다 한 칸씩 올림 (bump_status)
후    조회할 때마다 실제 데이터로부터 계산 (app/status.py)
```

저장 방식은 **한 방향으로만 움직인다.** 설계도를 지워도 상태는 그대로 남고,
비콘을 전부 지워도 `ready` 로 남는다. 관리자가 화면에서 "안내 가능"으로 보는데
실제로는 안내가 안 되는 상태가 만들어진다.

프론트는 처음부터 계산된 값을 기대하고 있었다.

```typescript
// WEB-FE/src/mocks/db.ts
// status는 더 이상 시드로 넣지 않는다 — handlers.ts가 매 조회마다
// 실제 데이터(설계도·마스크·비콘·연결자)로부터 계산해 내려준다.
```

`app/status.py` 의 `floor_status()` 는 `handlers.ts` 의 `computeFloorStatus()` 와
**같은 순서로 판정한다.** 두 곳이 갈라지면 관리자가 보는 뱃지가 서버와 달라지므로
고칠 때 같이 고쳐야 한다.

`scale_missing` 단계도 이때 추가됐다(프론트에는 있었고 백엔드에는 없었다).

- 고친 곳: `app/status.py`(신규), `app/floor/service.py`, `app/building/service.py`
- 지운 것: `bump_status()`, `recompute_status()`, `floors.status`·`buildings.status` 컬럼

---

## 3. 프론트에 있고 백엔드에 없어 새로 만든 것

| Method | 경로 | 쓰는 화면 |
|---|---|---|
| GET | `/api/admin/me` | 상단바 사용자 정보 |
| PATCH | `/api/admin/me` | 계정 화면 |
| GET | `/api/floors/{id}/scale` | 지도 검수 — 축척 |
| PUT | `/api/floors/{id}/scale` | 〃 |
| PUT | `/api/buildings/{bid}/connectors/{cid}/positions/{floorId}` | 연결자 배치 |
| DELETE | `/api/buildings/{bid}/connectors/{cid}/positions/{floorId}` | 〃 |

축척(`scaleMPerPx`)은 **보강비콘 자동배치가 이 값 없이는 못 돈다** —
D_max 6m 를 픽셀로 환산해야 하기 때문이다.

`Building.created_at` 도 응답에 추가했다(프론트 `Building` 타입에 있었다).

---

## 4. DB 스키마 변경 — 재구축이 필요하다

`Base.metadata.create_all()` 은 **없는 테이블만 만든다.** 컬럼 변경은 반영하지
않으므로 아래 변경을 적용하려면 DB 를 지우고 다시 만들어야 한다.
절차는 `docs/개발환경_준비.md` 참고.

| 테이블 | 변경 |
|---|---|
| `beacons` | `connector_id` 제거, `is_anchor` 제거, `source_uid`·`source_label` 추가 |
| `landmarks` | `type` → `category`, `source_uid`·`source_label` 추가 |
| `floors` | `status` 제거, `scale_m_per_px` 추가 |
| `buildings` | `status` 제거, `created_at` 추가 |
| `connector_positions` | **신설** (connector_id, floor_id, x, y) |

---

## 5. 검증

```bash
python tests/test_webfe_contract.py     # 경로·필드 대조 (서버·DB 불필요)
python tests/test_admin_api.py          # 실제 왕복 (SQLite 메모리, Postgres 불필요)
```

`test_webfe_contract.py` 는 WEB-FE 의 mock 핸들러와 도메인 타입을 직접 읽어
서버와 비교한다. **프론트가 바뀌면 이 테스트가 먼저 깨진다.**

```
✓ mock 경로 34개가 서버에 존재            전부 있음
✓ Beacon/Landmark/Building/Floor/Connector 응답이 프론트 타입을 덮는다
✓ BeaconType = semantic | reinforcement
✓ 층 상태 값이 프론트와 같다               서버 6개
전체 9개 통과 ✓
```

`test_admin_api.py` 는 동작을 본다. 특히 위 2-1·2-2 가 실제로 고쳐졌는지 확인한다.

```
✓ 설계도를 지우면 상태가 되돌아간다          floorplan_missing
✓ sourceUid 가 저장된다                  u-b3
✓ 수정해도 sourceUid 는 유지된다
✓ 운행층인데 좌표가 없으면 결손              connector_missing
✓ 좌표를 찍으면 결손이 풀린다               ready
전체 30개 통과 ✓
```

기존 로직 회귀도 확인했다.

```
test_landmark_matcher.py   66개 통과
test_llm_matcher.py        13개 통과
test_route_engine.py       24개 통과
eval_tracker.py            ③ 모드 26/31, 정지오탐 0   (변화 없음)
```

---

## 6. 경로노드 — 테이블을 만들지 않기로 했다

노드(코너·벽 끝·건너기 도착점)는 **이동영역 마스크에서 계산되는 값**이고,
관리자가 손으로 넣는 값이 없다. 마스크는 이미 DB 에 있으므로 저장할 이유가 없다.

관리자웹도 같은 방식이다 — 저장하지 않고 매번 다시 만든다.

```typescript
// WEB-FE/src/pages/pathnodes/PathNodePage.tsx
const { data: savedMask } = useMask(floorId)                    // 서버에서 마스크
const mask = await decodeMask(savedMask.dataUrl, dims.w, dims.h)
const { nodes, edges } = generatePathNodes(mask, dims.w, dims.h, entrances)
```

`pathNodes:${floorId}` 는 localStorage 캐시일 뿐 서버로 보내지 않는다.

안내에 쓰는 값 중 **사람이 넣는 것은 노드가 아니라 다른 곳에 붙는다.**

| 안내 값 | 출처 |
|---|---|
| `{방향}` `{상하}` `{위험방향}` | 계산 (노드·비콘 시퀀스) |
| `{문방향}` | 랜드마크 `doorSide` — 관리자 입력 |
| `{난간방향}` `{버튼방향}` | 연결자 속성 — 관리자 입력 |

랜드마크·연결자는 이미 테이블이 있다.

### 노드 생성기를 어느 쪽으로 맞추나 — WEB-FE

노드 생성기가 두 벌 있고 **결과가 다르다.**

| | 노드 종류 | 크기 |
|---|---|---|
| map-tool (`map_inspection.html`) | `corner` `junction` `waypoint` | 306줄 |
| **WEB-FE (`pathNodes.ts`)** | `corner` `connector` `landmark` `facing` | 373줄 |

**WEB-FE 쪽을 서버로 옮긴다.** map-tool 은 실측용으로 만든 것이고, 관리자가
실제로 검수하는 화면은 WEB-FE 다. 서버가 다른 그래프를 쓰면 관리자가 화면에서
확인한 경로와 사용자가 안내받는 경로가 달라진다.

`facing` 은 건너기(벽 → 벽) 도착점을 노드로 명시한 것이다. map-tool 에는 없다.

### 건너기는 양방향이다 — 프론트 기준

관리자웹이 경로 찾기를 직접 구현했다(`src/features/mapEditor/pathfind.ts`, 신규).
내 `route_engine.py` 와 세 군데가 달랐고, **전부 프론트를 따르기로 했다.**

| | 관리자웹 `pathfind.ts` | 고치기 전 `route_engine.py` |
|---|---|---|
| 건너기 방향 | **양방향** | 단방향 옵션(`Edge.directed`) |
| 페널티 단위 | `crossPenaltyPx` (픽셀) | `cross_penalty_m` (미터) |
| 반환 거리 | 페널티 **포함** | 페널티 제외(순수 이동거리) |

방향이 중요하다. 전에는 건너기가 "입구 ↔ 맞은편"뿐이라 벽 끝에서만 출발하는
단방향이 말이 됐는데, 이번에 **코너에서도 건너기가 생겼다.**

```typescript
// pathNodes.ts — 코너 노드(오목·볼록 모두)에서도 마주보는 벽 지점을 찾아 건너기 후보로 추가한다.
for (const cornerEntry of entries.filter((entry) => entry.kind === 'corner')) {
```

코너는 양쪽 다 벽에 붙어 있으므로 한쪽만 허용할 근거가 없어졌다.

`CROSSING_MAX_PX` 상수도 `crossingMaxPx` 인자로 바뀌어 **축척 기반**이 됐다.
포팅할 때 `scale_m_per_px` 에서 계산해 넘겨야 한다.

---

## 7. `/monitor` 지도가 DB 를 읽는다 (파일 모드는 유지)

실측 도구가 파일을, 관리자웹이 DB 를 보고 있으면 **어느 쪽이 최신인지 알 수 없다.**
그래서 `/monitor` 의 지도를 DB 기준으로 바꿨다. 파일 모드는 그대로 둔다 —
실측 나갔을 때 서버·DB 가 말썽이면 돌아갈 곳이 있어야 한다.

### 읽기만 한다

`/map-db` 에는 **GET 만 있다.** DB 로 쓰는 경로는 관리자웹 하나로 유지한다.
두 곳에서 같은 층을 고치면 덮어쓰기가 생기는데, 언제 일어났는지 추적할 방법이 없다.

`/monitor` 에서 고친 것은 화면에만 남고, 저장은 예전처럼 JSON 내보내기다.

| Method | 경로 | 하는 일 |
|---|---|---|
| GET | `/map-db/floors` | 건물/층 선택 목록 (설계도·마스크·축척 유무 포함) |
| GET | `/map-db/floors/{id}/project` | 한 층을 map-tool 이 읽는 모양으로 |

`/api/*` 에 넣지 않았다. 거기는 관리자웹의 계약이고 `test_webfe_contract.py` 가
프론트 mock 과 대조하는 자리라, 끼면 "프론트가 안 부르는 API"로 잡혀 잡음이 된다.
성격도 `/map-static`(파일) 쪽에 가깝다.

> **인증이 없다.** `/monitor` 자체가 로그인 없이 열리고, 기존 `/map-static/{파일}`
> 이 이미 평면도·비콘이 든 프로젝트 JSON 을 그냥 내주고 있어서 새로 노출되는
> 종류는 없다. **다만 범위가 넓어졌다** — 전에는 static 폴더의 파일 하나였는데
> 이제 DB 의 모든 건물·층이다. 운영에 올릴 때는 이 라우터를 빼거나 인증을 붙인다.

### 좌표계가 세 개라 환산이 필요하다

| | 기준 | 어디에 |
|---|---|---|
| 원본 픽셀 | 업로드한 이미지 그대로 | map-tool 의 `scale_m_per_px` |
| 설계도(900) | 폭을 900 으로 맞춘 것 | **DB 의 `x`, `y`** |
| 마스크 픽셀 | 지도검수 캔버스 해상도 | **DB 의 `scale_m_per_px`** |
| 작업 픽셀 | map-tool 처리 해상도 | 지도 위 비콘·노드 |

> **환산은 map-tool 이 구버전이라서 하는 게 아니다.** 두 도구가 서로 다른 픽셀
> 단위를 쓸 뿐이고 둘 다 자기 안에서는 맞다 — cm 와 inch 같은 관계다.
> 관리자웹은 화면 캔버스 폭이 고정(900)이라 그 기준으로 저장하고, map-tool 은
> 마스크를 픽셀 단위로 칠해야 해서 원본 해상도로 작업한다.
>
> DB 쪽 단위를 기준으로 삼는 이유도 "최신이라서"가 아니라 **DB 를 채우는 게
> 관리자웹이고 안내가 결국 DB 를 보기 때문**이다.
> map-tool 이 실제로 뒤처진 항목은 `uid` 하나뿐이다 (§8-1).
>
> 이 작업에서 **WEB-FE 는 한 줄도 고치지 않았다** (`git ls-files -m` 비어 있음).
> `mapImport.ts`·`reinforcementBeacons.ts`·`maskRaster.ts` 를 읽어 규칙을 알아낸 뒤
> 역함수를 map-tool 쪽에 구현했다.

**축척과 좌표의 기준이 서로 다르다.** 이게 이번에 제일 헷갈린 부분이다.

```typescript
// WEB-FE/src/lib/reinforcementBeacons.ts:80
// scaleMPerPx는 지도검수 캔버스(마스크 픽셀) 기준으로 캘리브레이션된 값이라,
const mPerDesignPx = ratio * scaleMPerPx      // ratio = mask.w / 900
```

그래서 map-tool 이 쓰는 값으로 바꾸려면 이렇게 된다.

```
m/원본px = scaleMPerPx(DB) × maskW / origW
좌표(작업px) = 좌표(900) × workW / 900
```

**서버는 환산하지 않는다.** `origW`·`workW` 는 브라우저가 이미지를 디코딩해야
정해지는 값이라 서버가 알 수 없다. 그래서 900 기준 좌표와 `maskW` 를 그대로
보내고 계산은 받는 쪽에서 한다. 서버가 몰래 바꾸면 어긋나도 티가 안 난다.

### 마스크는 형식이 다르다

| | 담는 방식 |
|---|---|
| map-tool | `workW × workH` 바이트 배열(0/1)을 base64 |
| DB·관리자웹 | 투명배경 PNG data URL — `alpha > 0` 이 통행 가능 |

PNG 를 캔버스에 `workW × workH` 로 다시 그려서 편다. 이때 **보간을 꺼야 한다.**

```javascript
cx.imageSmoothingEnabled = false;
```

켜두면 축소할 때 경계 픽셀이 반투명해지고, `alpha > 0` 으로 자르면 마스크가 한 겹
부푼다. 문틈이 메워져 옆방까지 복도가 되는 식으로 **조용히** 틀린다.

### 벽 마스크는 비워둔다

map-tool 은 마스크를 두 장 쓴다.

| | 뜻 |
|---|---|
| `corridorMask` | 다닐 수 있는 곳 — 결과물 |
| `wallMask` | "여기는 아니다" — **자동 보정에 대한 금지 표시** |

DB·관리자웹에는 통행 영역 한 장뿐이라 `wallMask` 를 담을 자리가 없다.

**경로·노드 생성에는 영향이 없다** — 노드 생성기는 `corridorMask` 만 읽는다.
`wallMask` 는 형태학 연산(간격 메우기·노이즈 제거)과 채우기가 번지는 것을 막는
편집 보조용이다.

```javascript
// map_inspection.html:2030
growAllowed[i] = grown[i] && !wallMask[i] ? 1 : 0;    // 부풀리되 벽은 안 넘는다
```

그래서 DB 모드에서 자동 보정을 누르면 금지 표시 없이 돌아 문틈으로 샐 수 있다.
컬럼은 늘리지 않기로 했고, 버튼도 잠그지 않는다(고쳐도 DB 에 안 남는다).

> **정정.** 여기 처음에 "칠하는 작업은 관리자웹 검수 화면이 담당하므로" 라고 적었는데
> **틀렸다.** 관리자웹에도 벽 그리기가 있고, **거기서도 저장하지 않는다.**
>
> ```typescript
> // WEB-FE/src/pages/map-editor/MapReviewPage.tsx:22
> type Tool = 'fill' | 'drawArea' | 'wall' | 'erase' | 'scale'
> // :41
> const barrierRef = useRef<Uint8Array | null>(null)   // 사용자가 그린 벽 — 메모리에만
> // :579  onSave()
> // 저장용: 통행영역(파랑)만 담은 PNG (벽선은 편집용이라 제외)
> ```
>
> 즉 벽은 **세 곳 중 두 곳에서 이미 휘발성이다.**
>
> | | 벽이 살아남나 |
> |---|---|
> | map-tool 파일 모드 | ✅ `saveProject` 가 `wallMaskB64` 를 쓴다 |
> | map-tool DB 모드 | ❌ |
> | 관리자웹 검수 화면 | ❌ 새로고침하면 사라진다 |
>
> 그래서 DB 모드에서 벽이 안 남는 것은 **회귀가 아니다.** 파일 모드만 우연히
> 되고 있었고, 남기려면 없던 기능을 새로 만드는 쪽이 된다 (§8-5).
>
> 두 도구가 다 버리는 이유도 짐작이 간다 — 벽은 자동 채우기가 문틈으로 새는 것을
> 막는 **작업 중 임시 표시**고, 이동영역이 완성되면 더 쓸 데가 없다. 다만 나중에
> 다시 손볼 때 매번 새로 그어야 한다.

### 연결자도 목적지에 넣었다

DB 는 랜드마크와 연결자를 다른 테이블에 두지만 사용자에게는 둘 다 갈 수 있는 곳이다.
빠지면 "엘리베이터로 가줘"를 말할 수 없다. `ConnectorPosition` 으로 **그 층의
좌표가 찍힌 것만** 골라 `isConnector: true` 를 붙여 합친다.

- 새 파일: `app/nav/db_map_source.py`(DbMapSource + 페이로드), `app/nav/router.py`
- 고친 곳: `app/main.py`(라우터 등록), `map-tool/map_inspection.html`(모드 선택 + DB 로더)
- 검증: `python tests/test_map_db.py` — 34개

`DbMapSource.graph()` 는 아직 못 만든다. 마스크에서 노드를 뽑는 코드(§6)가
파이썬으로 안 옮겨졌기 때문이다. "경로를 못 찾음"으로 뭉뚱그리지 않고 그 사실을
그대로 말하게 해뒀다.

---

## 7-2. 노드 생성기를 파이썬으로 옮겼다

`WEB-FE/src/features/mapEditor/pathNodes.ts` 를 `app/nav/path_nodes.py` 로 옮겼다.
이제 서버가 **파일 없이 DB 의 마스크만으로** 경로 그래프를 만든다.

### 손대지 않고 그대로 옮겼다

보기 좋게 고치고 싶은 곳이 여럿 있었지만 두지 않았다. 예를 들어 경계 추적이
닫히는 마지막 점을 버리는데(`loop.slice(0, -1)`), 고치면 노드 하나가 어긋나
두 구현이 갈라진다. **개선은 관리자웹 쪽에서 하고 다시 옮기는 순서여야 한다.**

### 기대값을 손으로 적지 않았다

`tests/fixtures/gen_pathnodes_reference.mjs` 가 **원본 TS 를 실제로 실행해서**
기대값을 뽑는다(Node 22 의 타입 벗기기 기능을 쓴다).

```bash
node --experimental-strip-types tests/fixtures/gen_pathnodes_reference.mjs
python tests/test_path_nodes.py
```

손으로 적으면 내가 원본을 잘못 읽은 것까지 같이 베끼게 되어 대조가 의미를 잃는다.
관리자웹이 알고리즘을 고치면 위를 다시 돌린다 — 그러면 테스트가 깨지면서
**따라가야 할 변경이 무엇인지** 그 자리에 드러난다.

### 실측 평면도로 확인했다

지어낸 도형은 알고리즘의 갈래를 다 밟지 못한다. 실제 4층 마스크(2372×1790,
통행영역 21만px, 입구 24개)로 돌린 결과가 **완전히 같았다.**

```
노드 수                    102 / 102
연결 수                    150 / 150
노드 id·종류·concave·pairKind   전부 일치
노드 좌표                   최대 오차 0.000e+00 px
연결 순서·종류               건너기 48개
```

### 결과를 들고 있는다

노드 생성이 이 구간에서 제일 비싸다(실측 4층 0.8초). 안내 한 번에 여러 번
불리므로 그래프를 캐시한다. 키에 **마스크·축척·입구 목록**이 전부 들어간다 —
하나라도 빠지면 관리자가 고친 뒤에도 옛 그래프가 나가는데, 그건 화면과 안내가
갈라지는 가장 알아채기 어려운 형태다.

```
첫 호출   0.9s
두 번째   0.001s
```

### 관리자웹과 같은 인자로 부른다

`PathNodePage.tsx` 가 부르는 방식을 그대로 따랐다. 다르면 관리자가 검수한
그래프와 사용자가 안내받는 그래프가 갈라진다.

| | 값 |
|---|---|
| 좌표 | 마스크 픽셀 — DB 의 900 좌표에 `maskW/900` |
| 입구 순서 | **연결자 먼저, 랜드마크 나중** (순서가 노드 번호를 정한다) |
| `crossingMaxPx` | `12m / scaleMPerPx` |
| 건너기 페널티 | `5m` |

> 12m·5m 는 관리자가 화면에서 바꿀 수 있는 값인데 **DB 에 저장하는 자리가 없다.**
> 지금은 서버도 같은 기본값을 쓴다. 화면에서 바꾼 값을 서버가 따르게 하려면
> 컬럼이 필요하다 (§8-6).

### 검증

```bash
python tests/test_path_nodes.py       # 원본 TS 출력과 대조 — 21개
python tests/test_route_from_db.py    # DB → 마스크 → 노드 → 경로 — 14개
```

`test_route_from_db.py` 는 파일 없이 DB 만으로 실제 경로가 나오는지 본다.

```
✓ B1 → 계단1     9m / 13초 / B1→B3
✓ B1 → 엘베1     0m / 1초 / B1        ← 이미 근처(2.0m)
✓ B1 → 엘베2    92m / 131초 / B1→B31→B4→B18→B8→B19
```

> **거리 0 이 정상인 경우가 있다.** 목적지가 출발 비콘 바로 옆이면 같은 노드에
> 붙는다. 처음엔 이걸 실패로 판정하는 테스트를 썼는데, 실측값을 재보니 엘베1 이
> B1 에서 2.0m 였다. 코드가 아니라 테스트가 틀렸다.
>
> 다만 **"이미 근처입니다" 같은 안내가 필요한지는 아직 안 정했다.**

---

## 8. 발견했지만 안 고친 것 — 결정 필요

### 8-1. map-tool 은 `uid` 를 만들지 않는다

`sourceUid` 를 저장하도록 고쳤지만(§2-1), **정작 넣어줄 값이 없다.**

```
map_inspection.html 에서 'uid' 검색 → 0건
저장된 프로젝트의 비콘: {"id": "B1", "x": 237, "y": 1135}
```

관리자웹은 그 값을 필수로 본다.

```typescript
// WEB-FE/src/lib/mapImport.ts:9
export interface MappinProjectBeacon { id: string; uid: string; ... }
// :84
const target = byUid.get(source.uid)     // undefined 가 들어간다
```

즉 지금 map-tool 파일을 가져오면 `sourceUid` 가 전부 `undefined` 라, §2-1 에서
막으려던 **중복 생성이 그대로 일어난다.** 저장 쪽이 뚫려 있어서 아직 안 드러났을 뿐이다.

고치려면 map-tool 이 비콘·랜드마크를 만들 때 `uid` 를 부여하고 저장에 실어야 한다.
map-tool 은 실측용이고 WEB-FE 는 건드리지 않기로 해서 보류한다.

### 8-2. `toDesignCoords` 가 `workW` 대신 `origW` 를 쓴다

```typescript
// WEB-FE/src/lib/mapImport.ts:52
export function toDesignCoords(x, y, origW) {
  const scale = MAP_DESIGN_W / origW
```

map-tool 의 비콘 좌표는 **작업 픽셀**이지 원본 픽셀이 아니다.

```javascript
// map_inspection.html:643 — 노드와 비콘 거리를 작업픽셀 단위로 잰다
const d = Math.hypot(node.x-b.x, node.y-b.y) * mPerWorkPx;
```

`workScale = 1` 일 때만 두 값이 같으므로 지금은 맞아 떨어진다.

```
MAX_DIM = 3200
현재 평면도 2372 × 1790  →  workScale = 1
```

**평면도가 3200px 를 넘는 순간 좌표가 통째로 어긋난다.** 예를 들어 4000px 이미지면
`workScale = 0.8` 이라 20% 밀린다. 올바른 식은 `MAP_DESIGN_W / workW` 다.

WEB-FE 를 건드리지 않기로 해서 보류한다. 큰 평면도를 쓰기 전에 고쳐야 한다.

### 8-3. 관리자웹은 아직 백엔드에 안 붙어 있다

`npm run dev` 는 `import.meta.env.DEV` 가 항상 참이라 MSW 가 무조건 켜진다.
`/api/*` 를 전부 가로채므로 **백엔드로 요청이 한 건도 안 나간다.**

```typescript
// WEB-FE/src/main.tsx:8
if (!import.meta.env.DEV) return
await worker.start({ onUnhandledRequest: 'bypass' })
```

붙이려면 WEB-FE 두 파일을 고쳐야 한다(각 한 군데). 적용하지 않고 수정안만 적어뒀다 —
`docs/WEBFE_백엔드_연결_수정안.md`.

### 8-4. 첫 관리자를 만들 수단이 REST 에 없다

```
signup  →  status="pending" 으로 생성
login   →  status != "active" 면 403
승인     →  이미 로그인한 super_admin 이 필요
```

닭과 달걀이다. 지금은 `tests/seed_from_project.py` 가 첫 계정만 DB 에 직접 넣어
때운다. **운영에는 이대로 못 간다** — 설치 스크립트나 환경변수 부트스트랩이 필요하다.

### 8-5. 벽 마스크를 남길지 — 결정 대기

§7 에서 본 대로 벽 마스크는 map-tool 파일 모드에서만 살아남고, 관리자웹과 DB 모드에서는
새로고침하면 사라진다. 남기려면 세 갈래가 있다.

| | 하는 일 | 대가 |
|---|---|---|
| **그대로** | 지금처럼 휘발 | 이동영역을 다시 손볼 때마다 벽을 새로 긋는다 |
| **localStorage** | 브라우저에 `wallMask:{floorId}` 로 | 서버·DB·WEB-FE 를 안 건드린다. 다른 PC 에서는 안 보인다 |
| **DB 저장** | `floor_masks.wall_data_url` 추가 | DB 재구축 + `/map-db` 읽기 전용 원칙이 깨짐 + 관리자웹도 같이 고쳐야 의미가 생김 |

**DB 저장은 관리자웹까지 같이 고치지 않으면 의미가 없다.** 채워 넣는 곳이 없어서
컬럼이 영원히 비어 있게 된다.

판단 기준은 "이동영역을 얼마나 자주 다시 손보느냐" 다. 한 번 칠하고 끝이면 굳이
늘릴 이유가 없고, 층마다 여러 번 손볼 거면 localStorage 정도가 값싸다.

### 8-6. 경로노드 튜닝값이 DB 에 없다 — 결정 대기

관리자웹 경로노드 화면에서 두 값을 바꿀 수 있는데 **저장되지 않는다.**

| | 기본값 | 뜻 |
|---|---|---|
| 횡단 가능 거리 | 12m | 이보다 넓으면 건너기를 만들지 않는다 |
| 건너기 페널티 | 5m | 이만큼 이상 절약될 때만 건넌다 |

지금은 서버도 같은 기본값을 박아 쓴다. 그래서 **관리자가 화면에서 값을 바꿔
검수해도 서버는 기본값으로 안내한다** — 화면과 안내가 갈라지는데 아무 표시가 없다.

`floors` 에 컬럼 두 개를 두면 해결되지만 DB 재구축이 필요하고, 관리자웹도 저장
호출을 붙여야 의미가 생긴다(§8-5 와 같은 구조의 문제다).

기본값으로 충분한지부터 실측으로 보는 편이 낫다. 값을 바꿀 일이 없으면 컬럼도
필요 없다.

---

## 9. 다음 작업 — `/monitor` 지도를 서버 그래프로 재구성 (결정됨, 미착수)

두 그래프를 나란히 놓고 보니 **눈에 띄게 달랐다.** map-tool 이 만든 것과 관리자웹이
만든 것이 노드 위치·개수부터 다르다. 서버는 이제 관리자웹 쪽을 정확히 재현하므로
(§7-2, 좌표 오차 0) `/monitor` 가 map-tool 그림을 보여줄 이유가 없다.

**map-tool 을 버리고 `/monitor` 의 지도를 서버가 주는 그래프로 다시 만든다.**

### 버려도 되는 근거

`/monitor` 가 지도한테 받는 것은 **하나뿐이다.**

```javascript
// monitor.html:748  __monitorBridge.receive
if (msg.event === 'beaconSequence' && Array.isArray(msg.beacons)) {
  applyMapSequence(msg.beacons);       // ← 추적 경로로 등록
}
```

반대로 지도에 보내는 것은 `beaconList`(지금 잡히는 이름)와 `currentBeacon`
(서버가 판단한 현재 위치) 둘. 나머지 3,200줄은 전부 **편집 기능**이고
(마스크 칠하기·노드 생성·비콘 배치·BLE 이름), 그건 이제 관리자웹이 한다.

그리고 그 비콘 순서는 서버가 이미 만들 수 있다(`build_route`).
그래서 지도는 **읽기 전용 그림 한 장**이면 된다.

### 할 일

| | |
|---|---|
| 새 엔드포인트 | `GET /map-db/floors/{id}/graph` — 노드·연결 (900 좌표) |
| 〃 | `GET /map-db/floors/{id}/route?from=&to=` — 비콘 순서 |
| `monitor.html` | 캔버스 한 장: 평면도 + 이동영역 + 비콘 + 노드/연결 + 경로 + 현재 위치 |
| 지울 것 | `_extract_map_tool_parts()`, map-tool 주입 |
| `/map`·`/map-static` | **필요한 것만 남긴다** |

3,200줄 → 300줄쯤 된다. 무엇보다 **화면에 뜨는 그래프가 곧 안내에 쓰는 그래프**가
되어, 지금처럼 둘이 갈라질 수가 없어진다.

### 경로를 고르는 방식 — 실제 흐름을 그대로 재현한다

단순히 "고르기 편한" 쪽이 아니라 **사용자 앱이 실제로 하는 것과 같은 모양**으로 만든다.

```
출발지    목록에서 고른다          ← 실제로는 폰이 올리는 비콘에서 정해지는 값
목적지    STT 텍스트로 들어온다     ← 텍스트가 오면 자동으로 선택된다
```

목적지를 드롭다운으로 고르게 하면 **정작 확인해야 할 구간을 건너뛴다.**
말한 문장이 목적지에 제대로 붙는지가 검증 대상인데, 목록에서 고르면 그 단계가
없어진다. 그래서 STT 로 들어온 텍스트가 목적지로 이어지는 것까지 화면에서 보여야 한다.

즉 `/monitor` 에서 확인하는 것은 이 사슬 전체다.

```
STT 텍스트 → LLM 매칭 → 목적지 확정 → 경로 생성 → 비콘 순서 → 추적 판정
```

지금은 이 사슬의 **가운데가 끊겨 있다** — 목적지가 정해져도 경로를 만들지 않는다
(`handler.py` 가 `route_engine` 을 import 조차 하지 않는다). 그 배선이 이 작업의
본체이고, 지도는 그 결과를 눈으로 보는 창일 뿐이다.

---

## 10. 아직 안 한 것

| 항목 | 상태 |
|---|---|
| `buildings` 에 UUID | 건물 자동 판별용. 결정 대기 (`API_목적지_되묻기_WS.md` §10-A) |
| 프론트 미구현 화면 | 회원가입 폼, 가입 승인 관리, 설정, 계정, 설치 가이드 |
| `visual_tag_id` | 컬럼만 있고 항상 null — 시각태그 기능 미구현 |
