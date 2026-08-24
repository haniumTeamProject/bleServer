"""목적지가 다른 층일 때, 안내를 **한 층짜리 구간 여러 개**로 쪼갠다.

── 왜 층을 합치지 않나 ────────────────────────────────────────────

층 그래프를 하나로 이어붙여 계단을 엣지로 두는 방법도 있다. 하지만 그러면
거리가 뜻을 잃는다. "약 20미터 뒤 오른쪽으로 꺾으세요"의 20m 는 바닥을 걷는
거리인데, 계단이 섞이면 그 안에 오르내리는 층이 들어간다. 사용자는 20m 를
걸어도 코너에 닿지 못한다.

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

── 연결자 테이블은 없앴다 ────────────────────────────────────────

예전에는 `connectors` 테이블이 "이 계단은 몇 층을 운행하고 층마다 어디에
있는가"를 들고 있었다. 그걸로 "목적지 층까지 가는 연결자"를 고를 수 있었지만,
대가가 컸다.

    관리 부담   층을 추가할 때마다 연결자 운행층·좌표를 다시 찍어야 한다
    이중 등록   계단이 `landmarks` 에도 `connectors` 에도 있어 목적지 목록에
                같은 이름이 두 번 떴다
    고아 데이터 건물을 지우면 연결자가 남았다

그런데 **운행층을 알 필요가 없다.** 어차피 층을 옮기면 신호가 끊기고, 새 층에서
가장 센 비콘으로 다시 시작한다(③). 잘못된 층에 내렸으면 거기서 다시 계획하면
된다 — 사용자가 실제로 어디 있는지는 비콘이 말해 주지, 우리가 미리 적어둔
운행층 표가 말해 주는 게 아니다.

그래서 지금은 **엘리베이터·계단도 그냥 목적지(landmark)**다. 관리자웹도 같은
판단으로 연결자 화면·API·타입을 전부 걷어냈다(`cac4633`).

── 어느 계단으로 보낼지 ──────────────────────────────────────────

운행층을 모르므로 "그 층까지 가는 것"을 고를 수가 없다. 대신 둘로 고른다.

    ① 가까운 것        지금 서 있는 자리에서 가장 가까운 계단·엘베
    ② 엘리베이터 우선   같은 거리면 엘리베이터. 계단은 전 층을 안 도는 일이
                       있지만 엘리베이터는 대개 전 층을 돈다

틀려도 갇히지 않는다. 3층까지만 가는 계단을 탔다면 3층에서 내릴 것이고,
`_maybe_resume` 이 "기다리던 층이 아니다"를 보고 거기서 다시 쪼갠다. 헛걸음
한 번의 비용과, 운행층 표를 정확히 유지하는 비용을 견준 결과다.

── 사용자에게는 한 번이다 ────────────────────────────────────────

구간이 둘이어도 **`arrived` 는 최종 목적지에서 한 번만** 나간다. 계단1에 닿은
것은 서버 내부의 일이지 사용자가 도착한 것이 아니다. 앱은 이 파일의 존재를
모른다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.floor.models import Floor
from app.landmark.models import Landmark
from app.nav.map_source import MapDataError, connector_kind

# 가장 가까운 계단보다 엘리베이터가 이만큼 더 멀어도 엘리베이터를 고른다.
#
# 설계도(900) 좌표 기준이라 미터가 아니다. 실측 4층 축척으로 대략 5m 쯤 된다.
# 층 하나를 통째로 가로지를 만큼은 아니고, "같은 복도 반대쪽" 정도를 봐준다.
#
# **실측으로 정한 값이 아니다.** 계단·엘베 배치가 건물마다 다르므로 첫 실측에서
# 봐야 한다.
ELEVATOR_BONUS_PX = 100.0


@dataclass(frozen=True)
class Leg:
    """한 층 안에서의 안내 한 구간."""

    floor_id: str
    dest_id: str
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
    """이 목적지가 어느 층인가. 못 찾으면 None."""
    row = db.get(Landmark, dest_id)
    return row.floor_id if row is not None else None


def _connectors_on(db: Session, floor_id: str) -> list[tuple[Landmark, str]]:
    """그 층의 계단·엘베 목적지. `(랜드마크, "elevator"|"stairs")`."""
    out: list[tuple[Landmark, str]] = []
    for lm in db.query(Landmark).filter(Landmark.floor_id == floor_id).all():
        kind = connector_kind(lm.name, lm.category)
        if kind is not None:
            out.append((lm, kind))
    return out


def plan_legs(db: Session, from_floor_id: str, dest_id: str, dest_name: str,
              origin_x: float | None = None,
              origin_y: float | None = None) -> list[Leg]:
    """구간 목록을 만든다. 같은 층이면 한 개, 다른 층이면 두 개.

    `origin_x/y` 는 지금 서 있는 자리(출발 비콘 좌표)다. 계단이 여럿일 때
    **가장 가까운 것**을 고르는 데 쓴다. 없으면 거리를 0으로 보고 종류만으로
    고른다 — 층 이동은 되고 멀리 돌 수 있을 뿐이다.
    """
    dest_floor_id = floor_of(db, dest_id)

    # 층을 모르거나 같은 층이면 쪼갤 것이 없다.
    #
    # 목적지 자체가 계단·엘베여도 마찬가지다. "이 층의 그 계단으로 가라"는
    # 뜻이므로 한 구간이면 된다.
    if dest_floor_id is None or dest_floor_id == from_floor_id:
        return [Leg(floor_id=from_floor_id, dest_id=dest_id,
                    dest_name=dest_name, is_final=True)]

    here = db.get(Floor, from_floor_id)
    there = db.get(Floor, dest_floor_id)
    if here is None or there is None:
        raise MapDataError("층 정보를 찾을 수 없어 층 이동 경로를 만들 수 없습니다.")

    candidates = _connectors_on(db, from_floor_id)
    if not candidates:
        raise MapDataError(
            f"{there.floor}층으로 가려면 계단이나 엘리베이터가 필요한데, "
            f"이 층에 등록된 것이 없습니다.\n"
            f"관리자웹에서 계단·엘리베이터를 목적지로 등록해 주세요."
        )

    def dist_of(lm: Landmark) -> float:
        if origin_x is None or origin_y is None:
            return 0.0
        return math.hypot(float(lm.x or 0) - origin_x, float(lm.y or 0) - origin_y)

    ranked = sorted(((dist_of(lm), lm, kind) for lm, kind in candidates),
                    key=lambda t: t[0])
    best_d, via, via_kind = ranked[0]

    # **가까운 것을 고르되, 엘리베이터가 조금만 더 멀면 그쪽으로 바꾼다.**
    #
    # 운행층을 모르므로 "그 층까지 가는 것"은 고를 수 없다. 엘리베이터를 밀어
    # 주는 이유는 전 층을 도는 경우가 많아서고, 계단을 못 쓰는 사람에게도 안전해서다.
    #
    # 그렇다고 무조건 엘리베이터를 고르면 반대편 끝까지 걷게 된다. 그래서
    # "거의 같은 거리면" 으로 한정한다.
    if via_kind != "elevator":
        for d, lm, kind in ranked:
            if kind == "elevator" and d - best_d <= ELEVATOR_BONUS_PX:
                via, via_kind = lm, kind
                break

    return [
        Leg(floor_id=from_floor_id,
            dest_id=via.id,
            dest_name=via.name or ("엘리베이터" if via_kind == "elevator" else "계단"),
            is_final=False,
            connector_type=via_kind,
            next_floor_id=dest_floor_id,
            next_floor_no=there.floor),
        Leg(floor_id=dest_floor_id, dest_id=dest_id,
            dest_name=dest_name, is_final=True),
    ]
