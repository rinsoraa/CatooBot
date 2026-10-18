package catoo.attrib;

import java.util.*;

/** P2 修复轮 E2：会话/维度/精确方块 ID/关联/竞态（纯逻辑，无 Minecraft 运行时）。 */
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

    static Map<String, String> fields(String action, String nonce, String pos, String block, String sessionRef) {
        Map<String, String> map = new LinkedHashMap<>();
        map.put("action_id", action);
        map.put("nonce", nonce);
        map.put("dimension", "minecraft:overworld");
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

        Booking.ReserveResult ok = booking.reserve(fields("act_1", "nonce-aaaaaaaaaaaaaaaa", "1,2,3", "Block{minecraft:oak_log}", "ref-1"), f -> "Block{minecraft:oak_log}[axis=y]");
        check(ok.ok, "reserve accepted for the current session");

        Booking.ReserveResult wrongSession = booking.reserve(fields("act_2", "nonce-bbbbbbbbbbbbbbbb", "1,2,3", "minecraft:oak_log", "ref-999"), f -> "Block{minecraft:oak_log}");
        check(!wrongSession.ok && "session_not_current".equals(wrongSession.reason), "unknown/non-current session_ref rejected");

        Booking.ReserveResult staleSession = booking.reserve(fields("act_3", "nonce-cccccccccccccccc", "1,2,3", "minecraft:oak_log", "ref-2"), f -> "Block{minecraft:oak_log}");
        check(staleSession.ok, "its own session can reserve");

        Booking.ReserveResult substring = booking.reserve(fields("act_4", "nonce-dddddddddddddddd", "5,2,3", "minecraft:oak_log", "ref-1"), f -> "Block{minecraft:dark_oak_log}");
        check(!substring.ok && "target_mismatch".equals(substring.reason), "oak_log must NOT match dark_oak_log (exact id)");

        Booking.ReserveResult replay = booking.reserve(fields("act_5", "nonce-aaaaaaaaaaaaaaaa", "1,2,3", "minecraft:oak_log", "ref-1"), f -> "Block{minecraft:oak_log}");
        check(!replay.ok && "nonce_replay".equals(replay.reason), "replayed nonce rejected");

        Booking.MatchResult self = booking.match(bot, "uuid-bot", "1,2,3", "Block{minecraft:oak_log}[axis=y]");
        check("SELF_CONFIRMED".equals(self.state) && self.reservation != null, "unique match -> SELF_CONFIRMED");

        Booking.MatchResult repeat = booking.match(bot, "uuid-bot", "1,2,3", "Block{minecraft:oak_log}");
        check(!"SELF_CONFIRMED".equals(repeat.state), "consumed reservation can never yield SELF_CONFIRMED again (got " + repeat.state + ")");

        booking.reserve(fields("act_6", "nonce-eeeeeeeeeeeeeeee", "9,2,3", "minecraft:oak_log", "ref-1"), f -> "Block{minecraft:oak_log}");
        Booking.MatchResult otherPlayer = booking.match(mate, "uuid-mate", "9,2,3", "Block{minecraft:oak_log}");
        check("UNKNOWN".equals(otherPlayer.state), "other player's session cannot consume bot reservation");

        Booking.MatchResult otherIdentity = booking.match(bot, "uuid-evil", "9,2,3", "Block{minecraft:oak_log}");
        check("UNKNOWN".equals(otherIdentity.state), "identity mismatch cannot produce SELF_CONFIRMED");

        Booking.MatchResult unregistered = booking.match(bot, "uuid-bot", "100,60,100", "Block{minecraft:dirt}");
        check("UNREGISTERED_BREAK".equals(unregistered.state), "unregistered break recorded as external evidence");

        booking.reserve(fields("act_7", "nonce-ffffffffffffffff", "7,2,3", "minecraft:oak_log", "ref-1"), f -> "Block{minecraft:oak_log}");
        booking.reserve(fields("act_8", "nonce-1111111111111111", "7,2,3", "minecraft:oak_log", "ref-1"), f -> "Block{minecraft:oak_log}");
        Booking.MatchResult many = booking.match(bot, "uuid-bot", "7,2,3", "Block{minecraft:oak_log}");
        check("AMBIGUOUS".equals(many.state), "multiple candidates -> AMBIGUOUS");

        Booking.Session botAgain = booking.newSession("Catodayo", "uuid-bot", "ref-3", new Object());
        Booking.MatchResult oldSession = booking.match(botAgain, "uuid-bot", "7,2,3", "Block{minecraft:oak_log}");
        check("UNKNOWN".equals(oldSession.state), "reservations from replaced session become UNKNOWN");

        booking.invalidateAll();
        check(booking.activeReservations() == 0, "invalidation clears all reservations");
        check(Booking.canonicalBlock("Block{minecraft:oak_leaves}[distance=3,persistent=false]").equals("minecraft:oak_leaves"), "canonical block id extraction");

        check(Hmac.verify("body", "token", Hmac.sign("body", "token")), "HMAC round-trip");
        check(!Hmac.verify("body", "token", "deadbeef"), "HMAC mismatch rejected");
        check(!Hmac.verify("body", "", Hmac.sign("body", "")), "missing token rejected");

        System.out.println("[booking-test] " + passed + "/" + (passed + failed) + " checks passed");
        if (failed > 0) {
            System.out.println("[booking-test] FAILED");
            System.exit(1);
        }
        System.out.println("[booking-test] ALL CHECKS PASSED");
    }
}
