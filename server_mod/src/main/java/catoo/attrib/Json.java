package catoo.attrib;

import java.util.LinkedHashMap;
import java.util.Map;

/** 极简扁平 JSON 解析：只支持 {\"k\":\"v\"} 形态，避免引入额外依赖。 */
final class Json {
    private Json() {}

    static Map<String, String> flat(String body) {
        Map<String, String> out = new LinkedHashMap<>();
        if (body == null) return out;
        String text = body.trim();
        if (text.startsWith("{")) text = text.substring(1);
        if (text.endsWith("}")) text = text.substring(0, text.length() - 1);
        for (String raw : text.split(",")) {
            String[] parts = raw.split(":", 2);
            if (parts.length != 2) continue;
            String key = clean(parts[0]);
            String value = clean(parts[1]);
            if (!key.isEmpty()) out.put(key, value);
        }
        return out;
    }

    private static String clean(String raw) {
        String value = raw.trim();
        if (value.length() >= 2 && value.startsWith("\"") && value.endsWith("\"")) {
            value = value.substring(1, value.length() - 1);
        }
        return value.replace("\\\"", "\"").trim();
    }
}
