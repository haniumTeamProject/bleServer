"""`/monitor` 의 지도가 DB 를 읽을 수 있게 해 주는 피드.

── 왜 `/api/...` 가 아닌가 ────────────────────────────────────────

`/api/*` 는 관리자웹(WEB-FE)의 계약이고, `tests/test_webfe_contract.py` 가 그
목록을 프론트의 mock 과 대조한다. 여기 끼면 "프론트가 안 부르는 API" 로 잡혀서
매번 잡음이 된다.

성격도 다르다. 이건 실측 도구가 쓰는 **읽기 전용 피드**라, 기존 `/map`,
`/map-static` 과 같은 자리에 두는 게 맞다.

── 인증이 없다 ────────────────────────────────────────────────────

`/monitor` 자체가 로그인 없이 열리고, 이미 있는 `/map-static/{파일}` 이
평면도와 비콘 배치가 통째로 든 프로젝트 JSON 을 그냥 내주고 있다. 그래서 이
엔드포인트가 새로 뭘 노출하는 건 아니다.

**다만 지금까지는 static 폴더에 둔 파일 하나만 나갔는데, 이제 DB 에 있는 모든
건물·층이 로그인 없이 읽힌다.** 실측 단계에서는 문제가 안 되지만, 서버를 밖으로
열어둔 채로 두면 안 된다. 운영에 올릴 때는 이 라우터를 빼거나 인증을 붙여야 한다.

── 쓰기는 없다 ────────────────────────────────────────────────────

GET 뿐이다. DB 로 쓰는 경로는 관리자웹 하나로 유지한다.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.nav.db_map_source import floor_choices, map_project
from app.nav.map_source import MapDataError

router = APIRouter(prefix="/map-db", tags=["map-db"])


@router.get("/floors")
def list_map_floors(db: Session = Depends(get_db)):
    """건물/층 선택 상자에 채울 목록."""
    return {"floors": floor_choices(db)}


@router.get("/floors/{floor_id}/project")
def get_map_project(floor_id: str, db: Session = Depends(get_db)):
    """한 층을 map-tool 이 읽는 프로젝트 모양으로 내려준다."""
    try:
        return map_project(db, floor_id)
    except MapDataError as e:
        # 원인을 그대로 보여준다. 실측 현장에서 "안 열린다"만 보면 손을 못 댄다.
        return JSONResponse({"error": str(e)}, status_code=404)
