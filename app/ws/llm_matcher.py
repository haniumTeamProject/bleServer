"""LLM으로 음성 목적지를 랜드마크에 잇는다.

규칙 기반(landmark_matcher.py)의 한계가 명확해서 만들었다.

    잘 되던 것    "407호로 가줘" → 407,  "사백칠" → 407,  "409" → 409(1)/409(2)
    안 되던 것    "변소" → ?,  "승강기" → ?,  "커피 마실 데" → ?

동의어와 자연스러운 문장은 사전을 아무리 늘려도 끝이 없다. 게다가 사전은
건물마다 달라서 코드에 박아두면 다른 건물에서 오히려 방해가 된다
(실제로 이름을 "화1"에서 "화장실 1"로 바꾸자 기존 별칭 사전이 매칭을 망가뜨렸다).

── 설계에서 신경 쓴 것 ─────────────────────────────────────────────

**출력을 목록 안으로 제약한다.** LLM이 자유 텍스트를 내면 없는 장소를 지어낼 수
있다. 그래서 랜드마크 id 목록을 주고 그중에서만 고르게 하고, 돌아온 id가 목록에
없으면 버린다. 지어내기를 프롬프트가 아니라 구조로 막는다.

**번호를 검증한다.** 사용자가 "407"이라고 분명히 말했는데 LLM이 406을 고르면
시각장애인이 잘못된 문 앞에 서게 된다. 발화에 방 번호가 뚜렷하게 있으면
결과의 번호와 대조해서, 다르면 LLM 답을 버리고 규칙 결과를 쓴다.
이건 판정을 두 번 하는 게 아니라 출력 검증이다.

**LLM이 없어도 돌아간다.** Ollama가 안 떠 있거나 응답이 늦으면 규칙 엔진으로
넘어간다. 팀원이 모델을 안 깔아도 저장소가 그대로 동작해야 하고,
데모 중 네트워크가 끊겨도 핵심 기능은 살아 있어야 한다.

── 환경변수 ────────────────────────────────────────────────────────

    LLM_PROVIDER   ollama | openai | off      (기본 ollama)
    LLM_MODEL      기본 exaone3.5:7.8b
    LLM_BASE_URL   기본 http://localhost:11434
    LLM_API_KEY    openai 계열일 때만 필요
    LLM_TIMEOUT    초. 기본 6
    LLM_VERIFY_NUMBER  1이면 번호 검증 켬 (기본 1)

── 모델이 맡는 것과 코드가 맡는 것 ──────────────────────────────

목적지 해석도, 되물은 뒤의 대답 해석도 모델이 한다. 순서 표현("두 번째",
"3번째")이나 동의어를 코드의 사전으로 처리하면 사전에 없는 말투에서 깨지는데,
사람의 말투는 끝이 없어서 사전을 늘려도 따라잡지 못한다.

코드에 남은 표(landmark_matcher 의 한글 수사)는 **판단이 아니라 검증**에 쓴다.
사용자가 "사백칠"이라고 말했는데 모델이 406을 고르면 시각장애인이 잘못된 문
앞에 선다. 이걸 잡으려면 모델과 무관한 수단이어야 한다 —
모델에게 자기 답이 맞는지 물어보는 건 검증이 아니다.

로컬 실행 예:

    ollama pull exaone3.5:7.8b      # LG EXAONE — 한국어 특화
    ollama serve
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from app.ws.landmark_matcher import Landmark, MatchResult, _arrive_speech
from app.ws.landmark_matcher import korean_numbers, normalize
from app.ws import landmark_matcher

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
PROVIDER = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()
MODEL = os.environ.get("LLM_MODEL", "exaone3.5:7.8b").strip()
# Ollama 기본 포트가 11434 다. 한동안 코드만 11435 로 되어 있어서, 문서대로
# `ollama serve` 만 하고 서버를 띄우면 **매칭이 조용히 규칙 엔진으로 떨어졌다.**
# 방 번호는 되고 동의어("변소")만 안 되는 형태라 눈치채기 어렵다.
BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434").rstrip("/")
API_KEY = os.environ.get("LLM_API_KEY", "").strip()
TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT", "6"))
VERIFY_NUMBER = os.environ.get("LLM_VERIFY_NUMBER", "1") != "0"

# 모델을 메모리에 붙잡아 두는 시간. Ollama 기본값이 5분이라 그냥 두면
# 쉬는 동안 내려갔다가 다음 요청에서 다시 올리느라 10초 넘게 걸린다.
KEEP_ALIVE = os.environ.get("LLM_KEEP_ALIVE", "30m")

# 첫 요청은 모델을 올리는 시간이 얹혀서 훨씬 오래 걸린다. 그래서 서버가 뜰 때
# 미리 한 번 깨워둔다. 이때만 타임아웃을 넉넉히 준다.
WARMUP_TIMEOUT_S = float(os.environ.get("LLM_WARMUP_TIMEOUT", "120"))

# 1이면 호출할 때마다 주고받은 내용을 그대로 찍는다.
# "LLM이 진짜 도는 건가"를 눈으로 확인해야 할 때 쓴다.
DEBUG = os.environ.get("LLM_DEBUG", "0") != "0"

# 마지막 호출의 원문과 통계. 추적 도구가 읽어간다.
# Ollama 는 응답에 실제 생성 통계를 담아주므로(eval_count, eval_duration 등)
# 그 값이 있다는 것 자체가 모델이 정말 돌았다는 증거가 된다.
last_call: dict = {}

# 같은 말을 반복하는 경우가 많아서 캐시한다. 목적지 종류가 몇십 개뿐이라
# 금방 대부분이 캐시에 들어가고, 그때부터는 지연이 사라진다.
_CACHE_MAX = 500
_cache: dict[tuple[str, str], list[str]] = {}

# LLM이 계속 실패하면 매번 타임아웃을 기다리지 않도록 잠시 쉰다.
_FAIL_COOLDOWN_S = 30.0
_last_fail_at = 0.0


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------
_SYSTEM = """너는 실내 길안내 시스템의 목적지 해석기다.
시각장애인이 음성으로 말한 목적지를, 주어진 장소 목록에서 찾아 id로 답한다.

