package catoo.attrib;

import java.util.Map;
import java.util.HashMap;

public final class CollectionProtocolTest {
    static int passed;
    static void check(boolean value, String label) {
        if (!value) throw new AssertionError(label);
        passed++; System.out.println("  OK   " + label);
    }
    public static void main(String[] args) {
        check(CollectionProtocol.check(Map.of(), false, false).isEmpty(), "legacy atomic break needs no collection capability");
        check("collection_protocol_required".equals(CollectionProtocol.check(Map.of(), true, true)), "pickup cannot silently downgrade");
        for (String version : new String[]{"", "v2", "true", "1", CollectionProtocol.VERSION + " "}) {
            check("collection_protocol_unsupported".equals(CollectionProtocol.check(Map.of("collection_protocol", version), true, false)), "unknown/extraneous protocol is refused");
        }
        check(CollectionProtocol.check(Map.of("collection_protocol", CollectionProtocol.VERSION), true, true).isEmpty(), "current available protocol admitted");
        check("collection_protocol_unavailable".equals(CollectionProtocol.check(Map.of("collection_protocol", CollectionProtocol.VERSION), false, false)), "closed ledger cannot acknowledge collection");
        for (String invalid : new String[]{"{\"collection_protocol\":1}", "{\"collection_protocol\":true}", "{\"collection_protocol\":[]}"}) {
            boolean refused = false;
            try { Json.flat(invalid); } catch (IllegalArgumentException expected) { refused = true; }
            check(refused, "typed parser refuses non-string protocol");
        }
        Booking book = new Booking();
        var session = book.newSession("bot", "uuid", "ref", new Object()); book.attachPlayer("ref", new Object());
        Map<String, String> request = new HashMap<>(Map.of("session_ref", "ref", "action_id", "act", "nonce", "nonce-0123456789012345", "dimension", "minecraft:overworld", "pos", "1,2,3", "expected_block", "minecraft:oak_log"));
        request.put("collection_protocol", "old");
        check(!book.reserve(request, f -> { throw new AssertionError("unsupported protocol must not read world"); }).ok && book.retainedNonces() == 0, "unsupported request has zero reservation/world access");
        request.put("collection_protocol", CollectionProtocol.VERSION);
        var result = book.reserve(request, f -> "minecraft:oak_log");
        request.put("collection_protocol", "mutated");
        var match = book.match(session, "uuid", "minecraft:overworld", "1,2,3", "minecraft:oak_log");
        check(result.ok && CollectionProtocol.VERSION.equals(match.reservation.collectionProtocol), "reservation freezes accepted contract independently of caller map");
        System.out.println("[collection-protocol-test] " + passed + "/" + passed + " checks passed");
    }
}
