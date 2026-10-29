package catoo.attrib;

import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

public final class ProtocolTest {
    static int passed;
    static void check(boolean value, String label) {
        if (!value) throw new AssertionError(label);
        passed++; System.out.println("  OK   " + label);
    }
    public static void main(String[] args) {
        check(Json.flat("{\"pos\":\"1,2,3\",\"x\":1,\"y\":2,\"z\":3}").get("pos").equals("1,2,3"), "commas in strings and typed integers round trip");
        check(Json.flat("{\"action_id\":\"quote\\\"slash\\\\line\\n\\u4e2d\"}").get("action_id").equals("quote\"slash\\line\n中"), "JSON escapes decoded precisely");
        check(Json.flat("{\"action_id\":" + Json.quote("a\"b\\c\n") + "}").get("action_id").equals("a\"b\\c\n"), "echoed fields cannot inject JSON");
        for (String invalid : new String[]{"", "[]", "null", "{\"x\":\"1\"}", "{\"x\":true}", "{\"x\":01}", "{\"x\":1.5}",
                "{\"x\":1e2}", "{\"action_id\":1}", "{\"x\":1,\"x\":2}", "{\"x\":{}}", "{\"x\":1,}",
                "{\"x\":1}trash", "{\"x\":1", "{\"action_id\":\"bad\\q\"}", "{\"action_id\":\"raw\nline\"}", " ".repeat(8193)}) {
            boolean refused = false;
            try { Json.flat(invalid); } catch (IllegalArgumentException expected) { refused = true; }
            check(refused, "invalid JSON/type/size refused");
        }
        Booking book = new Booking();
        Object player = new Object();
        var session = book.newSession("bot", "uuid", "ref", new Object()); book.attachPlayer("ref", player);
        for (int i = 0; i < Booking.MAX_NONCES; i++) {
            var result = book.reserve(Map.of("session_ref", "ref", "action_id", "act_" + i,
                    "nonce", "bounded_nonce_0123456789_" + i, "dimension", "minecraft:overworld",
                    "pos", "1,2,3", "expected_block", "minecraft:oak_log"), fields -> "minecraft:oak_log");
            if (!result.ok) throw new AssertionError(result.reason + " at " + i);
            book.match(session, "uuid", "minecraft:overworld", "1,2,3", "minecraft:oak_log");
        }
        check(book.retainedReservations() == 0 && book.retainedNonces() == Booking.MAX_NONCES,
                "consumed reservations pruned; current-session nonce history retained within fixed cap");
        AtomicInteger reads = new AtomicInteger();
        var refused = book.reserve(Map.of("session_ref", "ref", "action_id", "overflow", "nonce", "new_nonce_0123456789",
                "dimension", "minecraft:overworld", "pos", "1,2,3", "expected_block", "minecraft:oak_log"), fields -> { reads.incrementAndGet(); return "minecraft:oak_log"; });
        check(!refused.ok && "capacity".equals(refused.reason) && reads.get() == 0, "capacity fails closed before world read, never evicts nonce");
        book.disconnect("bot", "uuid", player);
        check(book.retainedNonces() == 0, "disconnect releases old-session nonce history");
        book.newSession("bot", "uuid", "new_ref", new Object());
        var replay = book.reserve(Map.of("session_ref", "ref", "action_id", "act_0", "nonce", "bounded_nonce_0123456789_0",
                "dimension", "minecraft:overworld", "pos", "1,2,3", "expected_block", "minecraft:oak_log"), fields -> "minecraft:oak_log");
        check(!replay.ok && "session_not_current".equals(replay.reason), "old raw request still refused after history cleanup and reconnect");
        System.out.println("[protocol-test] " + passed + "/" + passed + " checks passed");
    }
}
