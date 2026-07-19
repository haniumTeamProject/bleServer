# default_server 작업 내역

작성일: 2026-07-17

## 1. 프로젝트 구조

도메인(기능 단위)별로 패키지 분리, 그 안에서 다시 5계층 분리.

```
org.mcsmtp.wayfinder
├── building/     (entity, repository, dto, service, controller)
├── floor/        (entity, repository, dto, service, controller)
├── connector/    (entity, repository, dto, service, controller)
├── floorplan/    (entity, repository, dto, service, controller)
├── mask/         (entity, repository, dto, service, controller)
├── beacon/       (entity, repository, dto, service, controller)
├── landmark/     (entity, repository, dto, service, controller)
├── admin/        (entity, repository, dto, service, controller) — 계정/인증
├── security/     (JwtProvider, JwtAuthenticationFilter, SecurityConfig)
├── handler/      (WebSocketHandler — 실시간 RSSI 처리)
├── filter/       (RssiFilterPipeline)
└── config/       (WebSocketConfig 등)
```

5계층 역할:

- entity — DB 테이블 매핑
- repository — DB 조회/저장 (Spring Data JPA 자동 구현)
- dto — 요청/응답 데이터 모양
- service — 실제 로직
- controller — HTTP 요청 처리

흐름: Controller → Service → Repository → DB

도메인 대부분 독립적, `FloorService`만 예외. 층 세팅 상태(`floorplan_missing → review_needed → beacon_missing → ready`) 관리를 `FloorService.bumpStatus()`가 전담하고, `FloorplanService`·`FloorMaskService`·`BeaconService`가 작업 완료 시점마다 이걸 호출.

`admin`/`security`는 성격이 다름. `JwtAuthenticationFilter`가 모든 요청보다 먼저 실행되게 등록돼 있어서, 다른 도메인 컨트롤러엔 인증 코드 없어도 됨.

## 2. 완료된 기능

### API 목록

| 도메인 | 엔드포인트 |
|---|---|
| 인증 | `POST /api/admin/auth/signup`, `POST /api/admin/auth/login` |
| 가입 승인 | `GET /api/admin/accounts?status=`, `PATCH /api/admin/accounts/{id}/status` (super_admin 전용) |
| 건물 | `GET/POST /api/buildings`, `GET/PATCH/DELETE /api/buildings/{id}` |
| 층 | `GET/POST /api/buildings/{buildingId}/floors`, `DELETE .../{floorId}` |
| 연결자 | `GET/POST /api/buildings/{buildingId}/connectors`, `DELETE .../{connectorId}` |
| 설계도 | `GET/PUT/DELETE /api/floors/{floorId}/floorplan` |
| 이동영역 마스크 | `GET/PUT /api/floors/{floorId}/mask` |
| 비콘 | `GET/POST /api/floors/{floorId}/beacons`, `PATCH/DELETE .../{beaconId}` |
| 랜드마크 | `GET/POST /api/floors/{floorId}/landmarks`, `PATCH/DELETE .../{landmarkId}` |

URL은 프론트 `src/features/*/api.ts` 기준 (로그인/회원가입 빼고 `/admin` 접두어 없음).

### 인증

JWT 기반 무상태 방식. 로그인 성공 시 토큰 발급, 이후 요청은 `Authorization: Bearer` 헤더로 검증. 승인/거절 시 `Admin.approvedBy` / `approvedAt`에 승인자 id·처리 시각 자동 기록.

## 3. 기존 설계서(v3.0)와 다른 점

`App/API명세서.md v3.0` 대조 결과. 필드명이 아니라 저장/식별 방식 자체가 달라서 그대로 못 맞춘 부분들.

| 항목 | v3.0 설계서 | 현재 구현 | 차이 |
|---|---|---|---|
| 비콘 식별자 | `uuid` (BLE UUID) | `mac` (MAC 주소) | 식별 체계 자체가 다름 |
| Checkpoint 모델 | 별도 엔티티 존재 | 없음 | Beacon `type` 필드로만 구분 |
| Beacon-Checkpoint 연결 | `checkpoint_id` | 없음 | Checkpoint 모델 없어서 연결 불가 |
| 신호 임계값 | `rssi_threshold`, `tx_power` | 없음 | 위치판정용, 미구현 |
| 마스크 저장 방식 | `mask_url` (파일 경로) | `dataUrl` (이미지 base64 통째 저장) | 저장 전략 다름 |
| 마스크 축척 | `scale_m_per_px` | 없음 | 픽셀→미터 환산값 없음 |
| RssiScan 메시지 형식 | `{ beacons: [{minor, rssi}] }` | `{"MAC\|이름": rssi값}` (WebSocket) | 페이로드 구조 다름 |

추가로 URL `/admin` 접두어 여부가 팀 작업표랑 실제 프론트 코드가 다름 (`/api/admin/buildings` vs `/api/buildings`). 별도 팀 확인 중.
