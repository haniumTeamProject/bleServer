"""음성으로 말한 목적지를 실제 랜드마크에 이어주는 매칭 엔진.

사용자는 랜드마크 이름을 정확히 말해주지 않는다.

    데이터에 있는 이름          사용자가 말할 법한 것
    ────────────────────────────────────────────────────
    407                        "407호", "사백칠", "사공칠", "407호로 가줘"
    화1 / 화2                   "화장실", "변소", "두 번째 화장실"
    엘베1 / 엘베2               "엘리베이터", "승강기"
    계단1 / 계단2 / 계단3        "계단", "비상계단"
    409(1) / 409(2)            "409"

순수 문자열 유사도로는 "화장실"과 "화1"을 절대 못 잇는다(겹치는 글자가 한 자뿐).
그래서 3단으로 처리한다.

    1단 정리     소문자화, 공백·문장부호 제거, 숫자 뒤 "호/번/실" 제거
    2단 숫자     아라비아 숫자와 한글 수사를 뽑아낸다 (방 번호 정확 일치용)
    3단 유사도   이름이 발화 어디에 있든 찾는 **부분 유사도**로 비교

말투(조사·서술어)를 목록으로 벗겨내지 않는다. 목록 밖의 말투에서 오히려
결과가 나빠지기 때문이다. 부분 유사도가 그 역할을 대신한다.

핵심은 랜드마크 이름을 (접두사, 숫자, 변형) 으로 쪼개는 것이다.

    "407"     → 접두사 ""    숫자 407  변형 None   ← 접두사가 없으면 '방 번호'
    "409(1)"  → 접두사 ""    숫자 409  변형 1
    "화1"     → 접두사 "화"   숫자 1    변형 None   ← 접두사가 있으면 '종류 + 몇 번째'
    "계단2"   → 접두사 "계단"  숫자 2    변형 None

이 구분이 중요한 이유:

  * 방 번호는 **한 자리만 달라도 완전히 다른 곳**이다. 407과 406을 유사도로
    이어주면 위험하다. 그래서 접두사가 비어 있으면 숫자가 정확히 같아야 한다.
  * 반대로 "화1"의 1은 방 번호가 아니라 그냥 순번이다. 사용자가 "화장실"이라고만
    해도 후보로 올려야 하고, 여러 개면 몇 번째인지 되물으면 된다.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 별칭 사전은 없다 — 일부러 뺐다
# ---------------------------------------------------------------------------
# 예전에는 여기에 {"화" ← 화장실·변소·…} 같은 표를 손으로 적어뒀다.
# 랜드마크 이름이 "화1", "엘베1" 처럼 줄임말이라 문자 유사도로는 못 이었기 때문이다.
#
# 그런데 이름을 "화장실 1", "엘리베이터 1" 처럼 제대로 붙이자 이 표가 오히려
# 방해가 됐다. 표가 "화장실"을 접두사 "화"로 바꿔버리는데 실제 랜드마크 접두사는
# "화장실"이라 서로 안 맞아서, 멀쩡히 있는 화장실을 못 찾았다.
#
#     별칭 표 ON:  "화장실" → notFound        ← 화장실 1, 2 가 있는데도
#     별칭 표 OFF: "화장실" → 화장실 1, 화장실 2  ✓
#
# 그래서 표를 지우고, 종류 판단은 **실제 랜드마크 이름과 직접 비교**하도록 바꿨다
# (score() 참고). 건물이 바뀌어도 코드를 고칠 일이 없다.
#
# "변소", "승강기" 같은 동의어는 이제 LLM이 맡는다(llm_matcher.py).
# 여기 규칙 엔진은 LLM을 못 쓸 때의 안전망이라, 숫자와 문자 유사도만 다룬다.

# 질의가 랜드마크의 종류(접두사)와 같다고 볼 최소 유사도.
# "화장실" vs "화장실" = 1.0, "화장실1" vs "화장실" ≈ 0.89
CATEGORY_MATCH = 0.8

# ---------------------------------------------------------------------------
# 한글 수사
# ---------------------------------------------------------------------------
# 한글 수사 표. **판단이 아니라 검증에 쓰는 표다.**
#
# "이 정도는 LLM이 알지 않나"는 맞는 말이고, 실제로 목적지 해석은 모델이 한다.
# 이 표가 남아 있는 이유는 llm_matcher._number_conflict 때문이다.
#
#   사용자가 "사백칠"이라고 말했는데 모델이 406을 고르면?
#   시각장애인이 잘못된 문 앞에 선다. 눈으로 확인할 수 없으니 되돌릴 방법도 없다.
#
# 그래서 발화에서 방 번호를 **모델과 무관하게** 뽑아, 모델이 고른 곳의 번호와
# 대조한다. 다르면 모델 답을 버린다. 이 대조를 모델에게 시키면 검증이 아니다.
# 자기 답이 맞냐고 자기한테 묻는 셈이기 때문이다.
#
# 표를 둘 수 있는 이유는 한국어 수사가 **닫힌 집합**이기 때문이다.
# 열 개 남짓이고 늘어나지 않는다. 건물이나 말투에 따라 달라지지도 않는다.
# 없앤 표들(별칭·어미 목록)은 반대로 끝이 없는 집합이라 늘 새는 게 있었다.
_DIGIT_WORDS = {
    "영": 0, "공": 0, "빵": 0, "제로": 0,
    "일": 1, "하나": 1, "한": 1,
    "이": 2, "둘": 2, "두": 2,
    "삼": 3, "셋": 3, "세": 3,
    "사": 4, "넷": 4, "네": 4,
    "오": 5, "다섯": 5,
    "육": 6, "륙": 6, "여섯": 6,
    "칠": 7, "일곱": 7,
    "팔": 8, "여덟": 8,
    "구": 9, "아홉": 9,
}
_UNIT_WORDS = {"십": 10, "백": 100, "천": 1000, "만": 10000}

# 숫자 뒤에 붙는 말. 숫자 바로 뒤에서만 지운다 —
# 그냥 "실"을 지우면 "화장실"이 "화장"이 되어 별칭이 깨진다.
_NUM_SUFFIX = re.compile(r"(?<=\d)\s*(호실|호|번방|번|실|방|층)")

# 조사·서술어를 지우는 목록은 **일부러 두지 않는다.**
#
# 예전에는 45개짜리 목록으로 "로가줘", "어디야" 같은 끝말을 벗겨냈다.
# 그런데 실제로 재보니 목록에 있는 몇 개만 벗겨지고 나머지는 그대로 남았다.
#
#     '407호로 가줘'          → '407'                 ← 목록에 있음
#     '407호 좀 찾아주실래요'   → '407좀찾아주실래요'      ← 목록에 없음, 그대로
#     '화장실 어디 있나요'      → '화장실어디있나요'        ← 목록에 없음, 그대로
#
# 목록에 있는 말투만 잘 되고 나머지는 오히려 유사도가 떨어지니, 말투에 따라
# 결과가 널뛴다. 목록을 늘려도 끝이 없다.
#
# 그래서 목록을 없애고 **부분 유사도**로 바꿨다(partial_ratio 참고).
# 후보 이름이 발화 어디에 있든 찾으므로 앞뒤에 뭐가 붙든 상관없다.
#
#     '화장실 어디 있나요' vs '화장실'   전체 0.59 → 부분 1.00
#     '407호 좀 찾아주실래요' vs '407'   전체 0.24 → 부분 1.00
#     '용변 보러 갈래'      vs '화장실'   전체 0.17 → 부분 0.25   ← 낮은 게 맞다(LLM 몫)

# 순서를 가리키는 말 → 몇 번째인지.
#
# **평소에는 안 쓰인다.** 되묻기 대답 해석은 llm_matcher.choose 가 모델에게 맡긴다
# (후보 목록과 대답을 그대로 주고 고르게 한다). 모델은 "둘째", "가운데 거",
# "두번째요" 같은 걸 사전 없이 알아듣는다.
#
# 이 표는 모델이 안 떠 있을 때만 쓰는 폴백이다. 그래서 흔한 다섯 개만 적어두고
# 더 늘리지 않는다. 늘려봐야 사람 말투를 따라잡지 못하고, 늘리려는 충동이 들면
# 그건 모델을 띄우라는 신호다.
_ORDINALS: list[tuple[str, int]] = [
    ("첫번째", 1), ("첫째", 1), ("처음", 1), ("일번", 1), ("하나", 1),
    ("두번째", 2), ("둘째", 2), ("이번", 2), ("둘", 2),
    ("세번째", 3), ("셋째", 3), ("삼번", 3), ("셋", 3),
    ("네번째", 4), ("넷째", 4), ("사번", 4), ("넷", 4),
    ("다섯번째", 5), ("다섯째", 5), ("오번", 5),
]
_ORDINALS.sort(key=lambda p: -len(p[0]))

_ORDINAL_SPEECH = ["", "첫 번째", "두 번째", "세 번째", "네 번째", "다섯 번째"]

# ---------------------------------------------------------------------------
# 임계값
# ---------------------------------------------------------------------------
# 이 점수를 못 넘으면 "못 알아들었다"고 본다. 시각장애인이 쓰는 도구라
# 엉뚱한 곳으로 안내하느니 다시 말해달라고 하는 편이 안전하다.
MIN_ACCEPT = 0.55

# 1위와 이만큼 이내면 "구분이 안 된다"고 보고 되묻는다.
TIE_DELTA = 0.08

# 못 알아들었을 때 참고로 돌려줄 후보 수
SUGGEST_COUNT = 3


# ---------------------------------------------------------------------------
# 한글 자모 분해
# ---------------------------------------------------------------------------
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def to_jamo(text: str) -> str:
    """한글을 자모 단위로 편다. "화장실" → "ㅎㅘㅈㅏㅇㅅㅣㄹ"

    음절 단위로 비교하면 "화장실"과 "화장"의 차이가 한 글자(33%)지만
    자모로 펴면 두 자모(25%)라 더 부드럽게 비교된다. 음성인식이 받침이나
    모음 하나를 틀리는 경우가 많아서 자모 단위가 실전에서 잘 맞는다.
    """
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            i = code - 0xAC00
            out.append(_CHO[i // 588])
            out.append(_JUNG[(i % 588) // 28])
            jong = _JONG[i % 28]
            if jong != " ":
                out.append(jong)
        else:
            out.append(ch)
    return "".join(out)


def jamo_ratio(a: str, b: str) -> float:
    """전체끼리 비교."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, to_jamo(a), to_jamo(b)).ratio()


