import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws.rssi_filter import RssiFilterPipeline

router = APIRouter()

# Java WebSocketHandler와 동일하게 포팅.
# 주의(원본 그대로 유지된 한계): 연결된 모든 세션이 _filters를 공유함 —
# 사용자가 여러 명 동시 접속하면 같은 비콘 키의 필터 상태가 섞일 수 있음.
# (예전에 얘기했던 "세션별로 필터 분리 안 됨" 이슈, 실사용 단계에선 손봐야 함)
_connections: set[WebSocket] = set()
_filters: dict[str, RssiFilterPipeline] = {}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _connections.add(websocket)
    print(f"Connected: {id(websocket)}")

    try:
        while True:
            raw = await websocket.receive_text()
            payload = _process_message(raw)

            for conn in list(_connections):
                if conn is websocket:
                    continue
                try:
                    await conn.send_text(payload)
                except Exception:
                    pass  # 끊긴 연결에 보내다 실패하는 경우 무시하고 계속 진행
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(websocket)
        print(f"Disconnected: {id(websocket)}")


def _process_message(raw: str) -> str:
    try:
        data: dict = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"필터 오류, 원본 전송: {e}")
        return raw

    try:
        filtered: dict = {"timestamp": data.get("timestamp", int(time.time() * 1000))}

        for key, value in data.items():
            if key == "timestamp":
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue

            rssi = float(value)
            if rssi >= 0 or rssi == 127:
                continue

            pipeline = _filters.setdefault(key, RssiFilterPipeline())
            filtered_rssi = pipeline.filter(rssi)
            rounded = round(filtered_rssi, 1)

            filtered[key] = rssi  # 원본값
            filtered[f"{key}__f"] = rounded  # 칼만 필터값

            print(f"비콘 {key} | 원본: {rssi:.1f} | 필터: {rounded:.1f} | 상태: {pipeline.state.value}")

        return json.dumps(filtered, ensure_ascii=False)
    except Exception as e:
        print(f"필터 오류, 원본 전송: {e}")
        return raw
