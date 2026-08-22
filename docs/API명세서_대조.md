# API 명세서와 실제 구현의 차이

팀 API 명세서(v3.0 계열)와 지금 돌아가는 `bleServer` + `WEB-FE` 를 한 줄씩 맞춰본 기록이다.

**명세서에 맞추려고 코드를 고치지 않았다.** 어느 쪽이 맞는지는 따로 정할 일이고, 여기서는
무엇이 다른지만 남긴다. 다만 판단에 도움이 될 만한 사실 하나는 적어둔다 —
**`WEB-FE` 와 `bleServer` 는 서로 정확히 일치한다.** 차이가 나는 항목은 전부 "명세서 vs
(관리자웹 + 서버)" 구도이지, 프런트와 백엔드가 어긋난 것이 아니다.

대조 시점: 2026-08-22

---

## 0. 한눈에

| 구분 | 건수 |
| --- | --- |
| 명세서대로 구현됨 | 26개 엔드포인트 |
| 명세서에 없는데 구현된 것 | 6개 엔드포인트 |
| 명세서에 있는데 없는 것 | 0개 |
| 필드 이름·값이 다른 것 | 5곳 |
| 명세서가 "없다"고 적었지만 지금은 있는 것 | 2곳 |

없는 API 는 없다. 차이는 전부 **명세서 이후에 추가되거나 이름이 바뀐 것**이다.

---

## 1. 명세서에 없는데 구현되어 있는 것

### 1.1 경로노드 — `/api/floors/{floorId}/path-nodes`

| 유형 | URL | 인증 |
| --- | --- | --- |
| GET | `/api/floors/{floorId}/path-nodes` | 필요 |
| PUT | `/api/floors/{floorId}/path-nodes` | 필요 |

명세서에 항목 자체가 없다. 3장에서 따로 설명한다.

관리자웹도 이미 붙어 있다.

```
WEB-FE/src/features/mapEditor/api.ts     fetchPathNodes / savePathNodes
WEB-FE/src/features/mapEditor/hooks.ts   usePathNodes / useSavePathNodes
        ↓
pages/pathnodes/PathNodePage.tsx:68      불러오기
pages/pathnodes/PathNodePage.tsx:858     저장
pages/overview/FloorOverviewPage.tsx:65  층 개요에서 다시 그리기
```

서버는 `app/pathnode/{router,service,schemas,models}.py` 이고 `main.py:53` 에서 물려 있다.

### 1.2 축척 — `/api/floors/{floorId}/scale`

| 유형 | URL | 인증 |
| --- | --- | --- |
| GET | `/api/floors/{floorId}/scale` | 필요 |
| PUT | `/api/floors/{floorId}/scale` | 필요 |

`{ scaleMPerPx }`. 도면 1px 이 실제 몇 m 인지.

명세서는 마스크 항목에 이렇게 적어놨다.

> **v3.0 설계서와 차이**: `scale_m_per_px` 필드 없음. 위치판정에 실측 축척이 필요하면
> 이 필드부터 추가해야 함

**이미 추가됐다.** 다만 **마스크가 아니라 `floors` 테이블에** 붙었다. 층 하나에 축척
하나이고 마스크를 다시 그려도 축척은 그대로여야 하기 때문이다.

경로 안내가 이 값 없이는 아예 안 돈다. 없으면 `db_map_source.py` 가 이렇게 끊는다.

```
축척이 없어 경로 그래프를 만들 수 없습니다.
관리자웹의 지도 검수 화면에서 축척을 먼저 정해주세요.
```

### 1.3 연결자 층별 좌표 — `positions`

| 유형 | URL |
| --- | --- |
| PUT | `/api/buildings/{buildingId}/connectors/{connectorId}/positions/{floorId}` |
| DELETE | `/api/buildings/{buildingId}/connectors/{connectorId}/positions/{floorId}` |

명세서의 연결자 응답은 `{ name, type, floors }` 뿐이다. 실제로는 `positions` 가 더 있다.

```json
{ "id": "...", "buildingId": "...", "name": "계단1", "type": "stairs",
  "floors": [1, 2, 3, 4],
  "positions": [ { "floorId": "f-4", "x": 412.0, "y": 88.5 } ] }
```

`floors` 는 "몇 층을 운행하는가"이고 `positions` 는 "그 층 도면 어디에 있는가"다. 둘이
따로 있어야 하는 이유는 **운행층인데 좌표가 안 찍힌 상태**를 표현해야 하기 때문이다.
층 상태 `connector_missing` 이 정확히 그 경우다.

---

## 2. 필드가 다른 것

### 2.1 비콘 `type` — 값이 완전히 다르다

| | 값 |
| --- | --- |
| 명세서 | `anchor` / `checkpoint` / `connector` |
| 실제 | `semantic` / `reinforcement` |

