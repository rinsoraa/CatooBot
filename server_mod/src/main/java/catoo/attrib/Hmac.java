package catoo.attrib;

import java.nio.charset.StandardCharsets;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

final class Hmac {
    private Hmac() {}

    static String sign(String body, String token) {
        if (token == null || token.isEmpty()) return "";
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(token.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return HexFormat.of().formatHex(mac.doFinal(body.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception error) {
            return "";
        }
    }

    static boolean verify(String body, String token, String signature) {
        if (token == null || token.isEmpty()) return false;
        String expected = sign(body, token);
        return !expected.isEmpty() && signature != null && signature.matches("[a-fA-F0-9]{64}")
                && java.security.MessageDigest.isEqual(HexFormat.of().parseHex(expected), HexFormat.of().parseHex(signature));
    }
}
