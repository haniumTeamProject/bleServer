from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin.router import accounts_router, auth_router
from app.beacon.router import router as beacon_router
from app.building.router import router as building_router
from app.config import settings
from app.connector.router import router as connector_router
from app.database import Base, engine
from app.floor.router import router as floor_router
from app.floorplan.router import router as floorplan_router
from app.landmark.router import router as landmark_router
from app.mask.router import router as mask_router
from app.ws.handler import router as ws_router

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


app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(building_router)
app.include_router(floor_router)
app.include_router(connector_router)
app.include_router(floorplan_router)
app.include_router(mask_router)
app.include_router(beacon_router)
app.include_router(landmark_router)
app.include_router(ws_router)  # /ws — 인증 없음, 기존 Java WebSocketConfig의 setAllowedOrigins("*")와 동일하게 오픈
