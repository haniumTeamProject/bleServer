"""폰 실측을 서버가 자동으로 기록한다.

── 왜 서버가 기록하나 ────────────────────────────────────────────

지금까지 실측 기록은 `/monitor` 의 "측정 시작" 버튼으로 브라우저가 만들었다.
그런데 폰을 들고 걸어다니는 사람은 브라우저를 못 누른다. 누군가 옆에서 화면을
보며 눌러줘야 했고, 시작·종료 시점이 실제 출발·도착과 어긋났다.

목적지가 정해지는 순간이 곧 "구간 측정 시작"이고 도착이 "종료"다. 서버는 그 둘을
이미 알고 있으므로 버튼 없이 정확한 구간을 남길 수 있다.

── 무엇을 남기나 ────────────────────────────────────────────────

    <이름>.csv    RSSI 원본·필터값  — `tests/eval_tracker.py` 가 읽는 형식 그대로
    <이름>.json   경로·판정 설정·전환 시점

CSV 를 기존 형식에 맞춘 이유는 **다시 돌려볼 수 있어야** 하기 때문이다. 판정
파라미터를 바꿔가며 같은 걸음을 재평가하려면 원본 신호가 남아 있어야 하고,
이미 그 일을 하는 도구가 있다(`eval_tracker.py --sweep-full`).

JSON 에는 "그때 어떤 설정으로 어디서 판정했는가"를 남긴다. CSV 만 있으면 나중에
"이 전환이 왜 여기서 났지"를 재구성할 수 없다.

── 끄기 ──────────────────────────────────────────────────────────

    NAV_RECORD=0     기록 안 함
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

ENABLED = os.environ.get("NAV_RECORD", "1") != "0"

# bleServer/measurements/ — 실행 중 쌓이는 것이라 실측 데이터셋(tests/measurements)과
# 섞지 않는다. 쓸 만한 걸 고른 뒤 그쪽으로 옮기고 manifest 에 등록하면 된다.
_DIR = Path(__file__).resolve().parents[2] / "measurements"


def _safe(name: str) -> str:
    """파일 이름에 쓸 수 있게 다듬는다. 한글은 그대로 둔다 — 읽을 사람이 봐야 한다."""
    out = "".join(c for c in str(name or "") if c.isalnum() or c in "-_()")
    return out[:24] or "?"


def _iso(ms: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms / 1000)) + f".{int(ms % 1000):03d}Z"


class NavRecorder:
    """안내 한 번 = 파일 한 쌍. 목적지가 정해질 때 열고 도착·취소·끊김에 닫는다."""

    def __init__(self, session_id: str, origin: str, destination: str) -> None:
        self.started_ms = time.time() * 1000
        # 이름을 "출발_목적지_시각" 으로 둔다. 실측을 여러 번 돌리면 파일이 금방
        # 쌓이는데, 이 순서면 폴더를 이름으로 정렬하는 것만으로 같은 구간끼리
        # 모이고 그 안에서 시간순이 된다.
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        self.stem = f"{_safe(origin)}_{_safe(destination)}_{stamp}"
        self.rows = 0
        self.meta: dict = {}
        self.transitions: list[dict] = []
        self._csv = None
        try:
            _DIR.mkdir(parents=True, exist_ok=True)
            self._csv = (_DIR / f"{self.stem}.csv").open("w", encoding="utf-8", newline="")
            self._csv.write("timestamp_iso,elapsed_ms,beacon,raw_rssi,filtered_rssi\n")
        except Exception as e:
            print(f"[기록] 파일을 열지 못했습니다: {e}")
            self._csv = None

    # -- 쓰기 -------------------------------------------------------------
    def sample(self, beacon: str, raw: float, filtered: float) -> None:
        if self._csv is None:
            return
        now = time.time() * 1000
        elapsed = int(now - self.started_ms)
        try:
            self._csv.write(f'{_iso(now)},{elapsed},"{beacon}",{raw:.0f},{filtered:.1f}\n')
            self.rows += 1
        except Exception:
            self._csv = None

    def transition(self, t: dict, numbers: dict, verdict: str) -> None:
        """판정이 난 지점. **어디서 났는지가 이 기록의 핵심이다.**"""
        self.transitions.append({
            "elapsedMs": int(time.time() * 1000 - self.started_ms),
            "direction": t.get("direction"),
            "number": t.get("number"),
            "total": t.get("total"),
            "from": numbers.get("cur"),
            "to": numbers.get("next"),
            "gapDb": numbers.get("gapNext"),
            "trendCur": numbers.get("tCur"),
            "trendNext": numbers.get("tNext"),
            "verdict": verdict,
        })

    # -- 닫기 -------------------------------------------------------------
    def close(self, reason: str) -> None:
        if self._csv is not None:
            try:
                self._csv.close()
            except Exception:
                pass
            self._csv = None
        self.meta["끝난이유"] = reason
        self.meta["길이초"] = round((time.time() * 1000 - self.started_ms) / 1000, 1)
        self.meta["표본수"] = self.rows
        self.meta["전환"] = self.transitions
        try:
            (_DIR / f"{self.stem}.json").write_text(
                json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[기록] 저장 실패: {e}")
            return
        print(f"[기록] {self.stem} — {self.meta['길이초']}초 · 표본 {self.rows}개 · "
              f"전환 {len(self.transitions)}회 ({reason})")


def start(session_id: str, origin: str, destination: str, meta: dict) -> NavRecorder | None:
    if not ENABLED:
        return None
    rec = NavRecorder(session_id, origin, destination)
    rec.meta.update(meta)
    return rec
