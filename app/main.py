from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin.router import accounts_router, auth_router, me_router
from app.beacon.router import router as beacon_router
from app.building.router import router as building_router
from app.config import settings
from app.connector.router import router as connector_router
from app.database import Base, engine
from app.floor.router import router as floor_router
from app.floor.router import scale_router
from app.floorplan.router import router as floorplan_router
from app.landmark.router import router as landmark_router
from app.mask.router import router as mask_router
from app.nav.router import router as map_db_router
from app.pathnode.router import router as path_node_router
from app.ws import llm_matcher
from app.ws.handler import router as ws_router
from app.ws.navigation_ws import router as nav_ws_router

# 모델 import가 있어야 Base.metadata에 테이블이 등록됨 (각 router 모듈이 models를 import하므로 여기선 자동 포함)
app = FastAPI(title="wayfinder-python")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    # Java의 ddl-auto=update와 비슷하게, 없는 테이블을 자동 생성 (컬럼 변경은 자동 반영 안 됨)
    Base.metadata.create_all(bind=engine)

    # 목적지 해석용 LLM을 미리 깨워둔다. 안 하면 첫 사용자가 모델 적재를 기다리다
    # 타임아웃에 걸려 규칙 엔진으로 떨어진다. 백그라운드라 기동을 막지 않는다.
    llm_matcher.warmup()


app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(me_router)
app.include_router(building_router)
app.include_router(floor_router)
app.include_router(scale_router)   # /api/floors/{id}/scale
app.include_router(connector_router)
app.include_router(floorplan_router)
app.include_router(mask_router)
app.include_router(beacon_router)
app.include_router(landmark_router)
app.include_router(path_node_router)
# /map-db — 실측 도구(/monitor)의 지도가 DB 를 읽는 피드. 읽기 전용·인증 없음.
# /map-static(파일)과 같은 자리에 둔다. 자세한 근거는 app/nav/router.py 문서 참고.
app.include_router(map_db_router)
# /ws/navigation — 사용자앱 전용. 연결마다 필터·추적기를 따로 둔다.
# /ws 는 브로드캐스트라 성격이 반대라서 합치지 않는다(docs/사용자앱_API_명세.md).
app.include_router(nav_ws_router)
app.include_router(ws_router)  # /ws — 인증 없음, 기존 Java WebSocketConfig의 setAllowedOrigins("*")와 동일하게 오픈
