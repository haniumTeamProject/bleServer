"""실측 프로젝트 파일(`mappin_project.json`)을 DB 에 밀어넣는다.

    python tests/seed_from_project.py              # 넣는다
    python tests/seed_from_project.py --dry-run    # 변환만 해보고 서버는 안 부른다
    python tests/seed_from_project.py --reset      # 같은 건물이 있으면 지우고 다시

`/monitor` 의 지도를 DB 모드로 보려면 DB 에 층이 하나는 있어야 하는데, 관리자웹은
아직 가짜 API(MSW)를 쓰고 있어서 화면으로 넣으면 백엔드에 안 닿는다. 그래서
실측 때 만든 파일을 그대로 REST 로 밀어넣는다.

── 첫 관리자는 REST 로 못 만든다 ──────────────────────────────────

    signup  →  status="pending" 으로 생성
    login   →  status != "active" 면 403
    승인     →  이미 로그인한 super_admin 이 필요

닭이 먼저냐 달걀이 먼저냐가 된다. 그래서 **첫 계정만 DB 에 직접 넣고**, 나머지는
전부 REST 로 한다(그래야 서버의 검증·계산을 그대로 통과하는지 같이 확인된다).

── 좌표·축척을 어떻게 옮기나 ──────────────────────────────────────

파일은 작업 픽셀 기준, DB 는 설계도(900) 기준이다.

    좌표(900)     = 좌표(작업px) × 900 / workW
    scaleMPerPx  = scale_m_per_px(원본px 기준) × origW / maskW

마스크는 여기서 `workW × workH` 로 쓰므로 maskW = workW 다.
(자세한 근거는 docs/WEBFE_접합_변경기록.md §7)
"""

import argparse
import base64
import json
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DESIGN_W = 900
OK, BAD = "✓", "✗"

SEED_EMAIL = "seed@hanium.local"
SEED_PW = "seed1234"


# ---------------------------------------------------------------------------
# PNG 쓰기 — Pillow 없이
# ---------------------------------------------------------------------------
def gray_alpha_png(mask: bytes, w: int, h: int) -> bytes:
    """0/1 바이트 배열을 투명배경 PNG 로 만든다.

    관리자웹은 마스크를 **alpha > 0 이 통행 가능**인 PNG 로 읽는다
    (WEB-FE/src/lib/maskRaster.ts). 그 규칙에 맞춘다.

    Pillow 를 쓰지 않는 이유: 이 스크립트 하나 때문에 설치를 요구하고 싶지 않다.
    회색+알파(color type 4) 8bit 는 픽셀당 2바이트라 손으로 쓰기 쉽다.
    """
    rows = bytearray()
    for y in range(h):
        rows.append(0)                        # 필터 없음
        line = mask[y * w:(y + 1) * w]
        for v in line:
            # 통행 가능이면 흰색·불투명, 아니면 완전 투명
            rows += b"\xff\xff" if v else b"\x00\x00"

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 4, 0, 0, 0)   # 8bit, gray+alpha
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
def find_project() -> Path:
    for cand in (ROOT / "map-tool" / "static" / "mappin_project.json",
                 ROOT.parent / "map-tool" / "static" / "mappin_project.json"):
        if cand.is_file():
            return cand
    raise SystemExit("mappin_project.json 을 찾지 못했습니다 (map-tool/static/).")


