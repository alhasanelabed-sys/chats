package com.majlis.app;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;

/** Pure streaming PCM checks; codec integration is exercised on Android. */
public final class CloudAudioMathTest {
    private static int checks;

    public static void main(String[] args) throws Exception {
        for (int rate : new int[]{8000, 16000, 32000, 44100, 48000, 96000}) {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            PcmResampler writer = new PcmResampler(output, rate);
            for (int i = 0; i < rate; i++) writer.accept(0.5f);
            writer.finish();
            expect(writer.samples() == 16000 && output.size() == 32000,
                    "one second at " + rate + " Hz must become exactly 16000 samples");
            byte[] data = output.toByteArray();
            for (int i = 0; i < data.length; i += 2)
                expect(data[i] == 0 && data[i + 1] == 64, "DC amplitude survives resampling");
            int bytes = output.size(); writer.finish();
            expect(output.size() == bytes, "finish is idempotent");
        }
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        PcmResampler upsample = new PcmResampler(output, 8000);
        upsample.accept(-1.0f); upsample.accept(1.0f); upsample.finish();
        byte[] data = output.toByteArray();
        expect(data.length == 8, "upsampling retains the final partial interval");
        expect(sample(data, 0) == -32768 && sample(data, 1) == 0
                && sample(data, 2) == 32767 && sample(data, 3) == 32767,
                "PCM16 is signed, little endian, and interpolates between adjacent frames");
        try { upsample.accept(0); throw new AssertionError("accept after EOS succeeded"); }
        catch (IOException expected) { checks++; }
        PcmResampler finite = new PcmResampler(new ByteArrayOutputStream(), 16000);
        try { finite.accept(Float.NaN); throw new AssertionError("nonfinite PCM succeeded"); }
        catch (IOException expected) { checks++; }
        try { new PcmResampler(new ByteArrayOutputStream(), 1); throw new AssertionError("invalid rate succeeded"); }
        catch (IllegalArgumentException expected) { checks++; }
        // This streams an hour without retaining PCM: the exact budget must fit, one more frame must fail.
        final long[] bytes = {0};
        PcmResampler hour = new PcmResampler(new OutputStream() {
            @Override public void write(int value) { bytes[0]++; }
        }, 16000);
        for (long i = 0; i < 16000L * 3600; i++) hour.accept(0);
        expect(hour.samples() == 57_600_000L && bytes[0] == 115_200_000L,
                "one hour fits the exact decoded PCM budget");
        try { hour.accept(0); throw new AssertionError("an extra frame exceeded the one-hour budget"); }
        catch (IOException expected) { checks++; }
        hour.finish();
        expect(bytes[0] == 115_200_000L, "EOS does not grow the PCM file beyond its budget");
        System.out.println("PASS: " + checks + " streaming PCM checks");
    }

    private static int sample(byte[] data, int index) {
        return (short)((data[2 * index] & 255) | ((data[2 * index + 1] & 255) << 8));
    }
    private static void expect(boolean valid, String message) {
        checks++; if (!valid) throw new AssertionError(message);
    }
}
