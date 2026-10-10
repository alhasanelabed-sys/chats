package com.majlis.app;

/** Independent known vectors, with no Android classes or provider credentials. */
public final class TextCodecsTest {
    private static int passed;

    public static void main(String[] args) throws Exception {
        test("RFC 4648 Base64 vectors", () -> {
            equal(decode("base64", ""), "");
            equal(decode("base64", "Zg=="), "f");
            equal(decode("base64", "Zm8="), "fo");
            equal(decode("base64", "Zm9v"), "foo");
            equal(decode("base64", "Zm9vYg=="), "foob");
            equal(decode("base64", "Zm9vYmE="), "fooba");
            equal(decode("base64", "Zm9vYmFy"), "foobar");
        });
        test("Base64 decodes Arabic and supplementary Unicode", () ->
                equal(decode("base64", "2YXYsdit2KjYpyDYqNin2YTYudin2YTZhSDwn4yN"), "مرحبا بالعالم 🌍"));
        test("Base64 ignores only ASCII whitespace", () -> {
            equal(decode("base64", " \tZ\ng\r==\f\u000b"), "f");
            equal(decode("base64", " \n\t"), "");
            reject(() -> decode("base64", "Zg==\u00a0"), "Base64");
        });
        test("Base64 rejects missing misplaced and excessive padding", () -> {
            for (String invalid : new String[] {"Zg", "Zg=", "Zg===", "Z=g=", "=Zg=", "====", "A==="})
                reject(() -> decode("base64", invalid), "Base64");
        });
        test("Base64 rejects noncanonical pad bits and nonstandard alphabet", () -> {
            for (String invalid : new String[] {"Zh==", "Zm9=", "_w==", "-w==", "Zm9#", "????"})
                reject(() -> decode("base64", invalid), "Base64");
        });
        test("Base64 rejects malformed UTF-8 instead of replacing bytes", () -> {
            for (String invalid : new String[] {"/w==", "wIA=", "7aCA", "9JCAgA==", "4g=="})
                reject(() -> decode("base64", invalid), "UTF-8");
        });
        test("hex decodes known ASCII and mixed case", () -> {
            equal(decode("hex", ""), "");
            equal(decode("hex", "48656c6C6f2c20776f726c6421"), "Hello, world!");
        });
        test("hex decodes Arabic and supplementary Unicode", () ->
                equal(decode("hex", "d985d8b1d8add8a8d8a720d8a8d8a7d984d8b9d8a7d984d98520f09f8c8d"), "مرحبا بالعالم 🌍"));
        test("hex ignores only ASCII whitespace", () -> {
            equal(decode("hex", " 4\t8\n6\r9\f\u000b"), "Hi");
            equal(decode("hex", " \n\t"), "");
            reject(() -> decode("hex", "48\u00a0"), "Hex");
        });
        test("hex rejects odd length and non-ASCII hex digits", () -> {
            for (String invalid : new String[] {"F", "123", "0x48", "48gg", "ＦＦ", "٠٠"})
                reject(() -> decode("hex", invalid), "Hex");
        });
        test("hex rejects malformed UTF-8 instead of replacing bytes", () -> {
            for (String invalid : new String[] {"ff", "c080", "eda080", "f4908080", "e2", "80"})
                reject(() -> decode("hex", invalid), "UTF-8");
        });
        test("UTF-8 keeps valid control and replacement characters", () -> {
            equal(decode("hex", "00efbfbd"), "\u0000\ufffd");
            equal(decode("base64", "AO+/vQ=="), "\u0000\ufffd");
        });
        test("Morse covers Latin A-Z", () -> {
            equal(decode("morse", ".- -... -.-. -.. . ..-. --. .... .. .--- -.- .-.. -- -. --- .--. --.- .-. ... - ..- ...- .-- -..- -.-- --.."),
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ");
        });
        test("Morse covers all digits and word separators", () -> {
            equal(decode("morse", "----- .---- ..--- ...-- ....- ..... -.... --... ---.. ----."), "0123456789");
            equal(decode("morse", "... --- ... / .---- ..--- ...--"), "SOS 123");
            equal(decode("morse", "  .-/-...  "), "A B");
            equal(decode("morse", "\t...\n---\r...\f\u000b"), "SOS");
            equal(decode("morse", " \t\n"), "");
        });
        test("Morse rejects unknown tokens and unsupported punctuation", () -> {
            for (String invalid : new String[] {"......", "A", ".-.-.-", "..._", ".−", "...\u00a0---"})
                reject(() -> decode("morse", invalid), "مورس");
        });
        test("Morse rejects empty words", () -> {
            for (String invalid : new String[] {"/", "/ .-", ".- /", ".- // -...", ".- / / -..."})
                reject(() -> decode("morse", invalid), "مورس");
        });
        test("ROT13 known vector and mixed case", () -> {
            equal(decode("rot13", "Uryyb, Jbeyq!"), "Hello, World!");
            equal(decode("rot13", "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm"),
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz");
            equal(decode("rot13", ""), "");
        });
        test("ROT13 preserves Arabic emoji accents numbers and punctuation", () ->
                equal(decode("rot13", "Né مَجْلِس 🌍 ١٢٣!\n\t"), "Aé مَجْلِس 🌍 ١٢٣!\n\t"));
        test("Caesar decodes using the forward encoding shift", () -> {
            equal(TextCodecs.decode("caesar", "PHHW DW QRRQ", 3), "MEET AT NOON");
            equal(TextCodecs.decode("caesar", "Khoor, Zruog!", 3), "Hello, World!");
        });
        test("Caesar validates boundaries and wraps both cases", () -> {
            equal(TextCodecs.decode("caesar", "Az az!", 0), "Az az!");
            equal(TextCodecs.decode("caesar", "Zy zy!", 25), "Az az!");
            equal(TextCodecs.decode("caesar", "Ab Za", 1), "Za Yz");
            reject(() -> TextCodecs.decode("caesar", "A", -1), "25");
            reject(() -> TextCodecs.decode("caesar", "A", 26), "25");
            reject(() -> TextCodecs.decode("caesar", "A", Integer.MAX_VALUE), "25");
            reject(() -> TextCodecs.decode("caesar", "A", Integer.MIN_VALUE), "25");
        });
        test("Caesar preserves Arabic emoji accents numbers and punctuation", () ->
                equal(TextCodecs.decode("caesar", "Dé مَجْلِس 🌍 ١٢٣!\n\t", 3), "Aé مَجْلِس 🌍 ١٢٣!\n\t"));
        test("scheme is explicit with case-insensitive IDs and no guessing", () -> {
            equal(TextCodecs.decode(" BASE64 ", "Zg==", -1), "f");
            equal(TextCodecs.decode("HEX", "48", 26), "H");
            reject(() -> decode("unknown", "Zg=="), "مدعوم");
            reject(() -> decode("", "Zg=="), "مدعوم");
            reject(() -> decode(null, "Zg=="), "طريقة");
            reject(() -> decode("rot13", null), "النص");
        });
        test("raw source budget accepts 20000 and rejects 20001", () -> {
            String atLimit = repeated('A', 20000);
            equal(TextCodecs.decode("caesar", atLimit, 0), atLimit);
            equal(decode("rot13", atLimit), repeated('N', 20000));
            equal(decode("base64", repeated(' ', 20000)), "");
            equal(decode("hex", repeated(' ', 20000)), "");
            equal(decode("base64", repeated('A', 20000)), repeated('\u0000', 15000));
            equal(decode("hex", repeated('0', 20000)), repeated('\u0000', 10000));
            for (String scheme : new String[] {"base64", "hex", "morse", "rot13", "caesar"})
                reject(() -> TextCodecs.decode(scheme, repeated(' ', 20001), 0), "20000");
        });
        test("Unicode budget counts Java UTF-16 characters consistently", () -> {
            StringBuilder source = new StringBuilder();
            for (int i = 0; i < 10000; i++) source.append("🌍");
            equal(decode("rot13", source.toString()), source.toString());
            reject(() -> decode("rot13", source + "A"), "20000");
        });
        System.out.println("Passed " + passed + " text codec checks.");
    }

    private static String decode(String scheme, String source) {
        return TextCodecs.decode(scheme, source, 0);
    }

    private static String repeated(char value, int count) {
        StringBuilder source = new StringBuilder(count);
        for (int i = 0; i < count; i++) source.append(value);
        return source.toString();
    }

    private static void test(String name, Checked action) throws Exception {
        try {
            action.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable failure) {
            throw new AssertionError("FAIL " + name, failure);
        }
    }

    private static void reject(Checked action, String fragment) throws Exception {
        try {
            action.run();
        } catch (IllegalArgumentException expected) {
            if (expected.getMessage() == null || !expected.getMessage().contains(fragment))
                throw new AssertionError("Unexpected rejection: " + expected.getMessage(), expected);
            return;
        }
        throw new AssertionError("Expected rejection containing " + fragment);
    }

    private static void equal(String actual, String expected) {
        if (!expected.equals(actual)) throw new AssertionError("Unexpected decoded text");
    }

    private interface Checked { void run() throws Exception; }
}