def bootstrap_admin() -> None:
    """첫 super_admin 을 DB 에 직접 넣는다. 이미 있으면 아무것도 안 한다."""
    from app.admin.models import Admin
    from app.database import SessionLocal
    from app.security.password import hash_password

    db = SessionLocal()
    try:
        if db.query(Admin).filter(Admin.email == SEED_EMAIL).first():
            print(f" {OK} 시드 계정이 이미 있습니다            {SEED_EMAIL}")
            return
        db.add(Admin(
            email=SEED_EMAIL, password_hash=hash_password(SEED_PW),
            name="시드", org="한이음", status="active", role="super_admin",
        ))
        db.commit()
        print(f" {OK} 시드 계정을 만들었습니다               {SEED_EMAIL} / {SEED_PW}")
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="서버 주소")
    ap.add_argument("--building", default="ICT융합대학")
    ap.add_argument("--code", default="suwon_ict")
    ap.add_argument("--floor", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="변환만 하고 서버는 안 부른다")
    ap.add_argument("--reset", action="store_true", help="같은 code 의 건물이 있으면 지우고 다시")
    a = ap.parse_args()

    path = find_project()
    d = json.loads(path.read_text(encoding="utf-8"))
    print(f"\n── 읽음: {path.name} ──")

    work_w, work_h = int(d["workW"]), int(d["workH"])
    orig_w = int(d["origW"])
    m_per_orig = float(d.get("scale_m_per_px") or 0.05)
    src_beacons = d.get("beacons") or []
    src_landmarks = d.get("landmarks") or []
    print(f" 원본 {orig_w}×{d['origH']} / 작업 {work_w}×{work_h} / workScale {d.get('workScale')}")
    print(f" 비콘 {len(src_beacons)}개, 랜드마크 {len(src_landmarks)}개")

    # -- 마스크 -------------------------------------------------------------
    corridor = base64.b64decode(d.get("corridorMaskB64") or "")
    if len(corridor) != work_w * work_h:
        print(f" {BAD} 마스크 크기가 안 맞습니다: {len(corridor)} != {work_w * work_h}")
        return 1
    walkable = sum(1 for v in corridor if v)
    png = gray_alpha_png(corridor, work_w, work_h)
    mask_url = "data:image/png;base64," + base64.b64encode(png).decode()
    print(f" {OK} 이동영역 → PNG                      "
          f"{walkable:,}px ({walkable / (work_w * work_h) * 100:.1f}%), {len(png) / 1024:.0f}KB")

    # -- 좌표·축척 환산 -----------------------------------------------------
    k = DESIGN_W / work_w                       # 작업px → 설계도(900)
    scale_db = m_per_orig * orig_w / work_w     # 원본px 기준 → 마스크px 기준
    print(f" {OK} 좌표 환산 계수                       ×{k:.5f}")
    print(f" {OK} 축척 {m_per_orig}(원본px) → {scale_db:.6f}(마스크px)")

    beacons = [
        {
            "name": b.get("bleName") or b["id"],
            "minor": i + 1,
            "type": "semantic",
            "x": round(b["x"] * k, 2),
            "y": round(b["y"] * k, 2),
            "sourceUid": f"seed-{b['id']}",     # map-tool 이 uid 를 안 만든다 (§8-1)
            "sourceLabel": b["id"],
        }
        for i, b in enumerate(src_beacons)
    ]
    landmarks = [
        {
            "name": lm.get("name") or lm["id"],
            # 파일에는 분류가 없다(이름뿐). 지어내지 않고 비워둔다 —
            # 관리자웹에서 나중에 채우면 된다.
            "category": "미분류",
            "x": round(lm["x"] * k, 2),
            "y": round(lm["y"] * k, 2),
            "sourceUid": f"seed-{lm['id']}",
            "sourceLabel": lm["id"],
        }
        for lm in src_landmarks
    ]

    if a.dry_run:
        print("\n── --dry-run 이라 서버는 부르지 않습니다 ──")
        print(" 비콘 예:    ", json.dumps(beacons[0], ensure_ascii=False))
        print(" 랜드마크 예:", json.dumps(landmarks[0], ensure_ascii=False))
        print(f"\n 설계도 이미지 {len(d.get('imageDataUrl') or '') / 1024:.0f}KB, "
              f"마스크 {len(mask_url) / 1024:.0f}KB")
        return 0

    # -- 서버로 -------------------------------------------------------------
    try:
        import requests
    except ImportError:
        print(f" {BAD} requests 가 필요합니다:  pip install requests")
        return 1

    # 서버부터 확인한다. **DB 를 건드리기 전에** 해야 한다 —
    # 계정만 만들어놓고 죽으면 다음 실행 때 "이미 있음"으로 넘어가 흔적이 애매해진다.
    s = requests.Session()
    try:
        s.get(f"{a.base}/openapi.json", timeout=5)
    except requests.exceptions.ConnectionError:
        print(f"\n {BAD} 서버가 {a.base} 에 없습니다.\n")
        print("    다른 터미널에서 먼저 켜주세요:")
        print("        cd backend-python")
        print("        uvicorn app.main:app --reload\n")
        print("    'Application startup complete.' 가 뜬 뒤에 이 스크립트를 다시 돌리면 됩니다.")
        print("    (포트가 다르면  --base http://127.0.0.1:포트)")
        return 1

    bootstrap_admin()

    r = s.post(f"{a.base}/api/admin/auth/login",
               json={"email": SEED_EMAIL, "password": SEED_PW}, timeout=10)
    if r.status_code != 200:
        print(f" {BAD} 로그인 실패 {r.status_code}: {r.text[:200]}")
        return 1
    s.headers["Authorization"] = f"Bearer {r.json()['accessToken']}"
    print(f" {OK} 로그인")

    if a.reset:
        for b in s.get(f"{a.base}/api/buildings", timeout=10).json():
            if b.get("code") == a.code:
                s.delete(f"{a.base}/api/buildings/{b['id']}", timeout=30)
                print(f" {OK} 기존 건물 삭제                       {b['name']}")

    r = s.post(f"{a.base}/api/buildings", json={"code": a.code, "name": a.building}, timeout=10)
    if r.status_code >= 400:
        print(f" {BAD} 건물 생성 실패 {r.status_code}: {r.text[:300]}")
        return 1
    bid = r.json()["id"]
    fid = s.post(f"{a.base}/api/buildings/{bid}/floors",
                 json={"floor": a.floor}, timeout=10).json()["id"]
    print(f" {OK} 건물·층                             {a.building} {a.floor}층")

    s.put(f"{a.base}/api/floors/{fid}/floorplan",
          json={"imageUrl": d["imageDataUrl"]}, timeout=120)
    print(f" {OK} 설계도 업로드")
    s.put(f"{a.base}/api/floors/{fid}/mask",
          json={"width": work_w, "height": work_h, "dataUrl": mask_url}, timeout=120)
    print(f" {OK} 이동영역 업로드")
    s.put(f"{a.base}/api/floors/{fid}/scale", json={"scaleMPerPx": scale_db}, timeout=10)
    print(f" {OK} 축척")

    for b in beacons:
        rr = s.post(f"{a.base}/api/floors/{fid}/beacons", json=b, timeout=10)
        if rr.status_code >= 400:
            print(f" {BAD} 비콘 {b['sourceLabel']} 실패 {rr.status_code}: {rr.text[:200]}")
            return 1
    print(f" {OK} 비콘 {len(beacons)}개")

    for lm in landmarks:
        rr = s.post(f"{a.base}/api/floors/{fid}/landmarks", json=lm, timeout=10)
        if rr.status_code >= 400:
            print(f" {BAD} 랜드마크 {lm['sourceLabel']} 실패 {rr.status_code}: {rr.text[:200]}")
            return 1
    print(f" {OK} 랜드마크 {len(landmarks)}개")

    # -- 확인 ---------------------------------------------------------------
    print("\n── 확인 ──")
    rows = s.get(f"{a.base}/api/buildings/{bid}/floors", timeout=10).json()
    print(f" 층 상태:  {rows[0]['status']}   (ready 면 안내 가능)")

    p = requests.get(f"{a.base}/map-db/floors/{fid}/project", timeout=60)
    if p.status_code != 200:
        print(f" {BAD} /map-db 피드 실패 {p.status_code}: {p.text[:200]}")
        return 1
    pj = p.json()
    print(f" {OK} /map-db 피드                        "
          f"비콘 {len(pj['beacons'])} / 목적지 {len(pj['landmarks'])}")
    print(f"\n 이제 {a.base}/monitor 를 열면 지도가 DB 에서 뜹니다.")
    print(f" 관리자웹 로그인:  {SEED_EMAIL} / {SEED_PW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
