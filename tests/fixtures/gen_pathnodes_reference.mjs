// 관리자웹의 진짜 pathNodes.ts / pathfind.ts 를 돌려서 기대값을 뽑는다.
//
//     node --experimental-strip-types tests/fixtures/gen_pathnodes_reference.mjs
//
// 파이썬 포팅본이 같은 값을 내는지 대조하는 데 쓴다(tests/test_path_nodes.py).
// 손으로 적은 기대값이 아니라 **원본을 실행한 결과**여야 의미가 있다 —
// 손으로 적으면 내가 원본을 잘못 읽은 것까지 같이 베끼게 된다.
//
// 프론트가 알고리즘을 고치면 이 파일을 다시 돌려 기대값을 갱신하고,
// 파이썬 쪽 테스트가 깨지는 것으로 "따라가야 할 변경"을 알게 된다.

import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const WEBFE = resolve(here, '../../../WEB-FE/src/features/mapEditor')

const { generatePathNodes } = await import(resolve(WEBFE, 'pathNodes.ts'))
const { findShortestPath } = await import(resolve(WEBFE, 'pathfind.ts'))

// ---- 마스크를 사각형 목록으로 적는다 (파이썬도 같은 목록으로 만든다) ----
function build(w, h, rects) {
  const mask = new Uint8Array(w * h)
  for (const [x0, y0, x1, y1] of rects) {
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) mask[y * w + x] = 1
  }
  return mask
}

const FIXTURES = [
  {
    name: 'corridor_with_room',           // pathNodes.test.ts 1번
    w: 200, h: 60,
    rects: [[0, 20, 200, 40], [80, 0, 120, 20]],
    entrances: [{ x: 82, y: 2, kind: 'landmark' }],
  },
  {
    name: 'room_corridor_door',           // pathNodes.test.ts 2번
    w: 200, h: 60,
    rects: [[10, 0, 150, 30], [0, 35, 200, 55], [20, 30, 80, 35]],
    entrances: [{ x: 22, y: 28, kind: 'landmark' }],
  },
  {
    name: 'l_corridor',                   // pathNodes.test.ts 3번 — 코너 건너기
    w: 80, h: 80,
    rects: [[0, 0, 10, 60], [0, 50, 60, 60]],
    entrances: [],
  },
  {
    name: 'wide_room',                    // pathNodes.test.ts 4번 — 건너기 없어야 함
    w: 300, h: 300,
    rects: [[5, 5, 295, 295]],
    entrances: [],
  },
  {
    name: 'two_components',               // 덩어리 두 개 — 노드 번호가 이어지는지
    w: 120, h: 60,
    rects: [[0, 10, 40, 30], [70, 10, 110, 30]],
    entrances: [],
  },
  {
    name: 'tiny_blob_dropped',            // 25px 미만 덩어리는 버려야 함
    w: 60, h: 60,
    rects: [[0, 20, 60, 40], [2, 2, 5, 5]],
    entrances: [],
  },
  {
    name: 'two_entrances',                // 입구 두 개 + 코너 건너기 섞임
    w: 200, h: 80,
    rects: [[0, 30, 200, 50], [40, 0, 80, 30], [120, 50, 160, 80]],
    entrances: [
      { x: 45, y: 5, kind: 'landmark' },
      { x: 155, y: 75, kind: 'landmark' },   // 예전엔 connector. 관리자웹이 그 종류를 없앴다
    ],
  },
  {
    name: 'entrance_too_far',             // 50px 넘게 떨어진 입구는 버려야 함
    w: 100, h: 100,
    rects: [[0, 0, 20, 20]],
    entrances: [{ x: 95, y: 95, kind: 'landmark' }],
  },
  {
    name: 'narrow_crossing_limit',        // crossingMaxPx 를 직접 넘겨 거르는지
    w: 200, h: 120,
    rects: [[0, 0, 200, 120]],
    entrances: [],
    crossingMaxPx: 50,
  },
]

const out = { pathNodes: [], pathfind: [] }

