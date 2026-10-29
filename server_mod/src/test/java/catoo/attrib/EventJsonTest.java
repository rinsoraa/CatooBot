package catoo.attrib;

/** 缺口 A 的端到端证明：走真实 eventJson() 生成路径，验证 JSON 维度来自事件而非配置。 */
public final class EventJsonTest {
    static int passed = 0;
    static int failed = 0;

    static void check(boolean condition, String label) {
        if (condition) {
            passed += 1;
            System.out.println("  OK   " + label);
        } else {
            failed += 1;
            System.out.println("  FAIL " + label);
        }
    }

    public static void main(String[] args) {
        CatooAttributionMod.CFG.setProperty("dimension_label", "minecraft:overworld");
        Booking.MatchResult match = new Booking.MatchResult("SELF_CONFIRMED", "unique_match", null);

        String nether = CatooAttributionMod.eventJson("ev-1", match, null, "uuid-x", "minecraft:the_nether",
                "1,2,3", "Block{minecraft:oak_log}", "Block{minecraft:air}");
        check(nether.contains("\"dimension\":\"minecraft:the_nether\""),
                "G1: real event dimension (nether) is emitted verbatim");
        check(!nether.contains("minecraft:overworld"),
                "G1: config dimension_label must not leak into event json");

        String overworld = CatooAttributionMod.eventJson("ev-2", match, null, "uuid-x", "minecraft:overworld",
                "1,2,3", "a", "b");
        check(overworld.contains("\"dimension\":\"minecraft:overworld\""), "G1: overworld emitted correctly");

        String unknown = CatooAttributionMod.eventJson("ev-3", match, null, "uuid-x", "", "1,2,3", "a", "b");
        check(unknown.contains("\"dimension\":\"\""), "G1: unknown dimension stays empty (no overworld fallback)");

        check(CatooAttributionMod.eventJson("ev-4", match, null, "uuid-y", "minecraft:the_end", "0,0,0", "a", "b")
                .contains("\"player_uuid\":\"uuid-y\""), "G2: event uuid comes from the argument (event identity)");

        System.out.println("[event-json-test] " + passed + "/" + (passed + failed) + " checks passed");
        if (failed > 0) {
            System.out.println("[event-json-test] FAILED");
            System.exit(1);
        }
        System.out.println("[event-json-test] ALL CHECKS PASSED");
    }
}
