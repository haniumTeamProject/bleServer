package org.mcsmtp.wayfinder.filter;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.List;

public class RssiFilterPipeline {

    private static final int MEDIAN_WINDOW = 3;
    private final Deque<Double> medianBuffer = new ArrayDeque<>();

    private double Q;
    private double R;
    private double x;
    private double p;
    private boolean initialized;

    private double entryThreshold;
    private double exitThreshold;
    private BeaconState state;

    public enum BeaconState {
        ARRIVED,
        DEPARTED,
        STABLE
    }

    public RssiFilterPipeline() {
        this(0.008, 4.0, -65.0, -75.0);
    }

    public RssiFilterPipeline(double Q, double R, double entryThreshold, double exitThreshold) {
        this.Q = Q;
        this.R = R;
        this.entryThreshold = entryThreshold;
        this.exitThreshold = exitThreshold;
        this.x = 0.0;
        this.p = 1.0;
        this.initialized = false;
        this.state = BeaconState.DEPARTED;
    }

    public double filter(double measurement) {
        double medianFiltered = applyMedianFilter(measurement);
        double kalmanFiltered = applyKalmanFilter(medianFiltered);
        applyHysteresis(kalmanFiltered);
        return kalmanFiltered;
    }

    private double applyMedianFilter(double measurement) {
        medianBuffer.addLast(measurement);
        if (medianBuffer.size() > MEDIAN_WINDOW) {
            medianBuffer.pollFirst();
        }
        if (medianBuffer.size() < 3) return measurement;
        List<Double> sorted = new ArrayList<>(medianBuffer);
        Collections.sort(sorted);
        return sorted.get(sorted.size() / 2);
    }

    private double applyKalmanFilter(double measurement) {
        if (!initialized) {
            x = measurement;
            initialized = true;
            return x;
        }
        p = p + Q;
        double K = p / (p + R);
        x = x + K * (measurement - x);
        p = (1 - K) * p;
        return x;
    }

    private void applyHysteresis(double filteredRssi) {
        if (state == BeaconState.ARRIVED) {
            if (filteredRssi <= exitThreshold) {
                state = BeaconState.DEPARTED;
            }
        } else {
            if (filteredRssi >= entryThreshold) {
                state = BeaconState.ARRIVED;
            } else {
                state = BeaconState.DEPARTED;
            }
        }
    }

    public void reset() {
        medianBuffer.clear();
        x = 0.0;
        p = 1.0;
        initialized = false;
        state = BeaconState.DEPARTED;
    }

    public BeaconState getState() { return state; }
    public double getCurrentEstimate() { return x; }
    public double getQ() { return Q; }
    public double getR() { return R; }
    public void setQ(double Q) { this.Q = Q; }
    public void setR(double R) { this.R = R; }
    public void setEntryThreshold(double v) { this.entryThreshold = v; }
    public void setExitThreshold(double v) { this.exitThreshold = v; }
}