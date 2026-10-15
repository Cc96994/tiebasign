package org.dsh.tiebaweb;

import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.security.keystore.KeyProtection;
import android.util.Base64;

import java.security.KeyStore;
import java.security.SecureRandom;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * Keystore AES-GCM implemented in pure Java so that no pyjnius varargs /
 * array conversion is involved. Python calls only:
 *
 *   selfTest() -> "keystore-cipher" | "app-java-gcm"   (throws on total failure)
 *   encrypt(String plaintext, String alias) -> "k1.iv.ct" | "a2.iv.ct"
 *   decrypt(String blob, String alias) -> String
 *
 * PRIMARY   "keystore-cipher": AES key generated inside AndroidKeyStore and
 *           AES-GCM performed by the Keystore provider itself.
 * FALLBACK  "app-java-gcm": random 256-bit key stored in AndroidKeyStore as a
 *           SecretKeyEntry protected by KeyProtection(GCM/NoPadding), GCM math
 *           done in-process by javax.crypto (no Keystore cipher op).
 */
public final class KeystoreHelper {

    private static final String KEYSTORE = "AndroidKeyStore";
    private static final String TRANSFORMATION = "AES/GCM/NoPadding";
    private static final String KEY_ALGORITHM = "AES";
    private static final int KEY_SIZE_BITS = 256;
    private static final int GCM_TAG_BITS = 128;

    private static volatile String mode = null;

    private KeystoreHelper() {
    }

    private static KeyStore ks() throws Exception {
        KeyStore k = KeyStore.getInstance(KEYSTORE);
        k.load(null);
        return k;
    }

    private static void deleteEntry(String alias) {
        try {
            ks().deleteEntry(alias);
        } catch (Exception ignored) {
            // best effort cleanup
        }
    }

    private static String b64(byte[] data) {
        return Base64.encodeToString(data, Base64.NO_WRAP);
    }

    private static byte[] unb64(String data) {
        return Base64.decode(data, Base64.NO_WRAP);
    }

    // ------------------------------------------------------------------
    // PRIMARY path
    // ------------------------------------------------------------------

    private static SecretKey ensureCipherKey(String alias) throws Exception {
        KeyStore k = ks();
        if (!k.containsAlias(alias)) {
            KeyGenerator kg = KeyGenerator.getInstance(KEY_ALGORITHM, KEYSTORE);
            KeyGenParameterSpec spec = new KeyGenParameterSpec.Builder(
                    alias,
                    KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(KEY_SIZE_BITS)
                    .build();
            kg.init(spec);
            kg.generateKey();
        }
        return (SecretKey) k.getKey(alias, null);
    }

    private static String encryptPrimary(String plaintext, String alias)
            throws Exception {
        Cipher cipher = Cipher.getInstance(TRANSFORMATION);
        cipher.init(Cipher.ENCRYPT_MODE, ensureCipherKey(alias));
        byte[] iv = cipher.getIV();
        byte[] ct = cipher.doFinal(plaintext.getBytes("UTF-8"));
        return b64(iv) + "." + b64(ct);
    }

    private static String decryptPrimary(String blob, String alias)
            throws Exception {
        int dot = blob.indexOf('.');
        if (dot <= 0) {
            throw new IllegalArgumentException("bad primary blob");
        }
        byte[] iv = unb64(blob.substring(0, dot));
        byte[] ct = unb64(blob.substring(dot + 1));
        Cipher cipher = Cipher.getInstance(TRANSFORMATION);
        cipher.init(Cipher.DECRYPT_MODE, ensureCipherKey(alias),
                new GCMParameterSpec(GCM_TAG_BITS, iv));
        return new String(cipher.doFinal(ct), "UTF-8");
    }

    // ------------------------------------------------------------------
    // FALLBACK path
    // ------------------------------------------------------------------

    private static byte[] ensureImportedKey(String alias) throws Exception {
        KeyStore k = ks();
        if (!k.containsAlias(alias)) {
            byte[] raw = new byte[KEY_SIZE_BITS / 8];
            new SecureRandom().nextBytes(raw);
            SecretKeySpec sk = new SecretKeySpec(raw, KEY_ALGORITHM);
            KeyStore.SecretKeyEntry entry = new KeyStore.SecretKeyEntry(sk);
            KeyProtection prot = new KeyProtection.Builder(
                    KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .build();
            k.setEntry(alias, entry, prot);
        }
        KeyStore.SecretKeyEntry entry =
                (KeyStore.SecretKeyEntry) k.getEntry(alias, null);
        if (entry == null) {
            throw new IllegalStateException("imported key entry is null");
        }
        return entry.getSecretKey().getEncoded();
    }

    private static String encryptFallback(String plaintext, String alias)
            throws Exception {
        byte[] raw = ensureImportedKey(alias);
        Cipher cipher = Cipher.getInstance(TRANSFORMATION);
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(raw, KEY_ALGORITHM));
        byte[] iv = cipher.getIV();
        byte[] ct = cipher.doFinal(plaintext.getBytes("UTF-8"));
        return b64(iv) + "." + b64(ct);
    }

    private static String decryptFallback(String blob, String alias)
            throws Exception {
        int dot = blob.indexOf('.');
        if (dot <= 0) {
            throw new IllegalArgumentException("bad fallback blob");
        }
        byte[] iv = unb64(blob.substring(0, dot));
        byte[] ct = unb64(blob.substring(dot + 1));
        byte[] raw = ensureImportedKey(alias);
        Cipher cipher = Cipher.getInstance(TRANSFORMATION);
        cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(raw, KEY_ALGORITHM),
                new GCMParameterSpec(GCM_TAG_BITS, iv));
        return new String(cipher.doFinal(ct), "UTF-8");
    }

