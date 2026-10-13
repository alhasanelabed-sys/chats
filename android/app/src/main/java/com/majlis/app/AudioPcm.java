package com.majlis.app;

import android.media.AudioFormat;
import android.media.MediaCodec;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.os.SystemClock;
import android.system.ErrnoException;
import android.system.Os;
import android.system.OsConstants;
import android.system.StructStat;

import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/** Decodes only on the device; the temporary PCM file never enters a request. */
final class AudioPcm {
    static final int RATE = 16000;
    static final long MAX_BYTES = 115200000L;
    static final long MAX_INPUT_BYTES = 24_000_000L;
    private AudioPcm() {}

    static final class Decoded implements AutoCloseable {
        final File file;
        final long samples;
        Decoded(File file) throws IOException {
            this.file = file;
            long bytes = file.length();
            if (bytes <= 0 || bytes > MAX_BYTES || (bytes & 1) != 0) throw new IOException("تعذر قراءة الصوت أو تجاوز التسجيل ساعة.");
            samples = bytes/2;
        }
        double duration() { return samples/(double)RATE; }
        float[] read(double start, double end) throws IOException {
            if (!Double.isFinite(start) || !Double.isFinite(end) || start < 0 || end < start || end-start > 25.01)
                throw new IOException("نطاق الصوت المحلي غير صالح.");
            long first = Math.min(samples, Math.max(0, Math.round(start*RATE)));
            long last = Math.min(samples, Math.max(first, Math.round(end*RATE)));
            int count = (int)(last-first);
            byte[] bytes = new byte[count*2];
            try (RandomAccessFile input = new RandomAccessFile(file, "r")) {
                input.seek(first*2); input.readFully(bytes);
            }
            float[] result = new float[count];
            for (int i=0; i<count; ++i) result[i] = (short)((bytes[2*i]&255) | ((bytes[2*i+1]&255)<<8))/32768.0f;
            return result;
        }
        @Override public void close() throws IOException {
            if (file.exists() && !file.delete()) throw new IOException("تعذر حذف نسخة الصوت المؤقتة.");
        }
    }

    static Decoded decode(File audio, File temporaryDirectory) throws Exception {
        if (audio == null || !audio.isFile() || audio.length() <= 0 || audio.length() > MAX_INPUT_BYTES)
            throw new IOException("اختر تسجيلًا صالحًا لا يتجاوز 24 ميغابايت.");
        try (FileInputStream input = new FileInputStream(audio)) {
            return decode(input.getFD(), temporaryDirectory);
        }
    }

