"""관리자웹(WEB-FE)이 부르는 것과 서버가 내놓는 것이 맞는지 대조한다.

    python tests/test_webfe_contract.py

DB 도 서버도 필요 없다. FastAPI 앱의 라우트 표와 WEB-FE 의 mock 핸들러
(`WEB-FE/src/mocks/handlers.ts`)를 읽어서 비교할 뿐이다.

── 왜 mock 을 기준으로 삼나 ──────────────────────────────────────

`handlers.ts` 는 프론트가 **실제로 기대하는 계약**이다. 화면들이 그 mock 을 상대로
개발되고 동작하므로, 거기 적힌 경로·필드가 곧 명세다. 문서보다 이쪽이 정확하다
(문서에는 `/api/accounts` 로 적혀 있었지만 코드는 `/api/admin/accounts` 였다).

필드 대조는 WEB-FE 의 도메인 타입(`types/domain.ts`)과 서버 응답 스키마를 맞춰 본다.
"""

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WEBFE = ROOT.parent / "WEB-FE"
HANDLERS = WEBFE / "src" / "mocks" / "handlers.ts"
DOMAIN = WEBFE / "src" / "types" / "domain.ts"

OK, BAD, WARN = "✓", "✗", "!"


def mock_routes() -> list[tuple[str, str]]:
    """WEB-FE mock 이 정의한 (메서드, 경로) 목록."""
    src = HANDLERS.read_text(encoding="utf-8")
    out = []
    for m, p in re.findall(r"http\.(get|post|patch|put|delete)\(\s*[`'\"]\$\{base\}([^`'\"]+)", src):
        # :id 같은 파라미터를 FastAPI 스타일로
        norm = re.sub(r":(\w+)", r"{\1}", p)
        out.append((m.upper(), "/api" + norm))
    return out


def server_routes() -> list[tuple[str, str]]:
    from app.main import app  # DB 연결 없이 라우트 표만 읽는다

    out = []
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if not path or not methods:
            continue
        for m in methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            out.append((m, path))
    return out


def same_shape(a: str, b: str) -> bool:
    """경로 파라미터 이름이 달라도 자리만 같으면 같은 경로로 본다."""
    norm = lambda p: re.sub(r"\{[^}]+\}", "{}", p)  # noqa: E731
    return norm(a) == norm(b)


def ts_interface_fields(name: str) -> set[str]:
    """domain.ts 에서 인터페이스 하나의 필드 이름을 뽑는다."""
    src = DOMAIN.read_text(encoding="utf-8")
    m = re.search(r"export interface " + name + r"\s*\{(.*?)\n\}", src, re.S)
    if not m:
        return set()
    return {
        f.group(1)
        for f in re.finditer(r"^\s*(\w+)\??\s*:", m.group(1), re.M)
    }


def pydantic_fields(model) -> set[str]:
    """응답 스키마가 내보내는 camelCase 필드 이름."""
    out = set()
    for name, field in model.model_fields.items():
        alias = field.alias or name
        out.add(alias)
    return out


def main() -> int:
    fails: list[str] = []
    total = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal total
        total += 1
        print(f" {OK if ok else BAD} {label:<52} {detail}")
        if not ok:
            fails.append(label)

    if not HANDLERS.is_file():
        print(f"{BAD} WEB-FE mock 을 찾을 수 없습니다: {HANDLERS}")
        return 1

    # ── 경로 대조 ────────────────────────────────────────────────
    print("\n── 관리자웹이 부르는 경로가 서버에 있는가 ──")
    wanted = mock_routes()
    have = server_routes()
    missing = []
    for method, path in wanted:
        hit = any(m == method and same_shape(p, path) for m, p in have)
        if not hit:
            missing.append(f"{method} {path}")
    check(not missing, f"mock 경로 {len(wanted)}개가 서버에 존재",
          "전부 있음" if not missing else f"{len(missing)}개 없음")
    for x in missing:
        print(f"      없음: {x}")

    # 서버에만 있는 것 (사용자앱·모니터용은 제외)
    extra = []
    for method, path in have:
        if not path.startswith("/api/"):
            continue
        if not any(m == method and same_shape(p, path) for m, p in wanted):
            extra.append(f"{method} {path}")
    if extra:
        print(f" {WARN} 서버에만 있는 API {len(extra)}개 (프론트가 안 부름)")
        for x in extra:
            print(f"      {x}")

    # ── 필드 대조 ────────────────────────────────────────────────
    print("\n── 응답 필드가 관리자웹 타입과 맞는가 ──")
    from app.beacon.schemas import BeaconResponse
    from app.building.schemas import BuildingResponse
    from app.connector.schemas import ConnectorResponse
    from app.floor.schemas import FloorResponse
    from app.landmark.schemas import LandmarkResponse

    pairs = [
        ("Beacon", BeaconResponse),
        ("Landmark", LandmarkResponse),
        ("Building", BuildingResponse),
        ("Floor", FloorResponse),
        ("Connector", ConnectorResponse),
    ]
    for ts_name, model in pairs:
        want = ts_interface_fields(ts_name)
        got = pydantic_fields(model)
        if not want:
            print(f" {WARN} {ts_name}: domain.ts 에서 못 찾음")
            continue
        # 프론트에만 있는 필드 = 서버가 안 내려주는 것
        lacking = sorted(want - got)
        check(not lacking, f"{ts_name} 응답이 프론트 타입을 덮는다",
              "전부 있음" if not lacking else f"빠짐: {lacking}")
        onlyserver = sorted(got - want)
        if onlyserver:
            print(f"      (서버에만: {onlyserver})")

    # ── 값 목록 대조 ─────────────────────────────────────────────
    print("\n── 열거값이 맞는가 ──")
    dsrc = DOMAIN.read_text(encoding="utf-8")

    def ts_union(name: str) -> set[str]:
        m = re.search(r"export type " + name + r"\s*=([^\n]*(?:\n\s*\|[^\n]*)*)", dsrc)
        return set(re.findall(r"'([^']+)'", m.group(1))) if m else set()

    beacon_types = ts_union("BeaconType")
    check(beacon_types == {"semantic", "reinforcement"},
          "BeaconType = semantic | reinforcement", str(sorted(beacon_types)))

    statuses = ts_union("FloorSetupStatus")
    from app.status import STATUS_ORDER
    check(set(STATUS_ORDER) == statuses, "층 상태 값이 프론트와 같다",
          f"서버 {len(STATUS_ORDER)}개")
    if set(STATUS_ORDER) != statuses:
        print(f"      프론트만: {sorted(statuses - set(STATUS_ORDER))}")
        print(f"      서버만:   {sorted(set(STATUS_ORDER) - statuses)}")

    # 랜드마크는 이제 자유 입력이라 열거값이 없어야 한다
    check("LandmarkType" not in dsrc, "랜드마크 분류는 고정 목록이 아니다",
          "category 자유 입력")

    print(f"\n{'=' * 62}")
    if fails:
        print(f"실패 {len(fails)} / 전체 {total}")
        for f in fails:
            print("  ✗", f)
        return 1
    print(f"전체 {total}개 통과 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
