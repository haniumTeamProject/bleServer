"""경로 진행 추적 (서버 측).

원래 이 판정 로직은 /monitor 페이지의 JS에만 있었는데, 비콘이 바뀌는 시점을 폰에서
음성으로 안내하려면 브라우저가 아니라 서버가 판단해야 한다(모니터 페이지를 안 열어도
동작해야 하므로). 그래서 같은 알고리즘을 그대로 파이썬으로 옮긴 것.

── 판정 방식 세 가지 ────────────────────────────────────────────────

  ① trend    최근 40개 중 양끝 4개씩의 평균 차이를 추세로 쓴다
  ② segment  시간 창을 5구간으로 쪼개 구간 평균의 기울기를 추세로 쓴다
  ③ confirm  **② 위에 확인 단계를 얹은 것.** ②가 판정을 내리는 순간을 트리거로만
             삼고, 잠시 뒤 두 비콘의 절대 RSSI 차이를 다시 재서 확정한다

    ②   추세 조건 성립 + 2회 연속  →  즉시 확정
    ③   추세 조건 성립 + 2회 연속  →  대기 0.5초  →  신호차 5dB 확인  →  확정

②가 판정하는 그 순간의 신호차는 실측에서 중앙값 0.6dB(최소 -2.6dB)였다.
두 비콘이 거의 같은 순간에 결론이 나므로, 그 순간이 잡음이면 그대로 오판이다.
③은 거기서 기다렸다가 차이가 실제로 벌어졌는지 확인한다. 기다리는 동안
도로 역전되면 취소한다.

실측 13개 데이터셋 · 전환 31건:

    방식                          정지오탐   검출     평균지연   최대지연   최소통과
    ① trend                          6     27/31    +0.76초   +5.78초     8초
    ② segment                        0     26/31    +0.59초   +3.06초     6초
    ③ ② + 확인 0.5초 / 5dB           0     26/31    +1.51초   +4.67초    10초

③은 ②와 **검출이 같다**. 대신 판정 시점에 신호차 5dB를 실제로 확인하고 넘어가므로
잡음에 강하고, 그 대가로 안내가 평균 0.9초 늦는다.
"""

import time

TREND_WINDOW = 4
HISTORY_MAX = 40

# 전진/후퇴로 확정하기 전에 조건이 몇 번 연속 성립해야 하는가.
#
# **시간이 아니라 횟수다.** evaluate() 는 비콘 패킷이 올 때마다 불리는데 실측에서
# 초당 32~118회였다. 즉 "2회 연속"이 17~62ms 라 같은 잡음 봉우리 안이고,
# 시간 필터 역할은 못 한다(그건 추세 창 2초와 confirm 대기 500ms 가 한다).
#
# 예전에는 모듈 상수라 소스를 고치고 서버를 재시작해야만 바뀌었다. 실측 중에
# 만질 수 없는 유일한 값이어서 인스턴스 속성으로 뺐다.
FORWARD_STREAK = 2
BACK_STREAK = 3

# 추세 임계값(dB). 구간 분할(②·③)에서 검증된 값이 2.5 라 이것을 기본으로 둔다.
# ① trend 를 예전 그대로 쓰려면 threshold=3.0 을 명시해서 넘긴다.
DEFAULT_THRESHOLD = 2.5
DEFAULT_MIN_NEXT = -85.0

# ①② 에서 쓸 수 있는 선택적 추가 조건 — 교차 후 요구하는 신호차(dB).
#
# 기본값 0 = 끔. 즉 ①②는 원래대로 "다음 비콘이 더 세다"만 본다.
# 0보다 크게 두면 "이만큼 더 세다"를 요구하는 조건이 AND 로 붙는다.
#
# 절대 신호차를 판정 기준으로 쓰고 싶으면 이 값을 올리는 것보다
# ③ confirm 모드를 쓰는 편이 낫다. ①②는 추세와 같은 순간에 신호차를 봐야 해서,
# 신호차가 커질 즈음이면 추세가 이미 완만해져 두 조건이 동시에 성립하기 어렵다.
DEFAULT_MIN_GAP = 0.0

DEFAULT_GAP_WINDOW_MS = 300

# 전진 조건이 이만큼(ms) 계속 유지돼야 확정한다.
#
# FORWARD_STREAK(2회 연속)만으로는 시간 필터가 되지 않는다. evaluate()는 패킷이
# 올 때마다 불리는데, 실측에서 초당 32~118회였다. 즉 "2회 연속"이 실제로는
# 17~62ms에 불과해서 같은 잡음 봉우리 안이다.
#
# 다만 추세 조건을 함께 쓰는 지금 구조에서는 추세 창(2초)이 이미 시간 필터 역할을
# 하므로 기본값 0(끔)으로 둔다. 켜면 검출만 깎인다(27 → 25건).
# 추세를 끄고 절대 차이만 쓸 때는 반드시 켜야 한다.
DEFAULT_MIN_HOLD_MS = 0

