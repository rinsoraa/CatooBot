package catoo.attrib;

import java.util.LinkedHashMap;
import java.util.Map;

/** 极简扁平 JSON 解析：{ \"k\": \"v\" } 形态，**正确处理带逗号的字符串值**（如 pos=\"-247,67,-141\"）。 */
final class Json {
    private Json() {}

    static Map<String, String> flat(String body) {
        Map<String, String> out = new LinkedHashMap<>();
        if (body == null) return out;
        String text = body.trim();
        if (text.startsWith("{")) text = text.substring(1);
        if (text.endsWith("}")) text = text.substring(0, text.length() - 1);
        int index = 0;
        while (index < text.length()) {
            int keyStart = text.indexOf('"', index);
            if (keyStart < 0) break;
            int keyEnd = text.indexOf('"', keyStart + 1);
            if (keyEnd < 0) break;
            String key = text.substring(keyStart + 1, keyEnd);
            int colon = text.indexOf(':', keyEnd);
            if (colon < 0) break;
            int valueStart = colon + 1;
            while (valueStart < text.length() && Character.isWhitespace(text.charAt(valueStart))) valueStart += 1;
            String value;
            if (valueStart < text.length() && text.charAt(valueStart) == '"') {
                int valueEnd = text.indexOf('"', valueStart + 1);
                if (valueEnd < 0) break;
                value = text.substring(valueStart + 1, valueEnd);
                index = valueEnd + 1;
            } else {
                int valueEnd = valueStart;
                while (valueEnd < text.length() && text.charAt(valueEnd) != ',') valueEnd += 1;
                value = text.substring(valueStart, valueEnd).trim();
                index = valueEnd;
            }
            if (!key.isEmpty()) out.put(key, value);
        }
        return out;
    }
}
