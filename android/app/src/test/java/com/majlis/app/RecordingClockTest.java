package com.majlis.app;

/** Runs without Android or JUnit; exercises time and lifecycle contracts. */
public final class RecordingClockTest {
    private static int checks;

    public static void main(String[] args) {
        RecordingClock clock = new RecordingClock();
        expect(!clock.isRecording() && !clock.isPaused(), "idle initially");
        expect(clock.elapsedMillis(90_000L) == 0L, "idle time is zero");
        expect(!clock.pause(90_000L) && !clock.resume(90_000L), "idle transitions ignored");
        expect(clock.start(100_000L), "start accepted");
        expect(!clock.start(101_000L), "duplicate start preserves active session");
        expect(clock.elapsedMillis(103_000L) == 3_000L, "time starts with microphone");
        expect(!clock.resume(103_000L), "resume while active ignored");
        expect(clock.pause(105_000L), "pause accepted");
        expect(clock.isRecording() && clock.isPaused(), "paused session remains active");
        expect(clock.elapsedMillis(900_000L) == 5_000L, "pause excludes wall time");
        expect(!clock.pause(950_000L), "duplicate pause ignored");
        expect(clock.resume(1_000_000L), "resume accepted");
        expect(!clock.isPaused() && clock.isRecording(), "resume state");
        expect(clock.elapsedMillis(1_002_000L) == 7_000L, "active intervals accumulate");
        clock.stop(1_005_000L);
        expect(!clock.isRecording() && !clock.isPaused(), "stop resets session flags");
        expect(clock.elapsedMillis(10_000_000L) == 10_000L, "completed duration stays fixed");
        clock.stop(10_000_000L);
        expect(clock.elapsedMillis(11_000_000L) == 10_000L, "duplicate stop preserves duration");
        clock.reset();
        expect(clock.elapsedMillis(11_000_000L) == 0L, "new attempt resets completed time");
        expect(clock.start(12_000_000L), "second start");
        expect(clock.pause(12_003_000L), "pause second recording");
        clock.stop(20_000_000L);
        expect(clock.elapsedMillis(20_000_000L) == 3_000L, "stop while paused excludes pause");
        expect(!clock.isPaused(), "paused state reset after stop");
        clock.reset();
        expect(clock.start(30_000_000L), "long session start");
        expect(clock.pause(30_030_000L), "pause long session");
        expect(clock.resume(40_000_000L), "resume after long pause");
        expect(clock.elapsedMillis(43_570_000L) == 3_600_000L, "one-hour limit counts audio only");
        clock.stop(43_570_000L);
        expect(clock.elapsedMillis(90_000_000L) == 3_600_000L, "one-hour duration frozen");
        clock.reset();
        clock.start(100L);
        expect(clock.elapsedMillis(99L) == 0L, "negative clock delta never reported");
        System.out.println(checks + " recording-clock checks passed");
    }

    private static void expect(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }
}
