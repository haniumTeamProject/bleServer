"""목적지 매칭을 **내 프로젝트 파일의 실제 랜드마크 이름**으로 직접 시험한다.

서버도 폰도 필요 없다. 매칭 부분만 떼어서 바로 쳐볼 수 있다.

    python tests/try_destination.py                 # 대화형 — 말할 문장을 직접 입력
    python tests/try_destination.py "용변 보러"        # 한 문장만
    python tests/try_destination.py --file 목록.txt   # 내가 적은 문장들을 한 번에
    python tests/try_destination.py --list           # 등록된 랜드마크 보기
    python tests/try_destination.py --prompt         # 모델에게 보내는 내용 전문
    python tests/try_destination.py --rule           # LLM 끄고 규칙 엔진만
    python tests/try_destination.py --trace "용변 보러"  # 모델과 주고받은 전부 보기

── 시험 문장을 코드에 넣어두지 않는다 ──────────────────────────────

예전에는 BATCH·GENERALIZE 라는 목록에 시험 문장과 기대 답을 미리 적어뒀다.
그러면 두 가지가 망가진다.

  1. 그 목록에 있는 표현만 계속 확인하게 되어, 실제로 사람들이 어떻게 말하는지는
     끝내 알 수 없다.
  2. 더 나쁘게는, 그 표현이 프롬프트에도 들어가 있으면 "되네" 하고 확인한 것이
     모델의 이해가 아니라 프롬프트를 베낀 것이 된다(실제로 그런 적이 있다).

그래서 목록을 없앴다. 시험 문장은 **직접 말해보고 싶은 것을 그때그때 입력**하거나,
`--file` 로 자기가 적은 파일을 넘긴다. 무엇을 시험할지는 코드가 아니라 사람이 정한다.

── 결과 읽는 법 ──────────────────────────────────────────────────

    source=llm              LLM이 판단했다
    source=rule             LLM을 아예 안 썼다 (LLM_PROVIDER=off)
    source=llm→rule(사유)   LLM을 부르려다 실패해서 규칙 엔진으로 넘어갔다

`--prompt` 로 모델에게 무엇을 알려주는지 직접 확인할 수 있다.
시험하려는 표현이 거기 이미 있으면 그 확인은 의미가 없다.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ws import landmark_matcher, llm_matcher  # noqa: E402

# 서버(handler.py)와 같은 순서로 프로젝트 파일을 찾는다
_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATES = [
    Path(os.environ["MAP_TOOL_DIR"]) / "static" / "mappin_project.json"
    if os.environ.get("MAP_TOOL_DIR") else None,
    _ROOT / "map-tool" / "static" / "mappin_project.json",
    _ROOT / "backend-python" / "map-tool" / "static" / "mappin_project.json",
]


def find_project() -> Path | None:
    for p in _CANDIDATES:
        if p and p.is_file():
            return p
    return None


def load() -> tuple[list, Path | None]:
    path = find_project()
    if not path:
        print("프로젝트 파일을 찾지 못했습니다. 찾아본 곳:")
        for p in _CANDIDATES:
            if p:
                print(f"   {p}")
        print("\nMAP_TOOL_DIR 환경변수로 직접 지정할 수 있습니다.")
        return [], None
    data = json.loads(path.read_text(encoding="utf-8"))
    return landmark_matcher.load_landmarks(data.get("landmarks") or []), path


def show_landmarks(lms: list, path: Path) -> None:
    print(f"프로젝트 파일: {path}")
    print(f"랜드마크 {len(lms)}개 — LLM에게는 아래 목록 그대로 보여준다\n")
    for lm in lms:
        kind = "방 번호" if lm.is_room else f"종류 '{lm.prefix}' + {lm.number}번"
        print(f"   {lm.id:>5}  {lm.name:<12} → {kind}")


def one_line(text: str, lms: list) -> None:
    """한 줄짜리 요약 — 여러 문장을 훑을 때."""
    t0 = time.time()
    r = llm_matcher.resolve(text, lms)
    dt = time.time() - t0
    got = r.landmark.name if r.landmark else [c.name for c in r.candidates]
    print(f"  {text!r:20} → {r.status:10} {str(got):28} [{r.source}] ({dt:.2f}초)")


def detail(text: str, lms: list, interactive: bool = True) -> None:
    t0 = time.time()
    r = llm_matcher.resolve(text, lms)
    dt = time.time() - t0
    got = r.landmark.name if r.landmark else [c.name for c in r.candidates]
    print(f"\n  입력   {text!r}")
    print(f"  결과   {r.status}  →  {got}")
    print(f"  안내   \"{r.speech}\"")
    print(f"  엔진   {r.source}   ({dt:.2f}초)")

    if interactive and r.status == "ambiguous":
        pick = input("  되묻기 답변 (엔터로 건너뛰기) > ").strip()
        if pick:
            r2 = llm_matcher.choose(pick, r.candidates)
            got2 = r2.landmark.name if r2.landmark else "-"
            print(f"  → {r2.status}  {got2}   \"{r2.speech}\"")


def _kv(label: str, value) -> None:
    print(f"  {label:<14}{value}")


def trace(text: str, lms: list) -> None:
    """한 번의 매칭에서 오간 값을 전부 출력한다.

    모델 원문과 생성 통계가 포함되므로, 규칙 엔진이 답한 경우와 구분된다.
    """
    llm_matcher._cache.clear()
    llm_matcher._last_fail_at = 0.0
    llm_matcher.last_call.clear()

    print(f"\n[REQUEST]")
    _kv("provider", llm_matcher.PROVIDER)
    _kv("model", llm_matcher.MODEL)
    _kv("endpoint", f"{llm_matcher.BASE_URL}/api/chat")
    _kv("timeout", f"{llm_matcher.TIMEOUT_S:g} s")
    _kv("landmarks", len(lms))
    _kv("input", repr(text))

    print(f"\n[PROMPT] 마지막 메시지")
    for line in llm_matcher._build_user_prompt(text, lms).splitlines():
        print(f"  {line}")

    t0 = time.time()
    r = llm_matcher.resolve(text, lms)
    dt = time.time() - t0
    call = dict(llm_matcher.last_call)

    print(f"\n[RESPONSE]")
    if call:
        _kv("raw", call.get("모델_원문"))
        _kv("model", call.get("모델"))
        _kv("elapsed", f"{call.get('걸린시간초')} s")
        _kv("prompt_tokens", call.get("입력토큰"))
        _kv("eval_tokens", call.get("생성토큰"))
        if call.get("생성속도"):
            _kv("tokens/s", call.get("생성속도"))
        if call.get("적재시간초"):
            _kv("load", f"{call.get('적재시간초')} s")
    else:
        _kv("raw", "-")
        _kv("note", "모델 호출 없음")

    print(f"\n[PARSE]")
    known = {lm.id: lm.name for lm in lms}
    if call and call.get("모델_원문"):
        ids = llm_matcher._parse_ids(call["모델_원문"], lms)
        if ids is None:
            _kv("ids", "- (형식 오류 또는 전부 목록 밖)")
        else:
            _kv("ids", ids)
            _kv("names", [known[i] for i in ids])
    else:
        _kv("ids", "-")
    _kv("status", r.status)
    _kv("source", r.source)

    print(f"\n[OUTPUT]")
    got = r.landmark.name if r.landmark else [c.name for c in r.candidates]
    _kv("landmark", got)
    _kv("speech", r.speech)
    _kv("total", f"{dt:.2f} s")


def main() -> int:
    args = list(sys.argv[1:])

    if "--rule" in args:
        llm_matcher.PROVIDER = "off"
        args.remove("--rule")
        print("※ LLM을 끄고 규칙 엔진만 씁니다\n")

    lms, path = load()
    if not lms:
        return 1

    if "--list" in args:
        show_landmarks(lms, path)
        return 0

    if "--prompt" in args:
        args.remove("--prompt")
        sample = " ".join(args) or "여기에 말할 문장"
        print(f"모델에게 실제로 보내는 내용 (입력: {sample!r})\n")
        print("=" * 66)
        print(llm_matcher.dump_prompt(sample, lms))
        print("=" * 66)
        print("\n※ 시험하려는 표현이 위에 이미 있으면, 그건 모델의 이해가 아니라")
        print("   프롬프트를 베낀 것이므로 확인의 의미가 없다.")
        return 0

    print(f"랜드마크 {len(lms)}개 로드: {path.name}")
    print(f"이름: {', '.join(lm.name for lm in lms)}")
    if llm_matcher.PROVIDER != "off":
        print(f"LLM: {llm_matcher.PROVIDER}/{llm_matcher.MODEL} @ {llm_matcher.BASE_URL}")

    if "--file" in args:
        i = args.index("--file")
        try:
            src = Path(args[i + 1])
        except IndexError:
            print("--file 뒤에 파일 경로가 필요합니다.")
            return 1
        if not src.is_file():
            print(f"파일이 없습니다: {src}")
            return 1
        lines = [ln.strip() for ln in src.read_text(encoding="utf-8").splitlines()]
        lines = [ln for ln in lines if ln and not ln.startswith("#")]
        print(f"\n{src} 에서 {len(lines)}줄 읽음\n")
        for ln in lines:
            one_line(ln, lms)
        return 0

    if "--trace" in args:
        args.remove("--trace")
        trace(" ".join(args) or "여기에 말할 문장", lms)
        return 0

    if args:                       # 문장을 인자로 준 경우
        detail(" ".join(args), lms)
        return 0

    print("\n말할 문장을 입력하세요. 빈 줄이나 Ctrl+C 로 종료.")
    while True:
        try:
            text = input("\n목적지 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            return 0
        detail(text, lms)


if __name__ == "__main__":
    raise SystemExit(main())
