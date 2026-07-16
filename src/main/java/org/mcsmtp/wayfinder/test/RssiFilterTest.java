package org.mcsmtp.wayfinder.test;

import org.mcsmtp.wayfinder.filter.RssiFilterPipeline;

/**
 * RssiFilterPipeline 테스트
 * 비콘 없이 가짜 RSSI 값으로 필터 동작 확인
 *
 * 실행 방법:
 * 1. IDE에서 main() 직접 실행
 * 2. 또는 터미널: ./gradlew test
 */
public class RssiFilterTest {

    public static void main(String[] args) {
        System.out.println("===== RssiFilterPipeline 테스트 =====\n");

        test1_기본스무딩();
        test2_튀는값제거();
        test3_히스테리시스();
        test4_가까워지고멀어지기();
    }

    // ─── 테스트 1: 기본 스무딩 ───────────────────────────
    static void test1_기본스무딩() {
        System.out.println("[ 테스트 1 ] 기본 스무딩");
        System.out.println("→ 비슷한 값들이 들어올 때 안정적으로 출력되는지 확인\n");

        RssiFilterPipeline filter = new RssiFilterPipeline();

        double[] rssiValues = {-65, -67, -64, -66, -65, -68, -66, -65};

        System.out.printf("%-6s %-10s %-10s%n", "회차", "원본 RSSI", "필터 RSSI");
        System.out.println("─".repeat(30));

        for (int i = 0; i < rssiValues.length; i++) {
            double filtered = filter.filter(rssiValues[i]);
            System.out.printf("%-6d %-10.1f %-10.1f%n", i + 1, rssiValues[i], filtered);
        }
        System.out.println();
    }

    // ─── 테스트 2: 튀는 값 제거 ─────────────────────────
    static void test2_튀는값제거() {
        System.out.println("[ 테스트 2 ] 튀는 값 제거");
        System.out.println("→ -90 같은 튄 값이 들어와도 필터가 크게 안 흔들리는지 확인\n");

        RssiFilterPipeline filter = new RssiFilterPipeline();

        double[] rssiValues = {-65, -67, -90, -64, -91, -66, -65, -90, -65};

        System.out.printf("%-6s %-10s %-10s %-8s%n", "회차", "원본 RSSI", "필터 RSSI", "튄값?");
        System.out.println("─".repeat(38));

        for (int i = 0; i < rssiValues.length; i++) {
            double filtered = filter.filter(rssiValues[i]);
            boolean isSpiked = rssiValues[i] < -80;
            System.out.printf(
                "%-6d %-10.1f %-10.1f %-8s%n",
                i + 1, rssiValues[i], filtered,
                isSpiked ? "⚠ 튄값" : ""
            );
        }
        System.out.println();
    }

    // ─── 테스트 3: 히스테리시스 ─────────────────────────
    static void test3_히스테리시스() {
        System.out.println("[ 테스트 3 ] 히스테리시스");
        System.out.println("→ 경계선 (-65) 근처에서 왔다갔다해도 상태가 안 바뀌는지 확인");
        System.out.println("→ 진입 임계값: -65dBm / 이탈 임계값: -75dBm\n");

        RssiFilterPipeline filter = new RssiFilterPipeline();

        double[] rssiValues = {-80, -75, -70, -64, -66, -64, -66, -76, -70, -63};

        System.out.printf("%-6s %-10s %-10s %-12s%n", "회차", "원본 RSSI", "필터 RSSI", "비콘 상태");
        System.out.println("─".repeat(42));

        for (int i = 0; i < rssiValues.length; i++) {
            double filtered = filter.filter(rssiValues[i]);
            RssiFilterPipeline.BeaconState state = filter.getState();
            System.out.printf(
                "%-6d %-10.1f %-10.1f %-12s%n",
                i + 1, rssiValues[i], filtered,
                stateEmoji(state)
            );
        }
        System.out.println();
    }

    // ─── 테스트 4: 가까워지고 멀어지기 ──────────────────
    static void test4_가까워지고멀어지기() {
        System.out.println("[ 테스트 4 ] 가까워지고 멀어지기");
        System.out.println("→ 실제 이동 시뮬레이션\n");

        RssiFilterPipeline filter = new RssiFilterPipeline();

        // 멀리 있다가 → 가까워졌다가 → 다시 멀어짐
        double[] rssiValues = {
            -85, -83, -80,          // 멀리서 접근 중
            -75, -70, -68,          // 더 가까워짐
            -65, -63, -62,          // 비콘 도달
            -63, -90, -64,          // 튄 값 포함
            -65, -68, -72,          // 멀어지기 시작
            -76, -80, -85           // 이탈
        };

        System.out.printf("%-6s %-10s %-10s %-12s%n", "회차", "원본 RSSI", "필터 RSSI", "비콘 상태");
        System.out.println("─".repeat(42));

        for (int i = 0; i < rssiValues.length; i++) {
            double filtered = filter.filter(rssiValues[i]);
            RssiFilterPipeline.BeaconState state = filter.getState();
            System.out.printf(
                "%-6d %-10.1f %-10.1f %-12s%n",
                i + 1, rssiValues[i], filtered,
                stateEmoji(state)
            );
        }
        System.out.println();
    }

    // 상태 이모지 변환
    static String stateEmoji(RssiFilterPipeline.BeaconState state) {
        return switch (state) {
            case ARRIVED  -> "✅ 도달";
            case DEPARTED -> "❌ 이탈";
            case STABLE   -> "➡ 유지";
        };
    }
}
