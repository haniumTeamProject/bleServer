"""경로 진행 추적 (서버 측).

원래 이 판정 로직은 /monitor 페이지의 JS에만 있었는데, 비콘이 바뀌는 시점을 폰에서
음성으로 안내하려면 브라우저가 아니라 서버가 판단해야 한다(모니터 페이지를 안 열어도
동작해야 하므로). 그래서 같은 알고리즘을 그대로 파이썬으로 옮긴 것.

판정 규칙은 monitor의 updateTracking()과 동일하게 유지한다:
  - 추세 = 최근 N개 평균 - 가장 오래된 N개 평균 (N=TREND_WINDOW)
  - 전진: next 추세가 +임계값 초과 && current 추세가 -임계값 미만
          && next 절대값이 최소 감지 기준 초과 && next 절대값이 current를 앞지름
          => 2회 연속이어야 확정
  - 후퇴: prev 추세가 +임계값 초과 && prev 추세가 next 추세보다 큼
          && prev 절대값이 current를 앞지름
          => 3회 연속이어야 확정
"""

import time

TREND_WINDOW = 4
HISTORY_MAX = 40
FORWARD_STREAK = 2
BACK_STREAK = 3

DEFAULT_THRESHOLD = 3.0
DEFAULT_MIN_NEXT = -85.0

# ---- 판정 모드 ----
# "trend"   : 기존 방식. 최근 40개 중 양끝 4개씩만 평균내어 비교.
# "segment" : 시간 창을 여러 구간으로 쪼개 구간별 평균을 낸 뒤, 그 평균들의 기울기를 추세로 쓴다.
# "linreg"  : HISTORY_MAX개 원본 샘플 전체에 최소제곱 회귀선을 맞춰 기울기를 구한다.
#             segment처럼 구간 평균 없이 원시 샘플을 그대로 쓴다 (아직 실측 비교 전, trend/segment 대비 검증 필요).
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
MODE_LINREG = "linreg"

DEFAULT_MODE = MODE_TREND
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
        self.mode = DEFAULT_MODE
        self.window_ms = DEFAULT_WINDOW_MS
        self.segments = DEFAULT_SEGMENTS
        self.enabled = False   # 경로가 등록되어 있는가
        self.active = False    # 측정이 시작되어 실제로 안내를 내보내는 중인가

        # 마지막 판정 근거 — /monitor가 표시용으로만 쓴다 (판정은 여기서만 한다)
        self.last_trends: dict = {}
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
        if mode in (MODE_TREND, MODE_SEGMENT, MODE_LINREG):
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
        if self.mode == MODE_SEGMENT:
            return self._trend_segment(key)
        if self.mode == MODE_LINREG:
            return self._trend_linreg(key)
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

    def _trend_linreg(self, key: str):
        """선형회귀 방식: 최근 HISTORY_MAX개 원본 샘플 전체에 최소제곱 직선을 맞춰 기울기를 구한다.

        _trend_segment와 달리 구간으로 묶어 평균내지 않고 원시 샘플 각각을 그대로 회귀에 사용한다.
        구간 평균이 주는 1차 평활 효과는 없지만, 표본을 버리지 않고 전체 창의 추세를
        한 번에 반영한다는 점에서 _trend_ends(양끝 평균차)보다 non-monotonic 구간에 덜 흔들린다.

        x축은 실제 수신 시각(ms)이 아니라 샘플 인덱스를 쓴다 — 샘플 간격이 불규칙해도
        "표본 순서상의 추세"를 안정적으로 보기 위함. 시간 간격 자체가 중요하면
        buf의 timestamp를 x로 바꿔 쓰면 된다 (그 경우 반환값 스케일도 같이 바뀐다).

        반환값은 기울기 자체가 아니라 "윈도우 전체 구간의 변화량(dB)"으로 환산해서,
        기존 trend/segment 모드와 같은 임계값(threshold, dB)을 그대로 쓸 수 있게 한다.
        """
        buf = self.history.get(key)
        if not buf:
            return None
        vals = [v for _, v in buf][-HISTORY_MAX:]
        n = len(vals)
        if n < TREND_WINDOW * 2:
            return None

        sum_x = sum(range(n))
        sum_y = sum(vals)
        sum_xy = sum(i * v for i, v in enumerate(vals))
        sum_xx = sum(i * i for i in range(n))

        denom = n * sum_xx - sum_x * sum_x
        if denom == 0:
            return None
        slope = (n * sum_xy - sum_x * sum_y) / denom

        # 샘플 인덱스당 기울기 -> 윈도우 전체(n-1구간) 변화량으로 환산
        return slope * (n - 1)

    def _latest(self, key):
        buf = self.history.get(key) if key else None
        return buf[-1][1] if buf else None

    def evaluate(self) -> dict | None:
        """지금 상태로 전진/후퇴를 판정. 실제로 노드가 바뀌었을 때만 안내 dict를 반환.

        노드가 안 바뀌어도 판정 근거(추세·판정 문구)는 self.last_* 에 남겨둔다.
        /monitor는 이 값을 받아서 화면에 표시만 하고 자체 판정은 하지 않는다.
        """
        # 측정 중이 아니면 판정하지 않음 — 안내는 측정 구간 안에서만 나가야 하므로
        if not self.enabled or not self.active or len(self.path) < 2:
            return None

        prev_key = self.path[self.index - 1] if self.index > 0 else None
        cur_key = self.path[self.index]
        next_key = self.path[self.index + 1] if self.index < len(self.path) - 1 else None

        trend_prev = self._trend(prev_key) if prev_key else None
        trend_cur = self._trend(cur_key)
        trend_next = self._trend(next_key) if next_key else None

        prev_latest = self._latest(prev_key)
        cur_latest = self._latest(cur_key)
        next_latest = self._latest(next_key)

        self.last_trends = {"prev": trend_prev, "cur": trend_cur, "next": trend_next}

        # 전진
        if (
            trend_next is not None
            and trend_cur is not None
            and trend_next > self.threshold
            and trend_cur < -self.threshold
            and next_latest is not None
            and next_latest > self.min_next
            and cur_latest is not None
            and next_latest > cur_latest
        ):
            self.forward_streak += 1
            self.back_streak = 0
            if self.forward_streak >= FORWARD_STREAK:
                self.index += 1
                self.forward_streak = 0
                self.last_verdict = "전진 → 다음 노드로 이동"
                self.last_verdict_kind = "advance"
                return self._transition("forward")
            self.last_verdict = f"전진 감지 ({self.forward_streak}/{FORWARD_STREAK}, 연속되면 이동)"
            self.last_verdict_kind = "warn"
            return None

        # 후퇴
        if (
            trend_prev is not None
            and trend_prev > self.threshold
            and (trend_next is None or trend_prev > trend_next)
            and prev_latest is not None
            and cur_latest is not None
            and prev_latest > cur_latest
        ):
            self.back_streak += 1
            self.forward_streak = 0
            if self.back_streak >= BACK_STREAK:
                self.index = max(0, self.index - 1)
                self.back_streak = 0
                self.last_verdict = "후퇴 → 이전 노드로 되돌림"
                self.last_verdict_kind = "back"
                return self._transition("backward")
            self.last_verdict = f"이탈 의심 ({self.back_streak}/{BACK_STREAK}, 연속되면 되돌림)"
            self.last_verdict_kind = "warn"
            return None

        self.forward_streak = 0
        self.back_streak = 0
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
