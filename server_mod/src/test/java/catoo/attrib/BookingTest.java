package catoo.attrib;

import java.util.*;

/** P2 E2：纯逻辑竞态矩阵（无需 Minecraft 运行时）。 */
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

    static Map<String, String> fields(String action, String nonce, String pos, String block) {
        Map<String, String> map = new LinkedHashMap<>();
        map.put("action_id", action);
        map.put("nonce", nonce);
        map.put("dimension", "minecraft:overworld");
        map.put("pos", pos);
        map.put("expected_block", block);
        map.put("session_ref", "ref-1");
        map.put("ttl_ms", "60000");
        return map;
    }

    public static void main(String[] args) {
        Booking booking = new Booking();
        long seq = booking.newSession("Catodayo", "uuid-bot", "ref-1");
        check(seq == 1, "first JOIN allocates session_seq=1");

        Map<String, String> good = fields("act_1", "nonce-aaaaaaaaaaaaaaaa", "1,2,3", "minecraft:oak_log");
        Booking.ReserveResult ok = booking.reserve(good, f -> "Block{minecraft:oak_log}", n -> "ref-1", "ref-1");
        check(ok.ok && ok.httpCode == 200, "reserve accepted when server-read block matches");

        Map<String, String> wrongBlock = fields("act_2", "nonce-bbbbbbbbbbbbbbbb", "1,2,3", "minecraft:stone");
        Booking.ReserveResult mismatch = booking.reserve(wrongBlock, f -> "Block{minecraft:oak_log}", n -> "ref-1", "ref-1");
        check(!mismatch.ok && "target_mismatch".equals(mismatch.reason), "target mismatch rejects reservation");

        Map<String, String> replay = fields("act_3", "nonce-aaaaaaaaaaaaaaaa", "5,2,3", "minecraft:oak_log");
        Booking.ReserveResult nonceReplay = booking.reserve(replay, f -> "Block{minecraft:oak_log}", n -> "ref-1", "ref-1");
        check(!nonceReplay.ok && "nonce_replay".equals(nonceReplay.reason), "replayed nonce rejected");

        Map<String, String> dupAction = fields("act_1", "nonce-cccccccccccccccc", "1,2,3", "minecraft:oak_log");
        Booking.ReserveResult duplicate = booking.reserve(dupAction, f -> "Block{minecraft:oak_log}", n -> "ref-1", "ref-1");
        check(!duplicate.ok && "duplicate_action".equals(duplicate.reason), "duplicate action reservation rejected");

        Booking.MatchResult self = booking.match("Catodayo", "1,2,3", "Block{minecraft:oak_log}", "Block{minecraft:air}");
        check("SELF_CONFIRMED".equals(self.state) && self.reservation != null, "normal dig matches uniquely -> SELF_CONFIRMED");

        Booking.MatchResult repeat = booking.match("Catodayo", "1,2,3", "Block{minecraft:oak_log}", "Block{minecraft:air}");
        check("UNREGISTERED_BREAK".equals(repeat.state), "consumed reservation is not reusable");

        booking.reserve(fields("act_4", "nonce-dddddddddddddddd", "9,2,3", "minecraft:oak_log"), f -> "Block{minecraft:oak_log}", n -> "ref-1", "ref-1");
        Booking.MatchResult other = booking.match("CatodayoMate", "9,2,3", "Block{minecraft:oak_log}", "Block{minecraft:air}");
        check("UNKNOWN".equals(other.state) && "no_valid_session".equals(other.reason), "break by unknown session -> UNKNOWN");

        booking.reserve(fields("act_5", "nonce-eeeeeeeeeeeeeeee", "7,2,3", "minecraft:oak_log"), f -> "Block{minecraft:oak_log}", n -> "ref-1", "ref-1");
        booking.reserve(fields("act_6", "nonce-ffffffffffffffff", "7,2,3", "minecraft:oak_log"), f -> "Block{minecraft:oak_log}", n -> "ref-1", "ref-1");
        Booking.MatchResult many = booking.match("Catodayo", "7,2,3", "Block{minecraft:oak_log}", "Block{minecraft:air}");
        check("AMBIGUOUS".equals(many.state), "multiple candidates -> AMBIGUOUS");

        Booking.MatchResult unregistered = booking.match("Catodayo", "100,60,100", "Block{minecraft:dirt}", "Block{minecraft:air}");
        check("UNREGISTERED_BREAK".equals(unregistered.state), "unregistered break recorded as external-player evidence, never self");

        booking.newSession("Catodayo", "uuid-bot", "ref-2");
        Booking.MatchResult oldSession = booking.match("Catodayo", "7,2,3", "Block{minecraft:oak_log}", "Block{minecraft:air}");
        check("UNKNOWN".equals(oldSession.state), "stale session reservations become UNKNOWN");

        booking.invalidateAll();
        check(booking.activeReservations() == 0, "server restart / invalidation clears all reservations");

        check(Hmac.verify("body", "token", Hmac.sign("body", "token")), "HMAC round-trip verifies");
        check(!Hmac.verify("body", "token", "deadbeef"), "HMAC mismatch rejected");
        check(!Hmac.verify("body", "", Hmac.sign("body", "")), "missing token rejected (never accepted)");

        System.out.println("[booking-test] " + passed + "/" + (passed + failed) + " checks passed");
        if (failed > 0) {
            System.out.println("[booking-test] FAILED");
            System.exit(1);
        }
        System.out.println("[booking-test] ALL CHECKS PASSED");
    }
}