```
semantic        앵커·코너·연결자 입구·목적지 출입구·복도 끝 — 경로상 의미 있는 지점
reinforcement   의미비콘 사이가 D_max(6m)를 넘을 때 채워 넣는 비콘
```

분류 기준 자체가 바뀌었다. 명세서 쪽은 "이 비콘이 무엇을 가리키는가"이고, 지금은
"왜 여기 세웠는가"다.

딸린 차이도 있다.

- `isAnchor`(`type === "anchor"` 파생값) **없음.** 파생 근거인 `anchor` 가 없어졌다
- `connectorId` **없음.** 연결자와 비콘을 잇지 않는다
- 명세서가 "`type` 은 등록 폼에만 쓰이고 위치판정에서는 안 본다"고 한 것은 **지금도 맞다.**
  판정은 `major`/`minor` 로만 한다

### 2.2 비콘에 `mac` 이 있다

명세서 요청 필드에 없다. 실제로는 `mac`(BLE MAC 주소)이 요청·응답 양쪽에 있다.

판정에는 안 쓴다. **건물을 가리는 데만 쓴다** — A동 4층과 B동 4층이 둘 다 `major=104`
라 major/minor 만으로는 건물이 안 갈린다. `navigation_ws.building_from_mac()` 참고.

### 2.3 비콘·랜드마크에 `sourceUid` / `sourceLabel` 이 있다

명세서에 없다. map-tool(iframe)에서 가져온 원본 id 와 표시 라벨이다. 다시 가져올 때
같은 지점을 알아보고 덮어쓰기 위한 매칭 키다.

### 2.4 랜드마크 `type` → `category`, 값도 자유 입력

| | |
| --- | --- |
| 명세서 | `type`, 값은 `room`/`restroom`/`facility`/`entrance` 넷 중 하나 |
| 실제 | `category`, **고정 목록 아님** — 관리자웹이 기본 목록을 보여주되 직접 입력 가능 |

이름과 성격이 둘 다 바뀌었다. `visualTagId` 는 명세서대로 있고, 명세서가 적은 대로
**아직 항상 null** 이다(태그 연결 기능 미구현).

### 2.5 층 `status` 에 `scale_missing` 이 하나 더 있다

| | 단계 |
| --- | --- |
| 명세서 | `floorplan_missing → review_needed → beacon_missing → connector_missing → ready` |
| 실제 | `floorplan_missing → review_needed → **scale_missing** → beacon_missing → connector_missing → ready` |

축척이 생기면서 단계도 하나 늘었다. 판정은 `app/status.py:floor_status()` 에 있고,
관리자웹 `handlers.ts:computeFloorStatus` 와 **순서가 같아야 한다**(주석에 적어둠).

`FloorResponse` 에 `scaleMPerPx` 도 같이 실려 나간다 — 층 목록만 받아도 축척을 알 수 있다.

---

## 3. 경로노드는 어떻게 분류되고 저장되는가

명세서에 없는 부분이라 여기 정리한다.

### 3.1 층 하나 = 행 하나

```
floor_path_nodes
├ floor_id   PK. 층당 그래프 하나라 별도 id 를 두지 않았다
├ mask_w     저장 당시 마스크 픽셀 크기
├ mask_h
├ nodes      JSON 배열
└ edges      JSON 배열
```

**종류별로 나눠 저장하지 않는다.** 관리자웹이 애초에 노드·엣지를 하나의 그래프로
다루기 때문이다 — 드래그로 옮기거나 지울 때도 종류를 구분하지 않고 같은 배열을 갱신한다.
쪼개면 조회할 때 여러 번 합쳐야 하고, 노드 하나가 사라지면 그와 연결된 엣지도 같이
정리해야 하는 일관성 문제가 생긴다.

`mask_w`/`mask_h` 를 같이 저장하는 이유는 **좌표가 마스크 픽셀 기준**이라서다. 서버가
읽을 때 900(설계도 기준) 좌표로 환산한다.

```python
to_design = DESIGN_W / mask_w          # db_map_source.py
x = n["x"] * to_design
```

### 3.2 노드 네 종류

```ts
type NodeKind = 'corner' | 'connector' | 'landmark' | 'facing'
```

| 종류 | 무엇 | 어디서 나오나 |
| --- | --- | --- |
| `corner` | 이동영역 경계가 꺾이는 지점 | 마스크 외곽선을 단순화한 꼭짓점 |
| `connector` | 계단·엘리베이터 입구 | 연결자 좌표를 벽선에 스냅 |
| `landmark` | 목적지 출입구 | 랜드마크 좌표를 벽선에 스냅 |
| `facing` | 위 지점들의 **맞은편 벽** | 상하좌우로 쏘아 반대편 벽에 맞은 자리 |

`corner` 만 `concave` 를 갖는다.

