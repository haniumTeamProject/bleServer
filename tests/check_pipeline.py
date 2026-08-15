"""음성 목적지 파이프라인을 **구간별로** 점검한다.

폰에서 말한 한마디가 안내 음성으로 돌아오기까지 거치는 곳이 많다.
어딘가 안 되면 "어디서 막혔는지"부터 알아야 하는데, 앱만 보고는 알 수 없다.

    폰 STT ──► WebSocket ──► 서버 ──► Ollama ──► 응답 ──► 폰 TTS
              (여기부터 이 스크립트가 확인한다)

사용법:

    python tests/check_pipeline.py                    # 전 구간 점검
    python tests/check_pipeline.py --url ws://192.168.0.10:8000/ws
    python tests/check_pipeline.py --say "화장실"       # 한마디만 왕복
    python tests/check_pipeline.py --load             # RSSI를 흘리면서 목적지 요청

`--load` 가 중요하다. 목적지를 해석하는 동안 서버가 멈추면 RSSI 중계가 같이
끊기는데, 조용한 상태로 시험하면 그게 안 보인다. 실제로는 걸어가면서 말하므로
RSSI가 계속 흐르는 상태로 재봐야 한다.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import websockets
except ImportError:
    print("websockets 가 필요합니다:  pip install websockets")
    raise SystemExit(1)

DEFAULT_URL = "ws://localhost:8000/ws"

OK, BAD, WARN = "✓", "✗", "!"


def line(mark: str, label: str, detail: str = "") -> None:
    print(f"  {mark} {label:<34} {detail}")


# ---------------------------------------------------------------------------
# 구간 1~2 — 서버가 떠 있고 WebSocket 이 붙는가
# ---------------------------------------------------------------------------
async def connect(url: str):
    """연결하고 걸린 시간을 잰다.

    **`async with` 로 감싸지 않는다.** websockets 는 버전에 따라
    `await connect(...)` 가 돌려주는 객체가 비동기 컨텍스트 매니저가 아니다.

        websockets 14  → 지원함
        websockets 12  → TypeError: ... does not support the asynchronous
                         context manager protocol

    팀원마다 버전이 다르므로 어느 쪽에서도 도는 방식(try/finally + close)을 쓴다.
    """
    t0 = time.time()
    ws = await websockets.connect(url, open_timeout=10)
    return ws, time.time() - t0


async def close_quietly(ws) -> None:
    try:
        await ws.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 구간 3 — 서버가 랜드마크를 들고 있는가
# ---------------------------------------------------------------------------
async def check_landmarks(ws) -> list[str]:
    await ws.send(json.dumps({"type": "destination", "event": "list"}))
    msg = await wait_destination(ws, timeout=10)
    if not msg or msg.get("event") != "list":
        return []
    return [lm.get("name", "") for lm in msg.get("landmarks") or []]


# ---------------------------------------------------------------------------
# 구간 4~5 — 모델이 실제로 돌았는가
# ---------------------------------------------------------------------------
async def wait_destination(ws, timeout: float = 30.0) -> dict | None:
    """destination 응답만 골라낸다. /ws 는 RSSI 중계도 같이 흘러나온다."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        remain = timeout - (time.time() - t0)
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remain)
        except asyncio.TimeoutError:
            return None
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("type") == "destination":
            return msg
    return None


async def say(ws, text: str, event: str = "resolve") -> tuple[dict | None, float]:
    """폰이 보내는 것과 같은 JSON 을 보내고 응답까지 잰다."""
    await ws.send(json.dumps({
        "type": "destination", "event": event, "text": text,
        "requestId": f"check-{int(time.time() * 1000)}",
        "timestamp": int(time.time() * 1000), "device": "check_pipeline.py",
    }, ensure_ascii=False))
    t0 = time.time()
    return await wait_destination(ws), time.time() - t0


def describe(msg: dict) -> str:
    ev = msg.get("event")
    if ev == "resolved":
        return f"확정 {(msg.get('landmark') or {}).get('name')}"
    if ev == "ambiguous":
        return f"되묻기 {[c.get('name') for c in msg.get('candidates') or []]}"
    if ev == "notFound":
        return "못 찾음"
    return str(ev)


