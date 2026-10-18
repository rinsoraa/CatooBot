package catoo.attrib;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

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

    public static void main(String[] args) throws Exception {
        ExecutorService pool = Executors.newSingleThreadExecutor();
        CatooAttributionMod.SERVER_EXECUTOR = pool;
        CatooAttributionMod.SERVER_THREAD_NAME = "pool-test-thread";

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
            if (expired.get()) return CatooAttributionMod.refuse(503, "reserve_timeout");
            created.incrementAndGet();
            return CatooAttributionMod.refuse(200, "ok");
        }, 120);
        check(timeoutReply.code == 503 && "reserve_timeout".equals(timeoutReply.reason),
                "bounded wait returns reserve_timeout instead of blocking forever");
        release.countDown();
        Thread.sleep(600);
        check(created.get() == 0, "late queued task must NOT create a reservation after timeout");

        // 主线程自调用：直接执行，不排队（避免自等待死锁）。
        final AtomicBoolean directRun = new AtomicBoolean(false);
        CatooAttributionMod.SERVER_THREAD_NAME = Thread.currentThread().getName();
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
