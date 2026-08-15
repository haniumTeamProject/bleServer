"""음성 목적지 매칭 테스트.

여기서 보는 건 **LLM을 못 쓸 때의 안전망**이다. 실제 서비스 경로는
llm_matcher.py 이고, 이 규칙 엔진은 모델이 안 떠 있거나 응답이 이상할 때만 쓰인다.

    python tests/test_landmark_matcher.py

그래서 기대치가 두 종류로 갈린다.

    규칙 엔진이 책임지는 것   방 번호, 한글 수사, 종류+순번, 되묻기
    LLM 이 책임지는 것        동의어("변소", "승강기"), 문장형 발화

아래 NOT_RULES_JOB 묶음은 "규칙 엔진이 못 잡는 게 정상"임을 명시적으로 못 박은 것이다.
나중에 누가 별칭 표를 다시 넣으려 하면 이 테스트가 그 의도를 알려준다.

랜드마크 이름은 실제로 쓸 형태("화장실 1")로 적었다. 프로젝트 파일에 아직
남아 있는 임시 이름("화1")과는 다르다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ws.landmark_matcher import (  # noqa: E402
    Landmark, choose, korean_numbers, load_landmarks, normalize, resolve,
)

# 실제로 쓸 이름 형태
FIXTURE = [
    {"id": "L01", "name": "계단 1"}, {"id": "L02", "name": "엘리베이터 1"},
    {"id": "L03", "name": "엘리베이터 2"}, {"id": "L04", "name": "계단 2"},
    {"id": "L05", "name": "410"}, {"id": "L06", "name": "409(1)"},
    {"id": "L07", "name": "409(2)"}, {"id": "L08", "name": "411"},
    {"id": "L09", "name": "408"}, {"id": "L10", "name": "412"},
    {"id": "L11", "name": "407"}, {"id": "L12", "name": "화장실 1"},
    {"id": "L13", "name": "화장실 2"}, {"id": "L14", "name": "406"},
    {"id": "L15", "name": "413"}, {"id": "L16", "name": "405(1)"},
    {"id": "L17", "name": "405(2)"}, {"id": "L18", "name": "404"},
    {"id": "L19", "name": "403"}, {"id": "L20", "name": "402"},
    {"id": "L21", "name": "414"}, {"id": "L22", "name": "415"},
    {"id": "L23", "name": "401"}, {"id": "L24", "name": "계단 3"},
]

def load() -> list[Landmark]:
    return load_landmarks(FIXTURE)


# (발화, 기대 상태, 기대 결과)
#   resolved  → 랜드마크 이름
#   ambiguous → 후보 이름 목록
#   notFound  → None
RESOLVE_CASES = [
    # --- 방 번호를 그대로 ---
    ("407", "resolved", "407"),
    ("401", "resolved", "401"),
    ("415", "resolved", "415"),
    # --- 접미사가 붙은 경우 (데이터에는 "407", 사용자는 "407호") ---
    ("407호", "resolved", "407"),
    ("412번방", "resolved", "412"),
    ("406호실", "resolved", "406"),
    # --- 조사·서술어가 붙은 문장 ---
    ("407호로 가줘", "resolved", "407"),
    ("410호 안내해줘", "resolved", "410"),
    ("411로 가고 싶어", "resolved", "411"),
    ("413 어디야", "resolved", "413"),
    # --- 한글 수사 (자릿수 읽기 / 한 자씩 읽기) ---
    ("사백칠", "resolved", "407"),
    ("사공칠", "resolved", "407"),
    ("사백칠호", "resolved", "407"),
    ("사백일", "resolved", "401"),
    ("사일오", "resolved", "415"),
    # --- 종류만 말한 경우: 이름과 직접 비교해서 잡는다 (별칭 표 없음) ---
    ("화장실", "ambiguous", ["화장실 1", "화장실 2"]),
    ("화장실 어디야", "ambiguous", ["화장실 1", "화장실 2"]),
    ("엘리베이터", "ambiguous", ["엘리베이터 1", "엘리베이터 2"]),
    ("계단", "ambiguous", ["계단 1", "계단 2", "계단 3"]),
    # --- 종류 + 순서를 한 번에 말한 경우 ---
    ("첫번째 화장실", "resolved", "화장실 1"),
    ("두 번째 화장실", "resolved", "화장실 2"),
    ("세번째 계단", "resolved", "계단 3"),
    ("엘리베이터 2", "resolved", "엘리베이터 2"),
    # --- 데이터에 있는 이름을 그대로 말한 경우 ---
    ("화장실 2", "resolved", "화장실 2"),
    ("엘리베이터 1", "resolved", "엘리베이터 1"),
    ("409(2)", "resolved", "409(2)"),
    # --- 같은 번호가 두 개인 방 ---
    ("409", "ambiguous", ["409(1)", "409(2)"]),
    ("409호로 안내해줘", "ambiguous", ["409(1)", "409(2)"]),
    ("사백구", "ambiguous", ["409(1)", "409(2)"]),
    ("405", "ambiguous", ["405(1)", "405(2)"]),
    # --- 없는 곳은 확실히 거절해야 한다 ---
    ("999", "notFound", None),
    ("옥상", "notFound", None),
    ("옥상정원", "notFound", None),
    ("출구", "notFound", None),
    ("", "notFound", None),
]

# 되묻기: (처음 발화, 대답, 기대 랜드마크)
CHOOSE_CASES = [
    ("화장실", "첫번째", "화장실 1"),
    ("화장실", "두 번째", "화장실 2"),
    ("화장실", "둘째", "화장실 2"),
    ("409", "첫번째", "409(1)"),
    ("409", "두번째", "409(2)"),
    ("409", "409(2)", "409(2)"),          # 순서 대신 이름을 다시 말한 경우
    ("405", "둘째", "405(2)"),
    ("계단", "세 번째", "계단 3"),
    ("계단", "3번째", "계단 3"),
    ("엘리베이터", "2", "엘리베이터 2"),      # 숫자만 말한 경우
    ("계단", "몰라", None),                # 못 알아들으면 다시 물어야 한다
]

# 방 번호는 한 자리만 달라도 다른 곳이다. 절대 이어주면 안 된다.
MUST_NOT_MATCH = [
    ("407", "406"), ("407", "408"), ("401", "410"),
    ("403", "413"), ("405", "415"),
]

# 규칙 엔진이 못 잡는 게 **정상**인 것들. 동의어 지식은 LLM(llm_matcher.py)이 맡는다.
# 여기에 별칭 표를 다시 넣으면 이름이 바뀐 건물에서 매칭이 깨진다.
NOT_RULES_JOB = ["변소", "승강기", "층계", "화장실 급해요", "밥 먹는 데"]

NUMBER_CASES = [
    ("사백칠", 407), ("사공칠", 407), ("사백일", 401),
    ("사백십오", 415), ("사일오", 415), ("사백오", 405),
]

# 정규화는 **기계적인 정리만** 한다. 말투를 벗겨내지 않는다.
# 예전에는 45개짜리 어미 목록으로 "로가줘" 같은 걸 지웠는데, 목록에 있는 말투만
# 잘 되고 나머지는 그대로 남아 결과가 널뛰었다. 지금은 부분 유사도가 그 역할을 한다.
NORMALIZE_CASES = [
    ("407호로 가줘", "407로가줘"),        # 숫자 뒤 "호"만 지운다. 말투는 그대로 둔다
    ("  410 호 안내해줘 ", "410안내해줘"),  # 공백만 정리
    ("화장실", "화장실"),                  # "실"을 지우면 이름이 깨진다 — 숫자 뒤에서만
    ("두 번째 화장실", "두번째화장실"),
]


def main() -> int:
    lms = load()
    fails: list[str] = []
    total = 0

    print("\n── 정규화 ──")
    for text, want in NORMALIZE_CASES:
        total += 1
        got = normalize(text)
        ok = got == want
        if not ok:
            fails.append(f"normalize({text!r}) = {got!r}, 기대 {want!r}")
        print(f" {'✓' if ok else '✗'} {text!r:22} → {got!r}")

    print("\n── 한글 수사 ──")
    for text, want in NUMBER_CASES:
        total += 1
        got = korean_numbers(text)
        ok = want in got
        if not ok:
            fails.append(f"korean_numbers({text!r}) = {got}, {want} 없음")
        print(f" {'✓' if ok else '✗'} {text!r:12} → {sorted(got)}")

    print("\n── 목적지 해석 ──")
    for text, want_status, want in RESOLVE_CASES:
        total += 1
        r = resolve(text, lms)
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
            fails.append(f"resolve({text!r}) = {r.status}/"
                         f"{r.landmark.name if r.landmark else names}, 기대 {want_status}/{want}")
        shown = r.landmark.name if r.landmark else (names or "-")
        print(f" {'✓' if ok else '✗'} {text!r:18} → {r.status:10} {str(shown):22} │ {r.speech}")

    print("\n── 되묻기 ──")
    for first, second, want in CHOOSE_CASES:
        total += 1
        r1 = resolve(first, lms)
        r2 = choose(second, r1.candidates)
        got = r2.landmark.name if r2.landmark else None
        ok = got == want
        if not ok:
            fails.append(f"choose({first!r} → {second!r}) = {got}, 기대 {want}")
        print(f" {'✓' if ok else '✗'} \"{first}\" → \"{second}\" ⇒ {str(got):10} │ {r2.speech}")

    print("\n── 동의어는 LLM 몫 (규칙 엔진은 거절해야 정상) ──")
    for text in NOT_RULES_JOB:
        total += 1
        r = resolve(text, lms)
        ok = r.status != "resolved"
        if not ok:
            fails.append(f"{text!r} 를 규칙 엔진이 {r.landmark.name} 로 확정해버림")
        shown = r.landmark.name if r.landmark else "-"
        print(f" {'✓' if ok else '✗'} {text!r:14} → {r.status:10} {shown}")

    print("\n── 방 번호 오매칭 방지 ──")
    for spoken, must_not in MUST_NOT_MATCH:
        total += 1
        r = resolve(spoken, lms)
        got = r.landmark.name if r.landmark else None
        ok = got != must_not
        if not ok:
            fails.append(f"resolve({spoken!r}) 가 {must_not} 로 잘못 매칭됨")
        print(f" {'✓' if ok else '✗'} {spoken!r} 는 {must_not!r} 가 아니어야 함 → {got}")

    print(f"\n{'='*60}")
    if fails:
        print(f"실패 {len(fails)} / 전체 {total}")
        for f in fails:
            print("  ✗", f)
        return 1
    print(f"전체 {total}개 통과 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
