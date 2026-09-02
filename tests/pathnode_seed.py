"""테스트용 — 관리자가 경로노드를 저장해둔 상태를 DB 에 만들어 준다.

안내 서버는 `floor_path_nodes` 에 저장된 그래프만 쓴다. 노드·간선을 만드는 것은
관리자웹(`pathNodes.ts`)의 일이고, 서버는 관리자가 화면에서 검수하고 저장한 결과를
읽을 뿐이다.

테스트는 실측 프로젝트 파일에서 층을 세우므로 그 저장 과정을 대신할 것이 필요하다.
여기서 `app.nav.path_nodes`(관리자웹 생성 코드를 옮긴 것)를 돌려 결과를 그대로
넣는다 — 관리자가 화면을 열어 "저장"을 누른 것과 같은 상태가 된다.

    seed_path_nodes(db, floor_id)      # Floor · FloorMask · 랜드마크를 넣은 뒤에 부른다
"""

from app.nav.db_map_source import DESIGN_W, DbMapSource
from app.nav.path_nodes import EntrancePoint, generate_path_nodes
from app.pathnode.models import FloorPathNodes
from app.floor.models import Floor

# 관리자웹 경로노드 화면과 같은 값이어야 한다 (PathNodePage.tsx).
#
# 건너기 폭은 관리자가 바꿀 수 없는 고정값이다 — 건너는 동안 짚을 벽이 없어서
# 폭이 넓을수록 직진을 벗어나 맞은편을 놓치기 쉽다. 그래서 복도 정의와 같은 3m 로
# 묶어두고, 더 넓은 곳은 관리자가 '건너기 추가' 로 직접 긋게 한다.
#
# 서버가 그래프를 직접 만들던 시절 이 값이 12.0 이었다. 관리자 화면은 3m 로 만든
# 그래프를 보여주는데 안내는 훨씬 촘촘한 건너기를 쓰고 있었던 셈이다.
CROSSING_MAX_M = 3.0
CROSS_PENALTY_M = 5.0


def seed_path_nodes(db, floor_id: str) -> FloorPathNodes:
    src = DbMapSource(db)
    mask, mw, mh, _ = src._mask_bits(floor_id)

    f = db.get(Floor, floor_id)
    scale = float(f.scale_m_per_px) if f and f.scale_m_per_px else None
    if scale is None:
        raise ValueError(f"{floor_id} 에 축척이 없습니다")

    # 입구 순서가 노드 번호를 정한다. 관리자웹과 같은 순서(연결자 먼저)를 쓴다.
    to_mask = mw / DESIGN_W
    entrances = [
        EntrancePoint(x=lm.x * to_mask, y=lm.y * to_mask,
                      kind="connector" if lm.is_connector else "landmark")
        for lm in src._entrance_order(floor_id)
    ]

    built = generate_path_nodes(mask, mw, mh, entrances, CROSSING_MAX_M / scale)

    row = db.get(FloorPathNodes, floor_id)
    if row is None:
        row = FloorPathNodes(floor_id=floor_id)
        db.add(row)
    row.mask_w = mw
    row.mask_h = mh
    # 키 이름은 관리자웹이 보내는 모양(camelCase)을 따른다 — 서버가 읽는 자리가 같다.
    row.nodes = [
        {"id": n.id, "x": n.x, "y": n.y, "type": n.type,
         "concave": n.concave, "pairKind": n.pair_kind}
        for n in built.nodes
    ]
    row.edges = [
        {"a": e.a, "b": e.b, "type": e.type, "directed": e.directed}
        for e in built.edges
    ]
    row.cross_penalty_m = CROSS_PENALTY_M
    row.crossing_max_m = CROSSING_MAX_M
    db.commit()
    return row