# ---------------------------------------------------------------------------
# RSSI 를 흘리면서 — 목적지 해석 중에 중계가 끊기는지 본다
# ---------------------------------------------------------------------------
async def rssi_stream(ws, stop: asyncio.Event, sent: list) -> None:
    """폰처럼 초당 10번 RSSI 를 올려보낸다."""
    while not stop.is_set():
        await ws.send(json.dumps({
            "beacon1": -70.0, "beacon2": -78.0,
            "timestamp": int(time.time() * 1000),
        }))
        sent.append(time.time())
        await asyncio.sleep(0.1)


async def rssi_watch(ws, stop: asyncio.Event, seen: list) -> None:
    """다른 연결에서 중계가 실제로 흘러나오는지 지켜본다."""
    while not stop.is_set():
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        except websockets.exceptions.WebSocketException:
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("type") not in ("destination", "guide"):
            seen.append(time.time())


def biggest_gap(stamps: list) -> float:
    """중계가 가장 오래 끊긴 구간(초)."""
    if len(stamps) < 2:
        return 0.0
    return max(b - a for a, b in zip(stamps, stamps[1:]))


async def run_load_test(url: str, text: str) -> bool:
    """RSSI 를 흘리는 도중에 목적지를 물어보고, 중계가 끊기는지 잰다.

    text 는 **아직 안 물어본 말**이어야 한다. 서버가 캐시해둔 말이면 모델을
    아예 안 불러서 느린 경로를 안 타고, 그러면 이 측정이 무의미해진다.
    """
    print("\n── RSSI 를 흘리면서 목적지 해석 ──")
    print("  폰 역할과 모니터 역할로 각각 붙어서, 목적지를 해석하는 동안")
    print("  중계가 멈추는지 본다.\n")

    stop = asyncio.Event()
    sent: list = []
    seen: list = []

    phone = await websockets.connect(url, open_timeout=10)
    monitor = await websockets.connect(url, open_timeout=10)
    try:
        streamer = asyncio.create_task(rssi_stream(phone, stop, sent))
        watcher = asyncio.create_task(rssi_watch(monitor, stop, seen))

        await asyncio.sleep(1.0)          # 흐름이 자리잡을 때까지
        before = len(seen)

        msg, dt = await say(phone, text)

        await asyncio.sleep(1.0)          # 밀린 게 있으면 여기서 드러난다
        stop.set()
        await asyncio.gather(streamer, watcher, return_exceptions=True)
    finally:
        await close_quietly(phone)
        await close_quietly(monitor)

    gap = biggest_gap(seen)
    got = describe(msg) if msg else "응답 없음"

    line(OK if msg else BAD, "목적지 응답", f"{got}  ({dt:.2f}초)")
    line(OK, "그동안 보낸 RSSI", f"{len(sent)}개")

    if dt < 0.3:
        # 서버가 같은 말을 캐시해두면 모델을 안 부른다. 그러면 느린 경로를
        # 안 타므로 이 측정은 아무것도 증명하지 못한다.
        line(WARN, "측정 무효", f"{dt:.2f}초 — 캐시에 걸려 모델을 안 불렀다")
        print("\n  아직 안 물어본 이름으로 다시 재야 한다:")
        print("     python tests/check_pipeline.py --load --say <다른 이름>")
        return True
    line(OK, "모니터가 받은 중계", f"{len(seen)}개 (요청 시점 이후 {len(seen) - before}개)")

    # 해석에 걸린 시간만큼 중계가 끊겼으면 이벤트 루프가 막혔다는 뜻이다.
    blocked = gap > max(0.5, dt * 0.6)
    line(BAD if blocked else OK, "중계가 끊긴 최대 구간", f"{gap:.2f}초")
    if blocked:
        print(f"\n  {BAD} 목적지를 해석하는 동안 중계가 {gap:.1f}초 멈췄다.")
        print("     이벤트 루프가 막혔다는 뜻이다 — handler.websocket_endpoint 의")
        print("     asyncio.to_thread 분기가 동작하는지 확인할 것.")
    else:
        print(f"\n  {OK} 해석에 {dt:.1f}초가 걸렸는데도 중계는 최대 {gap:.2f}초만 비었다.")
        print("     목적지 해석이 중계를 막지 않는다.")
    return not blocked and msg is not None


