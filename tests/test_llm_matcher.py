"""LLM 목적지 매칭 테스트.

실제 모델 없이 돈다. 모델 호출부(_call_llm)를 가짜로 바꿔서
"모델이 이렇게 답했을 때 우리 코드가 제대로 처리하는가"만 본다.

    python tests/test_llm_matcher.py

여기서 확인하는 건 LLM의 언어 능력이 아니라 **방어 장치**다.
모델이 이상한 답을 냈을 때 그게 사용자에게 그대로 나가지 않아야 한다.

실제 모델까지 같이 보려면 Ollama를 띄우고 --live 를 붙인다.

    ollama serve && ollama pull exaone3.5:7.8b
    python tests/test_llm_matcher.py --live
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ws import llm_matcher  # noqa: E402
from app.ws.landmark_matcher import load_landmarks  # noqa: E402

# 실제로 쓸 이름 형태 (화1 → 화장실 1 로 바뀔 예정인 것을 반영)
LANDMARKS = load_landmarks([
    {"id": "L05", "name": "410"}, {"id": "L06", "name": "409(1)"},
    {"id": "L07", "name": "409(2)"}, {"id": "L11", "name": "407"},
    {"id": "L14", "name": "406"}, {"id": "L12", "name": "화장실 1"},
    {"id": "L13", "name": "화장실 2"}, {"id": "L02", "name": "엘리베이터 1"},
    {"id": "L03", "name": "엘리베이터 2"}, {"id": "L01", "name": "계단 1"},
    {"id": "L04", "name": "계단 2"}, {"id": "L24", "name": "계단 3"},
])


class FakeLLM:
    """정해둔 답을 돌려주는 가짜 모델."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def __call__(self, text, landmarks, mode="resolve"):
        self.calls += 1
        if isinstance(self.reply, dict):      # 단계별로 다른 답 (resolve / choose)
            return self.reply.get(mode)
        return self.reply(text) if callable(self.reply) else self.reply


def with_llm(reply):
    """_call_llm 을 가짜로 바꾸고 되돌리는 헬퍼."""
    fake = FakeLLM(reply)
    llm_matcher._call_llm = fake
    llm_matcher._cache.clear()
    llm_matcher._last_fail_at = 0.0
    return fake


CASES = [
    # (설명, 모델 응답, 발화, 기대 status, 기대 결과)
    (
        "정상 — 하나로 확정",
        '{"ids": ["L11"], "why": "방 번호 407"}',
        "407호로 가줘", "resolved", "407",
    ),
    (
        "동의어 — 규칙 엔진이 못 하던 것",
        '{"ids": ["L12", "L13"], "why": "변소=화장실"}',
        "변소 급해요", "ambiguous", ["화장실 1", "화장실 2"],
    ),
    (
        "동의어 — 승강기",
        '{"ids": ["L02", "L03"], "why": "승강기=엘리베이터"}',
        "승강기 어딨어요", "ambiguous", ["엘리베이터 1", "엘리베이터 2"],
    ),
    (
        "없는 곳 — 억지로 고르지 않음",
        '{"ids": [], "why": "목록에 없음"}',
        "옥상정원", "notFound", None,
    ),
    (
        "지어낸 id — 목록에 없으면 버린다",
        '{"ids": ["L99", "L11"], "why": "환각 섞임"}',
        "407", "resolved", "407",
    ),
    (
        "전부 지어냄 — 규칙 엔진으로 폴백",
        '{"ids": ["L99", "L98"], "why": "전부 환각"}',
        "407", "resolved", "407",
    ),
    (
        "번호 오답 — 407이라 했는데 406을 냄 → 버리고 규칙 결과",
        '{"ids": ["L14"], "why": "406"}',
        "407호", "resolved", "407",
    ),
    (
        "번호 오답 — 409라 했는데 410을 냄",
        '{"ids": ["L05"], "why": "410"}',
        "409호", "ambiguous", ["409(1)", "409(2)"],
    ),
    (
        "JSON 앞뒤에 잡소리를 붙인 경우",
        '네 알겠습니다. {"ids": ["L11"], "why": "407"} 이상입니다.',
        "407", "resolved", "407",
    ),
    (
        "JSON이 아예 깨짐 → 규칙 엔진으로 폴백",
        "죄송합니다 잘 모르겠어요",
        "407호", "resolved", "407",
    ),
    (
        "모델 서버가 죽음(None) → 규칙 엔진으로 폴백",
        None,
        "화장실", "ambiguous", ["화장실 1", "화장실 2"],
    ),
]


