package catoo.attrib;

/** 缺口 B：维度支持范围（只支持原版三维度）与一致性判定。 */
public final class DimensionPolicyTest {
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
        check("minecraft:overworld".equals(Booking.normalizeDimension("minecraft:overworld")), "overworld accepted");
        check("minecraft:the_nether".equals(Booking.normalizeDimension("minecraft:the_nether")), "nether accepted");
        check("minecraft:the_end".equals(Booking.normalizeDimension("minecraft:the_end")), "end accepted");
        check(Booking.normalizeDimension("minecraft:custom_dim").isEmpty(), "unknown vanilla path rejected");
        check(Booking.normalizeDimension("modded:overworld").isEmpty(), "custom namespace rejected (documented limit)");
        check(Booking.normalizeDimension("").isEmpty(), "empty rejected");
        check(Booking.normalizeDimension("unknown").isEmpty(), "unknown literal rejected");
        check(Booking.normalizeDimension("minecraft:overworld[foo]").equals("minecraft:overworld"),
                "bracketed readout normalized");
        check(Booking.checkDimension("minecraft:overworld", "minecraft:overworld").isEmpty(), "same dimension passes");
        check("dimension_not_current".equals(Booking.checkDimension("minecraft:overworld", "minecraft:the_nether")),
                "cross dimension rejected");
        check("dimension_unknown".equals(Booking.checkDimension("minecraft:overworld", "")), "unknown current rejected");
        check("dimension_unknown".equals(Booking.checkDimension("", "minecraft:overworld")), "missing request rejected");

        System.out.println("[dimension-policy-test] " + passed + "/" + (passed + failed) + " checks passed");
        if (failed > 0) {
            System.out.println("[dimension-policy-test] FAILED");
            System.exit(1);
        }
        System.out.println("[dimension-policy-test] ALL CHECKS PASSED");
    }
}
