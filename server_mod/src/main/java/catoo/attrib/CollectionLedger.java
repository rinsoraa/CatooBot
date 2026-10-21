package catoo.attrib;

import java.util.*;
import java.util.function.LongSupplier;

/** R3 evidence only. Callers supply exact server call-scope facts; no world mutation or authority. */
final class CollectionLedger {
    static final int MAX_DROPS_PER_BREAK = 16, MAX_ENTITIES = 4096, MAX_NONCES = 8192;
    static final long DROP_TTL_NANOS = 120_000_000_000L, PICKUP_TTL_NANOS = 30_000_000_000L;

    record Identity(String sessionRef, long sessionSeq, String playerUuid, String dimension, String pos, String block) {}
    record BreakProof(String eventId, String reservationId, String actionId, Identity identity) {}
    record Drop(String uuid, int entityId, String item, int count) {}
    record BreakResult(BreakProof proof, List<Drop> drops, String reason) {
        boolean confirmed() { return proof != null && reason.isEmpty() && !drops.isEmpty(); }
    }
    record PickupBooking(String id, String actionId, String nonce, Identity identity, String dropUuid,
            String breakEventId, String breakReservationId, String breakActionId, long createdAt) {}
    record PickupResult(PickupBooking booking, BreakProof parent, Drop drop, int collected, String reason) {
        boolean confirmed() { return booking != null && reason.isEmpty() && collected > 0; }
    }

    static final class Frame {
        final Object world, player;
        final Identity identity;
        final LinkedHashMap<String, Drop> drops = new LinkedHashMap<>();
        BreakProof proof;
        String invalid = "";
        int nested, dropDepth;
        boolean finished;
        Frame(Object world, Object player, Identity identity) {
            this.world = world; this.player = player; this.identity = identity;
        }
        void invalidate(String reason) { if (invalid.isEmpty()) invalid = reason; }
    }
    static final class Tracked {
        final BreakProof parent;
        final Drop drop;
        final long createdAt;
        String invalid = "";
        Tracked(BreakProof parent, Drop drop, long createdAt) {
            this.parent = parent; this.drop = drop; this.createdAt = createdAt;
        }
    }

    private final Thread owner;
    private final LongSupplier clock;
    private final LinkedHashMap<String, Tracked> entities = new LinkedHashMap<>();
    private final Map<String, PickupBooking> pickups = new HashMap<>();
    private final Map<String, Identity> nonces = new HashMap<>();
    private Frame active;
    private boolean closed;

    CollectionLedger(Thread owner, LongSupplier clock) {
        this.owner = Objects.requireNonNull(owner); this.clock = Objects.requireNonNull(clock);
    }
    private boolean available() { return !closed && Thread.currentThread() == owner; }
    private static boolean token(String value) {
        return value != null && !value.isEmpty() && value.length() <= 128
                && value.chars().allMatch(c -> c >= 33 && c <= 126);
    }
    private static boolean uuid(String value) {
        if (value == null) return false;
        try { return UUID.fromString(value).toString().equals(value); }
        catch (IllegalArgumentException error) { return false; }
    }
    private static boolean identity(Identity id) {
        return id != null && token(id.sessionRef()) && id.sessionSeq() > 0 && uuid(id.playerUuid())
                && Booking.SUPPORTED_DIMENSIONS.contains(id.dimension()) && token(id.pos())
                && token(id.block()) && id.block().startsWith("minecraft:");
    }
    private void prune() {
        long now = clock.getAsLong();
        entities.values().removeIf(item -> now - item.createdAt > DROP_TTL_NANOS);
        pickups.values().removeIf(item -> now - item.createdAt() > PICKUP_TTL_NANOS
                || !entities.containsKey(item.dropUuid()));
    }

    synchronized Frame currentFrame() { return available() ? active : null; }
    synchronized Frame beginBreak(Object world, Object player, Identity id) {
        if (!available()) return null;
        if (active != null) {
            active.invalidate("nested_break");
            active.nested = Math.min(1024, active.nested + 1);
            return null;
        }
        if (world == null || player == null || !identity(id)) return null;
        active = new Frame(world, player, id);
        return active;
    }
    synchronized boolean bindBreak(Frame frame, Object world, Object player, BreakProof proof) {
        if (!available() || frame == null || frame != active || frame.finished) return false;
        if (frame.world != world || frame.player != player || proof == null
                || !frame.identity.equals(proof.identity()) || !uuid(proof.eventId())
                || !token(proof.reservationId()) || !token(proof.actionId()) || frame.proof != null) {
            frame.invalidate("break_binding_mismatch"); return false;
        }
        frame.proof = proof; return true;
    }
    synchronized boolean beginDrop(Object world, String pos) {
        if (!available() || active == null || active.finished || active.nested != 0
                || active.world != world || !active.identity.pos().equals(pos)) return false;
        if (active.dropDepth != 0) { active.invalidate("nested_drop"); return false; }
        active.dropDepth = 1; return true;
    }
    synchronized void endDrop(boolean entered) {
        if (available() && entered && active != null) active.dropDepth = 0;
    }
    synchronized boolean spawned(Object world, Drop drop, boolean accepted) {
        if (!available() || active == null || active.finished || active.nested != 0
                || active.dropDepth != 1 || active.world != world || !accepted) return false;
        if (drop == null || !uuid(drop.uuid()) || drop.entityId() < 0 || !token(drop.item())
                || !drop.item().startsWith("minecraft:") || drop.count() < 1 || drop.count() > 64
                || active.drops.containsKey(drop.uuid()) || active.drops.size() >= MAX_DROPS_PER_BREAK) {
            active.invalidate("drop_invalid_ambiguous_or_capacity"); return false;
        }
        active.drops.put(drop.uuid(), drop); return true;
    }
    synchronized BreakResult finishBreak(Frame frame, boolean broken) {
        if (!available()) return null;
        if (frame == null) {
            if (active != null && active.nested > 0) active.nested--;
            return null;
        }
        if (frame != active || frame.finished) return null;
        try {
            frame.finished = true;
            if (!broken) frame.invalidate("break_not_completed");
            if (frame.dropDepth != 0 || frame.nested != 0) frame.invalidate("scope_not_closed");
            if (frame.proof == null) frame.invalidate("break_proof_missing");
            if (frame.drops.isEmpty()) frame.invalidate("drop_missing");
            prune();
            if (entities.size() + frame.drops.size() > MAX_ENTITIES) frame.invalidate("entity_capacity");
            if (frame.drops.keySet().stream().anyMatch(entities::containsKey)) frame.invalidate("entity_reused");
            if (!frame.invalid.isEmpty()) return new BreakResult(frame.proof, List.of(), frame.invalid);
            long now = clock.getAsLong();
            for (Drop drop : frame.drops.values()) entities.put(drop.uuid(), new Tracked(frame.proof, drop, now));
            return new BreakResult(frame.proof, List.copyOf(frame.drops.values()), "");
        } finally { active = null; }
    }