# 추세 조건을 절대 차이와 **함께**(AND) 요구할지.
#
# 두 조건은 서로 다른 것을 본다.
#   추세      : 지금 가까워지는 중인가 (방향)
#   절대 차이 : 실제로 얼마나 벌어졌는가 (크기)
# 둘 다 만족해야 확정하므로, 어느 한쪽만으로 생기는 오판을 서로 막아준다.
DEFAULT_REQUIRE_TREND = True

# ---- 판정 모드 ----
# "trend"   : 기존 방식. 최근 40개 중 양끝 4개씩만 평균내어 비교.
# "segment" : 시간 창을 여러 구간으로 쪼개 구간별 평균을 낸 뒤, 그 평균들의 기울기를 추세로 쓴다.
#
# 기존 방식은 40개 중 8개만 쓰고 32개를 버린다. 게다가 창이 "개수" 기준이라 수신 속도에 따라
# 실제 시간 폭이 달라진다(초당 10개면 4초, 초당 1개면 40초).
# 구간 방식은 창 안의 모든 샘플을 쓰고, 구간 평균이 1차 평활 역할을 해서
# BLE 광고 채널 전환으로 생기는 계단식 점프에 덜 흔들린다.
#
# 실측 3개 파일(정지 2, 이동 1)로 전수 비교한 결과:
#   기존   : 정지 오탐 1회, 최소 통과 시간 8초
#   구간   : 정지 오탐 0회, 최소 통과 시간 6초 (2.5초 / 5구간 / 임계 3dB)
# 구간을 10개 이상으로 잘게 쪼개면 구간당 샘플이 2~3개로 줄어 다시 노이즈에 흔들린다(오탐 복귀).
MODE_TREND = "trend"
MODE_SEGMENT = "segment"

# "confirm" : 2단계 확인 방식.
#
# 위 두 방식은 한 순간에 모든 조건을 동시에 본다. 그래서 교차하는 바로 그 순간에
# 판정이 나가고, 그 순간이 하필 잡음이면 그대로 오판이 된다.
#
# 이 방식은 교차를 **트리거**로만 쓰고, 잠시 기다렸다가 신호차를 **다시 재서**
# 확정한다. 기다리는 동안 실제로 걸어가고 있다면 신호차가 더 벌어지고,
# 잡음이었다면 도로 좁아진다.
#
#     신호차
#       ↑
#       │              ┌─ 확인: 이때 confirm_gap 이상이어야 확정
#       │        ╱─────┤
#     0 ├──╳─────┴─────┴──→ 시간
#         ↑     ←대기→
#      트리거(교차)
#
# 덕분에 확인 단계에서는 큰 값(8dB 등)을 요구해도 검출을 잃지 않는다.
# 한 순간에 8dB를 요구하면 그 조건이 성립하는 시점이 거의 없지만,
# 교차 후 1초쯤 기다린 뒤라면 대부분 그만큼 벌어져 있기 때문이다.
MODE_CONFIRM = "confirm"

# ── 2단계 확인(MODE_CONFIRM) 기본값 ──
#
# ②가 판정을 내리는 그 순간, 두 비콘의 신호차는 실측에서 이랬다.
#     26건 · 최소 -2.6dB · 하위10% -1.1dB · 중앙값 0.6dB
# 즉 ②는 **두 비콘이 거의 같은 순간에** 결론을 낸다. 그 순간이 잡음이면 그대로 오판이다.
# 확인 단계는 이 지점을 메운다.
#
# 대기 × 확인값 전수(정지오탐/검출, ② 원본은 0/26):
#         2dB    3dB    4dB    5dB    6dB    8dB
#   300ms 0/26   0/26   0/26   0/26   0/25   0/20
#   500ms 0/26   0/26   0/26   0/26   0/25   0/20
#   800ms 0/25   0/25   0/25   0/25   0/25   0/20
#  1500ms 0/24   0/24   0/24   0/24   0/24   0/19
#
#   상한 : 6dB 부터 검출이 줄기 시작한다(26 → 25). 8dB 면 6건을 놓친다.
#   대기 : 800ms 부터 검출이 줄기 시작한다.
#   채택 : 대기 500ms · 확인 5.0dB — ②와 **똑같은 검출(26/31)** 을 유지하면서
#          요구할 수 있는 가장 큰 신호차다.
#
# 확인 단계는 "5dB 될 때까지 기다린다"에 가깝다. 못 미치면 취소가 아니라 계속 지켜보므로,
# 값을 올리면 검출보다 지연이 먼저 늘어난다(② +0.59초 → ③ +1.51초).
DEFAULT_TRIGGER_GAP = 0.0        # (지금은 안 씀. ②의 판정 자체가 트리거다)
DEFAULT_CONFIRM_DELAY_MS = 500   # 판정 후 최소 대기 — 이때부터 확인 시작
DEFAULT_CONFIRM_GAP = 5.0        # 확인 시점에 요구하는 절대 신호차(dB)
# 확인 시점에 '다음 비콘 추세 > X dB' 를 더 걸 수 있다. 0 = 안 봄.
# 실측에서는 켜도 얻는 게 없고 검출만 줄었다. 필요할 때 쓰라고 남겨둔 레버.
DEFAULT_CONFIRM_TREND = 0.0