    // ------------------------------------------------------------------
    // Public API used from Python via pyjnius (String params only)
    // ------------------------------------------------------------------

    public static String selfTest() {
        String token = "tieba-sign-m22-java-" + System.nanoTime();
        String primaryErr = null;

        String aliasP = "tiebasign_ks_" + Long.toHexString(System.nanoTime() & 0xffffL);
        try {
            String blob = encryptPrimary(token, aliasP);
            String back = decryptPrimary(blob, aliasP);
            if (!token.equals(back)) {
                throw new IllegalStateException("primary round-trip mismatch");
            }
            mode = "keystore-cipher";
            return "keystore-cipher";
        } catch (Exception e) {
            primaryErr = e.getClass().getName() + ": " + e.getMessage();
        } finally {
            deleteEntry(aliasP);
        }

        String aliasF = "tiebasign_py_" + Long.toHexString(System.nanoTime() & 0xffffL);
        try {
            String blob = encryptFallback(token, aliasF);
            String back = decryptFallback(blob, aliasF);
            if (!token.equals(back)) {
                throw new IllegalStateException("fallback round-trip mismatch");
            }
            mode = "app-java-gcm";
            return "app-java-gcm";
        } catch (Exception e) {
            throw new RuntimeException(
                    "both Keystore paths failed: primary=" + primaryErr
                            + " | fallback=" + e.getClass().getName() + ": "
                            + e.getMessage(), e);
        } finally {
            deleteEntry(aliasF);
        }
    }

    public static String encrypt(String plaintext, String alias) throws Exception {
        if (mode == null) {
            selfTest();
        }
        if ("keystore-cipher".equals(mode)) {
            return "k1." + encryptPrimary(plaintext, alias);
        }
        return "a2." + encryptFallback(plaintext, alias);
    }

    public static String decrypt(String blob, String alias) throws Exception {
        if (blob.startsWith("k1.")) {
            return decryptPrimary(blob.substring(3), alias);
        }
        if (blob.startsWith("a2.")) {
            return decryptFallback(blob.substring(3), alias);
        }
        throw new IllegalArgumentException("unknown blob prefix");
    }
}
