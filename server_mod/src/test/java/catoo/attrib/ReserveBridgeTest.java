package catoo.attrib;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.Map;

/** 缺口 A：主线程调度、有界等待与「超时后绝不产生迟到预约」的竞态证明。 */
public final class ReserveBridgeTest {
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

    static void await(CountDownLatch latch) {
        try {
            if (!latch.await(3, TimeUnit.SECONDS)) throw new AssertionError("latch timeout");
        } catch (InterruptedException error) {
            throw new AssertionError(error);
        }
    }

    static void requestRace(ExecutorService pool, boolean commitFirst, boolean interrupt) throws Exception {
        CatooAttributionMod.BOOK.invalidateAll();
        CatooAttributionMod.BOOK.newSession("probe", "probe-uuid", "race-ref", new Object());
        Map<String, String> fields = Map.of("session_ref", "race-ref", "action_id", "race-action",
                "nonce", "race-nonce-0123456789", "dimension", "minecraft:overworld",
                "pos", "1,2,3", "expected_block", "minecraft:oak_log");
        CountDownLatch reached = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        AtomicReference<Thread> caller = new AtomicReference<>();
        AtomicBoolean interruptedFlag = new AtomicBoolean();
        ExecutorService http = Executors.newSingleThreadExecutor();
        try {
            var response = http.submit(() -> {
                caller.set(Thread.currentThread());
                var answer = CatooAttributionMod.runOnServerThread(request -> {
                    if (commitFirst) {
                        var committed = CatooAttributionMod.reserveForRequest(fields, f -> "minecraft:oak_log", request);
                        reached.countDown(); await(release); return committed;
                    }
                    return CatooAttributionMod.reserveForRequest(fields, f -> {
                        reached.countDown(); await(release); return "minecraft:oak_log";
                    }, request);
                }, interrupt ? 2000 : 150);
                interruptedFlag.set(Thread.currentThread().isInterrupted());
                return answer;
            });
            await(reached);
            if (interrupt) caller.get().interrupt();
            var answer = response.get(2, TimeUnit.SECONDS);
            release.countDown();
            int active = pool.submit(() -> CatooAttributionMod.BOOK.activeReservations()).get(2, TimeUnit.SECONDS);
            check(answer.code == (commitFirst ? 200 : 503) && active == (commitFirst ? 1 : 0),
                    "reply agrees with commit winner (commitFirst=" + commitFirst + ", interrupt=" + interrupt + ")");
            if (interrupt) check(interruptedFlag.get(), "interrupted HTTP caller retains interrupt status");
            if (!commitFirst) check("reserve_interrupted".equals(answer.reason), "cancel winner preserves interruption reason");
        } finally {
            release.countDown();
            pool.submit(() -> {}).get(2, TimeUnit.SECONDS);
            http.shutdownNow();
            CatooAttributionMod.BOOK.invalidateAll();
        }
    }

