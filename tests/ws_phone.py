"""폰인 척하고 서버에 붙어서 목적지 흐름을 시험한다.

안드로이드 앱을 빌드하지 않고도 **마이크를 뺀 전부**를 확인할 수 있다.
서버가 메시지를 제대로 받는지, 응답이 앱이 기대하는 모양인지, 되묻기가 이어지는지.

    # 서버를 먼저 띄운 상태에서
    python tests/ws_phone.py                          # 대화형
    python tests/ws_phone.py --url ws://localhost:8000/ws
    python tests/ws_phone.py --scenario               # 정해둔 대화 자동 실행
    python tests/ws_phone.py "변소 급해요"              # 한 마디만

앱이 실제로 보내는 것과 **똑같은 JSON**을 보낸다(BleScanner.sendDestination 참고).
그래서 여기서 되면 앱에서도 된다 — 남는 건 마이크(STT)뿐이다.
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

SCENARIO = [
    ("resolve", "407호로 가줘", "방 번호 — 한 번에 확정되어야 함"),
    ("resolve", "변소 급해요", "동의어 — LLM이 붙어 있어야 잡힌다"),
    ("choose", "두 번째", "되묻기 답변"),
    ("resolve", "409", "같은 번호 2곳 → 되묻기"),
    ("choose", "첫번째", "되묻기 답변"),
    ("resolve", "옥상정원", "없는 곳 → 거절"),
]


def phone_message(event: str, text: str) -> dict:
    """BleScanner.sendDestination() 이 만드는 것과 같은 형태."""
    return {
        "type": "destination",
        "event": event,
        "text": text,
        "requestId": time.strftime("%Y%m%d-%H%M%S") + "-test",
        "timestamp": int(time.time() * 1000),
        "device": "ws_phone.py",
    }


def render(msg: dict) -> str | None:
    """서버 응답을 사람이 읽을 수 있게. 목적지 응답이 아니면 None."""
    if msg.get("type") != "destination":
        return None
    ev = msg.get("event")
    if ev == "resolved":
        lm = msg.get("landmark") or {}
        return (f"  🖥  확정   {lm.get('name')}  (id={lm.get('id')}, "
                f"x={lm.get('x')}, y={lm.get('y')})\n"
                f"  🔊 \"{msg.get('speech')}\"")
    if ev == "ambiguous":
        names = [c.get("name") for c in msg.get("candidates") or []]
        return (f"  🖥  되묻기 {names}\n"
                f"  🔊 \"{msg.get('speech')}\"")
    if ev == "notFound":
        sug = [c.get("name") for c in msg.get("suggestions") or []]
        return (f"  🖥  못 찾음  참고후보={sug}\n"
                f"  🔊 \"{msg.get('speech')}\"")
    if ev == "list":
        names = [c.get("name") for c in msg.get("landmarks") or []]
        return f"  🖥  랜드마크 {len(names)}개: {names}"
    return f"  🖥  {ev}  {msg.get('speech', '')}"


async def send_and_wait(ws, event: str, text: str, timeout: float = 30.0) -> dict | None:
    """한 마디 보내고 목적지 응답이 올 때까지 기다린다.

    /ws 는 RSSI 중계도 같이 흘러나오므로 destination 만 골라야 한다.
    """
    await ws.send(json.dumps(phone_message(event, text), ensure_ascii=False))
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("type") == "destination":
            return msg
    return None


async def run(url: str, scenario: bool, once: str | None) -> int:
    print(f"연결 시도: {url}")
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            print("연결됨. 폰과 똑같은 JSON을 보냅니다.\n")

            if once:
                print(f"  📱 \"{once}\"")
                t0 = time.time()
                r = await send_and_wait(ws, "resolve", once)
                if r is None:
                    print("  ✗ 응답이 없습니다 (서버 로그 확인)")
                    return 1
                print(render(r), f"\n  ({time.time() - t0:.2f}초)")
                return 0

            if scenario:
                pending = False
                for event, text, why in SCENARIO:
                    if event == "choose" and not pending:
                        continue          # 되물은 적 없으면 건너뛴다
                    print(f"  📱 {event:8} \"{text}\"      ← {why}")
                    t0 = time.time()
                    r = await send_and_wait(ws, event, text)
                    if r is None:
                        print("  ✗ 응답 없음\n")
                        return 1
                    print(render(r), f"  ({time.time() - t0:.2f}초)\n")
                    pending = r.get("event") == "ambiguous"
                print("시나리오 완료.")
                return 0

            print("말할 문장을 입력하세요. 빈 줄이면 종료.")
            print("되물으면 그다음 입력은 자동으로 선택(choose)으로 보냅니다.\n")
            pending = False
            loop = asyncio.get_event_loop()
            while True:
                prompt = "  선택 > " if pending else "  목적지 > "
                text = (await loop.run_in_executor(None, input, prompt)).strip()
                if not text:
                    return 0
                t0 = time.time()
                r = await send_and_wait(ws, "choose" if pending else "resolve", text)
                if r is None:
                    print("  ✗ 응답 없음 (서버 로그 확인)\n")
                    continue
                print(render(r), f"  ({time.time() - t0:.2f}초)\n")
                pending = r.get("event") == "ambiguous"

    except (OSError, websockets.exceptions.WebSocketException) as e:
        print(f"✗ 연결 실패: {e}")
        print("\n  · 서버가 떠 있는지 확인:  uvicorn app.main:app --port 8000")
        print(f"  · 주소가 맞는지 확인:      --url {DEFAULT_URL}")
        print("  · 원격 서버면 wss:// 를 쓰세요")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--scenario", action="store_true", help="정해둔 대화 자동 실행")
    ap.add_argument("text", nargs="*", help="한 마디만 보내고 끝")
    a = ap.parse_args()
    return asyncio.run(run(a.url, a.scenario, " ".join(a.text) or None))


if __name__ == "__main__":
    raise SystemExit(main())
