package com.majlis.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.Arrays;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Provider API keys encrypted with a non-exportable, app-private Android key. */
public final class ProviderSecrets {
    private static final String ALIAS = "com.majlis.provider.keys";
    private static final String PREFERENCES = "provider_secrets";
    private static final int IV_BYTES = 12;
    private static final int TAG_BITS = 128;
    private static final int MAX_KEY_CHARACTERS = 512;
    private static final int MAX_RECORD_CHARACTERS = 4096;

    private ProviderSecrets() {}

    /** Returns an empty string only when no key has been saved for this provider. */
    public static synchronized String get(Context context, String provider) throws Exception {
        String field = field(provider);
        try {
            SharedPreferences preferences = preferences(context);
            if (!preferences.contains(field)) return "";
            String record = preferences.getString(field, null);
            if (record == null || record.length() == 0
                    || record.length() > MAX_RECORD_CHARACTERS) throw new Exception();
            byte[] sealed = Base64.decode(record, Base64.NO_WRAP);
            if (sealed.length <= IV_BYTES + TAG_BITS / 8
                    || !record.equals(Base64.encodeToString(sealed, Base64.NO_WRAP))) {
                throw new Exception();
            }
            SecretKey key = existingKey();
            if (key == null) throw new Exception();
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key,
                    new GCMParameterSpec(TAG_BITS, Arrays.copyOfRange(sealed, 0, IV_BYTES)));
            cipher.updateAAD(provider.getBytes(StandardCharsets.UTF_8));
            byte[] plain = cipher.doFinal(sealed, IV_BYTES, sealed.length - IV_BYTES);
            try {
                String value = new String(plain, StandardCharsets.UTF_8);
                if (!value.equals(checkedKey(value))) throw new Exception();
                return value;
            } finally {
                Arrays.fill(plain, (byte) 0);
            }
        } catch (Exception ignored) {
            throw new Exception("تعذر قراءة مفتاح المزوّد المحفوظ. احذف المفتاح من الإعدادات ثم احفظه مجددًا.");
        }
    }

    public static synchronized void put(Context context, String provider, String value) throws Exception {
        String field = field(provider);
        String clean = checkedKey(value);
        byte[] plain = clean.getBytes(StandardCharsets.UTF_8);
        try {
            SharedPreferences preferences = preferences(context);
            boolean hasRecords = preferences.contains("encrypted_openai")
                    || preferences.contains("encrypted_gemini");
            SecretKey key = hasRecords ? existingKey() : null;
            if (hasRecords && key == null) throw new Exception();
            if (!hasRecords) {
                // Regenerate only after every old record has been explicitly removed.
                // This also recovers an invalidated alias without discarding another provider.
                KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,
                        "AndroidKeyStore");
                generator.init(new KeyGenParameterSpec.Builder(ALIAS,
                        KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .setKeySize(256)
                        .setRandomizedEncryptionRequired(true)
                        .build());
                key = generator.generateKey();
            }
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key);
            cipher.updateAAD(provider.getBytes(StandardCharsets.UTF_8));
            byte[] iv = cipher.getIV();
            if (iv == null || iv.length != IV_BYTES) throw new Exception();
            byte[] encrypted = cipher.doFinal(plain);
            byte[] sealed = new byte[iv.length + encrypted.length];
            System.arraycopy(iv, 0, sealed, 0, iv.length);
            System.arraycopy(encrypted, 0, sealed, iv.length, encrypted.length);
            if (!preferences.edit().putString(field,
                    Base64.encodeToString(sealed, Base64.NO_WRAP)).commit()) throw new Exception();
        } catch (Exception ignored) {
            throw new Exception("تعذر حفظ مفتاح المزوّد بشكل آمن. تحقق من مساحة الجهاز، واحذف المفاتيح القديمة إذا تعذر قراءتها.");
        } finally {
            Arrays.fill(plain, (byte) 0);
        }
    }

    /** Deleting one provider deliberately retains the shared alias for the other provider. */
    public static synchronized void remove(Context context, String provider) throws Exception {
        String field = field(provider);
        try {
            if (!preferences(context).edit().remove(field).commit()) throw new Exception();
        } catch (Exception ignored) {
            throw new Exception("تعذر حذف مفتاح المزوّد. تحقق من مساحة الجهاز وحاول مجددًا.");
        }
    }

    private static SharedPreferences preferences(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
    }

    private static SecretKey existingKey() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias(ALIAS)) return null;
        java.security.Key key = store.getKey(ALIAS, null);
        if (!(key instanceof SecretKey)) throw new Exception();
        return (SecretKey) key;
    }

    private static String field(String provider) {
        if (!"openai".equals(provider) && !"gemini".equals(provider)) {
            throw new IllegalArgumentException("مزوّد التحليل غير مدعوم.");
        }
        return "encrypted_" + provider;
    }

    private static String checkedKey(String value) {
        if (value != null) {
            for (int i = 0; i < value.length(); i++) {
                if (Character.isISOControl(value.charAt(i))) {
                    throw new IllegalArgumentException("مفتاح API غير صالح: احذف محارف التحكم.");
                }
            }
            String clean = value.trim();
            if (clean.length() > 0 && clean.length() <= MAX_KEY_CHARACTERS) return clean;
        }
        throw new IllegalArgumentException("أدخل مفتاح API بطول لا يتجاوز 512 حرفًا.");
    }
}