# 세 방식 비교 (실측 13개 데이터셋 · 전환 31건, 정지 오탐은 셋 다 0):
#     ① trend   양끝평균 + 차이 6dB   검출 28/31  평균 +1.48초  최대 +4.73초  최소통과 12초
#     ② segment 구간분할 + 차이 6dB   검출 28/31  평균 +1.86초  최대 +10.5초  최소통과 16초
#     ③ confirm 2단계확인 0.5초/6dB   검출 29/31  평균 +1.27초  최대 +2.45초  최소통과 12초
# ③ 이 모든 지표에서 낫다. 못 잡은 2건은 녹화가 끊긴 파일이라 실질 29/29 다.
DEFAULT_MODE = MODE_CONFIRM
# 창 2.0초 / 5구간 / 임계 2.5dB 가 실측 4개 데이터셋 전수 비교에서 가장 좋았다.
# tests/eval_tracker.py --sweep-full 로 언제든 재현할 수 있다. 선정 근거:
#   - 정지 오탐 0 (임계 2.0~3.5 전 구간에서 0 → 마진이 가장 넓음)
#   - 이동 전환 10/10 검출, 초과 0
#   - 평균 지연 +0.24초, 최대 +1.28초 (이전 기본값 2.5초/3.0dB: +0.36 / +1.72)
#   - 최소 통과 시간 6초 (구간당 3초)
# 창을 1.5초로 더 줄이면 수치는 조금 더 좋지만 안전 임계 범위가 2.0~2.5로 좁아지고,
# 관측된 채널 체류 시간(약 1.8초)보다 짧아서 다른 기기에서 깨질 위험이 있어 택하지 않았다.
DEFAULT_WINDOW_MS = 2000
DEFAULT_SEGMENTS = 5
DEFAULT_SEGMENT_THRESHOLD = 2.5   # 구간 모드 권장 임계값 (trend 모드는 DEFAULT_THRESHOLD 사용)

# 시간 창을 쓰려면 개수 기준(40개)으로는 부족할 수 있어 버퍼를 넉넉히 잡는다.
# (trend 모드는 여전히 최근 40개만 보므로 동작이 바뀌지 않는다)
BUFFER_MAX = 300
BUFFER_MAX_AGE_MS = 15000


def short_name(key: str) -> str:
    """비콘 키는 "MAC|이름" 형태 — 안내 음성에는 이름만 쓴다."""
    if not key:
        return ""
    head, sep, tail = key.partition("|")
    return tail if sep else head


