# backend-python

한이음 프로젝트 6팀 — 시각장애인을 위한 BLE 비콘 기반 실내 음성 길 안내 시스템의 백엔드, **FastAPI + PostgreSQL 버전**.

기존 `default_server`(Spring Boot) 백엔드를 동일한 API 계약(경로, 요청/응답 형태)으로 이식한 프로젝트. 프론트(`WEB-FE`) 코드는 수정 없이 그대로 붙여 쓸 수 있음.

## 구조 (feature-by-package)

```
app/
  building/  floor/  connector/  floorplan/  mask/  beacon/  landmark/  admin/
    models.py    # SQLAlchemy 모델
    schemas.py   # Pydantic 요청/응답 스키마 (CamelModel 상속 — 아래 참고)
    service.py   # 비즈니스 로직
    router.py    # APIRouter, 엔드포인트
  security/
    password.py  # bcrypt 해싱
    jwt.py       # 토큰 발급/검증
    deps.py      # get_current_admin, require_super_admin (Depends 기반 인증)
  ws/
    rssi_filter.py  # 중앙값+칼만 필터, 히스테리시스 (Java RssiFilterPipeline 그대로 이식)
    handler.py      # /ws 웹소켓 핸들러
  common.py      # CamelModel 정의
  config.py      # 환경변수 (pydantic-settings)
  database.py    # SQLAlchemy engine/session
  main.py        # FastAPI 앱, 라우터 등록
```

### camelCase 응답

Java는 필드가 원래 camelCase라 그대로 JSON이 됐지만, 파이썬은 관례상 snake_case. 그래서 `common.py`의 `CamelModel`을 모든 스키마가 상속:

```python
class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)
```

`floor_count` 필드로 작성해도 실제 요청/응답 JSON은 `floorCount`로 나감. 프론트 계약과 동일.

## API

Java 백엔드와 경로·요청/응답 형태 동일 (33개 라우트, `/docs`·`/ws` 포함). 도메인별 CRUD + 인증:

| 도메인 | 엔드포인트 |
|---|---|
| 건물 | `GET/POST /api/buildings`, `GET/PATCH/DELETE /api/buildings/{id}` |
| 층 | `GET/POST /api/buildings/{buildingId}/floors`, `DELETE .../{floorId}` |
| 연결자 | `GET/POST /api/buildings/{buildingId}/connectors`, `DELETE .../{connectorId}` |
| 설계도 | `GET/PUT/DELETE /api/floors/{floorId}/floorplan` |
| 이동영역 마스크 | `GET/PUT /api/floors/{floorId}/mask` |
| 비콘 | `GET/POST /api/floors/{floorId}/beacons`, `PATCH/DELETE .../{beaconId}` |
| 랜드마크 | `GET/POST /api/floors/{floorId}/landmarks`, `PATCH/DELETE .../{landmarkId}` |
| 인증 | `POST /api/admin/auth/signup`, `POST /api/admin/auth/login` |
| 가입 승인 | `GET/PATCH /api/admin/accounts` (super_admin 전용) |

층 상태(`floorplan_missing → review_needed → beacon_missing → ready`)는 `floor/service.py`의 `bump_status()`가 관리, floorplan/mask/beacon 서비스가 작업 완료 시점마다 호출 — Java `FloorService.bumpStatus()`와 동일 로직.

인증은 Spring Security의 필터 체인 대신 FastAPI `Depends()`로 라우터마다 명시: `auth_router`(로그인/회원가입)만 인증 없음, 나머지는 전부 `get_current_admin` 필요, `/api/admin/accounts`는 `require_super_admin` 추가.

## 실행

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env  # DATABASE_URL, JWT_SECRET 채우기

uvicorn app.main:app --reload
```

PostgreSQL 준비 (DB만 미리 만들면 테이블은 첫 실행 시 자동 생성 — Java의 `ddl-auto=update`와 동일한 방식):

```bash
createdb wayfinder
# 또는: psql -c "CREATE DATABASE wayfinder;"
```

Swagger 문서: `http://localhost:8000/docs`

## Java 버전과 다른 점

- DB: H2(파일) → PostgreSQL
- `Connector.floors`: Java는 `@ElementCollection`(별도 조인 테이블) → 여기선 PostgreSQL `ARRAY(Integer)` 컬럼 하나로 처리 (SQLite는 이 타입 미지원이라 테스트 시 주의)
- 필드명은 프론트 계약과 무관한 내부 구현 디테일만 자유롭게 정리, `mac`/`dataUrl` 등 프론트가 실제로 쓰는 필드명은 그대로 유지

## 알려진 한계 (Java 원본에서 그대로 가져온 것)

`ws/handler.py`의 `_filters` 딕셔너리가 모든 웹소켓 연결에서 공유됨 — 여러 명이 동시 접속하면 같은 비콘 키의 필터 상태가 섞일 수 있음. Java 버전에도 있던 이슈라 동작 일치를 위해 그대로 이식, 고치지 않음.
