from collections import deque
from enum import Enum


class BeaconState(str, Enum):
    ARRIVED = "ARRIVED"
    DEPARTED = "DEPARTED"
    STABLE = "STABLE"  # Java 원본에도 정의만 있고 실제로 안 쓰이는 상태값. 그대로 포팅.


# Java RssiFilterPipeline 그대로 포팅: 중앙값 필터 -> 칼만 필터 -> 히스테리시스(도착/이탈 판정)
class RssiFilterPipeline:
    MEDIAN_WINDOW = 3

    def __init__(
        self,
        q: float = 0.008,
        r: float = 4.0,
        entry_threshold: float = -65.0,
        exit_threshold: float = -75.0,
    ):
        self.q = q
        self.r = r
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold

        self.x = 0.0
        self.p = 1.0
        self.initialized = False
        self.state = BeaconState.DEPARTED
        self._median_buffer: deque[float] = deque()

    def filter(self, measurement: float) -> float:
        median_filtered = self._apply_median_filter(measurement)
        kalman_filtered = self._apply_kalman_filter(median_filtered)
        self._apply_hysteresis(kalman_filtered)
        return kalman_filtered

    def _apply_median_filter(self, measurement: float) -> float:
        self._median_buffer.append(measurement)
        if len(self._median_buffer) > self.MEDIAN_WINDOW:
            self._median_buffer.popleft()
        if len(self._median_buffer) < 3:
            return measurement
        sorted_values = sorted(self._median_buffer)
        return sorted_values[len(sorted_values) // 2]

    def _apply_kalman_filter(self, measurement: float) -> float:
        if not self.initialized:
            self.x = measurement
            self.initialized = True
            return self.x
        self.p += self.q
        k = self.p / (self.p + self.r)
        self.x = self.x + k * (measurement - self.x)
        self.p = (1 - k) * self.p
        return self.x

    def _apply_hysteresis(self, filtered_rssi: float) -> None:
        if self.state == BeaconState.ARRIVED:
            if filtered_rssi <= self.exit_threshold:
                self.state = BeaconState.DEPARTED
        else:
            self.state = (
                BeaconState.ARRIVED if filtered_rssi >= self.entry_threshold else BeaconState.DEPARTED
            )

    def reset(self) -> None:
        self._median_buffer.clear()
        self.x = 0.0
        self.p = 1.0
        self.initialized = False
        self.state = BeaconState.DEPARTED
