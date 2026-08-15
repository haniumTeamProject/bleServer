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

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.nav.db_map_source import DbMapSource, floor_choices, map_project
from app.nav.map_source import MapDataError
from app.nav.route_engine import build_route, estimated_seconds

router = APIRouter(prefix="/map-db", tags=["map-db"])


@router.get("/floors")
def list_map_floors(db: Session = Depends(get_db)):
    """건물/층 선택 상자에 채울 목록."""
    return {"floors": floor_choices(db)}


@router.get("/floors/{floor_id}/project")
def get_map_project(floor_id: str, db: Session = Depends(get_db)):
    """한 층의 지도 데이터. `/monitor` 가 화면을 그리는 데 쓴다."""
    try:
        return map_project(db, floor_id)
    except MapDataError as e:
        # 원인을 그대로 보여준다. 실측 현장에서 "안 열린다"만 보면 손을 못 댄다.
        return JSONResponse({"error": str(e)}, status_code=404)


@router.get("/floors/{floor_id}/graph")
def get_map_graph(floor_id: str, db: Session = Depends(get_db)):
    """경로 노드와 연결. **서버가 안내에 실제로 쓰는 그래프 그대로다.**

    `/monitor` 가 이걸 그린다. 화면에 뜨는 것과 안내에 쓰는 것이 같은 값이어야
    둘이 갈라지는 일이 없다 — 예전에는 지도 도구가 자기 알고리즘으로 따로 만들어서
    관리자웹이 보여주는 그래프와 눈에 띄게 달랐다.

    좌표는 설계도(900) 기준이라 비콘·랜드마크와 같은 자리에 그리면 된다.
    """
    try:
        graph = DbMapSource(db).graph(floor_id)
    except MapDataError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return {
        "nodes": [
            {"id": n.id, "x": n.x, "y": n.y, "type": n.type, "concave": n.concave}
            for n in graph.nodes
        ],
        "edges": [
            {"a": e.a, "b": e.b, "type": e.type, "distM": round(e.dist_m, 3)}
            for e in graph.edges
        ],
    }


@router.get("/floors/{floor_id}/route")
def get_route(floor_id: str, to: str, from_: str | None = Query(None, alias="from"),
              db: Session = Depends(get_db)):
    """출발 비콘에서 목적지까지의 경로.

    `from` 을 안 주면 첫 비콘에서 출발한 것으로 친다(지도만 보고 확인할 때).
    실제 안내에서는 폰이 올린 RSSI 로 출발점을 정하므로 이 인자를 쓰지 않는다.
    """
    try:
        source = DbMapSource(db)
        beacons = source.beacons(floor_id)
        if not beacons:
            raise MapDataError("이 층에 등록된 비콘이 없습니다.")
        start = from_ or beacons[0].id
        route = build_route(source, floor_id, from_beacon_id=start, to_landmark_id=to)
    except MapDataError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return {
        "from": start,
        "to": to,
        "destination": route.destination.name,
        "distanceM": round(route.total_distance_m, 1),
        "seconds": estimated_seconds(route.total_distance_m),
        "crossings": route.crossings,
        "nodeIds": route.node_ids,
        "steps": [
            {"seq": s.seq, "beaconId": s.beacon_id, "nodeId": s.node_id,
             "turn": s.turn, "isArrival": s.is_arrival}
            for s in route.steps
        ],
    }