def main() -> int:
    live = "--live" in sys.argv
    fails: list[str] = []

    print("── 방어 장치 (가짜 모델) ──")
    for label, reply, text, want_status, want in CASES:
        with_llm(reply)
        r = llm_matcher.resolve(text, LANDMARKS)
        names = [c.name for c in r.candidates]
        if r.status != want_status:
            ok = False
        elif want_status == "resolved":
            ok = r.landmark is not None and r.landmark.name == want
        elif want_status == "ambiguous":
            ok = sorted(names) == sorted(want)
        else:
            ok = True
        if not ok:
            fails.append(f"{label}: {r.status}/{r.landmark.name if r.landmark else names}"
                         f" (기대 {want_status}/{want})")
        shown = r.landmark.name if r.landmark else (names or "-")
        print(f" {'✓' if ok else '✗'} {label}")
        print(f"     \"{text}\" → {r.status:10} {shown}")

    print("\n── 캐시 ──")
    fake = with_llm('{"ids": ["L11"], "why": "407"}')
    for _ in range(3):
        llm_matcher.resolve("407호로 가줘", LANDMARKS)
    ok = fake.calls == 1
    if not ok:
        fails.append(f"캐시: 3번 물었는데 모델을 {fake.calls}번 불렀다")
    print(f" {'✓' if ok else '✗'} 같은 말 3번 → 모델 호출 {fake.calls}번 (1번이어야 함)")

    print("\n── 되묻기도 LLM이 해석한다 ──")
    # 순서 표현("두 번째")을 코드의 사전으로 처리하지 않고 모델에게 넘긴다.
    # 되묻기 프롬프트는 후보만 담으므로, 두 단계에 다른 응답을 돌려주는 가짜를 쓴다.
    fake = with_llm({
        "resolve": '{"ids": ["L12","L13"], "why": "화장실"}',
        "choose":  '{"ids": ["L13"], "why": "순서 2"}',
    })
    r1 = llm_matcher.resolve("변소", LANDMARKS)
    before = fake.calls
    r2 = llm_matcher.choose("두 번째", r1.candidates)
    ok = (fake.calls == before + 1 and r2.landmark is not None
          and r2.landmark.name == "화장실 2" and r2.source == "llm")
    if not ok:
        fails.append(f"되묻기: 호출 {before}→{fake.calls}, 결과 "
                     f"{r2.landmark.name if r2.landmark else None} [{r2.source}]")
    print(f" {'✓' if ok else '✗'} \"변소\" → \"두 번째\" ⇒ "
          f"{r2.landmark.name if r2.landmark else '-'} "
          f"[{r2.source}] (추가 호출 {fake.calls - before}번)")

    print("\n── 되묻기에서 LLM이 실패하면 규칙 엔진이 받는다 ──")
    fake = with_llm({"resolve": '{"ids": ["L12","L13"], "why": "화장실"}',
                     "choose":  '말이 안 되는 응답'})
    r1 = llm_matcher.resolve("변소", LANDMARKS)
    r2 = llm_matcher.choose("두 번째", r1.candidates)
    ok = (r2.landmark is not None and r2.landmark.name == "화장실 2"
          and r2.source.startswith("llm→rule"))
    if not ok:
        fails.append(f"되묻기 폴백: {r2.landmark.name if r2.landmark else None} "
                     f"[{r2.source}]")
    print(f" {'✓' if ok else '✗'} 응답이 깨져도 ⇒ "
          f"{r2.landmark.name if r2.landmark else '-'} [{r2.source}]")

    if live:
        print(f"\n── 실제 모델 ({llm_matcher.PROVIDER}/{llm_matcher.MODEL}) ──")
        llm_matcher._call_llm = _REAL_CALL
        llm_matcher._cache.clear()
        llm_matcher._last_fail_at = 0.0
        if not llm_matcher.available():
            print(f"  모델 서버에 연결할 수 없습니다: {llm_matcher.BASE_URL}")
        else:
            for text in ["407호로 가줘", "변소 급해요", "승강기 어딨어요", "사백칠",
                         "화장실", "커피 마실 데", "옥상정원", "409"]:
                import time as _t
                t0 = _t.time()
                r = llm_matcher.resolve(text, LANDMARKS)
                dt = _t.time() - t0
                shown = r.landmark.name if r.landmark else [c.name for c in r.candidates]
                print(f"  {text!r:16} → {r.status:10} {str(shown):28} ({dt:.2f}초)")

    print("\n" + "=" * 60)
    if fails:
        print(f"실패 {len(fails)}")
        for f in fails:
            print("  ✗", f)
        return 1
    print(f"전체 {len(CASES) + 2}개 통과 ✓")
    return 0


_REAL_CALL = llm_matcher._call_llm

if __name__ == "__main__":
    raise SystemExit(main())