    synchronized PickupBooking reservePickup(Identity actor, String actionId, String nonce, String dropUuid) {
        return reservePickup(actor, actionId, nonce, dropUuid, "");
    }
    synchronized PickupBooking reservePickup(Identity actor, String actionId, String nonce, String dropUuid,
            String expectedBreakAction) {
        return reservePickup(actor, actionId, nonce, dropUuid, expectedBreakAction, candidate -> true);
    }
    synchronized PickupBooking reservePickup(Identity actor, String actionId, String nonce, String dropUuid,
            String expectedBreakAction, java.util.function.Predicate<PickupBooking> publish) {
        if (!available() || !identity(actor) || !token(actionId) || !token(nonce) || nonce.length() < 16) return null;
        prune();
        Tracked target = entities.get(dropUuid);
        if (target == null || !target.invalid.isEmpty() || !sameActor(actor, target.parent.identity())
                || pickups.containsKey(dropUuid) || nonces.containsKey(nonce) || nonces.size() >= MAX_NONCES
                || expectedBreakAction == null || (!expectedBreakAction.isEmpty()
                    && !target.parent.actionId().equals(expectedBreakAction))) return null;
        PickupBooking booking = new PickupBooking(UUID.randomUUID().toString(), actionId, nonce,
                actor, dropUuid, target.parent.eventId(), target.parent.reservationId(),
                target.parent.actionId(), clock.getAsLong());
        nonces.put(nonce, actor);
        if (publish == null || !publish.test(booking)) return null;
        pickups.put(dropUuid, booking); return booking;
    }
    private static boolean sameActor(Identity a, Identity b) {
        return a.sessionRef().equals(b.sessionRef()) && a.sessionSeq() == b.sessionSeq()
                && a.playerUuid().equals(b.playerUuid()) && a.dimension().equals(b.dimension());
    }
    synchronized PickupResult pickedUp(Identity actor, String dropUuid, String item, int before, int after,
            int inventoryBefore, int inventoryAfter) {
        if (!available()) return null;
        prune();
        Tracked target = entities.get(dropUuid);
        if (target == null) return null;
        PickupBooking booking = pickups.remove(dropUuid);
        String reason = target.invalid;
        if (reason.isEmpty() && (!identity(actor) || !sameActor(actor, target.parent.identity()))) reason = "external_or_old_session_pickup";
        else if (reason.isEmpty() && (booking == null || !sameActor(actor, booking.identity()))) reason = "pickup_not_reserved";
        else if (reason.isEmpty() && (!target.drop.item().equals(item) || before != target.drop.count() || after != 0
                || inventoryBefore < 0 || inventoryAfter - (long) inventoryBefore != before)) reason = "pickup_or_inventory_mismatch";
        if (!reason.isEmpty()) {
            target.invalid = reason;
            return new PickupResult(null, target.parent, target.drop, 0, reason);
        }
        target.invalid = "already_collected";
        return new PickupResult(booking, target.parent, target.drop, before, "");
    }
    synchronized void merged(String firstUuid, String secondUuid) {
        if (!available()) return;
        for (String uuid : new String[]{firstUuid, secondUuid}) {
            Tracked target = entities.get(uuid);
            if (target != null) { target.invalid = "entity_merged"; pickups.remove(uuid); }
        }
    }
    synchronized void invalidateSession(String ref, long seq) {
        if (!available()) return;
        for (Tracked target : entities.values()) {
            Identity id = target.parent.identity();
            if (id.sessionRef().equals(ref) && id.sessionSeq() == seq) {
                target.invalid = "session_closed"; pickups.remove(target.drop.uuid());
            }
        }
        if (active != null && active.identity.sessionRef().equals(ref) && active.identity.sessionSeq() == seq)
            active.invalidate("session_closed");
        nonces.values().removeIf(id -> id.sessionRef().equals(ref) && id.sessionSeq() == seq);
    }
    synchronized int retainedEntities() { if (!available()) return 0; prune(); return entities.size(); }
    synchronized boolean isOpen() { return !closed; }
    synchronized void close() {
        closed = true; active = null; entities.clear(); pickups.clear(); nonces.clear();
    }
}
