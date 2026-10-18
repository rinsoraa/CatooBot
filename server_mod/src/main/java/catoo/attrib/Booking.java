package catoo.attrib;

import java.util.*;
import java.util.function.Function;

/** 会话 + 登记 + 唯一匹配（纯逻辑，无 Minecraft 依赖，可离线单测）。 */
final class Booking {
    static final class Session {
        final String name; final String uuid; final String ref; final long seq;
        volatile boolean valid = true;
        Session(String name, String uuid, String ref, long seq) {
            this.name = name; this.uuid = uuid; this.ref = ref; this.seq = seq;
        }
    }

    static final class Reservation {
        final String id; final String actionId; final String nonce; final String dim; final String pos;
        final String expectedBlock; final String sessionRef; final long seq; final long expiresAtNanos;
        boolean consumed = false;
        Reservation(String id, String actionId, String nonce, String dim, String pos, String expectedBlock,
                String sessionRef, long seq, long ttlMs) {
            this.id = id; this.actionId = actionId; this.nonce = nonce; this.dim = dim; this.pos = pos;
            this.expectedBlock = expectedBlock; this.sessionRef = sessionRef; this.seq = seq;
            this.expiresAtNanos = System.nanoTime() + ttlMs * 1_000_000L;
        }
    }

    static final class MatchResult {
        final String state; final String reason; final Reservation reservation;
        MatchResult(String state, String reason, Reservation reservation) {
            this.state = state; this.reason = reason; this.reservation = reservation;
        }
    }

    static final class ReserveResult {
        final boolean ok; final String reason; final String reservationId; final int httpCode;
        ReserveResult(boolean ok, String reason, String reservationId, int httpCode) {
            this.ok = ok; this.reason = reason; this.reservationId = reservationId; this.httpCode = httpCode;
        }
    }

    private final Map<String, Session> sessions = new LinkedHashMap<>();
    private final List<Reservation> reservations = new ArrayList<>();
    private final Set<String> seenNonces = new HashSet<>();
    private long seqCounter = 0;
    int rejected = 0, expired = 0, ambiguous = 0, consumedCount = 0, unknown = 0;

    synchronized long newSession(String name, String uuid, String ref) {
        Session previous = sessions.get(name);
        if (previous != null) previous.valid = false;
        seqCounter += 1;
        sessions.put(name, new Session(name, uuid, ref, seqCounter));
        return seqCounter;
    }

    synchronized void invalidateAll() {
        for (Session session : sessions.values()) session.valid = false;
        reservations.clear();
        seenNonces.clear();
    }

    synchronized ReserveResult reserve(Map<String, String> fields,
            Function<Map<String, String>, String> blockAt, Function<String, String> sessionRefOf,
            String ignoredExplicitSessionRef) {
        String actionId = fields.getOrDefault("action_id", "").trim();
        String nonce = fields.getOrDefault("nonce", "").trim();
        String dim = fields.getOrDefault("dimension", "").trim();
        String pos = fields.getOrDefault("pos", "").trim();
        String expected = fields.getOrDefault("expected_block", "").trim();
        if (actionId.isEmpty() || nonce.isEmpty() || dim.isEmpty() || pos.isEmpty() || expected.isEmpty()) {
            rejected += 1;
            return new ReserveResult(false, "missing_field", "", 400);
        }
        if (nonce.length() < 16) {
            rejected += 1;
            return new ReserveResult(false, "nonce_too_short", "", 400);
        }
        if (!seenNonces.add(nonce)) {
            rejected += 1;
            return new ReserveResult(false, "nonce_replay", "", 409);
        }
        for (Reservation existing : reservations) {
            if (existing.consumed) continue;
            if (!existing.actionId.equals(actionId)) continue;
            if (expired(existing)) continue;
            rejected += 1;
            return new ReserveResult(false, "duplicate_action", "", 409);
        }
        String block = blockAt.apply(fields);
        if (block == null) {
            rejected += 1;
            return new ReserveResult(false, "world_unavailable", "", 503);
        }
        if (!block.contains(expected)) {
            rejected += 1;
            return new ReserveResult(false, "target_mismatch", "", 409);
        }
        String sessionRef = fields.getOrDefault("session_ref", "");
        long ttl = 60000L;
        try { ttl = Long.parseLong(fields.getOrDefault("ttl_ms", "60000")); } catch (Exception ignored) { }
        Reservation reservation = new Reservation(UUID.randomUUID().toString(), actionId, nonce, dim, pos,
                expected, sessionRef, seqCounter, Math.max(1000L, Math.min(ttl, 600000L)));
        reservations.add(reservation);
        return new ReserveResult(true, "ok", reservation.id, 200);
    }

    private boolean expired(Reservation reservation) {
        if (System.nanoTime() > reservation.expiresAtNanos) {
            if (!reservation.consumed) expired += 1;
            return true;
        }
        return false;
    }

    synchronized MatchResult match(String name, String pos, String blockBefore, String blockAfter) {
        Session session = sessions.get(name);
        if (session == null || !session.valid) {
            unknown += 1;
            return new MatchResult("UNKNOWN", "no_valid_session", null);
        }
        int stale = 0;
        List<Reservation> candidates = new ArrayList<>();
        List<Reservation> samePos = new ArrayList<>();
        for (Reservation reservation : reservations) {
            if (reservation.consumed) continue;
            if (!reservation.pos.equals(pos)) continue;
            if (!reservation.sessionRef.isEmpty() && !reservation.sessionRef.equals(session.ref)) {
                stale += 1;
                continue;
            }
            samePos.add(reservation);
            if (expired(reservation)) continue;
            if (!reservation.expectedBlock.isEmpty() && !blockBefore.contains(reservation.expectedBlock)) continue;
            candidates.add(reservation);
        }
        if (candidates.size() > 1) {
            ambiguous += 1;
            return new MatchResult("AMBIGUOUS", "multiple_candidates", null);
        }
        if (candidates.isEmpty() && stale > 0) {
            unknown += 1;
            return new MatchResult("UNKNOWN", "stale_session", null);
        }
        if (candidates.isEmpty()) {
            if (!samePos.isEmpty()) {
                unknown += 1;
                return new MatchResult("UNKNOWN", "target_mismatch", null);
            }
            unknown += 1;
            return new MatchResult("UNREGISTERED_BREAK", "no_reservation", null);
        }
        Reservation reservation = candidates.get(0);
        if (!reservation.sessionRef.isEmpty() && !reservation.sessionRef.equals(session.ref)) {
            unknown += 1;
            return new MatchResult("UNKNOWN", "session_mismatch", null);
        }
        reservation.consumed = true;
        consumedCount += 1;
        return new MatchResult("SELF_CONFIRMED", "unique_match", reservation);
    }

    synchronized int activeReservations() {
        int count = 0;
        for (Reservation reservation : reservations) if (!reservation.consumed) count += 1;
        return count;
    }
}
