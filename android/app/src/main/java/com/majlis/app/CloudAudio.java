package com.majlis.app;

import android.content.Context;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.media.MediaMuxer;
import android.os.SystemClock;
import android.system.ErrnoException;
import android.system.Os;
import android.system.OsConstants;
import android.system.StructStat;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.security.MessageDigest;

/** Local audio compression only: no network calls, models, or changes to the saved recording. */
public final class CloudAudio {
    // Base64 expands this to under 17.4 MB, leaving room in the 20 MB inline request.
    public static final long MAX_INLINE_BYTES = 13_000_000L;
    private static final int BIT_RATE = 24_000;
    private static final long STALL_MS = 45_000;

    private CloudAudio() {}

    /**
     * Run on a worker thread. Returns the unchanged source when it fits, otherwise a cache AAC copy.
     * The caller must delete the returned copy in finally when it differs from {@code original}.
     * Intermediate PCM is at most 115.2 MB and is always deleted before this method returns.
     */
    public static File prepare(Context context, File original, double duration) throws Exception {
        if (context == null || original == null || !original.isFile()
                || !Double.isFinite(duration) || duration <= 0 || duration > 3600)
            throw new IOException("اختر تسجيلًا صوتيًا صالحًا لا يتجاوز ساعة.");
        checkInterrupted();
        File canonical = original.getCanonicalFile();
        File encoded = null;
        boolean success = false;
        // Hold the source descriptor through both passes; extraction never reopens its pathname.
        try {
            try (FileInputStream source = new FileInputStream(canonical)) {
                StructStat identity = stat(source);
                if (!OsConstants.S_ISREG(identity.st_mode) || identity.st_size <= 0
                        || identity.st_size > AudioPcm.MAX_INPUT_BYTES)
                    throw new IOException("اختر تسجيلًا صالحًا لا يتجاوز 24 ميغابايت.");
                if (identity.st_size <= MAX_INLINE_BYTES) {
                    verifySource(canonical, original, source, identity);
                    return original;
                }
                byte[] fingerprint = fingerprint(source);
                verifySource(canonical, original, source, identity);
                File cache = new File(context.getCacheDir(), "majlis-cloud-audio");
                if (!cache.isDirectory() && !cache.mkdirs())
                    throw new IOException("تعذر إنشاء مساحة التحويل الصوتي.");
                try (AudioPcm.Decoded decoded = AudioPcm.decode(source.getFD(), cache)) {
                    encoded = File.createTempFile("majlis-cloud-", ".m4a", cache);
                    encode(decoded, encoded);
                    if (!MessageDigest.isEqual(fingerprint, fingerprint(source)))
                        throw new IOException("تغير التسجيل أثناء التحويل؛ أعد المحاولة بعد اكتمال حفظه.");
                    verifySource(canonical, original, source, identity);
                    if (!encoded.isFile() || encoded.length() <= 0 || encoded.length() > MAX_INLINE_BYTES)
                        throw new IOException("تعذر ضغط التسجيل ضمن حد خدمة التحليل.");
                }
            }
            // Both source and PCM handles have closed before the AAC copy changes ownership.
            success = true;
            return encoded;
        } finally {
            if (!success && encoded != null) encoded.delete();
        }
    }