# 부분 비교를 쓸 최소 이름 길이. 한 글자 이름("화")까지 부분 비교를 하면
# 아무 발화에나 걸려버린다.
PARTIAL_MIN_LEN = 2


def partial_ratio(query: str, name: str) -> float:
    """이름이 발화 **어디에 있든** 가장 잘 맞는 구간의 유사도.

    말투를 목록으로 벗겨내는 대신 이걸 쓴다. 앞뒤에 무슨 말이 붙어도
    이름 부분만 따로 보므로 목록을 유지할 필요가 없다.

        '화장실 어디 있나요' vs '화장실'   전체 0.59 → 부분 1.00
        '407호 좀 찾아주실래요' vs '407'   전체 0.24 → 부분 1.00

    이름이 발화보다 길거나 너무 짧으면 전체 비교로 되돌아간다.
    """
    if not query or not name:
        return 0.0
    q, n = to_jamo(query), to_jamo(name)
    if len(name) < PARTIAL_MIN_LEN or len(n) >= len(q):
        return difflib.SequenceMatcher(None, q, n).ratio()
    best = 0.0
    for i in range(len(q) - len(n) + 1):
        r = difflib.SequenceMatcher(None, q[i:i + len(n)], n).ratio()
        if r > best:
            best = r
            if best >= 1.0:
                break
    return best


