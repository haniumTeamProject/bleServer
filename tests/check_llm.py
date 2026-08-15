"""로컬 AI(Ollama) 목적지 매칭 — 단계별 점검 도구.

    python tests/check_llm.py

7단계를 순서대로 확인하고, 실패한 단계에서 **무엇을 하면 되는지** 알려준다.
어느 단계에서 막혔는지만 알면 대부분 바로 고칠 수 있다.

    1. 환경변수 확인
    2. Ollama 서버가 떠 있는가
    3. 모델을 받았는가
    4. 모델이 대답하는가 (+ 첫 응답 시간)
    5. 목적지 해석이 되는가
    6. 방어 장치가 도는가 (환각·번호 오답 차단)
    7. 규칙 엔진 폴백이 살아 있는가
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ws import llm_matcher  # noqa: E402
from app.ws.landmark_matcher import load_landmarks  # noqa: E402

LANDMARKS = load_landmarks([
    {"id": "L11", "name": "407"}, {"id": "L14", "name": "406"},
    {"id": "L05", "name": "410"}, {"id": "L06", "name": "409(1)"},
    {"id": "L07", "name": "409(2)"},
    {"id": "L12", "name": "화장실 1"}, {"id": "L13", "name": "화장실 2"},
    {"id": "L02", "name": "엘리베이터 1"}, {"id": "L03", "name": "엘리베이터 2"},
    {"id": "L01", "name": "계단 1"}, {"id": "L04", "name": "계단 2"},
])

OK, NG, WARN = "✓", "✗", "!"
_failed: list[tuple[str, str]] = []


def step(n: int, title: str) -> None:
    print(f"\n{'─' * 62}\n{n}단계. {title}\n{'─' * 62}")


def fail(what: str, how: str) -> None:
    _failed.append((what, how))
    print(f"  {NG} {what}")
    print(f"     → {how}")


def get(url: str, timeout: float = 3.0):
    req = urllib.request.Request(url)
    if llm_matcher.API_KEY:
        req.add_header("Authorization", f"Bearer {llm_matcher.API_KEY}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    print("로컬 AI 목적지 매칭 점검")

    # ---- 1 ----
    step(1, "환경변수")
    print(f"  제공자   LLM_PROVIDER  = {llm_matcher.PROVIDER}")
    print(f"  모델     LLM_MODEL     = {llm_matcher.MODEL}")
    print(f"  주소     LLM_BASE_URL  = {llm_matcher.BASE_URL}")
    print(f"  타임아웃 LLM_TIMEOUT   = {llm_matcher.TIMEOUT_S}초")
    if llm_matcher.PROVIDER == "off":
        print(f"  {WARN} LLM이 꺼져 있습니다. 규칙 엔진만 씁니다.")
        print("     켜려면: LLM_PROVIDER 를 지우거나 ollama 로 설정")
        return 0

    # ---- 2 ----
    step(2, "Ollama 서버 연결")
    tags = None
    try:
        tags = get(f"{llm_matcher.BASE_URL}/api/tags")
        print(f"  {OK} 연결됨 — {llm_matcher.BASE_URL}")
    except (urllib.error.URLError, OSError) as e:
        fail(f"연결 실패: {e}",
             "Ollama가 안 떠 있습니다.\n"
             "        Windows : 설치했으면 자동 실행됩니다. 작업표시줄 아이콘 확인\n"
             "        Linux   : sudo systemctl start ollama\n"
             "        직접    : ollama serve\n"
             "        ※ Ollama는 FastAPI 서버와 같은 컴퓨터에 있어야 합니다")
        return 1

    # ---- 3 ----
    step(3, "모델 확인")
    names = [m.get("name", "") for m in (tags or {}).get("models", [])]
    if not names:
        fail("받은 모델이 없습니다",
             f"ollama pull {llm_matcher.MODEL}")
        return 1
    print(f"  받은 모델 {len(names)}개: {', '.join(names[:6])}"
          + (" …" if len(names) > 6 else ""))
    base = llm_matcher.MODEL.split(":")[0]
    if not any(n == llm_matcher.MODEL or n.split(":")[0] == base for n in names):
        fail(f"'{llm_matcher.MODEL}' 이 없습니다",
             f"ollama pull {llm_matcher.MODEL}\n"
             f"        또는 받은 것을 쓰려면: export LLM_MODEL={names[0]}")
        return 1
    print(f"  {OK} {llm_matcher.MODEL} 있음")

    # ---- 4 ----
    step(4, "모델 응답 (첫 호출은 적재 시간이 얹힙니다)")
    t0 = time.time()
    raw = llm_matcher._call_llm("407호", LANDMARKS[:4])
    dt = time.time() - t0
    if raw is None:
        fail(f"응답 없음 ({dt:.1f}초)",
             f"타임아웃({llm_matcher.TIMEOUT_S}초)일 가능성이 큽니다.\n"
             "        모델을 미리 깨워두세요:  ollama run " + llm_matcher.MODEL + ' "안녕"\n'
             "        서버는 뜰 때 자동으로 예열합니다(llm_matcher.warmup).\n"
             "        계속 느리면 더 작은 모델로: export LLM_MODEL=exaone3.5:2.4b")
        return 1
    print(f"  {OK} 응답 {dt:.2f}초")
    print(f"     원문: {raw.strip()[:100]}")

    # ---- 5 ----
    step(5, "목적지 해석")
    cases = [
        ("407호로 가줘", "방 번호"),
        ("사백칠", "한글 수사"),
        ("변소 급해요", "동의어 — 규칙 엔진이 못 하던 것"),
        ("승강기 어딨어요", "동의어"),
        ("409", "같은 번호 2곳 → 되묻기"),
        ("옥상정원", "없는 곳 → 거절"),
    ]
    llm_matcher._cache.clear()
    for text, why in cases:
        t0 = time.time()
        r = llm_matcher.resolve(text, LANDMARKS)
        dt = time.time() - t0
        got = r.landmark.name if r.landmark else [c.name for c in r.candidates]
        print(f"  {OK} {text!r:16} → {r.status:10} {str(got):24} ({dt:.2f}초)  {why}")

    # ---- 6 ----
    step(6, "방어 장치")
    real = llm_matcher._call_llm

    def fake(reply):
        llm_matcher._call_llm = lambda t, l: reply
        llm_matcher._cache.clear()
        llm_matcher._last_fail_at = 0.0

    fake('{"ids": ["L99"], "why": "없는 장소를 지어냄"}')
    r = llm_matcher.resolve("407", LANDMARKS)
    good = r.landmark is not None and r.landmark.name == "407"
    print(f"  {OK if good else NG} 지어낸 id 차단 → {r.landmark.name if r.landmark else r.status}")
    if not good:
        _failed.append(("환각 차단 실패", "llm_matcher._parse_ids 확인"))

    fake('{"ids": ["L14"], "why": "406 을 골라버림"}')
    r = llm_matcher.resolve("407호", LANDMARKS)
    good = r.landmark is not None and r.landmark.name == "407"
    print(f"  {OK if good else NG} 번호 오답 차단 (407→406 방지) → "
          f"{r.landmark.name if r.landmark else r.status}")
    if not good:
        _failed.append(("번호 검증 실패", "LLM_VERIFY_NUMBER 가 0으로 꺼져 있는지 확인"))

    fake(None)
    r = llm_matcher.resolve("화장실", LANDMARKS)
    good = r.status == "ambiguous"
    print(f"  {OK if good else NG} 모델 죽었을 때 규칙 엔진 폴백 → {r.status} "
          f"{[c.name for c in r.candidates]}")
    if not good:
        _failed.append(("폴백 실패", "landmark_matcher 확인"))

    llm_matcher._call_llm = real
    llm_matcher._cache.clear()
    llm_matcher._last_fail_at = 0.0

    # ---- 7 ----
    step(7, "캐시")
    calls = {"n": 0}

    def counting(t, l):
        calls["n"] += 1
        return real(t, l)

    llm_matcher._call_llm = counting
    for _ in range(3):
        llm_matcher.resolve("407호로 가줘", LANDMARKS)
    llm_matcher._call_llm = real
    good = calls["n"] == 1
    print(f"  {OK if good else NG} 같은 말 3번 → 모델 호출 {calls['n']}번 (1번이어야 함)")

    print(f"\n{'=' * 62}")
    if _failed:
        print(f"{NG} 막힌 단계 {len(_failed)}개")
        for what, how in _failed:
            print(f"   · {what}\n     → {how}")
        return 1
    print(f"{OK} 전부 정상 — 로컬 AI 목적지 매칭을 쓸 수 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
