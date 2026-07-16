package org.mcsmtp.wayfinder;

import org.junit.jupiter.api.Test;
import org.mcsmtp.wayfinder.filter.RssiFilterPipeline;

public class RssiFilterTest {

    @Test
    void basicSmoothing() {
        RssiFilterPipeline filter = new RssiFilterPipeline();
        double[] rssiValues = {-65, -67, -64, -66, -65, -68, -66, -65};
        for (double rssi : rssiValues) {
            double filtered = filter.filter(rssi);
            System.out.printf("원본: %.1f | 필터: %.1f%n", rssi, filtered);
        }
    }

    @Test
    void spikeRemoval() {
        RssiFilterPipeline filter = new RssiFilterPipeline();
        double[] rssiValues = {-65, -67, -90, -64, -91, -66, -65};
        for (double rssi : rssiValues) {
            double filtered = filter.filter(rssi);
            System.out.printf("원본: %.1f | 필터: %.1f%n", rssi, filtered);
        }
    }

    @Test
    void hysteresis() {
        RssiFilterPipeline filter = new RssiFilterPipeline(0.008, 1.0, -65.0, -75.0);
        double[] rssiValues = {-85,-85,-85,-85,-85,-55,-55,-55,-55,-55,-55,-55,-55,-55,-55,-85,-85,-85,-85,-85};
        for (double rssi : rssiValues) {
            double filtered = filter.filter(rssi);
            System.out.printf("원본: %.1f | 필터: %.1f | 상태: %s%n",
                rssi, filtered, filter.getState());
        }
    }

    @Test
    void approachAndLeave() {
        RssiFilterPipeline filter = new RssiFilterPipeline();
        double[] rssiValues = {-85, -80, -75, -68, -63, -62, -90, -65, -72, -80, -85};
        for (double rssi : rssiValues) {
            double filtered = filter.filter(rssi);
            System.out.printf("원본: %.1f | 필터: %.1f | 상태: %s%n",
                rssi, filtered, filter.getState());
        }
    }
}