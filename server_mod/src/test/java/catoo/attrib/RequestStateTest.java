package catoo.attrib;

import java.util.Map;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;

/** No Fabric dependency. Deterministic lifecycle/Booking publication tests used by CI. */
public final class RequestStateTest {
    static int passed;

    static void check(boolean value, String label) {
        if (!value) throw new AssertionError(label);
        passed++;
        System.out.println("  OK   " + label);
    }

    static Map<String, String> fields(String action, String nonce) {
        return Map.of("session_ref", "ref", "action_id", action, "nonce", nonce,
                "dimension", "minecraft:overworld", "pos", "1,2,3", "expected_block", "minecraft:oak_log");
    }

    static Booking book() {
        Booking book = new Booking();
        book.newSession("probe", "uuid", "ref", new Object());
        return book;
    }

    static RequestState<Booking.ReserveResult> request(AtomicLong clock) {
        return new RequestState<>(100, reason -> new Booking.ReserveResult(false, reason, "", 503), clock::get);
    }

    static void await(CountDownLatch latch) {
        try {
            if (!latch.await(2, TimeUnit.SECONDS)) throw new AssertionError("latch timed out");
        } catch (InterruptedException error) {
            throw new AssertionError(error);
        }
    }

    public static void main(String[] args) throws Exception {
        AtomicLong clock = new AtomicLong();
        RequestState<Booking.ReserveResult> cancelled = request(clock);
        var timeout = cancelled.cancel("reserve_timeout");
        check(!cancelled.complete(new Booking.ReserveResult(true, "ok", "never", 200)), "cancel wins over later commit");
        check(cancelled.outcome() == timeout, "cancel outcome identity is immutable");

        RequestState<Booking.ReserveResult> committed = request(clock);
        var success = new Booking.ReserveResult(true, "ok", "r", 200);
        check(committed.complete(success), "commit can win while open");
        check(committed.cancel("reserve_timeout") == success, "timeout cannot overwrite successful commit");
        check(committed.cancel("reserve_interrupted") == success, "interruption cannot overwrite successful commit");
        clock.set(100_000_000L);
        check(committed.outcome() == success, "elapsed deadline cannot revoke published success");
        clock.set(0);
        RequestState<Booking.ReserveResult> deadline = request(clock);
        clock.set(100_000_000L);
        check(!deadline.complete(success) && "reserve_timeout".equals(deadline.outcome().reason),
                "commit at deadline fails closed without requiring HTTP waiter to wake");

        clock.set(0);
        Booking normal = book();
        RequestState<Booking.ReserveResult> normalRequest = request(clock);
        var reserved = normal.reserve(fields("normal", "nonce-normal-0123456789"), f -> "minecraft:oak_log", normalRequest::complete);
        check(reserved.ok && normalRequest.outcome() == reserved && normal.activeReservations() == 1,
                "successful outcome and published reservation are the same result");
        var session = normal.currentSessionOf("probe");
        check("SELF_CONFIRMED".equals(normal.match(session, "uuid", "minecraft:overworld", "1,2,3", "minecraft:oak_log").state),
                "normal published request remains consumable");
        check(!"SELF_CONFIRMED".equals(normal.match(session, "uuid", "minecraft:overworld", "1,2,3", "minecraft:oak_log").state),
                "published request can be consumed only once");

        Booking beforeCommit = book();
        RequestState<Booking.ReserveResult> beforeRequest = request(clock);
        var absent = beforeCommit.reserve(fields("cancel", "nonce-cancel-0123456789"), f -> {
            beforeRequest.cancel("reserve_timeout");
            return "minecraft:oak_log";
        }, beforeRequest::complete);
        check(!absent.ok && beforeCommit.activeReservations() == 0, "cancellation after read but before commit leaves no reservation");
        check(!"SELF_CONFIRMED".equals(beforeCommit.match(beforeCommit.currentSessionOf("probe"), "uuid",
                "minecraft:overworld", "1,2,3", "minecraft:oak_log").state), "cancelled candidate cannot prove an event");
        check("nonce_replay".equals(beforeCommit.reserve(fields("cancel", "nonce-cancel-0123456789"), f -> "minecraft:oak_log").reason),
                "cancelled attempt still burns nonce; replay protection not relaxed");
        check(beforeCommit.reserve(fields("cancel", "nonce-new-0123456789012"), f -> "minecraft:oak_log").ok,
                "new nonce can retry action after cancelled attempt with no valid reservation");

        Booking stale = book();
        var staleResult = stale.reserve(fields("stale", "nonce-stale-0123456789"), f -> {
            stale.newSession("probe", "uuid", "replacement", new Object());
            return "minecraft:oak_log";
        });
        check(!staleResult.ok && "session_not_current".equals(staleResult.reason) && stale.activeReservations() == 0,
                "session replacement during preparation cannot publish old candidate");
        Booking invalidated = book();
        check(!invalidated.reserve(fields("invalid", "nonce-invalid-0123456789"), f -> {
            invalidated.invalidateAll();
            return "minecraft:oak_log";
        }).ok && invalidated.activeReservations() == 0, "session invalidation during read fails closed");

        Booking connection = book();
        Object oldPlayer = new Object();
        Object newPlayer = new Object();
        connection.attachPlayer("ref", oldPlayer);
        connection.newSession("probe", "uuid", "next-ref", new Object());
        connection.attachPlayer("next-ref", newPlayer);
        check(!connection.disconnect("probe", "uuid", oldPlayer), "old connection disconnect cannot revoke replacement");
        check(connection.currentSessionOf("probe") != null, "replacement session remains current");
        check(connection.disconnect("probe", "uuid", newPlayer) && connection.currentSessionOf("probe") == null,
                "current connection disconnect invalidates its session");
        Booking dropped = book();
        dropped.attachPlayer("ref", oldPlayer);
        var droppedResult = dropped.reserve(fields("dropped", "nonce-dropped-0123456789"), f -> {
            dropped.disconnect("probe", "uuid", oldPlayer);
            return "minecraft:oak_log";
        });
        check(!droppedResult.ok && dropped.activeReservations() == 0, "disconnect during preparation cannot publish reservation");

        ExecutorService pool = Executors.newFixedThreadPool(2);
        CountDownLatch release = new CountDownLatch(1);
        try {
            Booking slow = book();
            RequestState<Booking.ReserveResult> slowRequest = request(clock);
            CountDownLatch entered = new CountDownLatch(1);
            var worker = pool.submit(() -> slow.reserve(fields("slow", "nonce-slow-0123456789"), f -> {
                entered.countDown(); await(release); return "minecraft:oak_log";
            }, slowRequest::complete));
            await(entered);
            check(pool.submit(slow::activeReservations).get(1, TimeUnit.SECONDS) == 0,
                    "slow world read holds no Booking monitor");
            clock.set(100_000_000L);
            var response = slowRequest.cancel("reserve_timeout");
            check(response.httpCode == 503, "HTTP cancellation completes while world read remains blocked");
            release.countDown();
            check(!worker.get(2, TimeUnit.SECONDS).ok && slow.activeReservations() == 0,
                    "released in-flight read cannot create a late valid reservation");

            clock.set(0);
            Booking competing = book();
            CountDownLatch bothReading = new CountDownLatch(2);
            CountDownLatch commit = new CountDownLatch(1);
            var first = pool.submit(() -> competing.reserve(fields("same", "nonce-first-0123456789"), f -> {
                bothReading.countDown(); await(commit); return "minecraft:oak_log";
            }));
            var second = pool.submit(() -> competing.reserve(fields("same", "nonce-second-0123456789"), f -> {
                bothReading.countDown(); await(commit); return "minecraft:oak_log";
            }));
            await(bothReading);
            commit.countDown();
            var a = first.get(2, TimeUnit.SECONDS); var b = second.get(2, TimeUnit.SECONDS);
            check(a.ok != b.ok && competing.activeReservations() == 1,
                    "concurrent preparation rechecks action uniqueness at commit");
            check("duplicate_action".equals(a.ok ? b.reason : a.reason), "losing candidate gets explicit duplicate_action");
        } finally {
            release.countDown(); pool.shutdownNow();
        }
        System.out.println("[request-state-test] " + passed + "/" + passed + " checks passed");
    }
}