# ---------------------------------------------------------------------------
# 한글 수사 → 숫자
# ---------------------------------------------------------------------------
def korean_numbers(text: str) -> set[int]:
    """한글로 읽은 수를 숫자로 바꾼다. 해석이 여러 개면 전부 돌려준다.

    방 번호는 두 가지로 읽힌다. 어느 쪽으로 말할지 모르니 둘 다 후보로 만든다.

        "사백칠"  → 4×100 + 7        = 407    (자릿수 읽기)
        "사공칠"  → 4, 0, 7 을 이어서 = 407    (한 자씩 읽기)

    음성인식기가 "407"을 그대로 숫자로 주는 경우가 대부분이지만,
    한글로 뱉는 기기도 있어서 둘 다 받는다.
    """
    results: set[int] = set()
    if not text:
        return results

    has_unit = any(u in text for u in _UNIT_WORDS)

    if has_unit:
        # 자릿수 읽기: "사백칠십오" → 475
        total = 0
        current = 0
        i = 0
        matched_any = False
        while i < len(text):
            ch = text[i]
            if ch in _DIGIT_WORDS:
                current = _DIGIT_WORDS[ch]
                matched_any = True
                i += 1
            elif ch in _UNIT_WORDS:
                unit = _UNIT_WORDS[ch]
                total += (current if current else 1) * unit
                current = 0
                matched_any = True
                i += 1
            else:
                i += 1
        if matched_any:
            total += current
            if total > 0:
                results.add(total)
    else:
        # 한 자씩 읽기: "사공칠" → "407"
        digits = ""
        matched = 0
        for ch in text:
            if ch in _DIGIT_WORDS:
                digits += str(_DIGIT_WORDS[ch])
                matched += 1
            elif ch.isdigit():
                digits += ch
                matched += 1
        if len(digits) >= 2:
            results.add(int(digits))
        elif len(digits) == 1 and matched == len(text):
            # 한 글자짜리는 전체가 숫자일 때만 인정한다.
            # "출구"의 "구"를 9로 읽어버리는 것을 막기 위함.
            results.add(int(digits))

    return results