규칙:
1. 반드시 주어진 목록에 있는 id만 답한다. 목록에 없는 id를 지어내면 안 된다.
2. 사용자가 방 번호를 말했으면 그 번호와 정확히 같은 곳만 고른다.
   201과 202는 완전히 다른 곳이다. 비슷하다고 고르면 안 된다.
3. 어느 것인지 좁혀지지 않으면 해당하는 것을 전부 답한다.
   예) "도서관"이라고만 했는데 도서관이 2곳이면 둘 다 답한다.
4. 목록에 없는 곳을 말했으면 빈 배열로 답한다. 억지로 고르지 않는다.
5. 사람이 쓰는 다른 말과 돌려 말한 표현도 알아듣는다.
   목록의 이름과 글자가 달라도 같은 곳을 가리키면 고른다.

반드시 아래 JSON 형식으로만 답한다. 다른 말은 쓰지 않는다.
{"ids": ["id1", "id2"], "why": "짧은 이유"}"""

# few-shot 은 **출력 형식만** 가르친다.
#
# 예전에는 여기에 "변소 급해요 → 화장실" 같은 예시를 넣었는데, 그러면
# 그 표현이 정답과 함께 프롬프트에 들어가버려서 "변소가 되네" 하는 확인이
# 아무 의미가 없어진다. 모델의 언어 이해가 아니라 프롬프트를 베낀 것이기 때문이다.
#
# 그래서 실제 건물에 없는 가상 이름(도서관·201호)만 쓴다.
# 동의어를 하나도 안 가르치므로, 동의어가 맞으면 그건 모델이 원래 아는 것이다.
#
# id 는 실제 요청과 **같은 모양(L1, L2 …)** 을 쓴다. 예전에는 여기만 `A1` 이고
# 실제로는 UUID 를 보내서, 모델이 배운 것과 다른 일을 하게 돼 있었다.
_DEMO = ('장소 목록:\n- L1: 201\n- L2: 202\n'
         '- L3: 도서관 1\n- L4: 도서관 2\n\n사용자: ')

_FEWSHOT = [
    (_DEMO + '"201호로 가줘"',
     '{"ids": ["L1"], "why": "방 번호 201"}'),
    (_DEMO + '"도서관"',
     '{"ids": ["L3", "L4"], "why": "2곳이라 좁혀지지 않음"}'),
    (_DEMO + '"수영장"',
     '{"ids": [], "why": "목록에 없음"}'),
]


# 되묻기 대답 해석용. 순서 표현 사전을 코드에 두지 않기 위해 이것도 모델에 맡긴다.
_CHOOSE_SYSTEM = """여러 곳을 순서대로 읽어주고 하나를 고르라고 물었다.
사용자의 대답이 어느 것을 가리키는지 판단해 id 하나로 답한다.

