package catoo.attrib;

import java.util.*;
import java.util.concurrent.atomic.*;

/** Exact supplied call-scope facts, not a game hook or real-server proof. */
public final class CollectionLedgerTest {
    static int passed;
    static void check(boolean value, String label) {
        if (!value) throw new AssertionError(label);
        passed++; System.out.println("  OK   " + label);
    }
    static String uuid(long value) { return new UUID(0, value).toString(); }
    static final class Rig {
        final AtomicLong now = new AtomicLong(1);
        final CollectionLedger ledger = new CollectionLedger(Thread.currentThread(), now::get);
        final Object world = new Object(), player = new Object();
        final CollectionLedger.Identity id = new CollectionLedger.Identity("session-1", 1, uuid(1),
                "minecraft:overworld", "1,64,2", "minecraft:stone");
        long seq = 100;
        CollectionLedger.BreakProof proof(CollectionLedger.Identity identity) {
            return new CollectionLedger.BreakProof(uuid(++seq), "reservation-" + seq, "dig-" + seq, identity);
        }
        CollectionLedger.Drop drop() { return new CollectionLedger.Drop(uuid(++seq), (int)seq, "minecraft:cobblestone", 1); }
        CollectionLedger.BreakResult add(CollectionLedger.Drop drop) {
            var frame = ledger.beginBreak(world, player, id);
            ledger.bindBreak(frame, world, player, proof(id));
            boolean entered = ledger.beginDrop(world, id.pos());
            ledger.spawned(world, drop, true); ledger.endDrop(entered);
            return ledger.finishBreak(frame, true);
        }
    }
    public static void main(String[] args) throws Exception {
        Rig rig = new Rig();
        var frame = rig.ledger.beginBreak(rig.world, rig.player, rig.id);
        var drop = rig.drop();
        check(!rig.ledger.spawned(rig.world, drop, true), "spawn outside exact drop scope is not evidence");
        check(!rig.ledger.beginDrop(new Object(), rig.id.pos()), "same coordinates in another world cannot enter scope");
        boolean entered = rig.ledger.beginDrop(rig.world, rig.id.pos());
        check(entered && !rig.ledger.spawned(rig.world, drop, false), "rejected vanilla spawn supplies no drop");
        check(rig.ledger.spawned(rig.world, drop, true), "exact accepted spawn recorded once");
        rig.ledger.endDrop(entered);
        var proof = rig.proof(rig.id);
        check(rig.ledger.bindBreak(frame, rig.world, rig.player, proof), "AFTER certificate can bind after drop creation");
        var result = rig.ledger.finishBreak(frame, true);
        check(result.confirmed() && result.proof().equals(proof) && result.drops().equals(List.of(drop)), "separate exact break and drop identities retained");
        check(rig.ledger.finishBreak(frame, true) == null, "repeated frame finish cannot replay a certificate");

        Rig missing = new Rig();
        var missingFrame = missing.ledger.beginBreak(missing.world, missing.player, missing.id);
        boolean scope = missing.ledger.beginDrop(missing.world, missing.id.pos());
        missing.ledger.spawned(missing.world, missing.drop(), true); missing.ledger.endDrop(scope);
        check(!missing.ledger.finishBreak(missingFrame, true).confirmed(), "drop without confirmed break is unknown");
        Rig wrong = new Rig();
        var wrongFrame = wrong.ledger.beginBreak(wrong.world, wrong.player, wrong.id);
        check(!wrong.ledger.bindBreak(wrongFrame, wrong.world, new Object(), wrong.proof(wrong.id)), "same textual identity does not replace actual player reference");
        check(!wrong.ledger.finishBreak(wrongFrame, true).confirmed(), "bad binding never becomes collection proof");
        Rig duplicate = new Rig();
        var repeated = duplicate.drop();
        check(duplicate.add(repeated).confirmed(), "first entity registered");
        check(!duplicate.add(repeated).confirmed(), "entity UUID reused across breaks is not a new contribution");
        Rig nested = new Rig();
        var outer = nested.ledger.beginBreak(nested.world, nested.player, nested.id);
        check(nested.ledger.beginBreak(nested.world, nested.player, nested.id) == null, "nested break does not share the parent frame");
        check(!nested.ledger.beginDrop(nested.world, nested.id.pos()), "nested origin cannot emit parent drops");
        nested.ledger.finishBreak(null, true);
        check(!nested.ledger.finishBreak(outer, true).confirmed(), "ambiguous nested call stays unknown after unwind");
        Rig thread = new Rig();
        AtomicReference<CollectionLedger.Frame> foreign = new AtomicReference<>();
        Thread outsider = new Thread(() -> foreign.set(thread.ledger.beginBreak(thread.world, thread.player, thread.id)));
        outsider.start(); outsider.join();
        check(foreign.get() == null, "off-server-thread call rejected");

        check(rig.ledger.reservePickup(rig.id, "pickup-wrong", "nonce_for_pickup_bad_1", drop.uuid(),
                "another-break") == null, "same actor and item cannot substitute a different break action");
        var booking = rig.ledger.reservePickup(rig.id, "pickup-1", "nonce_for_pickup_0001", drop.uuid(), proof.actionId());
        check(booking != null, "pickup reservation binds exact drop and actor/session");
        check(rig.ledger.reservePickup(rig.id, "pickup-2", "nonce_for_pickup_0001", drop.uuid()) == null, "nonce replay cannot issue a second reservation");
        var pickup = rig.ledger.pickedUp(rig.id, drop.uuid(), drop.item(), 1, 0, 7, 8);
        check(pickup.confirmed() && pickup.parent().equals(proof) && pickup.booking().equals(booking), "pickup and existing inventory net increment independently bound");
        check(!rig.ledger.pickedUp(rig.id, drop.uuid(), drop.item(), 1, 0, 8, 9).confirmed(), "duplicate pickup cannot replay success");
        Rig changed = new Rig(); var changedDrop = changed.drop(); changed.add(changedDrop);
        var oldSession = new CollectionLedger.Identity("session-1", 2, changed.id.playerUuid(), changed.id.dimension(), changed.id.pos(), changed.id.block());
        check(changed.ledger.reservePickup(oldSession, "pickup", "nonce_old_session_001", changedDrop.uuid()) == null, "new session sequence cannot consume old drop lineage");
        changed.ledger.reservePickup(changed.id, "pickup", "nonce_valid_actor_001", changedDrop.uuid());
        var otherActor = new CollectionLedger.Identity("session-other", 3, uuid(2), changed.id.dimension(), changed.id.pos(), changed.id.block());
        check(!changed.ledger.pickedUp(otherActor, changedDrop.uuid(), changedDrop.item(), 1, 0, 0, 1).confirmed(), "other player pickup is not self collection");
        Rig inventory = new Rig(); var invDrop = inventory.drop(); inventory.add(invDrop);
        inventory.ledger.reservePickup(inventory.id, "pickup", "nonce_inventory_00001", invDrop.uuid());
        check(!inventory.ledger.pickedUp(inventory.id, invDrop.uuid(), invDrop.item(), 1, 0, 5, 5).confirmed(), "entity disappearance without inventory gain is unknown");
        Rig cancelled = new Rig(); var cancelDrop = cancelled.drop(); var cancelProof = cancelled.add(cancelDrop).proof();
        check(cancelled.ledger.reservePickup(cancelled.id, "cancelled-pickup", "nonce_cancel_pickup_001", cancelDrop.uuid(),
                cancelProof.actionId(), candidate -> false) == null, "cancelled HTTP publication leaves no pickup booking");
        check(cancelled.ledger.reservePickup(cancelled.id, "retry-pickup", "nonce_cancel_pickup_001", cancelDrop.uuid()) == null,
                "cancelled pickup nonce cannot be replayed");
        check(cancelled.ledger.reservePickup(cancelled.id, "retry-pickup", "nonce_new_pickup_00001", cancelDrop.uuid()) != null,
                "fresh nonce can register after unpublished cancellation without a stale booking");
        Rig merged = new Rig(); var mergeDrop = merged.drop(); merged.add(mergeDrop);
        merged.ledger.reservePickup(merged.id, "pickup", "nonce_merge_000000001", mergeDrop.uuid());
        merged.ledger.merged(mergeDrop.uuid(), uuid(999));
        var mergeResult = merged.ledger.pickedUp(merged.id, mergeDrop.uuid(), mergeDrop.item(), 1, 0, 0, 1);
        check(!mergeResult.confirmed() && mergeResult.reason().equals("entity_merged"), "merged untracked contribution remains unknown even when counts look plausible");
        Rig expired = new Rig(); var expiredDrop = expired.drop(); expired.add(expiredDrop);
        expired.ledger.reservePickup(expired.id, "pickup", "nonce_expire_00000001", expiredDrop.uuid());
        expired.now.addAndGet(CollectionLedger.PICKUP_TTL_NANOS + 1);
        check(!expired.ledger.pickedUp(expired.id, expiredDrop.uuid(), expiredDrop.item(), 1, 0, 0, 1).confirmed(), "late pickup cannot consume expired reservation");
        Rig disconnected = new Rig(); var disconnectedDrop = disconnected.drop(); disconnected.add(disconnectedDrop);
        disconnected.ledger.invalidateSession(disconnected.id.sessionRef(), disconnected.id.sessionSeq());
        check(disconnected.ledger.reservePickup(disconnected.id, "pickup", "nonce_disconnect_001", disconnectedDrop.uuid()) == null, "disconnect closes old collection lineage");
        disconnected.ledger.close();
        check(disconnected.ledger.beginBreak(disconnected.world, disconnected.player, disconnected.id) == null, "stopped runtime cannot accept new evidence");

        Rig bounded = new Rig();
        var boundedFrame = bounded.ledger.beginBreak(bounded.world, bounded.player, bounded.id);
        bounded.ledger.bindBreak(boundedFrame, bounded.world, bounded.player, bounded.proof(bounded.id));
        boolean boundedScope = bounded.ledger.beginDrop(bounded.world, bounded.id.pos());
        for (int i = 0; i <= CollectionLedger.MAX_DROPS_PER_BREAK; i++) bounded.ledger.spawned(bounded.world, bounded.drop(), true);
        bounded.ledger.endDrop(boundedScope);
        check(!bounded.ledger.finishBreak(boundedFrame, true).confirmed(), "per-break drop overflow is unknown, not truncated success");
        Rig capacity = new Rig();
        for (int i = 0; i < CollectionLedger.MAX_ENTITIES; i++) {
            if (!capacity.add(capacity.drop()).confirmed()) throw new AssertionError("capacity setup " + i);
        }
        check(!capacity.add(capacity.drop()).confirmed() && capacity.ledger.retainedEntities() == CollectionLedger.MAX_ENTITIES,
                "entity capacity refuses without evicting active provenance");
        capacity.now.addAndGet(CollectionLedger.DROP_TTL_NANOS + 1);
        check(capacity.ledger.retainedEntities() == 0 && capacity.add(capacity.drop()).confirmed(), "expired lineage releases bounded storage without extending deadlines");
        System.out.println("[collection-ledger-test] " + passed + "/" + passed + " checks passed (no live server)");
    }
}