for (const f of FIXTURES) {
  const mask = build(f.w, f.h, f.rects)
  const args = [mask, f.w, f.h, f.entrances]
  if (f.crossingMaxPx !== undefined) args.push(f.crossingMaxPx)
  const { nodes, edges } = generatePathNodes(...args)
  out.pathNodes.push({
    name: f.name, w: f.w, h: f.h, rects: f.rects,
    entrances: f.entrances,
    crossingMaxPx: f.crossingMaxPx ?? null,
    nodes: nodes.map((n) => ({
      id: n.id, x: n.x, y: n.y, type: n.type, concave: n.concave,
      pairKind: n.pairKind ?? null,
    })),
    // **directed 를 떨어뜨리면 안 된다.** 이게 없으면 파이썬 쪽이 건너기를
    // 양방향으로 다뤄도 테스트가 통과한다 — 실제로 한동안 그랬다.
    edges: edges.map((e) => ({ a: e.a, b: e.b, type: e.type, directed: e.directed ?? null })),
  })
}

// ---- 경로 찾기도 같이 뽑는다 ----
//
// 노드는 [id, x, y, type?], 엣지는 [a, b, type, directed?] 다.
// **type 과 directed 가 픽스처에 실려야** 건너기 규칙 둘을 검사할 수 있다.
//
//     ① 목적지 건너기 제한   노드 type 이 'landmark' 인지 알아야 한다
//     ② 단방향              엣지 directed 를 알아야 한다
//
// 둘 다 안 싣던 시절에는 파이썬 포팅본이 규칙을 하나도 안 지켜도 전부 통과했다.
const PF = [
  {
    name: 'straight_wall',
    nodes: [['A', 0, 0], ['B', 10, 0], ['C', 20, 0]],
    edges: [['A', 'B', 'wall'], ['B', 'C', 'wall']],
    start: 'A', end: 'C', penalty: 0,
  },
  {
    name: 'cross_wins',
    nodes: [['A', 0, 0], ['M', 0, 100], ['B', 5, 0]],
    edges: [['A', 'M', 'wall'], ['M', 'B', 'wall'], ['A', 'B', 'cross']],
    start: 'A', end: 'B', penalty: 0,
  },
  {
    name: 'penalty_wins',
    nodes: [['A', 0, 0], ['M', 10, 0], ['B', 20, 0]],
    edges: [['A', 'M', 'wall'], ['M', 'B', 'wall'], ['A', 'B', 'cross']],
    start: 'A', end: 'B', penalty: 50,
  },
  {
    name: 'disconnected',
    nodes: [['A', 0, 0], ['B', 10, 0]], edges: [],
    start: 'A', end: 'B', penalty: 0,
  },
  {
    name: 'same_node',
    nodes: [['A', 0, 0]], edges: [], start: 'A', end: 'A', penalty: 0,
  },
  {
    // 건너기를 **거꾸로** 타야만 닿는 경우. 양방향인지 여기서 갈린다.
    name: 'cross_reverse',
    nodes: [['A', 0, 0], ['B', 5, 0]],
    edges: [['B', 'A', 'cross']],
    start: 'A', end: 'B', penalty: 0,
  },
  {
    // ② 단방향. directed 건너기는 a→b 만 — pathfind.test.ts 65행
    name: 'directed_forward',
    nodes: [['A', 0, 0], ['M', 0, 100], ['B', 5, 0]],
    edges: [['A', 'M', 'wall'], ['M', 'B', 'wall'], ['A', 'B', 'cross', true]],
    start: 'A', end: 'B', penalty: 0,
  },
  {
    // 같은 그래프를 거꾸로. 건너기를 못 타고 우회로를 타야 한다
    name: 'directed_backward',
    nodes: [['A', 0, 0], ['M', 0, 100], ['B', 5, 0]],
    edges: [['A', 'M', 'wall'], ['M', 'B', 'wall'], ['A', 'B', 'cross', true]],
    start: 'B', end: 'A', penalty: 0,
  },
  {
    // ① 지나가는 길의 목적지 건너기를 지름길로 쓰면 안 된다 — pathfind.test.ts 85행
    name: 'landmark_cross_not_shortcut',
    nodes: [['A', 0, 0], ['L', 10, 0, 'landmark'], ['M', 10, -100], ['F', 16, 8], ['B', 16, 0]],
    edges: [['A', 'L', 'wall'], ['L', 'M', 'wall'], ['M', 'B', 'wall'],
            ['L', 'F', 'cross'], ['F', 'B', 'wall']],
    start: 'A', end: 'B', penalty: 0,
  },
  {
    // 그 목적지가 출발지 자신이면 정상적으로 쓴다 — pathfind.test.ts 105행
    name: 'landmark_cross_from_itself',
    nodes: [['A', 0, 0], ['L', 10, 0, 'landmark'], ['M', 10, -100], ['F', 16, 8], ['B', 16, 0]],
    edges: [['A', 'L', 'wall'], ['L', 'M', 'wall'], ['M', 'B', 'wall'],
            ['L', 'F', 'cross'], ['F', 'B', 'wall']],
    start: 'L', end: 'B', penalty: 0,
  },
  {
    name: 'penalty_boundary_equal',
    nodes: [['A', 0, 0], ['M', 10, 0], ['B', 20, 0]],
    edges: [['A', 'M', 'wall'], ['M', 'B', 'wall'], ['A', 'B', 'cross']],
    start: 'A', end: 'B', penalty: 0,
  },
]

