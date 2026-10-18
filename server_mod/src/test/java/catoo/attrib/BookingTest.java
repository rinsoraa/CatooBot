package catoo.attrib;

import java.util.*;

/** P2 验收修复轮 E2：维度/玩家身份/关闭语义 + 原有契约回归（纯逻辑）。 */
public final class BookingTest {
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

    static Map<String, String> fields(String action, String nonce, String pos, String block, String sessionRef, String dim) {
        Map<String, String> map = new LinkedHashMap<>();
        map.put("action_id", action);
        map.put("nonce", nonce);
        map.put("dimension", dim);
        map.put("pos", pos);
        map.put("expected_block", block);
        map.put("session_ref", sessionRef);
        map.put("ttl_ms", "60000");
        return map;
    }

    public static void main(String[] args) {
        Booking booking = new Booking();
        Booking.Session bot = booking.newSession("Catodayo", "uuid-bot", "ref-1", new Object());
        Booking.Session mate = booking.newSession("CatodayoMate", "uuid-mate", "ref-2", new Object());
        check(bot.seq == 1 && mate.seq == 2, "two players get distinct monotonic session_seq");

        Booking.ReserveResult ok = booking.reserve(
                fields("act_1", "nonce-aaaaaaaaaaaaaaaa", "1,2,3", "Block{minecraft:oak_log}", "ref-1", "minecraft:overworld"),
                f -> "Block{minecraft:oak_log}[axis=y]");
        check(ok.ok, "reserve accepted for the current session with a real dimension");

        Booking.ReserveResult noDim = booking.reserve(
                fields("act_2", "nonce-bbbbbbbbbbbbbbbb", "1,2,3", "minecraft:oak_log", "ref-1", ""),
                f -> "Block{minecraft:oak_log}");
        check(!noDim.ok && "missing_field".equals(noDim.reason), "reserve without dimension is rejected (fail closed)");

        Booking.ReserveResult wrongSession = booking.reserve(
                fields("act_3", "nonce-cccccccccccccccc", "1,2,3", "minecraft:oak_log", "ref-999", "minecraft:overworld"),
                f -> "Block{minecraft:oak_log}");
        check(!wrongSession.ok && "session_not_current".equals(wrongSession.reason), "unknown/non-current session_ref rejected");

        Booking.ReserveResult substring = booking.reserve(
                fields("act_4", "nonce-dddddddddddddddd", "5,2,3", "minecraft:oak_log", "ref-1", "minecraft:overworld"),
                f -> "Block{minecraft:dark_oak_log}");
        check(!substring.ok && "target_mismatch".equals(substring.reason), "oak_log must NOT match dark_oak_log (exact id)");

        Booking.ReserveResult replay = booking.reserve(
                fields("act_5", "nonce-aaaaaaaaaaaaaaaa", "1,2,3", "minecraft:oak_log", "ref-1", "minecraft:overworld"),
                f -> "Block{minecraft:oak_log}");
        check(!replay.ok && "nonce_replay".equals(replay.reason), "replayed nonce rejected");

        Booking.MatchResult self = booking.match(bot, "uuid-bot", "minecraft:overworld", "1,2,3",
                "Block{minecraft:oak_log}[axis=y]");
        check("SELF_CONFIRMED".equals(self.state) && self.reservation != null,
                "G1/G2: same dimension + real event uuid -> SELF_CONFIRMED");

        Booking.MatchResult repeat = booking.match(bot, "uuid-bot", "minecraft:overworld", "1,2,3", "Block{minecraft:oak_log}");
        check(!"SELF_CONFIRMED".equals(repeat.state), "consumed reservation can never yield SELF_CONFIRMED again");

        booking.reserve(fields("act_6", "nonce-eeeeeeeeeeeeeeee", "9,2,3", "minecraft:oak_log", "ref-1", "minecraft:overworld"),
                f -> "Block{minecraft:oak_log}");
        Booking.MatchResult dimMismatch = booking.match(bot, "uuid-bot", "minecraft:the_nether", "9,2,3",
                "Block{minecraft:oak_log}");
        check("UNKNOWN".equals(dimMismatch.state), "G1: dimension mismatch cannot be SELF_CONFIRMED (got " + dimMismatch.state + ")");

        Booking.MatchResult dimUnknown = booking.match(bot, "uuid-bot", "", "9,2,3", "Block{minecraft:oak_log}");
        check("UNKNOWN".equals(dimUnknown.state) && "event_dimension_unknown".equals(dimUnknown.reason),
                "G1: unknown event dimension fails closed");

        Booking.MatchResult identityMismatch = booking.match(mate, "uuid-mate", "minecraft:overworld", "9,2,3",
                "Block{minecraft:oak_log}");
        check("UNKNOWN".equals(identityMismatch.state), "G2: other player's session cannot consume bot reservation");

        Booking.MatchResult forgedUuid = booking.match(bot, "uuid-evil", "minecraft:overworld", "9,2,3",
                "Block{minecraft:oak_log}");
        check("UNKNOWN".equals(forgedUuid.state) && "event_identity_mismatch".equals(forgedUuid.reason),
                "G2: event uuid differing from session binding fails closed");

        Booking.MatchResult unknownUuid = booking.match(bot, "unknown", "minecraft:overworld", "9,2,3",
                "Block{minecraft:oak_log}");
        check("UNKNOWN".equals(unknownUuid.state) && "event_identity_unknown".equals(unknownUuid.reason),
                "G2: missing/unreadable event uuid fails closed");

        Booking.MatchResult unregistered = booking.match(bot, "uuid-bot", "minecraft:overworld", "100,60,100",
                "Block{minecraft:dirt}");
        check("UNREGISTERED_BREAK".equals(unregistered.state), "unregistered break recorded as external evidence");

        Booking.MatchResult otherPlayerBreak = booking.match(mate, "uuid-mate", "minecraft:overworld", "9,2,3",
                "Block{minecraft:oak_log}");
        check(!"SELF_CONFIRMED".equals(otherPlayerBreak.state), "other player's real break never becomes self-proof");

        booking.reserve(fields("act_7", "nonce-ffffffffffffffff", "7,2,3", "minecraft:oak_log", "ref-1", "minecraft:overworld"),
                f -> "Block{minecraft:oak_log}");
        booking.reserve(fields("act_8", "nonce-1111111111111111", "7,2,3", "minecraft:oak_log", "ref-1", "minecraft:overworld"),
                f -> "Block{minecraft:oak_log}");
        Booking.MatchResult many = booking.match(bot, "uuid-bot", "minecraft:overworld", "7,2,3", "Block{minecraft:oak_log}");
        check("AMBIGUOUS".equals(many.state), "multiple candidates -> AMBIGUOUS");

        Booking.Session botAgain = booking.newSession("Catodayo", "uuid-bot", "ref-3", new Object());
        Booking.MatchResult oldSession = booking.match(botAgain, "uuid-bot", "minecraft:overworld", "7,2,3",
                "Block{minecraft:oak_log}");
        check("UNKNOWN".equals(oldSession.state), "reservations from replaced session become UNKNOWN");

        booking.invalidateAll();
        check(booking.activeReservations() == 0, "invalidation clears all reservations");
        check(Booking.canonicalBlock("Block{minecraft:oak_leaves}[distance=3]").equals("minecraft:oak_leaves"),
                "canonical block id extraction");
        check(Booking.normalizeDimension("minecraft:the_nether").equals("minecraft:the_nether"),
                "dimension normalization keeps namespace id");
        check(Booking.normalizeDimension("").isEmpty() && Booking.normalizeDimension("unknown").isEmpty(),
                "unknown dimension normalizes to empty (fail closed)");
        check(Hmac.verify("body", "token", Hmac.sign("body", "token")), "HMAC round-trip");
        check(!Hmac.verify("body", "token", "deadbeef"), "HMAC mismatch rejected");

        System.out.println("[booking-test] " + passed + "/" + (passed + failed) + " checks passed");
        if (failed > 0) {
            System.out.println("[booking-test] FAILED");
            System.exit(1);
        }
        System.out.println("[booking-test] ALL CHECKS PASSED");
    }
}