```ts
const concave = (x - px) * (ny - y) - (y - py) * (nx - x) < 0
```

이웃 두 점과의 외적 부호다. `concave === true` 면 **벽 끝**이다 — 복도가 거기서 끊긴다는
뜻이라, 건너기 후보를 찾는 것도 이 지점에서만 한다.

`facing` 만 `pairKind`(`connector` | `landmark`)를 갖는다. 맞은편이 어느 종류의 입구냐다.
코너의 맞은편으로도 쓰이는 지점이면 **`pairKind` 를 지운다** — 특정 입구 하나의 것이라고
말할 수 없어서다.

노드 id 는 `N01`, `N02`… 순번이다. 의미가 없다.

### 3.3 엣지 두 종류

```ts
type EdgeKind = 'wall' | 'cross'
```

| 종류 | 무엇 |
| --- | --- |
| `wall` | 벽을 짚고 따라 걷는 구간 |
| `cross` | 벽에서 손을 떼고 건너는 구간 |

**`cross` 는 단방향이다.**

```ts
edges.push({ a: a.id, b: b.id, type, directed: type === 'cross' ? true : undefined })
```

`a`(입구 또는 벽 끝) → `b`(맞은편) 으로만 갈 수 있다. 맞은편 지점은 벽에서 떨어진
허공이라 거기서 출발할 수가 없기 때문이다. `b` 가 자기 나름의 자격(벽 끝이거나 입구)을
갖췄다면 그건 별도의 엣지로 따로 생긴다.

서버도 같다.

```python
directed=bool(e.get("directed", e["type"] == "cross"))
```

옛 JSON 에 `directed` 가 없으면 건너기 여부로 정한다. 관리자웹이 `cross` 를 항상
단방향으로 만들기 때문에 이 기본값이 안전하다.

### 3.4 거리는 저장하지 않는다

엣지에 길이가 없다. 서버가 읽을 때 좌표와 축척으로 매번 계산한다.

```python
dist_m = math.hypot(a["x"] - b["x"], a["y"] - b["y"]) * scale
```

축척을 고치면 저장된 그래프를 건드리지 않아도 거리가 따라 바뀐다. 저장해두면 축척을
고칠 때마다 전 층 그래프를 다시 써야 한다.

### 3.5 노드 사이의 비콘도 저장하지 않는다

노드는 코너·입구에만 있어서 **간격이 넓다.** 긴 복도는 양 끝 두 점뿐이라 그 사이에
늘어선 비콘이 통째로 빠진다.

그래프에 노드를 더 넣으면 관리자웹이 보여주는 것과 갈라지므로, 노드는 그대로 두고
**경로를 계산할 때 선을 1m 간격으로 훑는다**(`route_engine._walk`). 그 표본은 저장되지
않는다. 자세한 것은 `경로안내_생성과_진행판정.md` §1.2.

---

## 4. 인증

명세서대로 전부 인증이 필요하다. 라우터 단위로 걸려 있다.

```python
dependencies=[Depends(get_current_admin)]
```

명세서에 없는 관리자 계정 API 도 있다.

```
POST   /api/admin/auth/login
GET    /api/admin/accounts        승인 대기 목록 등
PATCH  /api/admin/accounts/{id}
GET    /api/admin/me
PATCH  /api/admin/me
```

`/map-db/*`(관리·모니터용)와 `/ws`, `/ws/navigation` 은 인증이 없다. `/ws` 가 열려 있는
것은 기존 Java `WebSocketConfig.setAllowedOrigins("*")` 와 맞춘 것이고, 실측 도구
수준의 선택이다.

---

## 5. 그래서 명세서를 고쳐야 하나

고칠 거리만 정리한다. 결정은 팀에서.

**명세서에 추가해야 할 것**

- 경로노드 GET/PUT
- 축척 GET/PUT, `FloorResponse.scaleMPerPx`
- 연결자 `positions` PUT/DELETE 와 응답 필드
- 관리자 계정 5개
- 층 상태에 `scale_missing`

**명세서 내용을 바꿔야 할 것**

- 비콘 `type`: `anchor|checkpoint|connector` → `semantic|reinforcement`
- 비콘에 `mac` 추가, `isAnchor`·`connectorId` 삭제
- 랜드마크 `type` → `category`, 고정 목록 아님
- 비콘·랜드마크에 `sourceUid`/`sourceLabel` 추가

**명세서의 "미구현" 메모 중 이미 된 것**

- 축척(`scale_m_per_px`) — 됐다. 위치는 마스크가 아니라 `floors`

**명세서 메모가 아직 맞는 것**

- `visualTagId` 는 여전히 항상 null
- 비콘 `type` 은 여전히 위치판정에서 참조 안 함
- `rssiThreshold`/`txPower` 필드 없음 — 판정 임계값은 DB 가 아니라 `/monitor` 튜닝값으로 다룬다