# ---------------------------------------------------------------------------
# 정규화
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    """발화를 비교 가능한 형태로 다듬는다. **기계적인 정리만** 한다.

        "407호로 가줘"     → "407로가줘"
        "화장실 어디 있나요" → "화장실어디있나요"

    말투를 벗겨내지 않는다. 어떤 말투가 올지 목록으로 예상하는 방식은
    목록 밖의 말투에서 오히려 결과를 나쁘게 만들기 때문이다.
    남은 말은 부분 유사도(partial_ratio)가 알아서 넘어간다.
    """
    if not text:
        return ""

    s = text.strip().lower()
    s = s.replace("－", "-").replace("（", "(").replace("）", ")")
    # 숫자 뒤 접미사 제거 — "407호" → "407", "화장실"은 건드리지 않음
    s = _NUM_SUFFIX.sub("", s)
    # 공백과 문장부호 제거 (괄호는 랜드마크 이름에 쓰이므로 남긴다)
    s = re.sub(r"[\s.,!?~・·\-_/]+", "", s)
    return s


def extract_ordinal(text: str, require_rest: bool = True) -> tuple[int | None, str]:
    """"두번째화장실" → (2, "화장실"). 순서 표현을 떼어내고 나머지를 돌려준다.

    require_rest=True 면 순서 표현을 뗀 뒤에 뭔가 남아야만 인정한다.
    목적지를 처음 말할 때는 "둘"이 이름의 일부일 수 있어서 이 조건이 필요하다.

    되물은 뒤의 대답은 반대다. "두 번째"라고만 말하는 게 정상이므로
    require_rest=False 로 불러야 한다. (이 구분을 안 해서 되묻기가 전부
    실패했던 버그가 있었다)
    """
    for word, num in _ORDINALS:
        if word in text:
            rest = text.replace(word, "", 1)
            if rest or not require_rest:
                return num, rest
    return None, text


# ---------------------------------------------------------------------------
# 랜드마크 이름 파싱
# ---------------------------------------------------------------------------
_NAME_RE = re.compile(r"^(?P<prefix>\D*?)(?P<number>\d+)?(?:\((?P<variant>\d+)\))?$")


@dataclass(frozen=True)
class Landmark:
    id: str
    name: str
    x: float = 0.0
    y: float = 0.0
    prefix: str = ""          # "" 면 방 번호, 아니면 종류(화/계단/엘베)
    number: int | None = None  # 방 번호 또는 순번
    variant: int | None = None  # "409(2)" 의 2

    @property
    def is_room(self) -> bool:
        """접두사가 없고 숫자가 있으면 방 번호로 본다."""
        return not self.prefix and self.number is not None


def parse_landmark(raw: dict) -> Landmark:
    name = str(raw.get("name") or "").strip()
    norm = normalize(name)
    m = _NAME_RE.match(norm)
    prefix, number, variant = norm, None, None
    if m:
        prefix = m.group("prefix") or ""
        number = int(m.group("number")) if m.group("number") else None
        variant = int(m.group("variant")) if m.group("variant") else None
    return Landmark(
        id=str(raw.get("id") or ""),
        name=name,
        x=float(raw.get("x") or 0.0),
        y=float(raw.get("y") or 0.0),
        prefix=prefix,
        number=number,
        variant=variant,
    )


