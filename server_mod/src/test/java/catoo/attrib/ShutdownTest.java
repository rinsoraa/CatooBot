package catoo.attrib;

import com.sun.net.httpserver.HttpServer;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;

/** Real loopback sockets and deterministic cancellation, with no Fabric dependency. */
public final class ShutdownTest {
    static int passed;
    static void check(boolean value, String label) {
        if (!value) throw new AssertionError(label);
        passed++;
        System.out.println("  OK   " + label);
    }
    static int freePort() throws Exception {
        try (ServerSocket socket = new ServerSocket(0, 8, InetAddress.getLoopbackAddress())) {
            return socket.getLocalPort();
        }
    }
    static ShadowResources resources(int port, String url) {
        return new ShadowResources(port, url, "test-only", 4, System.out::println);
    }
    static void health(HttpServer server) {
        server.createContext("/health", exchange -> {
            exchange.sendResponseHeaders(200, 2);
            try (var out = exchange.getResponseBody()) { out.write("ok".getBytes(StandardCharsets.UTF_8)); }
            finally { exchange.close(); }
        });
    }
    static int get(int port) throws Exception {
        HttpURLConnection request = (HttpURLConnection) URI.create("http://127.0.0.1:" + port + "/health").toURL().openConnection();
        request.setConnectTimeout(1000); request.setReadTimeout(1000);
        try { return request.getResponseCode(); }
        finally { request.disconnect(); }
    }
    static void released(int port) throws Exception {
        try (ServerSocket socket = new ServerSocket()) {
            socket.setReuseAddress(true);
            socket.bind(new InetSocketAddress("127.0.0.1", port));
            check(true, "listener port can be rebound");
        }
    }
    public static void main(String[] args) throws Exception {
        RequestRegistry<String> registry = new RequestRegistry<>();
        Object old = new Object(), next = new Object();
        check(registry.begin(1000, r -> r, old) == null, "closed registry refuses admission");
        registry.close(); registry.close(); registry.open(old);
        var pending = registry.begin(5000, r -> r, old);
        var committed = registry.begin(5000, r -> r, old);
        committed.complete("success");
        registry.close();
        check(pending.await(20) && "server_stopping".equals(pending.outcome()), "stop wakes pending request immediately");
        check(!pending.complete("late"), "cancelled generation cannot publish late success");
        check("success".equals(committed.outcome()), "stop preserves already committed response");
        registry.finish(pending); registry.open(next);
        check(registry.begin(1000, r -> r, old) == null, "old HTTP generation refused after restart");
        check(registry.begin(1000, r -> r, next) != null, "new generation admits requests");
        registry.close();

        Booking book = new Booking();
        var session = book.newSession("player", "uuid", "ref", new Object());
        book.attachPlayer("ref", new Object()); book.invalidateAll();
        check(!session.valid && book.currentSessionOf("player") == null && book.sessionOfRef("ref") == null,
                "shutdown invalidates sessions and removes retained player references");

        int port = freePort();
        ShadowResources never = resources(port, "");
        never.close(); never.close();
        check(never.workerExited() && never.executorTerminated(), "stop without start is safe");
        for (int i = 0; i < 2; i++) {
            ShadowResources owner = resources(port, "");
            try {
                owner.start(ShutdownTest::health);
                check(owner.isRunning() && get(port) == 200, "real HTTP listener responds in generation " + i);
            } finally { owner.close(); owner.close(); }
            check(!owner.isRunning() && !owner.offer("late"), "shutdown rejects outbox admission");
            check(owner.workerExited() && owner.executorTerminated(), "worker and executor terminate");
            released(port);
        }
        ShadowResources conflict = resources(port, "");
        try (ServerSocket occupied = new ServerSocket(port, 8, InetAddress.getLoopbackAddress())) {
            boolean failed = false;
            try { conflict.start(ShutdownTest::health); } catch (BindException expected) { failed = true; }
            check(failed && !conflict.isRunning() && conflict.workerExited(), "bind conflict rolls back all resources");
        }
        ShadowResources partial = resources(port, "");
        boolean failed = false;
        try { partial.start(server -> { throw new IllegalStateException("injected initialization failure"); }); }
        catch (IllegalStateException expected) { failed = true; }
        check(failed && partial.executorTerminated() && partial.workerExited(), "partial startup closes bound HTTP and executor");
        released(port);

        CountDownLatch delivering = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        ExecutorService receiverThreads = Executors.newSingleThreadExecutor();
        HttpServer receiver = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 1);
        receiver.setExecutor(receiverThreads);
        receiver.createContext("/event", exchange -> {
            exchange.getRequestBody().readAllBytes();
            delivering.countDown();
            try { release.await(5, TimeUnit.SECONDS); }
            catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
            finally { exchange.close(); }
        });
        receiver.start();
        ShadowResources slow = resources(port, "http://127.0.0.1:" + receiver.getAddress().getPort() + "/event");
        try {
            slow.start(ShutdownTest::health);
            check(slow.offer("{\"event\":1}"), "first event accepted");
            check(delivering.await(2, TimeUnit.SECONDS), "receiver is hanging during actual delivery");
            slow.offer("queued-1"); slow.offer("queued-2");
            long began = System.nanoTime(); slow.close();
            check(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - began) < 4000, "hanging receiver cannot block shutdown indefinitely");
            check(slow.workerExited() && slow.executorTerminated(), "network failure leaves no worker or HTTP executor");
            check(slow.pushOk.get() == 0 && slow.pushFail.get() == 1, "cancelled in-flight delivery is failure, never success");
            check(slow.shutdownDropped.get() == 2, "two queued events explicitly counted as shutdown drops");
            released(port);
        } finally {
            slow.close(); release.countDown(); receiver.stop(0); receiverThreads.shutdownNow();
        }
        System.out.println("[shutdown-test] " + passed + "/" + passed + " checks passed");
    }
}