    private static void encode(AudioPcm.Decoded decoded, File destination) throws Exception {
        MediaCodec codec = null;
        MediaMuxer muxer = null;
        boolean codecStarted = false, muxerStarted = false;
        try (BufferedInputStream input = new BufferedInputStream(new FileInputStream(decoded.file), 65536)) {
            MediaFormat format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC, AudioPcm.RATE, 1);
            format.setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC);
            format.setInteger(MediaFormat.KEY_BIT_RATE, BIT_RATE);
            format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 32768);
            codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC);
            codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            codec.start(); codecStarted = true;
            muxer = new MediaMuxer(destination.getAbsolutePath(), MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
            byte[] chunk = new byte[32768];
            long remaining = decoded.samples * 2, frames = 0, encodedBytes = 0;
            long lastMovement = SystemClock.elapsedRealtime();
            boolean inputDone = false, outputDone = false;
            int track = -1;
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            while (!outputDone) {
                checkInterrupted();
                if (!inputDone) {
                    int index = codec.dequeueInputBuffer(10000);
                    if (index >= 0) {
                        ByteBuffer buffer = codec.getInputBuffer(index);
                        if (buffer == null) throw new IOException("تعذر فتح مخزن ضغط الصوت.");
                        buffer.clear();
                        long timeUs = frames * 1_000_000L / AudioPcm.RATE;
                        if (remaining == 0) {
                            codec.queueInputBuffer(index, 0, 0, timeUs, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inputDone = true;
                        } else {
                            int count = (int)Math.min(remaining, Math.min(chunk.length, buffer.remaining() & ~1));
                            if (count <= 0) throw new IOException("مخزن ضغط الصوت أصغر من عينة PCM.");
                            int copied = 0;
                            while (copied < count) {
                                int read = input.read(chunk, copied, count - copied);
                                if (read <= 0) throw new IOException("انقطعت قراءة نسخة الصوت المؤقتة.");
                                copied += read;
                            }
                            buffer.put(chunk, 0, count);
                            codec.queueInputBuffer(index, 0, count, timeUs, 0);
                            frames += count / 2; remaining -= count;
                        }
                        lastMovement = SystemClock.elapsedRealtime();
                    }
                }
                // Drain after each input attempt, including when no input buffer is available.
                while (true) {
                    int index = codec.dequeueOutputBuffer(info, inputDone ? 10000 : 0);
                    if (index == MediaCodec.INFO_TRY_AGAIN_LATER) break;
                    if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                        if (muxerStarted || track >= 0) throw new IOException("تغير تنسيق الصوت المضغوط أثناء التحويل.");
                        track = muxer.addTrack(codec.getOutputFormat());
                        muxer.start(); muxerStarted = true;
                        lastMovement = SystemClock.elapsedRealtime();
                    } else if (index >= 0) {
                        try {
                            if (info.size > 0 && (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0) {
                                ByteBuffer buffer = codec.getOutputBuffer(index);
                                if (!muxerStarted || buffer == null || info.offset < 0 || info.size > buffer.capacity() - info.offset)
                                    throw new IOException("بيانات الصوت المضغوط غير صالحة.");
                                encodedBytes += info.size;
                                if (encodedBytes > MAX_INLINE_BYTES)
                                    throw new IOException("التسجيل المضغوط يتجاوز حد خدمة التحليل.");
                                buffer.position(info.offset); buffer.limit(info.offset + info.size);
                                muxer.writeSampleData(track, buffer, info);
                                if (destination.length() > MAX_INLINE_BYTES)
                                    throw new IOException("التسجيل المضغوط يتجاوز حد خدمة التحليل.");
                            }
                            outputDone = (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                        } finally {
                            codec.releaseOutputBuffer(index, false);
                        }
                        lastMovement = SystemClock.elapsedRealtime();
                        if (outputDone) break;
                    }
                    checkInterrupted();
                    if (SystemClock.elapsedRealtime() - lastMovement > STALL_MS)
                        throw new IOException("توقف ضغط الصوت عن الاستجابة.");
                }
                if (SystemClock.elapsedRealtime() - lastMovement > STALL_MS)
                    throw new IOException("توقف ضغط الصوت عن الاستجابة.");
            }
            if (!muxerStarted || encodedBytes == 0 || remaining != 0)
                throw new IOException("لم ينتج التحويل ملفًا صوتيًا صالحًا.");
            // A stop error must fail the operation: this writes the MP4 sample table.
            muxerStarted = false;
            muxer.stop();
        } finally {
            if (codec != null) {
                if (codecStarted) try { codec.stop(); } catch (Exception ignored) {}
                try { codec.release(); } catch (Exception ignored) {}
            }
            if (muxer != null) {
                if (muxerStarted) try { muxer.stop(); } catch (Exception ignored) {}
                try { muxer.release(); } catch (Exception ignored) {}
            }
        }
    }

    private static StructStat stat(FileInputStream input) throws IOException {
        try { return Os.fstat(input.getFD()); }
        catch (ErrnoException error) { throw new IOException("تعذر قراءة التسجيل.", error); }
    }

    private static byte[] fingerprint(FileInputStream input) throws Exception {
        input.getChannel().position(0);
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[65536];
        long readBytes = 0;
        int count;
        while ((count = input.read(buffer)) != -1) {
            checkInterrupted();
            readBytes += count;
            if (readBytes > AudioPcm.MAX_INPUT_BYTES)
                throw new IOException("تغير حجم التسجيل أثناء التحويل.");
            digest.update(buffer, 0, count);
        }
        return digest.digest();
    }

    private static void verifySource(File canonical, File original, FileInputStream input, StructStat before) throws IOException {
        try {
            StructStat descriptor = stat(input);
            StructStat path = Os.stat(canonical.getAbsolutePath());
            if (!canonical.equals(original.getCanonicalFile()) || !sameSource(before, descriptor) || !sameSource(before, path))
                throw new IOException("تغير التسجيل أثناء التحويل؛ أعد المحاولة بعد اكتمال حفظه.");
        } catch (ErrnoException error) {
            throw new IOException("تغير التسجيل أثناء التحويل؛ أعد المحاولة بعد اكتمال حفظه.", error);
        }
    }

    private static boolean sameSource(StructStat first, StructStat second) {
        return first.st_dev == second.st_dev && first.st_ino == second.st_ino
                && first.st_size == second.st_size && first.st_mtime == second.st_mtime
                && first.st_ctime == second.st_ctime;
    }

    private static void checkInterrupted() throws IOException {
        if (Thread.currentThread().isInterrupted()) throw new IOException("أُلغي تحويل التسجيل الصوتي.");
    }
}