# ---------------------------------------------------------------------------
# 질의 해석
# ---------------------------------------------------------------------------
@dataclass
class Query:
    raw: str
    norm: str                        # 정규화된 전체
    body: str                        # 순서 표현을 뗀 나머지
    prefix: str = ""                 # 별칭으로 알아낸 종류 (화/계단/엘베)
    numbers: set[int] = field(default_factory=set)
    ordinal: int | None = None       # "두 번째" 의 2
    variant: int | None = None       # "409(2)" 의 2


def parse_query(text: str) -> Query:
    norm = normalize(text)
    ordinal, body = extract_ordinal(norm)

    # "화장실 2" 처럼 종류 뒤에 숫자를 붙여 말한 경우를 잡는다.
    #
    # 반드시 숫자가 함께 있을 때만 접두사로 인정한다. 이 조건이 없으면
    # "사백칠" 같은 순수 한글 발화가 통째로 접두사로 잡혀서
    # 한글 수사 해석이 아예 돌지 않는다(실제로 났던 버그).
    prefix = ""
    rest = body
    m = _NAME_RE.match(body)
    if m and m.group("prefix") and m.group("number"):
        prefix = m.group("prefix")
        rest = body[len(prefix):]

    # "409(2)" 처럼 변형까지 말한 경우
    m_all = _NAME_RE.match(body)
    variant = int(m_all.group("variant")) if (m_all and m_all.group("variant")) else None

    numbers: set[int] = set()
    if m_all and m_all.group("number"):
        # 이름 형태로 딱 떨어지면 그 숫자만 쓴다.
        # findall 을 쓰면 "409(2)" 에서 변형 2까지 방 번호 후보로 들어간다.
        numbers.add(int(m_all.group("number")))
    else:
        for digits in re.findall(r"\d+", body):
            numbers.add(int(digits))
    numbers |= korean_numbers(body if not prefix else rest)

    # "화장실 2" 처럼 별칭 뒤에 숫자가 오면 그건 순번이다
    if prefix and ordinal is None and numbers:
        ordinal = min(numbers)

    return Query(raw=text, norm=norm, body=body, prefix=prefix,
                 numbers=numbers, ordinal=ordinal, variant=variant)


# ---------------------------------------------------------------------------
# 점수 계산
# ---------------------------------------------------------------------------
def score(query: Query, lm: Landmark) -> float:
    """0.0 ~ 1.0. 높을수록 잘 맞는다."""
    if not query.norm:
        return 0.0

    # 완전히 같은 이름을 말한 경우
    if query.norm == normalize(lm.name):
        return 1.0

    # ---- 종류 + 순번인 랜드마크 (화장실 1, 계단 2, 엘리베이터 1) ----
    #
    # 손으로 적은 별칭 표를 쓰지 않고, 말한 내용을 **실제 랜드마크 이름과 직접**
    # 비교한다. 그래서 건물이 바뀌어도 코드를 고칠 필요가 없다.
    if lm.prefix:
        said = query.body or query.norm      # 순서 표현을 뗀 나머지
        ratio = partial_ratio(said, lm.prefix)
        if ratio >= CATEGORY_MATCH:
            if query.ordinal is not None:
                # 몇 번째인지 말했으면 그 번호와 맞아야 한다
                return 1.0 if lm.number == query.ordinal else 0.15
            return 0.95   # 종류만 말함 — 같은 종류가 여럿이면 되묻게 된다
        # 종류가 다르면(화장실 vs 계단) 유사도로 떨어뜨린다
        return ratio * 0.5

    # ---- 방 번호 (407, 409(1)) ----
    if lm.is_room:
        if query.numbers:
            if lm.number in query.numbers:
                # "409(2)" 처럼 변형까지 말했으면 변형도 맞아야 한다
                if query.variant is not None:
                    return 1.0 if lm.variant == query.variant else 0.15
                return 1.0
            # 숫자를 말했는데 다르면 다른 방이다. 407과 406은 남남이므로
            # 유사도로 이어주지 않고 확실히 배제한다.
            return 0.1
        # 숫자를 안 말했는데 상대는 방 번호 → 관계 없음
        return partial_ratio(query.body, normalize(lm.name)) * 0.4

    # ---- 그 밖 (숫자 없는 이름 등) ----
    return partial_ratio(query.norm, normalize(lm.name)) * 0.9


