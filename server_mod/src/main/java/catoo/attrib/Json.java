package catoo.attrib;

import java.util.LinkedHashMap;
import java.util.Map;

/** Bounded flat protocol object: unique keys, strings and integer tokens only. */
final class Json {
    private final String text;
    private int index;
    private Json(String text) { this.text = text; }

    static Map<String, String> flat(String body) {
        if (body == null || body.length() > 8192) throw new IllegalArgumentException("size");
        Json parser = new Json(body);
        Map<String, String> out = new LinkedHashMap<>();
        parser.space(); parser.expect('{'); parser.space();
        if (!parser.take('}')) {
            do {
                parser.space(); String key = parser.string(); parser.space(); parser.expect(':'); parser.space();
                boolean quoted = parser.peek('"');
                String value = quoted ? parser.string() : parser.integer();
                boolean numericField = key.equals("x") || key.equals("y") || key.equals("z") || key.equals("ttl_ms");
                if (numericField == quoted || out.size() >= 16 || out.putIfAbsent(key, value) != null) {
                    throw new IllegalArgumentException("type_or_duplicate");
                }
                parser.space();
            } while (parser.take(','));
            parser.expect('}');
        }
        parser.space();
        if (parser.index != body.length()) throw new IllegalArgumentException("trailing");
        return out;
    }

    private String integer() {
        int start = index;
        take('-');
        if (!take('0')) {
            if (index >= text.length() || text.charAt(index) < '1' || text.charAt(index) > '9') throw new IllegalArgumentException("integer");
            while (index < text.length() && text.charAt(index) >= '0' && text.charAt(index) <= '9') index++;
        }
        return text.substring(start, index);
    }
    private String string() {
        expect('"'); StringBuilder out = new StringBuilder();
        while (index < text.length()) {
            char value = text.charAt(index++);
            if (value == '"') return out.toString();
            if (value < 32) throw new IllegalArgumentException("control");
            if (value == '\\') {
                if (index >= text.length()) throw new IllegalArgumentException("escape");
                char code = text.charAt(index++);
                value = switch (code) {
                    case '"', '\\', '/' -> code;
                    case 'b' -> '\b'; case 'f' -> '\f'; case 'n' -> '\n'; case 'r' -> '\r'; case 't' -> '\t';
                    case 'u' -> unicode();
                    default -> throw new IllegalArgumentException("escape");
                };
            }
            out.append(value);
            if (out.length() > 256) throw new IllegalArgumentException("string_size");
        }
        throw new IllegalArgumentException("unterminated");
    }
    private char unicode() {
        if (index + 4 > text.length()) throw new IllegalArgumentException("unicode");
        String digits = text.substring(index, index + 4);
        if (!digits.matches("[a-fA-F0-9]{4}")) throw new IllegalArgumentException("unicode");
        index += 4;
        return (char) Integer.parseInt(digits, 16);
    }
    private void space() { while (index < text.length() && " \r\n\t".indexOf(text.charAt(index)) >= 0) index++; }
    private boolean peek(char value) { return index < text.length() && text.charAt(index) == value; }
    private boolean take(char value) { if (!peek(value)) return false; index++; return true; }
    private void expect(char value) { if (!take(value)) throw new IllegalArgumentException("syntax"); }
    static String quote(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '"' || c == '\\') out.append('\\').append(c);
            else if (c < 32) out.append(String.format("\\u%04x", (int) c));
            else out.append(c);
        }
        return out.append('"').toString();
    }
}
