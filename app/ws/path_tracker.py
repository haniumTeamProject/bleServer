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


def short_name(key: str) -> str:
    """비콘 키는 "MAC|이름" 형태 — 안내 음성에는 이름만 쓴다."""
    if not key:
        return ""
    head, sep, tail = key.partition("|")
    return tail if sep else head


class PathTracker:
    def __init__(self) -> None:
        self.path: list[str] = []
        self.history: dict[str, list[float]] = {}
        self.index = 0
        self.forward_streak = 0
        self.back_streak = 0
        self.threshold = DEFAULT_THRESHOLD
        self.min_next = DEFAULT_MIN_NEXT
        self.enabled = False   # 경로가 등록되어 있는가
        self.active = False    # 측정이 시작되어 실제로 안내를 내보내는 중인가

        # 마지막 판정 근거 — /monitor가 표시용으로만 쓴다 (판정은 여기서만 한다)
        self.last_trends: dict = {}
        self.last_verdict = "대기 중"
        self.last_verdict_kind = ""

    # ---- 설정 ----

    def set_path(self, path: list[str], threshold=None, min_next=None) -> dict:
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

    def feed(self, key: str, filtered_rssi: float) -> None:
        buf = self.history.setdefault(key, [])
        buf.append(filtered_rssi)
        if len(buf) > HISTORY_MAX:
            del buf[: len(buf) - HISTORY_MAX]

    # ---- 판정 ----

    def _trend(self, key: str):
        buf = self.history.get(key)
        if not buf or len(buf) < TREND_WINDOW * 2:
            return None
        recent = sum(buf[-TREND_WINDOW:]) / TREND_WINDOW
        old = sum(buf[:TREND_WINDOW]) / TREND_WINDOW
        return recent - old

    def _latest(self, key):
        buf = self.history.get(key) if key else None
        return buf[-1] if buf else None

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
