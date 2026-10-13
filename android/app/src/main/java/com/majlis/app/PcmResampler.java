package com.majlis.app;

import java.io.IOException;
import java.io.OutputStream;

/** Streaming mono conversion keeps its phase across codec output buffers. */
final class PcmResampler {
    private final OutputStream output;
    private final double step;
    private final int sourceRate;
    private long frames;
    private long written;
    private double next;
    private float previous;
    private boolean finished;
    PcmResampler(OutputStream output, int sourceRate) {
        if (output == null || sourceRate < 8000 || sourceRate > 384000)
            throw new IllegalArgumentException("Invalid PCM sample rate");
        this.output = output;
        this.sourceRate = sourceRate;
        this.step = sourceRate/16000.0;
    }
    void accept(float sample) throws IOException {
        if (finished || !Float.isFinite(sample)) throw new IOException("Invalid decoded PCM");
        sample = Math.max(-1, Math.min(1, sample));
        if (frames == 0) {
            write(sample); next = step;
        } else {
            while (next <= frames + 1e-8) {
                double fraction = Math.max(0, Math.min(1, next-(frames-1)));
                write((float)(previous + (sample-previous)*fraction));
                next += step;
            }
        }
        previous = sample; frames++;
    }
    void finish() throws IOException {
        if (finished) return;
        long expected = (long)Math.ceil(frames * 16000.0/sourceRate);
        while (written < expected) write(previous);
        output.flush(); finished = true;
    }
    long samples() { return written; }
    private void write(float value) throws IOException {
        if (written >= 16000L*3600L) throw new IOException("التسجيل يتجاوز ساعة من الصوت الفعلي.");
        int pcm = value < 0 ? Math.round(value*32768) : Math.round(value*32767);
        output.write(pcm & 255); output.write((pcm >>> 8) & 255); written++;
    }
}
