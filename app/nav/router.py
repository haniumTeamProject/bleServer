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
from app.nav import cues
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


@router.get("/current-route")
def get_current_route():
    """지금 안내 중인 경로. 없으면 `null`.

    **폰이 말해서 정해진 경로도 여기로 나온다.** 목적지 응답은 물어본 폰에게만
    보내므로(되묻기 후보가 다른 연결로 새면 안 된다) `/monitor` 는 그 메시지를
    보지 못한다. 대신 서버가 들고 있는 값을 가져가 화면에 그린다.

    언제 가져갈지는 RSSI 중계에 실려 오는 `_track.routeSeq` 로 안다 —
    그 번호가 바뀌었을 때만 한 번 부르면 된다.
    """
    from app.ws.handler import _current_route      # 순환 import 를 피해 여기서

    return _current_route


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


@router.get("/floors/{floor_id}/cues")
def get_route_cues(floor_id: str, to: str, from_: str | None = Query(None, alias="from"),
                   db: Session = Depends(get_db)):
    """어느 비콘에서 무슨 안내가 나가는지. **배정 방식 두 가지를 나란히 준다.**

    안내 문구는 경로 노드가 정하고(코너·횡단·도착), 그것을 어느 비콘에서 말할지는
    배정 규칙이 정한다. 규칙이 두 가지라 실제 배치에서 어느 쪽이 나은지 눈으로
    비교해야 해서, 하나를 고르지 않고 둘 다 돌려준다.

        거리   사건보다 여유(회전 2m·횡단 4m)만큼 앞선 마지막 비콘
        소유   노드에서 가장 가까운 비콘의 한 칸 앞
        절충   한 칸 앞을 쓰되 10m 를 넘으면 거리 방식으로

    `orphan*` 은 그 방식으로 붙일 비콘을 못 찾은 사건이다. 비어 있어야 정상이고,
    남아 있으면 그 안내가 실제로는 안 나간다는 뜻이다.
    """
    try:
        source = DbMapSource(db)
        beacons = source.beacons(floor_id)
        if not beacons:
            raise MapDataError("이 층에 등록된 비콘이 없습니다.")
        start = from_ or beacons[0].id
        route = build_route(source, floor_id, from_beacon_id=start, to_landmark_id=to)
        graph = source.graph(floor_id)
        result = cues.build(graph, route.node_ids, beacons,
                            source.beacon_match_radius_m(floor_id),
                            source.meters_per_px(floor_id),
                            route.destination.name)
    except MapDataError as e:
        return JSONResponse({"error": str(e)}, status_code=404)

    def cue_json(c):
        return {"kind": c.kind, "nodeId": c.node_id, "distM": round(c.dist_m, 1),
                "direction": c.direction, "template": c.template, "text": c.text}

    return {
        "from": start,
        "destination": route.destination.name,
        "steps": [
            {"seq": s.seq, "beaconId": s.beacon_id, "distM": s.dist_m,
             "byDistance": [cue_json(c) for c in s.cues_by_distance],
             "byOwner": [cue_json(c) for c in s.cues_by_owner],
             "byHybrid": [cue_json(c) for c in s.cues_by_hybrid]}
            for s in result.steps
        ],
        "cues": [cue_json(c) for c in result.cues],
        "orphanDistance": [cue_json(c) for c in result.orphan_distance],
        "orphanOwner": [cue_json(c) for c in result.orphan_owner],
        "orphanHybrid": [cue_json(c) for c in result.orphan_hybrid],
    }