class PathTracker:
    def __init__(self) -> None:
        self.path: list[str] = []
        # (수신시각ms, 필터값). 시간 창을 쓰려면 시각이 필요해서 값만 담던 것을 튜플로 바꿨다.
        self.history: dict[str, list[tuple[int, float]]] = {}
        self.index = 0
        self.forward_streak = 0
        self.back_streak = 0
        self.threshold = DEFAULT_THRESHOLD
        self.min_next = DEFAULT_MIN_NEXT
        self.forward_streak_need = FORWARD_STREAK
        self.back_streak_need = BACK_STREAK
        self.min_gap = DEFAULT_MIN_GAP
        self.gap_window_ms = DEFAULT_GAP_WINDOW_MS
        self.min_hold_ms = DEFAULT_MIN_HOLD_MS
        # 추세 조건을 절대 차이와 함께(AND) 요구한다. 기본 True.
        self.require_trend = DEFAULT_REQUIRE_TREND
        self.forward_since = None   # 전진 조건이 계속 참이었던 시작 시각
        self.back_since = None
        self.mode = DEFAULT_MODE
        # ---- 2단계 확인 방식(MODE_CONFIRM) ----
        self.trigger_gap = DEFAULT_TRIGGER_GAP
        self.confirm_delay_ms = DEFAULT_CONFIRM_DELAY_MS
        self.confirm_gap = DEFAULT_CONFIRM_GAP
        self.confirm_trend = DEFAULT_CONFIRM_TREND
        self.armed_dir = None      # "forward" | "backward" | None
        self.armed_at = None       # 트리거된 시각
        self.armed_index = None    # 트리거 당시의 위치 (도중에 바뀌면 취소)
        self.window_ms = DEFAULT_WINDOW_MS
        self.segments = DEFAULT_SEGMENTS
        self.enabled = False   # 경로가 등록되어 있는가
        self.active = False    # 측정이 시작되어 실제로 안내를 내보내는 중인가

        # 마지막 판정 근거 — /monitor가 표시용으로만 쓴다 (판정은 여기서만 한다)
        self.last_trends: dict = {}
        # 판정에 실제로 쓴 값들 — 왜 안 넘어갔는지 밖에서 보려고 둔다
        self.last_numbers: dict = {}
        self.last_verdict = "대기 중"
        self.last_verdict_kind = ""

    # ---- 설정 ----

    def set_path(
        self,
        path: list[str],
        threshold=None,
        min_next=None,
        mode=None,
        window_ms=None,
        segments=None,
        min_gap=None,
        gap_window_ms=None,
        min_hold_ms=None,
        require_trend=None,
        trigger_gap=None,
        confirm_delay_ms=None,
        confirm_gap=None,
        confirm_trend=None,
        forward_streak_need=None,
        back_streak_need=None,
    ) -> dict:
        self.path = [p for p in path if p]
        self.history.clear()
        self.index = 0
        self.forward_streak = 0
        self.back_streak = 0
        self.last_trends = {}
        self.last_verdict = "대기 중"
        self.last_verdict_kind = ""
        self.active = False
        if threshold is not None:
            self.threshold = float(threshold)
        if min_next is not None:
            self.min_next = float(min_next)
        if min_gap is not None:
            self.min_gap = max(0.0, float(min_gap))
        if gap_window_ms is not None:
            self.gap_window_ms = max(0, int(gap_window_ms))
        if min_hold_ms is not None:
            self.min_hold_ms = max(0, int(min_hold_ms))
        if require_trend is not None:
            self.require_trend = bool(require_trend)
        if trigger_gap is not None:
            self.trigger_gap = float(trigger_gap)
        if confirm_delay_ms is not None:
            self.confirm_delay_ms = max(0, int(confirm_delay_ms))
        if confirm_gap is not None:
            self.confirm_gap = float(confirm_gap)
        if confirm_trend is not None:
            self.confirm_trend = float(confirm_trend)
        if forward_streak_need is not None:
            self.forward_streak_need = max(1, int(forward_streak_need))
        if back_streak_need is not None:
            self.back_streak_need = max(1, int(back_streak_need))
        self.armed_dir = None
        self.armed_at = None
        self.armed_index = None
        if mode in (MODE_TREND, MODE_SEGMENT, MODE_CONFIRM):
            self.mode = mode
        if window_ms is not None:
            self.window_ms = max(500, int(window_ms))
        if segments is not None:
            # 구간이 2개 미만이면 기울기를 낼 수 없고, 너무 잘게 쪼개면 구간당 샘플이 부족해진다
            self.segments = max(2, min(20, int(segments)))
        self.enabled = len(self.path) >= 2

        if not self.enabled:
            return {
                "type": "guide",
                "event": "pathSet",
                "enabled": False,
                "path": self.path,
                "speech": "",
                "timestamp": _now_ms(),
            }

        return {
            "type": "guide",
            "event": "pathSet",
            "enabled": True,
            "path": self.path,
            "numbers": {p: i + 1 for i, p in enumerate(self.path)},
            "threshold": self.threshold,
            "minNext": self.min_next,
            "minGap": self.min_gap,
            "gapWindowMs": self.gap_window_ms,
            "minHoldMs": self.min_hold_ms,
            "requireTrend": self.require_trend,
            "triggerGap": self.trigger_gap,
            "confirmDelayMs": self.confirm_delay_ms,
            "confirmGap": self.confirm_gap,
            "confirmTrend": self.confirm_trend,
            "mode": self.mode,
            "windowMs": self.window_ms,
            "segments": self.segments,
            # 경로 등록만으로는 말하지 않음 — 측정을 시작해야 안내가 나간다
            "speech": "",
            "timestamp": _now_ms(),
        }

    def stop(self) -> dict:
        self.enabled = False
        self.active = False
        return {
            "type": "guide",
            "event": "stopped",
            "enabled": False,
            "speech": "",
            "timestamp": _now_ms(),
        }

    # ---- 측정 구간과 연동 ----
    # 안내는 "측정 중"에만 나간다. 측정을 시작하는 순간 지금 가장 가까운(신호가 가장 센)
    # 비콘을 시작 지점으로 확정해서, 그 지점을 기준으로 이후 전진/후퇴를 센다.

    def start_session(self) -> dict | None:
        if not self.enabled:
            return None

        best_idx, best_val = 0, None
        for idx, key in enumerate(self.path):
            latest = self._latest(key)
            if latest is not None and (best_val is None or latest > best_val):
                best_val, best_idx = latest, idx

        self.index = best_idx
        self.forward_streak = 0
        self.back_streak = 0
        self.active = True
        self.last_verdict = "측정 시작"
        self.last_verdict_kind = ""

        return {
            "type": "guide",
            "event": "sessionStart",
            "index": self.index,
            "number": self.index + 1,
            "total": len(self.path),
            "beacon": self.path[self.index],
            "name": short_name(self.path[self.index]),
            "speech": str(self.index + 1),   # 시작 지점 번호만 짧게
            "timestamp": _now_ms(),
        }

    def end_session(self) -> dict | None:
        if not self.active:
            return None
        self.active = False
        self.last_verdict = "측정 종료"
        self.last_verdict_kind = ""
        return {
            "type": "guide",
            "event": "sessionEnd",
            "speech": "",   # 종료는 말하지 않음
            "timestamp": _now_ms(),
        }

    # ---- 데이터 수집 ----

    def feed(self, key: str, filtered_rssi: float, now_ms: int | None = None) -> None:
        now = _now_ms() if now_ms is None else int(now_ms)
        buf = self.history.setdefault(key, [])
        buf.append((now, filtered_rssi))
        # 개수와 나이 둘 다로 정리한다. trend 모드는 어차피 최근 40개만 보므로 영향 없음.
        while buf and (len(buf) > BUFFER_MAX or now - buf[0][0] > BUFFER_MAX_AGE_MS):
            buf.pop(0)

    # ---- 판정 ----

    def _trend(self, key: str):
        # confirm 모드는 ② 구간 분할 위에 확인 단계를 얹은 것이므로 추세도 구간 방식을 쓴다
        if self.mode in (MODE_SEGMENT, MODE_CONFIRM):
            value = self._trend_segment(key)
            if value is not None:
                return value
            # 구간 방식은 창 안에 (구간수 × 2)개가 있어야 값을 낸다. 수신이 희박한
            # 비콘은 그 조건을 못 채워서 추세가 아예 None이 되고, 추세를 AND로
            # 요구하면 그 비콘으로의 전환이 통째로 막힌다.
            # 실제로 walk_b12365 는 1번 비콘이 3.1개/초뿐이라 4건 중 1건만 잡혔다.
            # 그럴 때는 개수 기준(양끝 평균) 방식으로 대신 낸다.
            return self._trend_ends(key)
        return self._trend_ends(key)

    def _trend_ends(self, key: str):
        """기존 방식: 최근 40개 중 양끝 4개씩의 평균 차이."""
        buf = self.history.get(key)
        if not buf:
            return None
        vals = [v for _, v in buf][-HISTORY_MAX:]
        if len(vals) < TREND_WINDOW * 2:
            return None
        recent = sum(vals[-TREND_WINDOW:]) / TREND_WINDOW
        old = sum(vals[:TREND_WINDOW]) / TREND_WINDOW
        return recent - old

    def _trend_segment(self, key: str):
        """구간 방식: 시간 창을 N구간으로 쪼개 구간 평균을 낸 뒤, 그 평균들의 기울기.

        예) 2.5초 창에 초당 10개면 약 25개 -> 5개씩 묶어 평균 5개 -> 그 5점의 기울기.
        반환값은 기울기 자체가 아니라 "창 전체에 걸친 변화량(dB)"으로 환산한 값이라,
        기존 방식의 임계값(dB)을 그대로 쓸 수 있다.
        """
        buf = self.history.get(key)
        if not buf:
            return None

        now = buf[-1][0]
        pts = [(t, v) for t, v in buf if now - t <= self.window_ms]
        # 구간당 최소 2개는 있어야 평균이 의미가 있다
        if len(pts) < self.segments * 2:
            return None

        t0, t1 = pts[0][0], pts[-1][0]
        if t1 == t0:
            return None

        buckets: list[list[float]] = [[] for _ in range(self.segments)]
        for t, v in pts:
            idx = int((t - t0) / (t1 - t0 + 1) * self.segments)
            buckets[min(self.segments - 1, idx)].append(v)

        means = [(i, sum(b) / len(b)) for i, b in enumerate(buckets) if b]
        if len(means) < 2:
            return None

        # 구간 평균들에 직선을 맞춰 기울기를 구한다 (최소제곱)
        n = len(means)
        mean_i = sum(i for i, _ in means) / n
        mean_v = sum(v for _, v in means) / n
        den = sum((i - mean_i) ** 2 for i, _ in means)
        if den == 0:
            return None
        slope = sum((i - mean_i) * (v - mean_v) for i, v in means) / den

        # 구간 인덱스당 기울기 -> 창 전체 변화량으로 환산
        return slope * (means[-1][0] - means[0][0])

    def _now_data_ms(self) -> int:
        """가장 최근에 들어온 데이터의 시각. 실시간이 아니라 데이터 시각을 써야
        평가 하네스에서 CSV를 재생할 때도 같은 결과가 나온다."""
        latest = 0
        for buf in self.history.values():
            if buf and buf[-1][0] > latest:
                latest = buf[-1][0]
        return latest

    def _audible_idx(self, direction: int) -> int | None:
        """지금 자리에서 그 방향으로, **신호가 잡히는** 가장 가까운 자리.

        경로에는 아직 안 잡힌 비콘도 들어 있으므로 바로 옆 칸이 비어 있을 수 있다.
        그때 멈추면 안내가 영영 안 나간다.
        """
        i = self.index + direction
        while 0 <= i < len(self.path):
            if self._latest(self.path[i]) is not None:
                return i
            i += direction
        return None

    def _latest(self, key):
        buf = self.history.get(key) if key else None
        return buf[-1][1] if buf else None

    def _gap_value(self, key):
        """신호차 비교에 쓸 대표값 — 최근 gap_window_ms 구간의 평균.

        마지막 한 값만 보면 잡음을 그대로 맞는다(정지 상태에서도 신호차가
        최신값 기준 최대 10.6dB 흔들렸다). 반대로 창을 너무 길게 잡으면
        교차 지점을 걸쳐 평균내면서 차이가 작아져 빠른 걸음을 놓친다.
        그래서 추세 창과 별도로 짧은 창을 둔다.
        """
        buf = self.history.get(key) if key else None
        if not buf:
            return None
        if self.gap_window_ms <= 0:
            return buf[-1][1]
        now = buf[-1][0]
        vals = [v for t, v in buf if now - t <= self.gap_window_ms]
        if not vals:
            return buf[-1][1]
        return sum(vals) / len(vals)

    # ---- 2단계 확인 (MODE_CONFIRM) ----
    #
    # ② 구간 분할 방식을 그대로 쓰되, ②가 "확정"을 내리는 순간을 **트리거**로만 삼는다.
    # 거기서 confirm_delay_ms 를 기다린 뒤 두 비콘의 절대 RSSI 차이를 다시 재서,
    # confirm_gap 이상일 때 비로소 확정한다.
    #
    #   ②        추세 조건 성립 + 2회 연속  →  즉시 확정
    #   ③        추세 조건 성립 + 2회 연속  →  대기  →  절대 신호차 확인  →  확정
    #
    # 기다리는 동안 실제로 걸어가고 있으면 차이가 더 벌어지고,
    # 잡음이었으면 도로 좁아져서 취소된다.

    def _disarm(self, reason: str = "") -> None:
        self.armed_dir = None
        self.armed_at = None
        self.armed_index = None
        if reason:
            self.last_verdict = reason
            self.last_verdict_kind = ""

    def _confirm_stage(self) -> dict | None:
        """트리거된 뒤의 확인 단계. 아직 확정 못 하면 None."""
        cur_key = self.path[self.index]
        forward = self.armed_dir == "forward"
        # 확인 상대도 **들리는 자리**로 잡는다. 바로 옆 칸만 보면, 아직 안 잡힌
        # 비콘이 사이에 있을 때 상대가 None 이 되어 그대로 취소된다
        # (evaluate 에서 이웃을 건너뛰어 무장해놓고 여기서 풀리는 셈이다).
        other_idx = self._audible_idx(1 if forward else -1)
        other_key = self.path[other_idx] if other_idx is not None else None

        cur_g = self._gap_value(cur_key)
        other_g = self._gap_value(other_key)
        now = self._now_data_ms()

        if other_g is None or cur_g is None or self.armed_index != self.index:
            self._disarm()
            return None

        if other_g <= cur_g:
            # 기다리는 동안 도로 역전됐다 = 잡음이었다
            self._disarm("교차가 되돌아감 — 취소")
            return None

        # 후퇴로 무장했는데 그 사이 **다음 비콘이 오르기 시작했다면** 되돌아가는
        # 것이 아니라 골짜기를 지나던 중이다. evaluate 에서 막는 것과 같은 이유인데,
        # 무장은 그 전에 이미 걸렸을 수 있으므로 여기서 한 번 더 본다.
        if not forward:
            ahead = self._audible_idx(+1)
            if ahead is not None:
                t_ahead = self._trend(self.path[ahead])
                if t_ahead is not None and t_ahead > self.threshold:
                    self._disarm("다음 비콘이 오르는 중 — 후퇴 취소")
                    return None

        if now - self.armed_at < self.confirm_delay_ms:
            waited = (now - self.armed_at) / 1000
            self.last_verdict = (f"추세 판정됨 — 확인 대기 {waited:.1f}"
                                 f"/{self.confirm_delay_ms / 1000:.1f}초")
            self.last_verdict_kind = "warn"
            return None

        gap = other_g - cur_g
        trend_ok = True
        if self.confirm_trend > 0:
            t = self._trend(other_key)
            trend_ok = t is not None and t > self.confirm_trend

        if gap >= self.confirm_gap and trend_ok:
            self._disarm()
            target = self._audible_idx(1 if forward else -1)
            self.index = target if target is not None else (
                min(len(self.path) - 1, self.index + 1) if forward else max(0, self.index - 1))
            self.last_verdict = (f"확인 완료 ({gap:.1f}dB) → "
                                 f"{'다음' if forward else '이전'} 노드로")
            self.last_verdict_kind = "advance" if forward else "back"
            return self._transition("forward" if forward else "backward")

        # 아직 못 미쳤으면 취소하지 않고 계속 지켜본다.
        # 대기 시간은 "이때부터 보기 시작한다"는 뜻이지 마감 시한이 아니다.
        self.last_verdict = (f"확인 중 {gap:.1f}/{self.confirm_gap:.1f}dB"
                             + ("" if trend_ok else " (추세 미달)"))
        self.last_verdict_kind = "warn"
        return None

    def evaluate(self) -> dict | None:
        """지금 상태로 전진/후퇴를 판정. 실제로 노드가 바뀌었을 때만 안내 dict를 반환.

        노드가 안 바뀌어도 판정 근거(추세·판정 문구)는 self.last_* 에 남겨둔다.
        /monitor는 이 값을 받아서 화면에 표시만 하고 자체 판정은 하지 않는다.
        """
        # 측정 중이 아니면 판정하지 않음 — 안내는 측정 구간 안에서만 나가야 하므로
        if not self.enabled or not self.active or len(self.path) < 2:
            return None

        # confirm 모드에서 이미 트리거된 상태면 확인 단계만 돌린다
        if self.mode == MODE_CONFIRM and self.armed_dir is not None:
            return self._confirm_stage()

        prev_key = self.path[self.index - 1] if self.index > 0 else None
        cur_key = self.path[self.index]
        next_key = self.path[self.index + 1] if self.index < len(self.path) - 1 else None

        # **신호가 안 잡히는 자리는 건너뛴다.**
        #
        # 경로에는 아직 한 번도 안 잡힌 비콘도 들어 있다(목적지가 정해지면 DB 로
        # 경로 전체를 미리 세우기 때문이다 — navigation.tracking_key 참고).
        # 바로 옆 칸만 보면 그런 자리에서 판정이 멈춰 안내가 영영 안 나간다.
        # 실제로 실물 비콘 두 개가 경로에서 떨어져 있으면 한 건도 못 나갔다.
        #
        # 들리는 것 중 가장 가까운 자리를 이웃으로 삼는다. 사이에 있는 비콘은
        # 지나가긴 했지만 들리지 않았을 뿐이라, 건너뛰는 것이 실제와 맞는다.
        prev_idx = self._audible_idx(-1)
        next_idx = self._audible_idx(+1)
        prev_key = self.path[prev_idx] if prev_idx is not None else None
        next_key = self.path[next_idx] if next_idx is not None else None

        trend_prev = self._trend(prev_key) if prev_key else None
        trend_cur = self._trend(cur_key)
        trend_next = self._trend(next_key) if next_key else None

        prev_latest = self._latest(prev_key)
        cur_latest = self._latest(cur_key)
        next_latest = self._latest(next_key)

        # 교차 후 신호차 판정용 — 흔들림을 줄이려고 창 평균을 쓴다
        prev_mean = self._gap_value(prev_key)
        cur_mean = self._gap_value(cur_key)
        next_mean = self._gap_value(next_key)

        self.last_trends = {"prev": trend_prev, "cur": trend_cur, "next": trend_next}

        # 전진
        trend_ok_fwd = (
            trend_next is not None and trend_cur is not None
            and trend_next > self.threshold and trend_cur < -self.threshold
        ) if self.require_trend else True

        # ── 어느 조건에서 막혔는지 남긴다 ──────────────────────────
        #
        # 전진 조건이 넷이라(추세·최소세기·값존재·신호차) 안 넘어갈 때 무엇 때문인지
        # 밖에서 알 수가 없었다. 그래프에서는 분명히 교차했는데 안내가 안 나가는
        # 상황을 눈으로만 보고 원인을 좁히려니 추측이 될 수밖에 없다.
        # 그래서 판정에 쓴 값과 실패한 조건 이름을 그대로 들고 있는다.
        gap_next = (next_mean - cur_mean) if (next_mean is not None and cur_mean is not None) else None
        gap_ok_fwd = (
            (next_latest > cur_latest) if self.min_gap <= 0
            else (gap_next is not None and gap_next >= self.min_gap)
        ) if (next_latest is not None and cur_latest is not None) else False

        blockers: list[str] = []
        if next_key is None:
            blockers.append("다음칸없음")
        if not trend_ok_fwd:
            blockers.append("추세")
        if next_latest is None:
            blockers.append("다음값없음")
        elif next_latest <= self.min_next:
            blockers.append("다음약함")
        if cur_latest is None:
            blockers.append("현재값없음")
        if not gap_ok_fwd:
            blockers.append("신호차")

        self.last_numbers = {
            "prev": prev_key, "cur": cur_key, "next": next_key,
            "tPrev": trend_prev, "tCur": trend_cur, "tNext": trend_next,
            "vPrev": prev_latest, "vCur": cur_latest, "vNext": next_latest,
            "gapNext": gap_next,
            "threshold": self.threshold, "minNext": self.min_next, "minGap": self.min_gap,
            "mode": self.mode, "requireTrend": self.require_trend,
            "blockers": blockers,
        }

        if (
            trend_ok_fwd
            and next_latest is not None
            and next_latest > self.min_next
            and cur_latest is not None
            # min_gap 이 0이면 원래대로 "다음이 더 세다"만 본다.
            # 0보다 크면 "이만큼 더 세다"를 요구하는 선택적 조건이 된다.
            and gap_ok_fwd
        ):
            self.forward_streak += 1
            self.back_streak = 0
            self.back_since = None
            now = self._now_data_ms()
            if self.forward_since is None:
                self.forward_since = now
            held = now - self.forward_since
            if self.forward_streak >= self.forward_streak_need and held >= self.min_hold_ms:
                self.forward_streak = 0
                self.forward_since = None
                if self.mode == MODE_CONFIRM:
                    # 여기서 바로 확정하지 않고 확인 단계로 넘긴다
                    self.armed_dir, self.armed_at = "forward", now
                    self.armed_index = self.index
                    self.last_verdict = "추세 판정됨 — 확인 대기 시작"
                    self.last_verdict_kind = "warn"
                    return None
                self.index = next_idx if next_idx is not None else min(len(self.path) - 1, self.index + 1)
                self.last_verdict = "전진 → 다음 노드로 이동"
                self.last_verdict_kind = "advance"
                return self._transition("forward")
            self.last_verdict = f"전진 감지 ({self.forward_streak}/{self.forward_streak_need}, 연속되면 이동)"
            self.last_verdict_kind = "warn"
            return None

        # 후퇴
        #
        # ── 다음이 오르는 중이면 후퇴가 아니다 ────────────────────
        #
        # 코너를 돌아 다음 비콘 쪽으로 향하면 **현재 비콘은 이미 등졌는데 다음
        # 비콘은 아직 안 올라온** 골짜기가 생긴다. 그 몇 초 동안은 이전 비콘이
        # 셋 중 제일 세다. 이것만 보면 "뒤로 갔다"로 읽힌다.
        #
        # 실측 412 경로에서 4번(B24) 도달 3.4초 뒤에 3번(B11)으로 되돌려
        # "경로를 벗어났습니다"가 나갔다. 사용자는 정상적으로 걷고 있었다.
        #
        # 다음 비콘이 오르고 있다는 것은 그쪽으로 가고 있다는 뜻이므로, 이전
        # 비콘이 잠깐 세더라도 후퇴로 보지 않는다. 정말 되돌아가는 중이라면
        # 다음 비콘은 멀어지므로 이 조건이 후퇴를 막지 않는다.
        next_rising = trend_next is not None and trend_next > self.threshold

        trend_ok_back = (
            trend_prev is not None and trend_prev > self.threshold
            and (trend_next is None or trend_prev > trend_next)
        ) if self.require_trend else True

        if (
            trend_ok_back
            and not next_rising
            and prev_latest is not None
            and cur_latest is not None
            and (prev_latest > cur_latest if self.min_gap <= 0 else
                 (prev_mean is not None and cur_mean is not None
                  and prev_mean - cur_mean >= self.min_gap))
        ):
            self.back_streak += 1
            self.forward_streak = 0
            self.forward_since = None
            now = self._now_data_ms()
            if self.back_since is None:
                self.back_since = now
            if self.back_streak >= self.back_streak_need and now - self.back_since >= self.min_hold_ms:
                self.back_streak = 0
                self.back_since = None
                if self.mode == MODE_CONFIRM:
                    self.armed_dir, self.armed_at = "backward", now
                    self.armed_index = self.index
                    self.last_verdict = "역방향 추세 판정됨 — 확인 대기 시작"
                    self.last_verdict_kind = "warn"
                    return None
                self.index = prev_idx if prev_idx is not None else max(0, self.index - 1)
                self.last_verdict = "후퇴 → 이전 노드로 되돌림"
                self.last_verdict_kind = "back"
                return self._transition("backward")
            self.last_verdict = f"이탈 의심 ({self.back_streak}/{self.back_streak_need}, 연속되면 되돌림)"
            self.last_verdict_kind = "warn"
            return None

        self.forward_streak = 0
        self.back_streak = 0
        self.forward_since = None
        self.back_since = None
        self.last_verdict = "유지"
        self.last_verdict_kind = ""
        return None

    def snapshot(self) -> dict | None:
        """현재 추적 상태 — /monitor가 화면에 그대로 표시하기 위한 것 (판정 결과의 사본)."""
        if not self.enabled or len(self.path) < 2:
            return None

        prev_key = self.path[self.index - 1] if self.index > 0 else None
        next_key = self.path[self.index + 1] if self.index < len(self.path) - 1 else None

        return {
            "enabled": True,
            "active": self.active,
            "mode": self.mode,
            "index": self.index,
            "number": self.index + 1,
            "total": len(self.path),
            "prev": prev_key,
            "current": self.path[self.index],
            "next": next_key,
            "trendPrev": _round(self.last_trends.get("prev")),
            "trendCur": _round(self.last_trends.get("cur")),
            "trendNext": _round(self.last_trends.get("next")),
            "minGap": self.min_gap,
            "verdict": self.last_verdict,
            "verdictKind": self.last_verdict_kind,
        }

    def _transition(self, direction: str) -> dict:
        beacon = self.path[self.index]
        name = short_name(beacon)
        number = self.index + 1
        is_last = self.index == len(self.path) - 1

        # 문장이 길면 다 읽기 전에 다음 지점으로 이동해버려서 안내가 겹침.
        # 그래서 경로 순서대로 매긴 번호(1, 2, 3...)만 읽어준다.
        return {
            "type": "guide",
            "event": "transition",
            "direction": direction,
            "index": self.index,
            "number": number,
            "total": len(self.path),
            "beacon": beacon,
            "name": name,
            "isLast": is_last,
            "speech": str(number),
            "timestamp": _now_ms(),
        }


def _now_ms() -> int:
    return int(time.time() * 1000)


def _round(v):
    return None if v is None else round(v, 1)