# ---------------------------------------------------------------------------
# 결과
# ---------------------------------------------------------------------------
@dataclass
class MatchResult:
    status: str                       # "resolved" | "ambiguous" | "notFound"
    query: str
    landmark: Landmark | None = None
    candidates: list[Landmark] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    speech: str = ""
    # 어느 엔진이 답했는지. "rule" | "llm" | "llm→rule(사유)"
    # 화면과 점검 도구가 "지금 LLM이 실제로 일하고 있나"를 알 수 있어야 한다.
    source: str = "rule"


def _ordinal_speech(n: int) -> str:
    return _ORDINAL_SPEECH[n] if n < len(_ORDINAL_SPEECH) else f"{n} 번째"


def _has_jongseong(word: str) -> bool | None:
    """마지막 글자에 받침이 있는지. 판단할 수 없으면 None."""
    if not word:
        return None
    ch = word[-1]
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:
        return (code - 0xAC00) % 28 != 0
    if ch.isdigit():
        # 숫자는 읽는 소리로 판단한다 (0 영, 1 일, 3 삼, 6 육, 7 칠, 8 팔 은 받침 있음)
        return ch in "0136780"
    return None


def _josa_iga(word: str) -> str:
    """"화장실이", "엘리베이터가" — 받침에 맞는 조사를 고른다."""
    j = _has_jongseong(word)
    return "이" if j else "가"


def _josa_ro(word: str) -> str:
    """"407호로", "계단으로" — 받침이 있으면 '으로', 단 ㄹ 받침은 '로'."""
    if not word:
        return "로"
    ch = word[-1]
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:
        jong = (code - 0xAC00) % 28
        return "로" if jong in (0, 8) else "으로"   # 8 = ㄹ
    return "로" if _has_jongseong(word) is False else "으로"


def _spoken_label(lm: Landmark) -> str:
    """확정 안내에서 읽어줄 이름. "407" → "407호", "화장실 1" → "화장실" """
    if lm.is_room:
        return f"{lm.number}호"
    return lm.prefix or lm.name


def _spoken_item(lm: Landmark) -> str:
    """되묻기 목록에서 읽어줄 이름. 개별 항목을 구분할 수 있어야 한다.

        407      → "407호"
        409(1)   → "409호 1번"
        화장실 1  → "화장실 1번"
        계단 2    → "계단 2번"
    """
    if lm.is_room:
        return f"{lm.number}호 {lm.variant}번" if lm.variant is not None else f"{lm.number}호"
    if lm.prefix and lm.number is not None:
        return f"{lm.prefix} {lm.number}번"
    return lm.name


def ambiguous_speech(candidates: list[Landmark]) -> str:
    """후보를 **이름 그대로 순서대로** 읽어준다.

    예전에는 "화장실이 2곳 있습니다. 첫 번째, 두 번째 중에…" 처럼 첫 후보의 종류로
    묶어서 말하고 순서만 물었다. 후보가 한 종류일 때만 맞는 방식이라,
    종류가 섞이면 틀린 안내가 나갔다.

        후보  계단1, 엘베1, 계단2, 계단3
        안내  "계단이 4곳 있습니다..."      ← 엘리베이터가 계단이 되어버린다

    이름을 그대로 부르면 그런 문제가 없고, 사용자도 무엇을 고르는지 알 수 있다.
    """
    items = [_spoken_item(c) for c in candidates]
    if len(items) == 1:
        return f"{items[0]}(으)로 안내할까요?"
    return ", ".join(items) + " 중에서 말씀해 주세요."


def _arrive_speech(lm: Landmark) -> str:
    label = _spoken_label(lm)
    return f"{label}{_josa_ro(label)} 안내합니다."


