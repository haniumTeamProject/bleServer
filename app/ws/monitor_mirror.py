"""폰이 `/ws/navigation` 으로 올린 것을 `/monitor` 가 볼 수 있게 옮긴다.

── 왜 필요한가 ────────────────────────────────────────────────────

`/ws` 와 `/ws/navigation` 은 상태를 하나도 공유하지 않는다. 일부러 그렇게 나눴다 —
`/ws` 는 붙어 있는 전부에게 뿌리는 전역 하나짜리이고, `/ws/navigation` 은 연결마다
필터·추적기를 따로 둔다. 폰 두 대가 서로의 경로를 집어가는 일을 막으려면 그래야 한다.

그런데 앱을 `/ws/navigation` 으로 옮기고 나니 **`/monitor` 가 아무것도 못 보게
됐다.** RSSI 그래프도, 서버 판정도, 경로도 전부 `/ws` 로 오는 것만 그리기 때문이다.
현장에서 폰을 들고 걸어다니며 확인해야 하는 일인데 화면이 통째로 비어버린 것이다.

그래서 **한 방향으로만** 옮긴다. 나브 세션이 만든 값을 `/ws` 모양으로 바꿔
브로드캐스트한다. 반대 방향은 없다 — 모니터에서 누른 것이 폰의 안내를 건드리면
안 된다.

── 한계 ───────────────────────────────────────────────────────────

폰이 여러 대면 마지막에 올린 것이 화면을 덮어쓴다. `_filters`·`_tracker` 가 전역
하나인 것과 같은 성격의 한계이고, 실측 도구라 그대로 둔다.

── 키를 왜 바꿔 보내는가 ──────────────────────────────────────────

    /ws              "MAC|이름"      ← 옛 앱이 JSON 키로 그대로 올리던 것
    /ws/navigation   "major-minor"   ← 아직 안 잡힌 비콘도 경로에 세우려고

`/monitor` 는 `shortName(key)` = `|` 뒤쪽 을 비콘 이름으로 보고 지도의 비콘과
맞춘다. 그래서 `"101-1"` 을 그대로 보내면 지도에서 현재 위치가 영영 안 뜬다.
여기서 DB 이름을 붙여 `"101-1|ESP32-B1"` 로 만들어 보내면 모니터 쪽은 한 줄도
안 고쳐도 된다.
"""

from __future__ import annotations

import json
import time

# 비콘 이름은 관리자웹에서 바꿀 수 있으므로 캐시하되 오래 들고 있지 않는다.
_NAME_TTL_S = 30.0
_names: dict[str, tuple[float, dict[str, str]]] = {}


def _names_for(floor_id: str | None) -> dict[str, str]:
    """`{"101-1": "ESP32-B1", ...}` — 추적 키에서 광고 이름으로."""
    if not floor_id:
        return {}
    hit = _names.get(floor_id)
    now = time.time()
    if hit and now - hit[0] < _NAME_TTL_S:
        return hit[1]
    table: dict[str, str] = {}
    try:
        from app.beacon.models import Beacon
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            for bc in db.query(Beacon).filter(Beacon.floor_id == floor_id).all():
                if bc.minor is None:
                    continue
                key = f"{bc.major if bc.major is not None else '?'}-{bc.minor}"
                if bc.name:
                    table[key] = bc.name
        finally:
            db.close()
    except Exception as e:
        print(f"[mirror] 비콘 이름 조회 실패: {e}")
        return hit[1] if hit else {}
    _names[floor_id] = (now, table)
    return table


def display_key(floor_id: str | None, key: str) -> str:
    """추적 키에 이름을 붙인다. 이름을 모르면 그대로 둔다(그래도 그래프에는 뜬다)."""
    name = _names_for(floor_id).get(key)
    return f"{key}|{name}" if name else key


# ---------------------------------------------------------------------------
# RSSI · 판정
# ---------------------------------------------------------------------------
def beacon_payload(session, samples: list[tuple[str, float, float]]) -> dict | None:
    """`/ws` 가 중계하는 것과 같은 모양을 만든다.

    `samples` 는 `(추적 키, 원본 rssi, 필터값)`. `/monitor` 는 `key` 와 `key__f`
    한 쌍으로 원본과 칼만값을 겹쳐 그린다.
    """
    if not samples:
        return None

    floor_id = getattr(session, "floor_id", None)
    payload: dict = {"timestamp": int(time.time() * 1000)}
    for key, raw, filtered in samples:
        dk = display_key(floor_id, key)
        payload[dk] = raw
        payload[f"{dk}__f"] = round(filtered, 1)

    # 판정 결과. 여기 실린 키도 같은 규칙으로 바꿔야 지도의 현재 위치가 맞는다.
    snap = session.tracker.snapshot()
    if snap:
        snap = dict(snap)
        for field in ("prev", "current", "next"):
            if snap.get(field):
                snap[field] = display_key(floor_id, snap[field])
        payload["_track"] = snap

    # 지금 폰이 있는 층. 모니터가 이걸 보고 지도를 그 층으로 옮긴다.
    if floor_id:
        payload["_floorId"] = floor_id

    # 경로가 바뀐 순간을 알리는 번호. 경로 자체는 `/map-db/current-route` 로 간다.
    from app.ws import handler

    payload["_routeSeq"] = handler._route_seq
    return payload


async def publish(payload: dict | None) -> None:
    if not payload:
        return
    from app.ws.handler import _broadcast

    await _broadcast(json.dumps(payload, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 경로
# ---------------------------------------------------------------------------
def set_route(plan, landmark, heard: str = "") -> None:
    """폰이 만든 경로를 `/monitor` 가 가져갈 수 있게 서버에 둔다.

    `/ws` 쪽 `_attach_route` 가 하는 것과 같은 일이다. 같은 자리에 넣는 이유는
    `/map-db/current-route` 가 거기 하나만 보기 때문이다 — 둘로 나누면 모니터가
    어느 쪽을 봐야 할지 정해야 하고, 그 판단이 또 갈라진다.
    """
    from app.ws import handler

    handler._route_seq += 1
    handler._current_route = {
        "seq": handler._route_seq,
        "floorId": plan.floor_id,
        "from": plan.from_beacon,
        "destinationId": landmark.id,
        "destinationName": landmark.name,
        "heard": heard,
        "distanceM": round(plan.distance_m, 1),
        "seconds": plan.seconds,
        "crossings": plan.route.crossings,
        "nodeIds": plan.route.node_ids,
        "beacons": [s.beacon_id for s in plan.route.steps],
        "missing": plan.missing,
    }


def clear_route() -> None:
    """안내가 끝났다. **번호는 올린다** — 안 올리면 모니터가 지워진 걸 모른다."""
    from app.ws import handler

    handler._route_seq += 1
    handler._current_route = None
