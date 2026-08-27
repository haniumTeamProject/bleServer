"""목적지가 다른 층일 때, 안내를 **한 층짜리 구간 여러 개**로 쪼갠다.

── 왜 층을 합치지 않나 ────────────────────────────────────────────

층 그래프를 하나로 이어붙여 계단을 엣지로 두는 방법도 있다. 하지만 그러면
거리가 뜻을 잃는다. 안내 문구가 "지금 할 일"과 "조금 뒤에 할 일"을 가르는 기준
(`cues.lead_phrase`)은 **바닥을 걷는 거리**다. 계단이 섞이면 그 안에 오르내리는
층이 들어가서, 몇 걸음이면 되는 것을 조금 뒤라 하고 그 반대도 된다.

그리고 신호가 실제로 끊긴다. 층을 옮기는 동안 출발 층 비콘은 죽고 도착 층
비콘은 아직 안 잡힌다. 한 경로로 이어두면 그 구간이 **경로 이탈로 읽힌다.**

그래서 나누기로 한다.

    1층에서 407호(4층)로
      ①  지금 비콘 → 계단1        1층 안내. 계단1을 목적지처럼 다룬다
      ②  (신호 끊김)              층 이동 중. 판정을 멈추고 기다린다
      ③  4층 비콘 잡힘            major 가 바뀌는 것으로 안다
      ④  그 비콘 → 407호          4층 안내. ①과 **같은 코드**를 다시 돌린다

①과 ④는 완전히 같은 일이다. `plan_route` 도 `PathTracker` 도 `cues` 도 전부
"한 층 + 출발 비콘 + 목적지" 하나만 알면 되게 되어 있어서, 위에 구간 목록
하나만 얹으면 나머지는 손대지 않아도 된다.

── 사용자에게는 한 번이다 ────────────────────────────────────────

구간이 둘이어도 **`arrived` 는 최종 목적지에서 한 번만** 나간다. 계단1에 닿은
것은 서버 내부의 일이지 사용자가 도착한 것이 아니다. 앱은 이 파일의 존재를
모른다.

── 아직 안 하는 것 ────────────────────────────────────────────────

구간 목록은 N개를 담을 수 있게 해뒀지만, 지금 만드는 것은 최대 2개다.
1층→4층인데 그 계단이 3층까지만 간다면 갈아타야 하고, 그건 연결자 그래프를
따로 풀어야 하는 일이라 여기서는 "갈 수 있는 연결자가 없습니다"로 끊는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.connector.models import Connector, ConnectorPosition
from app.floor.models import Floor
from app.landmark.models import Landmark
from app.nav.map_source import MapDataError


@dataclass(frozen=True)
class Leg:
    """한 층 안에서의 안내 한 구간."""

    floor_id: str
    dest_id: str                    # 랜드마크 id 또는 연결자 id
    dest_name: str
    is_final: bool

    # 경유지일 때만 채운다. 마지막 구간에서는 전부 None.
    connector_type: str | None = None      # elevator | stairs
    next_floor_id: str | None = None
    next_floor_no: int | None = None

    def handoff_speech(self) -> str:
        """이 구간 끝에서 할 말. **도착 안내가 아니라 층 이동 안내다.**

        여기서 "도착했습니다"라고 하면 사용자는 다 왔다고 생각하고 멈춘다.
        """
        where = "엘리베이터를 타고" if self.connector_type == "elevator" else "계단을 이용해"
        floor = f"{self.next_floor_no}층" if self.next_floor_no is not None else "다음 층"
        return f"{self.dest_name}입니다. {where} {floor}으로 이동해 주세요."


def floor_of(db: Session, dest_id: str) -> str | None:
    """이 목적지가 어느 층인가. 연결자면 None — **층이 하나가 아니다.**"""
    row = db.get(Landmark, dest_id)
    return row.floor_id if row is not None else None


def _floor_no(db: Session, floor_id: str) -> int | None:
    f = db.get(Floor, floor_id)
    return f.floor if f is not None else None


def _connectors_between(db: Session, building_id: str,
                        from_floor: Floor, to_floor: Floor) -> list[tuple[Connector, ConnectorPosition]]:
    """두 층을 **둘 다** 운행하면서 두 층 모두에 좌표가 찍힌 연결자.

    운행층(`floors`)만 보면 안 된다. 운행은 하는데 그 층 도면에 위치를 안 찍은
    연결자가 실제로 있다(층 상태 `connector_missing` 이 그 경우다). 그걸 고르면
    경로 노드를 찾을 수 없어 안내가 그 자리에서 끊긴다.
    """
    out: list[tuple[Connector, ConnectorPosition]] = []
    rows = db.query(Connector).filter(Connector.building_id == building_id).all()
    for c in rows:
        serves = c.floors or []
        if from_floor.floor not in serves or to_floor.floor not in serves:
            continue
        here = (db.query(ConnectorPosition)
                .filter(ConnectorPosition.connector_id == c.id,
                        ConnectorPosition.floor_id == from_floor.id)
                .first())
        there = (db.query(ConnectorPosition)
                 .filter(ConnectorPosition.connector_id == c.id,
                         ConnectorPosition.floor_id == to_floor.id)
                 .first())
        if here is None or there is None:
            continue
        out.append((c, here))
    return out


def plan_legs(db: Session, from_floor_id: str, dest_id: str, dest_name: str,
              origin_x: float | None = None,
              origin_y: float | None = None) -> list[Leg]:
    """구간 목록을 만든다. 같은 층이면 한 개, 다른 층이면 두 개.

    `origin_x/y` 는 지금 서 있는 자리(출발 비콘 좌표)다. 연결자가 여럿일 때
    **가장 가까운 것**을 고르는 데만 쓴다. 없으면 첫 번째를 고른다 — 층 이동은
    되지만 멀리 돌 수 있다.
    """
    dest_floor_id = floor_of(db, dest_id)

    # 목적지가 연결자면 그건 "이 층의 그 계단으로 가라"는 뜻이다. 연결자는 여러
    # 층에 걸쳐 있어서 자기 층을 말할 수 없으므로, 지금 층에 있는 것으로 본다.
    if dest_floor_id is None or dest_floor_id == from_floor_id:
        return [Leg(floor_id=from_floor_id, dest_id=dest_id,
                    dest_name=dest_name, is_final=True)]

    here = db.get(Floor, from_floor_id)
    there = db.get(Floor, dest_floor_id)
    if here is None or there is None:
        raise MapDataError("층 정보를 찾을 수 없어 층 이동 경로를 만들 수 없습니다.")

    pairs = _connectors_between(db, here.building_id, here, there)
    if not pairs:
        raise MapDataError(
            f"{there.floor}층으로 갈 수 있는 계단이나 엘리베이터가 없습니다.\n"
            f"관리자웹에서 두 층을 모두 운행하는 연결자에 위치를 찍어주세요."
        )

    # 가장 가까운 것. 좌표를 모르면 첫 번째.
    if origin_x is not None and origin_y is not None:
        pairs.sort(key=lambda cp: math.hypot(float(cp[1].x or 0) - origin_x,
                                             float(cp[1].y or 0) - origin_y))
    connector, _pos = pairs[0]

    return [
        Leg(floor_id=from_floor_id,
            dest_id=connector.id,
            dest_name=connector.name or "연결 통로",
            is_final=False,
            connector_type=connector.type,
            next_floor_id=dest_floor_id,
            next_floor_no=there.floor),
        Leg(floor_id=dest_floor_id, dest_id=dest_id,
            dest_name=dest_name, is_final=True),
    ]
