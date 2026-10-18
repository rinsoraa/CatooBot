package catoo.attrib;

import java.util.*;
import java.util.function.Function;

/** 会话 / 登记 / 唯一匹配（纯逻辑）。维度与玩家身份必须来自**真实事件**，不得用预期值自证。 */
final class Booking {
    static final class Session {
        final String name;
        final String uuid;
        final String ref;
        final long seq;
        final Object world;
        boolean valid = true;
        Session(String name, String uuid, String ref, long seq, Object world) {
            this.name = name; this.uuid = uuid; this.ref = ref; this.seq = seq; this.world = world;
        }
    }

    static final class Reservation {
        final String id; final String actionId; final String nonce; final String dim; final String pos;
        final String expectedId; final String sessionRef; final long seq; final String playerUuid;
        final long expiresAtNanos; boolean consumed = false;
        Reservation(String id, String actionId, String nonce, String dim, String pos, String expectedId,
                String sessionRef, long seq, String playerUuid, long ttlMs) {
            this.id = id; this.actionId = actionId; this.nonce = nonce; this.dim = dim; this.pos = pos;
            this.expectedId = expectedId; this.sessionRef = sessionRef; this.seq = seq;
            this.playerUuid = playerUuid;
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
        final boolean ok; final String reason; final String id; final int httpCode;
        ReserveResult(boolean ok, String reason, String id, int httpCode) {
            this.ok = ok; this.reason = reason; this.id = id; this.httpCode = httpCode;
        }
    }

    /** Block{minecraft:oak_leaves}[...] -> minecraft:oak_leaves（精确比较）。 */
    static String canonicalBlock(String raw) {
        if (raw == null) return "";
        String text = raw.trim();
        int start = text.indexOf("minecraft:");
        if (start < 0) start = 0;
        int end = text.length();
        int bracket = text.indexOf('[', start);
        if (bracket > 0) end = bracket;
        int brace = text.indexOf('}', start);
        if (brace > 0 && brace < end) end = brace;
        return text.substring(start, end).trim();
    }

    /** 维度规范化：只接受 namespace:path 形态；空/未知一律返回 ""（调用方必须 fail closed）。 */
    static String normalizeDimension(String raw) {
        if (raw == null) return "";
        String text = raw.trim().toLowerCase(Locale.ROOT);
        if (text.isEmpty() || "unknown".equals(text)) return "";
        int idx = text.indexOf("minecraft:");
        if (idx >= 0) {
            String tail = text.substring(idx + "minecraft:".length());
            int cut = tail.indexOf('[');
            if (cut >= 0) tail = tail.substring(0, cut);
            tail = tail.trim();
            return tail.isEmpty() ? "" : "minecraft:" + tail;
        }
        return "";
    }

    private final Map<String, Session> byName = new LinkedHashMap<>();
    private final Map<String, Session> byRef = new LinkedHashMap<>();
    private final List<Reservation> reservations = new ArrayList<>();
    private final Set<String> seenNonces = new HashSet<>();
    private long seqCounter = 0;

    int rejected = 0, expired = 0, ambiguous = 0, unknown = 0, consumedCount = 0;
    int identityRejected = 0, dimensionRejected = 0;

    synchronized Session newSession(String name, String uuid, String ref, Object world) {
        Session previous = byName.get(name);
        if (previous != null) {
            previous.valid = false;
            byRef.remove(previous.ref);
        }
        seqCounter += 1;
        Session session = new Session(name, uuid, ref, seqCounter, world);
        byName.put(name, session);
        byRef.put(ref, session);
        return session;
    }

    synchronized Session currentSessionOf(String name) {
        Session session = byName.get(name);
        return session != null && session.valid ? session : null;
    }

    synchronized void invalidateAll() {
        for (Session session : byName.values()) session.valid = false;
        byRef.clear();
        reservations.clear();
        seenNonces.clear();
    }

    synchronized int activeReservations() {
        int count = 0;
        for (Reservation reservation : reservations) if (!reservation.consumed) count += 1;
        return count;
    }

