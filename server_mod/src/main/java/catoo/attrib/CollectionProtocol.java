package catoo.attrib;

import java.util.Map;

/** A signed evidence contract, never authority or a game action. Legacy P3 is separate. */
final class CollectionProtocol {
    static final String VERSION = "catoobot.collection.v1";
    static String check(Map<String, String> fields, boolean available, boolean required) {
        if (!fields.containsKey("collection_protocol")) return required ? "collection_protocol_required" : "";
        if (!VERSION.equals(fields.get("collection_protocol"))) return "collection_protocol_unsupported";
        return available ? "" : "collection_protocol_unavailable";
    }
    static String field() { return ",\"collection_protocol\":" + Json.quote(VERSION); }
}