    /** The caller keeps this descriptor open, so extraction cannot reopen a replaced pathname. */
    static Decoded decode(FileDescriptor sourceDescriptor, File temporaryDirectory) throws Exception {
        try {
            StructStat source = Os.fstat(sourceDescriptor);
            if (!OsConstants.S_ISREG(source.st_mode) || source.st_size <= 0 || source.st_size > MAX_INPUT_BYTES)
                throw new IOException("اختر تسجيلًا صالحًا لا يتجاوز 24 ميغابايت.");
        } catch (ErrnoException error) {
            throw new IOException("تعذر فتح التسجيل للتحويل المحلي.", error);
        }
        if (!temporaryDirectory.isDirectory() && !temporaryDirectory.mkdirs()) throw new IOException("تعذر إنشاء مساحة الصوت المحلي.");
        File pcm = File.createTempFile("majlis-local-", ".pcm", temporaryDirectory);
        MediaExtractor extractor = null;
        MediaCodec codec = null;
        boolean success = false;
        try {
            try (BufferedOutputStream output = new BufferedOutputStream(new FileOutputStream(pcm), 65536)) {
                extractor = new MediaExtractor();
                extractor.setDataSource(sourceDescriptor);
                MediaFormat format = null;
                for (int i=0; i<extractor.getTrackCount(); ++i) {
                    MediaFormat candidate = extractor.getTrackFormat(i);
                    String mime = candidate.getString(MediaFormat.KEY_MIME);
                    if (mime != null && mime.startsWith("audio/")) { format = candidate; extractor.selectTrack(i); break; }
                }
                if (format == null) throw new IOException("لا يوجد مسار صوتي قابل للقراءة.");
                String mime = format.getString(MediaFormat.KEY_MIME);
                int rate = format.getInteger(MediaFormat.KEY_SAMPLE_RATE);
                int channels = format.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
                // Compressed input PCM metadata is not the decoder's output contract.
                Sink sink = new Sink(output, rate, channels,
                        "audio/raw".equals(mime) ? encoding(format) : AudioFormat.ENCODING_PCM_16BIT);
                if ("audio/raw".equals(mime)) {
                    ByteBuffer buffer = ByteBuffer.allocateDirect(1024*1024);
                    while (true) {
                        checkInterrupted(); buffer.clear();
                        int count = extractor.readSampleData(buffer, 0);
                        if (count < 0) break;
                        if (count > buffer.capacity()) throw new IOException("عينة الصوت أكبر من مخزن التحويل المحلي.");
                        buffer.position(0); buffer.limit(count); sink.accept(buffer);
                        extractor.advance();
                    }
                } else {
                    codec = MediaCodec.createDecoderByType(mime);
                    codec.configure(format, null, null, 0); codec.start();
                    boolean inputDone = false, outputDone = false;
                    MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
                    long lastMovement = SystemClock.elapsedRealtime();
                    while (!outputDone) {
                        checkInterrupted();
                        if (!inputDone) {
                            int index = codec.dequeueInputBuffer(10000);
                            if (index >= 0) {
                                ByteBuffer input = codec.getInputBuffer(index);
                                if (input == null) throw new IOException("تعذر فتح مخزن فك الصوت.");
                                input.clear(); int count = extractor.readSampleData(input, 0);
                                if (count < 0) { codec.queueInputBuffer(index, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM); inputDone = true; }
                                else {
                                    if (count > input.capacity()) throw new IOException("عينة الصوت أكبر من مخزن فك التسجيل.");
                                    if ((extractor.getSampleFlags() & MediaExtractor.SAMPLE_FLAG_ENCRYPTED) != 0)
                                        throw new IOException("التسجيل الصوتي المشفّر غير مدعوم.");
                                    codec.queueInputBuffer(index, 0, count, Math.max(0, extractor.getSampleTime()), 0); extractor.advance();
                                }
                                lastMovement = SystemClock.elapsedRealtime();
                            }
                        }
                        int index = codec.dequeueOutputBuffer(info, 10000);
                        if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                            MediaFormat actual = codec.getOutputFormat();
                            sink.format(actual.getInteger(MediaFormat.KEY_SAMPLE_RATE), actual.getInteger(MediaFormat.KEY_CHANNEL_COUNT), encoding(actual));
                            lastMovement = SystemClock.elapsedRealtime();
                        } else if (index >= 0) {
                            try {
                                if (info.size > 0 && (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0) {
                                    ByteBuffer decoded = codec.getOutputBuffer(index);
                                    if (decoded == null || info.offset < 0 || info.size > decoded.capacity()-info.offset)
                                        throw new IOException("بيانات الصوت المفكوكة غير صالحة.");
                                    decoded.position(info.offset); decoded.limit(info.offset+info.size); sink.accept(decoded);
                                }
                                outputDone = (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                            } finally { codec.releaseOutputBuffer(index, false); }
                            lastMovement = SystemClock.elapsedRealtime();
                        }
                        if (SystemClock.elapsedRealtime()-lastMovement > 45000) throw new IOException("توقف فك الصوت المحلي عن الاستجابة.");
                    }
                }
                sink.finish(); output.flush();
            }
            Decoded result = new Decoded(pcm); success = true; return result;
        } finally {
            if (codec != null) {
                try { codec.stop(); } catch (Exception ignored) {}
                try { codec.release(); } catch (Exception ignored) {}
            }
            if (extractor != null) try { extractor.release(); } catch (Exception ignored) {}
            if (!success) pcm.delete();
        }
    }

    private static int encoding(MediaFormat format) {
        return format.containsKey(MediaFormat.KEY_PCM_ENCODING) ? format.getInteger(MediaFormat.KEY_PCM_ENCODING) : AudioFormat.ENCODING_PCM_16BIT;
    }
    private static void checkInterrupted() throws IOException {
        if (Thread.currentThread().isInterrupted()) throw new IOException("أُلغي تحليل الصوت المحلي.");
    }
    private static final class Sink {
        private final BufferedOutputStream output;
        private PcmResampler resampler;
        private int rate, channels, encoding, sampleBytes;
        private long sourceFrames;
        private final byte[] pending = new byte[32];
        private int pendingCount;
        Sink(BufferedOutputStream output, int rate, int channels, int encoding) throws IOException {
            this.output = output; format(rate, channels, encoding);
        }
        void format(int rate, int channels, int encoding) throws IOException {
            if (rate < 8000 || rate > 384000) throw new IOException("معدل عينات التسجيل غير مدعوم على هذا الهاتف.");
            if (channels < 1 || channels > 8) throw new IOException("عدد قنوات الصوت غير مدعوم محليًا.");
            if (resampler != null && this.rate != rate && resampler.samples() > 0) throw new IOException("تغير معدل الصوت أثناء فك التسجيل.");
            if (pendingCount != 0) throw new IOException("تغير تنسيق الصوت وسط عينة.");
            if (resampler == null || this.rate != rate) resampler = new PcmResampler(output, rate);
            this.rate = rate; this.channels = channels; this.encoding = encoding;
            if (encoding == AudioFormat.ENCODING_PCM_FLOAT) sampleBytes = 4;
            else if (encoding == AudioFormat.ENCODING_PCM_16BIT) sampleBytes = 2;
            else throw new IOException("ترميز PCM غير مدعوم على هذا الهاتف.");
        }
        void accept(ByteBuffer buffer) throws IOException {
            buffer.order(ByteOrder.LITTLE_ENDIAN);
            int frameBytes = channels*sampleBytes;
            while (buffer.hasRemaining()) {
                if (pendingCount > 0 || buffer.remaining() < frameBytes) {
                    while (buffer.hasRemaining() && pendingCount < frameBytes) pending[pendingCount++] = buffer.get();
                    if (pendingCount < frameBytes) return;
                    frame(ByteBuffer.wrap(pending,0,frameBytes).order(ByteOrder.LITTLE_ENDIAN)); pendingCount = 0;
                } else frame(buffer);
            }
        }
        void frame(ByteBuffer buffer) throws IOException {
            if (sourceFrames >= rate * 3600L) throw new IOException("التسجيل يتجاوز ساعة من الصوت الفعلي.");
            float mono = 0;
            for (int c=0; c<channels; ++c) {
                float value = encoding == AudioFormat.ENCODING_PCM_FLOAT ? buffer.getFloat()
                    : buffer.getShort()/32768.0f;
                if (!Float.isFinite(value)) throw new IOException("عينة صوت غير صالحة.");
                mono += value/channels;
            }
            resampler.accept(mono);
            sourceFrames++;
        }
        void finish() throws IOException {
            if (pendingCount != 0) throw new IOException("التسجيل يحتوي على عينة صوت مبتورة.");
            resampler.finish();
        }
    }
}