    synchronized ReserveResult reserve(Map<String, String> fields, Function<Map<String, String>, String> blockAt) {
        String sessionRef = fields.getOrDefault("session_ref", "").trim();
        Session session = byRef.get(sessionRef);
        if (session == null || !session.valid) {
            rejected += 1;
            return new ReserveResult(false, "session_not_current", "", 409);
        }
        String actionId = fields.getOrDefault("action_id", "").trim();
        String nonce = fields.getOrDefault("nonce", "").trim();
        String dim = normalizeDimension(fields.getOrDefault("dimension", ""));
        String pos = fields.getOrDefault("pos", "").trim();
        String expected = canonicalBlock(fields.getOrDefault("expected_block", ""));
        if (actionId.isEmpty() || nonce.isEmpty() || pos.isEmpty() || expected.isEmpty() || dim.isEmpty()) {
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
            if (existing.consumed || !existing.actionId.equals(actionId)) continue;
            if (System.nanoTime() > existing.expiresAtNanos) continue;
            rejected += 1;
            return new ReserveResult(false, "duplicate_action", "", 409);
        }
        String block = blockAt.apply(fields);
        if (block == null) {
            rejected += 1;
            return new ReserveResult(false, "world_unavailable", "", 503);
        }
        if (!canonicalBlock(block).equals(expected)) {
            rejected += 1;
            return new ReserveResult(false, "target_mismatch", "", 409);
        }
        long ttl = 60000L;
        try {
            ttl = Long.parseLong(fields.getOrDefault("ttl_ms", "60000"));
        } catch (NumberFormatException ignored) {
            ttl = 60000L;
        }
        Reservation reservation = new Reservation(UUID.randomUUID().toString(), actionId, nonce, dim, pos, expected,
                session.ref, session.seq, session.uuid, Math.max(1000L, Math.min(ttl, 600000L)));
        reservations.add(reservation);
        return new ReserveResult(true, "ok", reservation.id, 200);
    }

    /**
     * 匹配：入参的 sessionUuid/dimension 必须是**事件现场实际观测值**。
     * 任何缺失/不一致都 fail closed（UNKNOWN），绝不用 Session 的预期值顶替事件观测值。
     */
    synchronized MatchResult match(Session session, String eventUuid, String eventDimension, String pos,
            String blockBeforeRaw) {
        String dim = normalizeDimension(eventDimension);
        if (session == null || !session.valid) {
            unknown += 1;
            return new MatchResult("UNKNOWN", "no_valid_session", null);
        }
        if (eventUuid == null || eventUuid.isEmpty() || "unknown".equals(eventUuid)) {
            unknown += 1;
            identityRejected += 1;
            return new MatchResult("UNKNOWN", "event_identity_unknown", null);
        }
        if (!session.uuid.equals(eventUuid)) {
            unknown += 1;
            identityRejected += 1;
            return new MatchResult("UNKNOWN", "event_identity_mismatch", null);
        }
        if (dim.isEmpty()) {
            unknown += 1;
            dimensionRejected += 1;
            return new MatchResult("UNKNOWN", "event_dimension_unknown", null);
        }
        String blockBefore = canonicalBlock(blockBeforeRaw);
        List<Reservation> candidates = new ArrayList<>();
        boolean samePosSeen = false;
        boolean stale = false;
        for (Reservation reservation : reservations) {
            if (reservation.consumed || !reservation.pos.equals(pos)) continue;
            samePosSeen = true;
            if (System.nanoTime() > reservation.expiresAtNanos) {
                expired += 1;
                continue;
            }
            if (!reservation.sessionRef.equals(session.ref)) {
                stale = true;
                continue;
            }
            if (!reservation.playerUuid.equals(eventUuid)) {
                identityRejected += 1;
                continue;
            }
            if (!reservation.dim.equals(dim)) {
                dimensionRejected += 1;
                continue;
            }
            if (!reservation.expectedId.equals(blockBefore)) continue;
            candidates.add(reservation);
        }
        if (candidates.size() > 1) {
            ambiguous += 1;
            return new MatchResult("AMBIGUOUS", "multiple_candidates", null);
        }
        if (candidates.isEmpty()) {
            unknown += 1;
            if (stale) return new MatchResult("UNKNOWN", "stale_session", null);
            if (samePosSeen) return new MatchResult("UNKNOWN", "target_or_identity_or_dimension_mismatch", null);
            return new MatchResult("UNREGISTERED_BREAK", "no_reservation", null);
        }
        Reservation reservation = candidates.get(0);
        reservation.consumed = true;
        consumedCount += 1;
        return new MatchResult("SELF_CONFIRMED", "unique_match", reservation);
    }
}
