from app.common import CamelModel


# 필드 구성은 WEB-FE/src/features/mapEditor/pathNodes.ts의 PathNode/PathEdge와 1:1로 맞췄다.
class PathNodeItem(CamelModel):
    id: str
    x: float
    y: float
    type: str  # corner | landmark | facing
    concave: bool  # corner 타입에서만 의미 있음
    pair_kind: str | None = None  # landmark — type === 'facing'일 때만 의미 있음


class PathEdgeItem(CamelModel):
    a: str
    b: str
    type: str  # wall | cross
    directed: bool | None = None  # type === 'cross'일 때만 의미 있음


class FloorPathNodesRequest(CamelModel):
    nodes: list[PathNodeItem]
    edges: list[PathEdgeItem]
    mask_w: int
    mask_h: int


class FloorPathNodesResponse(CamelModel):
    floor_id: str
    nodes: list[PathNodeItem]
    edges: list[PathEdgeItem]
    mask_w: int | None
    mask_h: int | None
