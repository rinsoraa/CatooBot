package catoo.attrib;

import java.net.ServerSocket;
import java.util.Map;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

/** Uses the actual mod start/stop/admission bridge; Fabric dependencies are classpath-only. */
public final class LifecycleBridgeTest {
    static int passed;
    static void check(boolean value, String label) {
        if (!value) throw new AssertionError(label);
        passed++; System.out.println("  OK   " + label);
    }
    static void await(CountDownLatch latch) {
        try { if (!latch.await(3, TimeUnit.SECONDS)) throw new AssertionError("latch timed out"); }
        catch (InterruptedException error) { throw new AssertionError(error); }
    }
    public static void main(String[] args) throws Exception {
        int port;
        try (ServerSocket probe = new ServerSocket(0)) { port = probe.getLocalPort(); }
        CatooAttributionMod.CFG.setProperty("port", Integer.toString(port));
        ExecutorService server = Executors.newSingleThreadExecutor();
        ExecutorService http = Executors.newSingleThreadExecutor();
        CountDownLatch release = new CountDownLatch(1);
        try {
            Thread serverThread = server.submit(Thread::currentThread).get(2, TimeUnit.SECONDS);
            CatooAttributionMod.startRuntime(server, serverThread);
            ShadowResources old = CatooAttributionMod.RESOURCES;
            check(CatooAttributionMod.accepting(), "actual mod resource start opens admission");
            CatooAttributionMod.BOOK.newSession("probe", "uuid", "ref", new Object());
            CountDownLatch reading = new CountDownLatch(1);
            var response = http.submit(() -> CatooAttributionMod.runOnServerThread(request ->
                    CatooAttributionMod.reserveForRequest(Map.of("session_ref", "ref", "action_id", "test",
                            "nonce", "shutdown-nonce-0123456789", "dimension", "minecraft:overworld",
                            "pos", "1,2,3", "expected_block", "minecraft:oak_log"), fields -> {
                        reading.countDown(); await(release); return "minecraft:oak_log";
                    }, request), 5000, old));
            await(reading);
            CatooAttributionMod.stopRuntime(new Object());
            check(CatooAttributionMod.accepting(), "unrelated server stop cannot close current owner");
            CatooAttributionMod.stopRuntime(server);
            var result = response.get(500, TimeUnit.MILLISECONDS);
            check(result.code == 503 && "server_stopping".equals(result.reason), "stop cancels actual reading request without waiting for world read");
            release.countDown(); server.submit(() -> {}).get(2, TimeUnit.SECONDS);
            check(CatooAttributionMod.BOOK.activeReservations() == 0, "late read cannot publish a post-stop reservation");
            check(CatooAttributionMod.SERVER_EXECUTOR == null && CatooAttributionMod.SERVER_THREAD == null,
                    "stop removes both server scheduling references");
            check(old.workerExited() && old.executorTerminated(), "actual mod owns and closes resources");
            CatooAttributionMod.stopRuntime(server);

            CatooAttributionMod.startRuntime(server, serverThread);
            check(CatooAttributionMod.accepting() && CatooAttributionMod.BOOK.currentSessionOf("probe") == null,
                    "second generation rebinds same port with no old session");
            var stale = CatooAttributionMod.runOnServerThread(r -> CatooAttributionMod.refuse(200, "wrong"), 1000, old);
            check(stale.code == 503, "old handler cannot dispatch into replacement generation");

            CountDownLatch occupied = new CountDownLatch(1), unblock = new CountDownLatch(1);
            server.execute(() -> { occupied.countDown(); await(unblock); }); await(occupied);
            AtomicInteger dispatched = new AtomicInteger();
            CountDownLatch queued = new CountDownLatch(1);
            // Registry admission is synchronized before this executor hook announces scheduling.
            CatooAttributionMod.SERVER_EXECUTOR = command -> { server.execute(command); queued.countDown(); };
            var waiting = http.submit(() -> CatooAttributionMod.runOnServerThread(r -> {
                dispatched.incrementAndGet(); return CatooAttributionMod.refuse(200, "wrong");
            }, 5000));
            await(queued); CatooAttributionMod.stopRuntime(server);
            check("server_stopping".equals(waiting.get(500, TimeUnit.MILLISECONDS).reason), "queued request is cancelled promptly on stop");
            unblock.countDown(); server.submit(() -> {}).get(2, TimeUnit.SECONDS);
            check(dispatched.get() == 0, "cancelled queued work never enters callback");
        } finally {
            release.countDown(); CatooAttributionMod.stopRuntime(server); http.shutdownNow(); server.shutdownNow();
        }
        System.out.println("[lifecycle-bridge-test] " + passed + "/" + passed + " checks passed");
    }
}
