package com.majlis.app;

/** Monotonic session time: only intervals with an active microphone count. */
final class RecordingClock {
    private boolean recording;
    private boolean paused;
    private long accumulatedMillis;
    private long activeStartedMillis;

    synchronized boolean isRecording() {
        return recording;
    }

    synchronized boolean isPaused() {
        return paused;
    }

    synchronized void reset() {
        recording = false;
        paused = false;
        accumulatedMillis = 0L;
        activeStartedMillis = 0L;
    }

    synchronized boolean start(long nowMillis) {
        if (recording) return false;
        accumulatedMillis = 0L;
        activeStartedMillis = nowMillis;
        paused = false;
        recording = true;
        return true;
    }

    synchronized boolean pause(long nowMillis) {
        if (!recording || paused) return false;
        accumulatedMillis = elapsedMillis(nowMillis);
        paused = true;
        return true;
    }

    synchronized boolean resume(long nowMillis) {
        if (!recording || !paused) return false;
        activeStartedMillis = nowMillis;
        paused = false;
        return true;
    }

    synchronized void stop(long nowMillis) {
        if (recording) accumulatedMillis = elapsedMillis(nowMillis);
        recording = false;
        paused = false;
    }

    synchronized long elapsedMillis(long nowMillis) {
        if (!recording || paused) return accumulatedMillis;
        return accumulatedMillis + Math.max(0L, nowMillis - activeStartedMillis);
    }
}