# ---------------------------------------------------------------------------
async def run(url: str, one: str | None, load: bool) -> int:
    print(f"점검 대상: {url}\n")

    print("── 연결 ──")
    try:
        ws, dt = await connect(url)
    except (OSError, websockets.exceptions.WebSocketException) as e:
        line(BAD, "WebSocket 연결", str(e))
        print("\n  서버가 떠 있는지 확인:  uvicorn app.main:app --host 0.0.0.0 --port 8000")
        print(f"  주소가 맞는지 확인:      --url {DEFAULT_URL}")
        print("  폰에서 붙을 때는 localhost 가 아니라 PC 의 실제 IP 를 써야 한다.")
        return 1
    line(OK, "WebSocket 연결", f"{dt * 1000:.0f} ms")

    failed = 0
    try:
        print("\n── 랜드마크 ──")
        names = await check_landmarks(ws)
        if not names:
            line(BAD, "서버가 들고 있는 랜드마크", "0개")
            print("\n  지도 프로젝트 파일을 못 찾았을 가능성이 크다.")
            print("  MAP_TOOL_DIR 환경변수로 지정할 수 있다.")
            return 1
        line(OK, "서버가 들고 있는 랜드마크", f"{len(names)}개")
        print(f"     {', '.join(names[:10])}{' …' if len(names) > 10 else ''}")

        if one:
            print("\n── 한마디 왕복 ──")
            msg, dt = await say(ws, one)
            if not msg:
                line(BAD, f'"{one}"', "응답 없음")
                return 1
            line(OK, f'"{one}"', f"{describe(msg)}  ({dt:.2f}초)")
            line(OK, "판단 주체", msg.get("source", "?"))
            line(OK, "안내 음성", f'"{msg.get("speech")}"')
            return 0

        # 실제 이름을 하나 골라서 왕복시킨다. 시험 문장을 코드에 박지 않는다.
        sample = names[0]
        print("\n── 왕복 ──")
        msg, dt = await say(ws, sample)
        if not msg:
            line(BAD, f'"{sample}"', "응답 없음 (서버 로그 확인)")
            failed += 1
        else:
            line(OK, f'"{sample}"', f"{describe(msg)}  ({dt:.2f}초)")
            line(OK, "판단 주체", msg.get("source", "?"))

            src = msg.get("source", "")
            if src == "llm":
                line(OK, "Ollama", "모델이 판단했다")
            elif src.startswith("llm→rule"):
                line(WARN, "Ollama", f"실패해서 규칙 엔진이 받았다 — {src}")
                print("     ollama serve 가 떠 있는지, 모델이 받아져 있는지 확인:")
                print("       ollama ps    /    ollama list")
                failed += 1
            elif src == "rule":
                line(WARN, "Ollama", "LLM_PROVIDER=off 로 아예 끄고 있다")

            # 되묻기가 이어지는지
            if msg.get("event") == "ambiguous":
                cands = [c.get("name") for c in msg.get("candidates") or []]
                print("\n── 되묻기 ──")
                line(OK, "안내 음성", f'"{msg.get("speech")}"')
                pick, dt2 = await say(ws, cands[-1], event="choose")
                want = cands[-1]
                if pick and (pick.get("landmark") or {}).get("name") == want:
                    line(OK, f'"{want}" 라고 답하면', f"{describe(pick)}  ({dt2:.2f}초)")
                else:
                    line(BAD, f'"{want}" 라고 답하면',
                         describe(pick) if pick else "응답 없음")
                    failed += 1
    finally:
        await close_quietly(ws)

    if load:
        # 위에서 한 번 물어본 이름은 서버 캐시에 남아 모델을 안 부른다.
        # 부하 측정은 반드시 **처음 묻는 이름**으로 해야 의미가 있다.
        fresh = names[1] if len(names) > 1 else names[0]
        ok = await run_load_test(url, fresh)
        if not ok:
            failed += 1

    print()
    if failed:
        print(f"{BAD} {failed}군데에서 막혔다. 위 표시를 따라가면 된다.")
        return 1
    print(f"{OK} 폰이 말한 한마디가 안내 음성으로 돌아오기까지 전 구간이 살아 있다.")
    print("  남은 것은 폰의 마이크(STT)와 스피커(TTS)뿐이고, 그건 실제 기기에서 확인해야 한다.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--say", dest="one", default=None, help="이 한마디만 왕복시킨다")
    ap.add_argument("--load", action="store_true",
                    help="RSSI를 흘리면서 목적지를 물어 중계가 끊기는지 잰다")
    a = ap.parse_args()
    try:
        return asyncio.run(run(a.url, a.one, a.load))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