규칙:
1. 반드시 주어진 후보의 id 중 하나만 답한다. 없는 id를 지어내면 안 된다.
2. 이름으로 말했으면 그 이름의 후보를 고른다.
3. 순서로 말했으면 읽어준 차례대로 센다.
4. 어느 것인지 알 수 없으면 빈 배열로 답한다. 억지로 고르지 않는다.

반드시 아래 JSON 형식으로만 답한다. 다른 말은 쓰지 않는다.
{"ids": ["id"], "why": "짧은 이유"}"""

_CHOOSE_DEMO = ('후보:\n1) L1: 도서관 1\n2) L2: 자료실\n3) L3: 도서관 2\n\n대답: ')

_CHOOSE_FEWSHOT = [
    (_CHOOSE_DEMO + '"자료실"', '{"ids": ["L2"], "why": "이름으로 지목"}'),
    (_CHOOSE_DEMO + '"세 번째"', '{"ids": ["L3"], "why": "순서 3"}'),
    (_CHOOSE_DEMO + '"글쎄"', '{"ids": [], "why": "무엇을 고른 것인지 알 수 없음"}'),
]


# ---------------------------------------------------------------------------
# 모델에게 주는 id 는 짧은 번호다
#
# DB 의 랜드마크 id 는 UUID 다. 그걸 그대로 목록에 실어 보내면 모델이 답을 낼 때
# **36자짜리 16진 문자열을 옮겨 적어야 한다.** 스무 줄이 전부 비슷하게 생겼으니
# 한 줄 어긋나게 집기 딱 좋고, 실제로 그렇게 틀렸다:
#
#     "411호로 가죠"  →  410호        ← 바로 윗줄
#
# 게다가 few-shot 은 `A1`, `A2` 같은 짧은 id 로 형식을 가르쳐 놓고 정작 실제
# 요청에서는 UUID 를 주고 있었다. 모델 입장에서는 배운 것과 다른 일을 시킨 셈이다.
#
# 그래서 여기서 `L1`, `L2` … 로 갈아 끼우고 돌아온 답을 다시 UUID 로 되돌린다.
# 프롬프트 길이도 크게 줄어서(줄당 36자 → 2~3자) 응답이 그만큼 빨라진다.
#
# **번호는 목록 순서일 뿐 의미가 없다.** 목록이 바뀌면 같은 곳이라도 번호가
# 달라진다 — 그래서 이 번호는 밖으로 절대 나가지 않는다. 캐시 키도 UUID 로 만든다.
# ---------------------------------------------------------------------------
def _alias(landmarks: list[Landmark]) -> dict[str, Landmark]:
    """`{"L1": 랜드마크, ...}` — 프롬프트와 응답 해석이 **같이** 쓰는 표.

    번호가 진짜 id 와 겹치면 접두사를 바꾼다. 겹친 채로 두면 모델이 `L11` 을
    냈을 때 그것이 "목록 11번째"인지 "id 가 L11 인 곳"인지 알 수 없고, 둘이
    다른 장소면 **조용히 엉뚱한 곳으로 안내한다.** 구조로 막을 수 있는 것을
    확률에 맡기지 않는다.
    """
    real = {lm.id for lm in landmarks}
    prefix = "L"
    while any(f"{prefix}{i + 1}" in real for i in range(len(landmarks))):
        prefix = "#" + prefix
    return {f"{prefix}{i + 1}": lm for i, lm in enumerate(landmarks)}


def _build_choose_prompt(text: str, candidates: list[Landmark]) -> str:
    listing = "\n".join(f"{i + 1}) {key}: {lm.name}"
                        for i, (key, lm) in enumerate(_alias(candidates).items()))
    return f'후보:\n{listing}\n\n대답: "{text}"'


def _build_user_prompt(text: str, landmarks: list[Landmark]) -> str:
    listing = "\n".join(f"- {key}: {lm.name}"
                        for key, lm in _alias(landmarks).items())
    return f'장소 목록:\n{listing}\n\n사용자: "{text}"'


# ---------------------------------------------------------------------------
# 모델 호출
# ---------------------------------------------------------------------------
def _post_json(url: str, body: dict, headers: dict | None = None,
               timeout: float | None = None) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout or TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _messages(text: str, landmarks: list[Landmark], mode: str = "resolve") -> list[dict]:
    if mode == "choose":
        system, shots, last = (_CHOOSE_SYSTEM, _CHOOSE_FEWSHOT,
                               _build_choose_prompt(text, landmarks))
    else:
        system, shots, last = _SYSTEM, _FEWSHOT, _build_user_prompt(text, landmarks)
    msgs = [{"role": "system", "content": system}]
    for user, assistant in shots:
        msgs.append({"role": "user", "content": user})
        msgs.append({"role": "assistant", "content": assistant})
    msgs.append({"role": "user", "content": last})
    return msgs


def dump_prompt(text: str, landmarks: list[Landmark]) -> str:
    """모델에게 실제로 보내는 내용을 그대로 돌려준다.

    "이 표현이 프롬프트에 들어있는 것 아니냐"를 눈으로 확인할 수 있어야 한다.
    실제로 예전 프롬프트에는 "변소 급해요 → 화장실" 예시가 들어 있었고,
    그 상태로 "변소가 되네" 하고 확인하는 것은 아무 의미가 없었다.
    """
    out = []
    for m in _messages(text, landmarks):
        out.append(f"[{m['role']}]\n{m['content']}")
    return "\n\n".join(out)


def _record(text: str, msgs: list[dict], raw_response: dict,
            content: str | None, elapsed: float) -> None:
    """방금 주고받은 것을 남긴다. 사람이 확인할 수 있어야 한다."""
    ns = 1_000_000_000
    last_call.clear()
    last_call.update({
        "질문": text,
        "보낸_마지막_메시지": msgs[-1]["content"] if msgs else "",
        "메시지_수": len(msgs),
        "모델_원문": content,
        "모델": raw_response.get("model", MODEL),
        "걸린시간초": round(elapsed, 2),
        # Ollama 가 돌려주는 실제 생성 통계
        "입력토큰": raw_response.get("prompt_eval_count"),
        "생성토큰": raw_response.get("eval_count"),
        "적재시간초": round(raw_response.get("load_duration", 0) / ns, 2)
        if raw_response.get("load_duration") else None,
        "생성속도": (round(raw_response["eval_count"] /
                        (raw_response["eval_duration"] / ns), 1)
                   if raw_response.get("eval_duration") and raw_response.get("eval_count")
                   else None),
    })
    if DEBUG:
        print(f"\n[LLM] 질문 {text!r}")
        print(f"[LLM] 모델 {last_call['모델']} · {last_call['걸린시간초']}초 "
              f"· 입력 {last_call['입력토큰']}토큰 · 생성 {last_call['생성토큰']}토큰"
              + (f" · {last_call['생성속도']}토큰/초" if last_call["생성속도"] else ""))
        print(f"[LLM] 원문 응답: {content!r}")


def _call_llm(text: str, landmarks: list[Landmark], mode: str = "resolve") -> str | None:
    """모델을 불러 응답 문자열을 받는다. 실패하면 None."""
    msgs = _messages(text, landmarks, mode)
    try:
        t0 = time.time()
        if PROVIDER == "ollama":
            out = _post_json(
                f"{BASE_URL}/api/chat",
                {
                    "model": MODEL,
                    "messages": msgs,
                    "stream": False,
                    "format": "json",          # 모델이 JSON만 뱉도록 강제
                    "options": {"temperature": 0},   # 매번 같은 답이 나오게
                    # 모델을 메모리에 붙잡아 둔다. 기본값은 5분이라, 잠깐 안 쓰면
                    # 내려갔다가 다음 요청에서 다시 올리느라 10초 넘게 걸린다.
                    # 안내가 그만큼 늦어지면 쓸 수 없다.
                    "keep_alive": KEEP_ALIVE,
                },
            )
            content = (out.get("message") or {}).get("content")
            _record(text, msgs, out, content, time.time() - t0)
            return content

        if PROVIDER == "openai":
            headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
            out = _post_json(
                f"{BASE_URL}/v1/chat/completions",
                {
                    "model": MODEL,
                    "messages": msgs,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
                headers,
            )
            content = out["choices"][0]["message"]["content"]
            _record(text, msgs, out, content, time.time() - t0)
            return content

    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        print(f"[목적지] LLM 호출 실패({PROVIDER}/{MODEL}): {e}")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        print(f"[목적지] LLM 응답 형식이 예상과 다름: {e}")
    return None


# ---------------------------------------------------------------------------
# 응답 해석과 검증
# ---------------------------------------------------------------------------
def _parse_ids(raw: str, landmarks: list[Landmark]) -> list[str] | None:
    """LLM 응답에서 id 목록을 뽑아 **진짜 랜드마크 id 로 되돌린다.**

    모델에게는 `L1`, `L2` 로 줬으므로 여기서 UUID 로 바꾼다. 목록에 없는 것은
    버린다 — 지어내기를 프롬프트가 아니라 구조로 막는 자리다.
    """
    if not raw:
        return None

    # format=json 을 줘도 앞뒤에 설명을 붙이는 모델이 있어서 JSON 부분만 잘라낸다
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None

    ids = data.get("ids")
    if not isinstance(ids, list):
        return None

    # `L3` → 진짜 id. 모델이 UUID 를 그대로 냈다면(있을 수 없지만) 그것도 받는다.
    alias = _alias(landmarks)
    real = {lm.id for lm in landmarks}
    clean, dropped = [], []
    for i in ids:
        i = str(i).strip()
        hit = alias[i].id if i in alias else (i if i in real else None)
        if hit is not None:
            if hit not in clean:
                clean.append(hit)
        elif i:
            dropped.append(i)

    if dropped:
        # 지어낸 id — 구조로 걸러졌다는 뜻이라 로그로 남겨둔다
        print(f"[목적지] LLM이 목록에 없는 id를 냄, 무시: {dropped}")

    if not clean and dropped:
        # 낸 게 전부 지어낸 것이었다. 이건 "그런 곳이 없다"는 답이 아니라
        # 응답을 통째로 못 믿는다는 뜻이므로 규칙 엔진으로 넘긴다.
        # (빈 배열 [] 을 낸 것과 반드시 구분해야 한다)
        return None

    return clean


def _number_conflict(text: str, picked: list[Landmark]) -> bool:
    """발화에 방 번호가 뚜렷한데 결과가 그 번호와 다르면 True.

    사용자가 "407"이라고 분명히 말했는데 406이 나오는 상황을 막는다.
    LLM 판정을 못 믿어서가 아니라, 틀렸을 때의 대가가 커서 두는 안전장치다.
    """
    if not VERIFY_NUMBER or not picked:
        return False

    norm = normalize(text)
    spoken: set[int] = {int(d) for d in re.findall(r"\d{3,4}", norm)}
    spoken |= {n for n in korean_numbers(norm) if n >= 100}
    if not spoken:
        return False   # 번호를 말한 게 아니면 검증할 게 없다

    for lm in picked:
        if lm.number is not None and lm.number in spoken:
            return False   # 하나라도 맞으면 통과
    return True


# ---------------------------------------------------------------------------
# 공개 함수
# ---------------------------------------------------------------------------
def warmup() -> None:
    """모델을 미리 메모리에 올려둔다. 서버가 뜰 때 백그라운드로 한 번 부른다.

    안 하면 **첫 사용자가 6초 타임아웃에 걸린다.** 모델을 디스크에서 올리는 데
    수십 초가 걸리는데 평소 타임아웃은 6초라, 첫 요청은 무조건 실패하고
    규칙 엔진으로 떨어진다. 데모에서 하필 첫 번째 시연이 그렇게 된다.

    스레드로 돌리는 이유: 모델 적재가 1~2분 걸릴 수 있어서 서버 기동을 막으면 안 된다.
    """
    if PROVIDER == "off":
        return

    def run():
        import time as _t
        t0 = _t.time()
        try:
            if PROVIDER == "ollama":
                _post_json(f"{BASE_URL}/api/chat",
                           {"model": MODEL,
                            "messages": [{"role": "user", "content": "안녕"}],
                            "stream": False, "keep_alive": KEEP_ALIVE},
                           timeout=WARMUP_TIMEOUT_S)
            else:
                return   # 클라우드 API는 적재 시간이 없다
            print(f"[목적지] LLM 준비 완료: {MODEL} ({_t.time() - t0:.1f}초)")
        except Exception as e:
            print(f"[목적지] LLM 예열 실패({PROVIDER}/{MODEL}): {e}")
            print("[목적지] 규칙 엔진으로 동작합니다. Ollama가 떠 있는지 확인하세요: "
                  f"{BASE_URL}")

    threading.Thread(target=run, name="llm-warmup", daemon=True).start()


def available() -> bool:
    """모델 서버가 응답하는지. handler가 시작할 때 한 번 확인하는 용도."""
    if PROVIDER == "off":
        return False
    try:
        url = f"{BASE_URL}/api/tags" if PROVIDER == "ollama" else f"{BASE_URL}/v1/models"
        req = urllib.request.Request(url)
        if API_KEY:
            req.add_header("Authorization", f"Bearer {API_KEY}")
        with urllib.request.urlopen(req, timeout=min(TIMEOUT_S, 3)):
            return True
    except Exception:
        return False


def resolve(text: str, landmarks: list[Landmark]) -> MatchResult:
    """LLM으로 목적지를 해석한다. 실패하면 규칙 엔진 결과를 그대로 돌려준다.

    반환 형식은 landmark_matcher.resolve() 와 같아서 handler는 구분하지 않아도 된다.
    """
    global _last_fail_at

    def fallback(reason: str) -> MatchResult:
        r = landmark_matcher.resolve(text, landmarks)
        r.source = f"llm→rule({reason})"
        return r

    rule_result = landmark_matcher.resolve(text, landmarks)

    if PROVIDER == "off" or not text.strip() or not landmarks:
        rule_result.source = "rule"
        return rule_result

    # 최근에 계속 실패했으면 잠시 건너뛴다 (매번 타임아웃을 기다리지 않도록)
    if time.time() - _last_fail_at < _FAIL_COOLDOWN_S:
        return fallback("최근 실패로 대기 중")

    key = (normalize(text), ",".join(lm.id for lm in landmarks))
    ids = _cache.get(key)

    if ids is None:
        raw = _call_llm(text, landmarks)
        if raw is None:
            _last_fail_at = time.time()
            print("[목적지] LLM을 못 써서 규칙 엔진으로 처리합니다.")
            return fallback("호출 실패")
        ids = _parse_ids(raw, landmarks)
        if ids is None:
            print(f"[목적지] LLM 응답을 해석 못 함, 규칙 엔진으로 처리: {raw[:120]!r}")
            return fallback("응답 해석 실패")
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = ids

    by_id = {lm.id: lm for lm in landmarks}
    picked = [by_id[i] for i in ids]

    if _number_conflict(text, picked):
        names = ", ".join(lm.name for lm in picked)
        print(f"[목적지] LLM 결과가 말한 번호와 안 맞아 버림: \"{text}\" → {names}")
        return fallback("번호 불일치")

    if not picked:
        return MatchResult(
            status="notFound", query=text,
            speech="목적지를 알아듣지 못했습니다. 다시 말씀해 주세요.",
            source="llm",
        )

    if len(picked) == 1:
        return MatchResult(status="resolved", query=text, landmark=picked[0],
                           speech=_arrive_speech(picked[0]), source="llm")

    # 후보가 여러 종류일 수 있으므로(계단·엘리베이터가 함께 나오는 등)
    # 첫 후보의 종류로 묶지 않고 이름을 그대로 순서대로 읽어준다.
    return MatchResult(
        status="ambiguous", query=text, candidates=picked,
        speech=landmark_matcher.ambiguous_speech(picked),
        source="llm",
    )


def choose(text: str, candidates: list[Landmark]) -> MatchResult:
    """되물은 뒤의 대답도 모델이 해석한다.

    순서 표현("두 번째", "3번째")을 코드의 사전으로 처리하면 사전에 없는 말투에서
    깨진다. 후보 목록과 대답을 그대로 주고 모델이 고르게 한다.

    후보가 이미 좁혀져 있어 선택지가 몇 개뿐이므로, 출력 검증만 하면
    엉뚱한 곳으로 갈 위험이 없다.
    """
    global _last_fail_at

    if not candidates:
        return landmark_matcher.choose(text, candidates)

    def fallback(reason: str) -> MatchResult:
        r = landmark_matcher.choose(text, candidates)
        r.source = f"llm→rule({reason})"
        return r

    if PROVIDER == "off" or not text.strip():
        r = landmark_matcher.choose(text, candidates)
        r.source = "rule"
        return r

    if time.time() - _last_fail_at < _FAIL_COOLDOWN_S:
        return fallback("최근 실패로 대기 중")

    raw = _call_llm(text, candidates, mode="choose")
    if raw is None:
        _last_fail_at = time.time()
        return fallback("호출 실패")

    ids = _parse_ids(raw, candidates)
    if ids is None:
        return fallback("응답 해석 실패")

    if len(ids) == 1:
        picked = next(c for c in candidates if c.id == ids[0])
        return MatchResult(status="resolved", query=text, landmark=picked,
                           speech=_arrive_speech(picked), source="llm")

    # 못 골랐거나 여러 개를 골랐으면 다시 묻는다
    return MatchResult(
        status="ambiguous", query=text, candidates=candidates,
        speech="잘 못 들었습니다. " + landmark_matcher.ambiguous_speech(candidates),
        source="llm",
    )