    public static void main(String[] args) throws Exception {
        ExecutorService pool = Executors.newSingleThreadExecutor();
        CatooAttributionMod.SERVER_EXECUTOR = pool;
        CatooAttributionMod.SERVER_THREAD = pool.submit(Thread::currentThread).get(2, TimeUnit.SECONDS);
        CatooAttributionMod.REQUESTS.open(null);

        CatooAttributionMod.Reply fast = CatooAttributionMod.runOnServerThread(
                expired -> CatooAttributionMod.refuse(200, "ok_fast"), 1000);
        check(fast.code == 200 && "ok_fast".equals(fast.reason), "fast work returns its own reply");

        CatooAttributionMod.Reply thrown = CatooAttributionMod.runOnServerThread(expired -> {
            throw new IllegalStateException("boom");
        }, 1000);
        check(thrown.code == 500 && "work_error".equals(thrown.reason), "work exception is fail-closed");

        // 让执行器先被一个慢任务占住，bridge 等待会超时；随后队列中的任务必须放弃创建。
        final CountDownLatch release = new CountDownLatch(1);
        final AtomicInteger created = new AtomicInteger();
        pool.execute(() -> {
            try {
                release.await(2000, TimeUnit.MILLISECONDS);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        });
        CatooAttributionMod.Reply timeoutReply = CatooAttributionMod.runOnServerThread(expired -> {
            if (!expired.isOpen()) return expired.outcome();
            created.incrementAndGet();
            return CatooAttributionMod.refuse(200, "ok");
        }, 120);
        check(timeoutReply.code == 503 && "reserve_timeout".equals(timeoutReply.reason),
                "bounded wait returns reserve_timeout instead of blocking forever");
        release.countDown();
        Thread.sleep(600);
        check(created.get() == 0, "late queued task must NOT create a reservation after timeout");

        // Task 29 regression: the request has already entered the real Booking read.
        CatooAttributionMod.BOOK.invalidateAll();
        CatooAttributionMod.BOOK.newSession("probe", "probe-uuid", "probe-session", new Object());
        CountDownLatch reading = new CountDownLatch(1);
        CountDownLatch releaseRead = new CountDownLatch(1);
        ExecutorService http = Executors.newSingleThreadExecutor();
        try {
            var response = http.submit(() -> CatooAttributionMod.runOnServerThread(expired ->
                    CatooAttributionMod.reserveForRequest(Map.of(
                            "session_ref", "probe-session", "action_id", "probe-action",
                            "nonce", "probe-nonce-0123456789", "dimension", "minecraft:overworld",
                            "pos", "1,2,3", "expected_block", "minecraft:oak_log"), f -> {
                        reading.countDown();
                        try {
                            if (!releaseRead.await(3, TimeUnit.SECONDS)) throw new IllegalStateException("read stuck");
                        } catch (InterruptedException error) {
                            throw new IllegalStateException(error);
                        }
                        return "minecraft:oak_log";
                    }, expired), 150));
            if (!reading.await(2, TimeUnit.SECONDS)) throw new AssertionError("read did not start");
            CatooAttributionMod.Reply reply = response.get(2, TimeUnit.SECONDS);
            releaseRead.countDown();
            int active = pool.submit(() -> CatooAttributionMod.BOOK.activeReservations()).get(2, TimeUnit.SECONDS);
            check(reply.code == 503 && active == 0,
                    "in-flight read timeout must leave ZERO valid reservations (actual=" + active + ")");
        } finally {
            releaseRead.countDown();
            http.shutdownNow();
            CatooAttributionMod.BOOK.invalidateAll();
        }

        requestRace(pool, true, false);
        requestRace(pool, false, true);
        requestRace(pool, true, true);

        Thread actualServer = CatooAttributionMod.SERVER_THREAD;
        ExecutorService sameName = Executors.newSingleThreadExecutor(r -> new Thread(r, actualServer.getName()));
        try {
            var result = sameName.submit(() -> CatooAttributionMod.runOnServerThread(request -> {
                return CatooAttributionMod.refuse(Thread.currentThread() == actualServer ? 200 : 500, "identity");
            }, 1000)).get(2, TimeUnit.SECONDS);
            check(result.code == 200, "same-name foreign thread is scheduled, never mistaken for server thread");
        } finally { sameName.shutdownNow(); }

        CatooAttributionMod.SERVER_EXECUTOR = command -> { throw new java.util.concurrent.RejectedExecutionException(); };
        var rejected = CatooAttributionMod.runOnServerThread(request -> CatooAttributionMod.refuse(200, "never"), 1000);
        check(rejected.code == 503 && "server_thread_rejected".equals(rejected.reason), "rejected dispatch fails closed");
        CatooAttributionMod.SERVER_EXECUTOR = pool;

        // 主线程自调用：直接执行，不排队（避免自等待死锁）。
        final AtomicBoolean directRun = new AtomicBoolean(false);
        CatooAttributionMod.SERVER_THREAD = Thread.currentThread();
        CatooAttributionMod.Reply direct = CatooAttributionMod.runOnServerThread(expired -> {
            directRun.set(true);
            return CatooAttributionMod.refuse(200, "direct");
        }, 1000);
        check(directRun.get() && direct.code == 200, "server-thread caller runs inline (no self-wait deadlock)");

        CatooAttributionMod.SERVER_EXECUTOR = null;
        CatooAttributionMod.Reply unavailable = CatooAttributionMod.runOnServerThread(
                expired -> CatooAttributionMod.refuse(200, "never"), 200);
        check(unavailable.code == 503 && "server_thread_unavailable".equals(unavailable.reason),
                "missing executor is fail-closed");

        pool.shutdownNow();
        System.out.println("[reserve-bridge-test] " + passed + "/" + (passed + failed) + " checks passed");
        if (failed > 0) {
            System.out.println("[reserve-bridge-test] FAILED");
            System.exit(1);
        }
        System.out.println("[reserve-bridge-test] ALL CHECKS PASSED");
    }
}