def resolve(text: str, landmarks: list[Landmark]) -> MatchResult:
    """발화 하나를 랜드마크에 잇는다."""
    query = parse_query(text)
    if not query.norm or not landmarks:
        return MatchResult(status="notFound", query=text,
                           speech="목적지를 알아듣지 못했습니다. 다시 말씀해 주세요.")

    ranked = sorted(
        ((score(query, lm), lm) for lm in landmarks),
        key=lambda p: (-p[0], p[1].name),
    )
    top_score, top_lm = ranked[0]

    if top_score < MIN_ACCEPT:
        suggestions = [lm for s, lm in ranked[:SUGGEST_COUNT] if s > 0.2]
        return MatchResult(
            status="notFound", query=text,
            candidates=suggestions,
            scores=[s for s, _ in ranked[:SUGGEST_COUNT] if s > 0.2],
            speech="목적지를 알아듣지 못했습니다. 다시 말씀해 주세요.",
        )

    # 1위와 비슷한 점수가 더 있으면 구분이 안 된 것 — 되묻는다
    tied = [(s, lm) for s, lm in ranked if top_score - s <= TIE_DELTA]
    if len(tied) > 1:
        cands = [lm for _, lm in tied]
        return MatchResult(
            status="ambiguous", query=text,
            candidates=cands, scores=[s for s, _ in tied],
            speech=ambiguous_speech(cands),
        )

    return MatchResult(
        status="resolved", query=text, landmark=top_lm, scores=[top_score],
        speech=_arrive_speech(top_lm),
    )


# 되묻기 대답을 이름으로 인정할 최소 유사도. 순서 해석보다 먼저 보므로
# 느슨하면 "계단 2번"의 2를 순서로 읽는 것보다 더 나쁜 오답이 난다.
CHOOSE_NAME_MATCH = 0.85


def choose(text: str, candidates: list[Landmark]) -> MatchResult:
    """되물은 뒤 사용자가 고른 것을 해석한다.

    되묻기에서 이름을 그대로 읽어주므로("계단 2번, 엘리베이터 1번 …")
    사용자가 그중 하나를 따라 말하는 것이 가장 흔한 대답이다.
    그래서 **이름 대조를 순서 해석보다 먼저** 한다.

    순서를 먼저 보면 이름 안의 숫자를 순서로 읽어버린다. 실제로 그랬다.

        후보  [계단1, 엘베1, 계단2, 계단3]
        대답  "엘베 1번"  →  숫자 1 을 순서로 읽어 첫 후보(계단1)를 고름   ← 오답
    """
    if not candidates:
        return MatchResult(status="notFound", query=text,
                           speech="다시 말씀해 주세요.")

    norm = normalize(text)

    # 1) 이름으로 고른 경우
    best, best_score = None, 0.0
    for c in candidates:
        r = max(partial_ratio(norm, normalize(c.name)),
                partial_ratio(norm, normalize(_spoken_item(c))))
        if r > best_score:
            best, best_score = c, r
    if best is not None and best_score >= CHOOSE_NAME_MATCH:
        return MatchResult(status="resolved", query=text, landmark=best,
                           speech=_arrive_speech(best))

    # 2) 순서로 고른 경우 ("두 번째", "3번째")
    ordinal, _rest = extract_ordinal(norm, require_rest=False)

    # 3) 숫자로 말한 경우 ("2", "3번", "3번째"). 다른 말이 섞여 있으면 이름의
    #    일부일 수 있으므로, 발화가 숫자와 순서 접미사뿐일 때만 순서로 본다.
    #    (정규화가 숫자 뒤 "번"을 지우므로 "3번째"는 "3째"로 들어온다)
    if ordinal is None:
        m = re.fullmatch(r"(\d{1,2})(째|번|번째)?", norm)
        if m:
            ordinal = int(m.group(1))

    if ordinal is not None and 1 <= ordinal <= len(candidates):
        picked = candidates[ordinal - 1]
        return MatchResult(status="resolved", query=text, landmark=picked,
                           speech=_arrive_speech(picked))

    return MatchResult(
        status="ambiguous", query=text, candidates=candidates,
        speech="잘 못 들었습니다. " + ambiguous_speech(candidates),
    )


def load_landmarks(raw_list: list[dict]) -> list[Landmark]:
    """프로젝트 JSON의 landmarks 배열을 매칭용 구조로 바꾼다."""
    out = []
    for raw in raw_list or []:
        if not str(raw.get("name") or "").strip():
            continue
        out.append(parse_landmark(raw))
    return out
