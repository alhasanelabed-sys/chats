package com.majlis.app;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Locale;

/** Offline text decoding for an explicitly selected, supported encoding. */
public final class TextCodecs {
    private static final int MAX_CHARACTERS = 20000;
    private static final String MORSE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    private static final String[] MORSE_CODES = {
            ".-", "-...", "-.-.", "-..", ".", "..-.", "--.", "....", "..", ".---",
            "-.-", ".-..", "--", "-.", "---", ".--.", "--.-", ".-.", "...", "-",
            "..-", "...-", ".--", "-..-", "-.--", "--..",
            "-----", ".----", "..---", "...--", "....-", ".....", "-....", "--...", "---..", "----."
    };

    private TextCodecs() { }

    /**
     * Supported IDs: base64, hex, morse, rot13, caesar (case-insensitive).
     * Caesar's shift is the forward encoding shift, from 0 through 25; decoding reverses it.
     * Base64 requires standard canonical padding; Base64/hex permit ASCII whitespace.
     * Limits count Java UTF-16 characters, including source whitespace.
     *
     * @throws IllegalArgumentException for unsupported schemes, invalid text, or size limits
     */
    public static String decode(String scheme, String source, int shift) {
        if (scheme == null) throw new IllegalArgumentException("اختر طريقة فك النص.");
        if (source == null) throw new IllegalArgumentException("النص مطلوب لفك الترميز.");
        if (source.length() > MAX_CHARACTERS)
            throw new IllegalArgumentException("النص المدخل يتجاوز الحد المسموح: 20000 حرف.");

        String decoded;
        switch (scheme.trim().toLowerCase(Locale.ROOT)) {
            case "base64": decoded = decodeBase64(source); break;
            case "hex": decoded = decodeHex(source); break;
            case "morse": decoded = decodeMorse(source); break;
            case "rot13": decoded = reverseShift(source, 13); break;
            case "caesar":
                if (shift < 0 || shift > 25)
                    throw new IllegalArgumentException("إزاحة قيصر يجب أن تكون بين 0 و25.");
                decoded = reverseShift(source, shift);
                break;
            default: throw new IllegalArgumentException("طريقة فك النص غير مدعومة؛ اختر طريقة معروفة صراحةً.");
        }

        if (decoded.length() > MAX_CHARACTERS)
            throw new IllegalArgumentException("النص الناتج يتجاوز الحد المسموح: 20000 حرف.");
        return decoded;
    }

    private static String decodeBase64(String source) {
        String compact = withoutAsciiWhitespace(source);
        if (compact.length() % 4 != 0) throw invalidBase64();
        byte[] bytes;
        try {
            bytes = Base64.getDecoder().decode(compact);
        } catch (IllegalArgumentException invalid) {
            throw invalidBase64();
        }
        // The JDK accepts unpadded input and nonzero unused pad bits; require canonical text.
        if (!Base64.getEncoder().encodeToString(bytes).equals(compact)) throw invalidBase64();
        return decodeUtf8(bytes, "Base64");
    }

    private static IllegalArgumentException invalidBase64() {
        return new IllegalArgumentException("نص Base64 غير صالح؛ استخدم الأبجدية القياسية وحشو = الصحيح.");
    }

    private static String decodeHex(String source) {
        String compact = withoutAsciiWhitespace(source);
        if (compact.length() % 2 != 0)
            throw new IllegalArgumentException("نص Hex غير صالح؛ يجب أن يكون عدد الأرقام زوجيًا.");
        byte[] bytes = new byte[compact.length() / 2];
        for (int i = 0; i < compact.length(); i += 2) {
            int high = hexDigit(compact.charAt(i));
            int low = hexDigit(compact.charAt(i + 1));
            if (high < 0 || low < 0)
                throw new IllegalArgumentException("نص Hex غير صالح؛ استخدم الأرقام 0–9 والحروف A–F فقط.");
            bytes[i / 2] = (byte) ((high << 4) | low);
        }
        return decodeUtf8(bytes, "Hex");
    }

    private static int hexDigit(char value) {
        if (value >= '0' && value <= '9') return value - '0';
        if (value >= 'a' && value <= 'f') return value - 'a' + 10;
        if (value >= 'A' && value <= 'F') return value - 'A' + 10;
        return -1;
    }

    private static String decodeUtf8(byte[] bytes, String scheme) {
        try {
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes)).toString();
        } catch (CharacterCodingException invalid) {
            throw new IllegalArgumentException("الناتج من " + scheme + " ليس نص UTF-8 صالحًا.", invalid);
        }
    }

    private static String decodeMorse(String source) {
        StringBuilder decoded = new StringBuilder();
        boolean hasSymbol = false;
        boolean needsSymbolAfterSlash = false;
        int position = 0;
        while (position < source.length()) {
            if (isAsciiWhitespace(source.charAt(position))) {
                position++;
                continue;
            }
            if (source.charAt(position) == '/') {
                if (!hasSymbol || needsSymbolAfterSlash) throw emptyMorseWord();
                decoded.append(' ');
                needsSymbolAfterSlash = true;
                position++;
                continue;
            }
            int start = position;
            while (position < source.length() && !isAsciiWhitespace(source.charAt(position))
                    && source.charAt(position) != '/') position++;
            String token = source.substring(start, position);
            int index = -1;
            for (int i = 0; i < MORSE_CODES.length; i++) {
                if (MORSE_CODES[i].equals(token)) { index = i; break; }
            }
            if (index < 0)
                throw new IllegalArgumentException("رمز مورس غير معروف عند الموضع " + (start + 1) + ". يدعم الحروف A–Z والأرقام فقط.");
            decoded.append(MORSE_ALPHABET.charAt(index));
            hasSymbol = true;
            needsSymbolAfterSlash = false;
        }
        if (needsSymbolAfterSlash) throw emptyMorseWord();
        return decoded.toString();
    }

    private static IllegalArgumentException emptyMorseWord() {
        return new IllegalArgumentException("صيغة مورس غير صالحة؛ ضع / بين كلمتين غير فارغتين.");
    }

    private static String reverseShift(String source, int shift) {
        StringBuilder decoded = new StringBuilder(source.length());
        for (int i = 0; i < source.length(); i++) {
            char value = source.charAt(i);
            if (value >= 'A' && value <= 'Z') value = (char) ('A' + (value - 'A' - shift + 26) % 26);
            else if (value >= 'a' && value <= 'z') value = (char) ('a' + (value - 'a' - shift + 26) % 26);
            decoded.append(value);
        }
        return decoded.toString();
    }

    private static String withoutAsciiWhitespace(String source) {
        StringBuilder compact = new StringBuilder(source.length());
        for (int i = 0; i < source.length(); i++) {
            char value = source.charAt(i);
            if (!isAsciiWhitespace(value)) compact.append(value);
        }
        return compact.toString();
    }

    private static boolean isAsciiWhitespace(char value) {
        return value == ' ' || value == '\t' || value == '\n' || value == '\r'
                || value == '\f' || value == '\u000b';
    }
}