for (const c of PF) {
  const nodes = c.nodes.map(([id, x, y, type]) => ({ id, x, y, type: type ?? 'corner', concave: false }))
  const edges = c.edges.map(([a, b, type, directed]) => (
    directed === undefined ? { a, b, type } : { a, b, type, directed }
  ))
  const r = findShortestPath(nodes, edges, c.start, c.end, c.penalty)
  out.pathfind.push({
    name: c.name, nodes: c.nodes, edges: c.edges,
    start: c.start, end: c.end, penalty: c.penalty,
    result: r === null ? null : { path: r.path, distancePx: r.distancePx },
  })
}

// ---- 실측 평면도 ----
//
// 손으로 만든 도형은 알고리즘의 갈래를 다 밟지 못한다. 실제 4층 마스크(2372×1790,
// 통행영역 21만px, 입구 24개)로 돌려서 노드 100여 개가 전부 맞는지 본다.
// 여기서만 드러나는 차이(부동소수 누적, 병합 순서 등)가 있을 수 있다.
const projectPath = resolve(here, '../../../map-tool/static/mappin_project.json')
try {
  const { readFileSync } = await import('node:fs')
  const project = JSON.parse(readFileSync(projectPath, 'utf-8'))
  const w = project.workW
  const h = project.workH
  const raw = Buffer.from(project.corridorMaskB64, 'base64')
  const mask = new Uint8Array(w * h)
  for (let i = 0; i < mask.length; i++) mask[i] = raw[i] ? 1 : 0

  const entrances = project.landmarks.map((l) => ({
    x: l.x, y: l.y, kind: 'landmark',
  }))
  const scale = project.scale_m_per_px / project.workScale
  const crossingMaxPx = 12 / scale

  const { nodes, edges } = generatePathNodes(mask, w, h, entrances, crossingMaxPx)
  out.realFloor = {
    source: 'map-tool/static/mappin_project.json',
    w, h, crossingMaxPx,
    entrances,
    nodes: nodes.map((n) => ({
      id: n.id, x: n.x, y: n.y, type: n.type, concave: n.concave,
      pairKind: n.pairKind ?? null,
    })),
    // **directed 를 떨어뜨리면 안 된다.** 이게 없으면 파이썬 쪽이 건너기를
    // 양방향으로 다뤄도 테스트가 통과한다 — 실제로 한동안 그랬다.
    edges: edges.map((e) => ({ a: e.a, b: e.b, type: e.type, directed: e.directed ?? null })),
  }
  console.log(`  실측 4층: 노드 ${nodes.length} / 연결 ${edges.length}`)
} catch (err) {
  out.realFloor = null
  console.log(`  실측 평면도 건너뜀: ${err.message}`)
}

mkdirSync(here, { recursive: true })
const dest = resolve(here, 'pathnodes_reference.json')
writeFileSync(dest, JSON.stringify(out, null, 1), 'utf-8')
console.log(`기대값을 적었습니다: ${dest}`)
console.log(`  노드 생성 ${out.pathNodes.length}개 / 경로 찾기 ${out.pathfind.length}개`)
for (const f of out.pathNodes) {
  const cross = f.edges.filter((e) => e.type === 'cross').length
  console.log(`  ${f.name.padEnd(24)} 노드 ${String(f.nodes.length).padStart(3)} / 연결 ${String(f.edges.length).padStart(3)} (건너기 ${cross})`)
}
