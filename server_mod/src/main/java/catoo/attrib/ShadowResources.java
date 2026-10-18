package catoo.attrib;

import com.sun.net.httpserver.HttpServer;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Consumer;

/** One server generation owns its listener, executor, queue, worker and active delivery. */
final class ShadowResources implements AutoCloseable {
    private final int port;
    private final String endpoint;
    private final String token;
    private final Consumer<String> log;
    private final BlockingQueue<String> outbox;
    private HttpServer http;
    private ExecutorService executor;
    private Thread worker;
    private HttpURLConnection activeConnection;
    private volatile boolean running;
    private boolean started;
    private boolean closed;
    final AtomicLong overflow = new AtomicLong();
    final AtomicLong pushOk = new AtomicLong();
    final AtomicLong pushFail = new AtomicLong();
    final AtomicLong shutdownDropped = new AtomicLong();

    ShadowResources(int port, String endpoint, String token, int capacity, Consumer<String> log) {
        this.port = port;
        this.endpoint = endpoint;
        this.token = token;
        this.outbox = new ArrayBlockingQueue<>(capacity);
        this.log = log;
    }

    synchronized void start(Consumer<HttpServer> configure) throws Exception {
        if (started || closed) throw new IllegalStateException("resources cannot restart");
        started = true;
        try {
            // Configure before binding: on Windows an unstarted selector can retain a closed
            // bound channel until deregistration. A configuration failure must never claim a port.
            http = HttpServer.create();
            executor = new ThreadPoolExecutor(2, 2, 0, TimeUnit.MILLISECONDS,
                    new ArrayBlockingQueue<>(32), task -> {
                        Thread thread = new Thread(task, "catoo-attrib-http");
                        thread.setDaemon(true);
                        return thread;
                    }, new ThreadPoolExecutor.AbortPolicy());
            http.setExecutor(executor);
            configure.accept(http);
            worker = new Thread(this::work, "catoo-attrib-outbox");
            worker.setDaemon(true);
            http.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), port), 8);
            running = true;
            http.start();
            worker.start();
            log.accept("HTTP_LISTEN 127.0.0.1:" + port + " OUTBOX_WORKER_STARTED");
        } catch (Exception | Error error) {
            close();
            throw error;
        }
    }

    boolean isRunning() { return running; }

    synchronized boolean offer(String payload) {
        if (!running) return false;
        if (outbox.offer(payload)) return true;
        overflow.incrementAndGet();
        return false;
    }

    private void work() {
        while (running) {
            try {
                String payload = outbox.poll(1, TimeUnit.SECONDS);
                if (payload != null) deliver(payload);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return;
            } catch (Exception error) {
                pushFail.incrementAndGet();
                log.accept("OUTBOX_ERROR " + error.getClass().getSimpleName());
            }
        }
    }

    private void deliver(String payload) {
        HttpURLConnection connection = null;
        try {
            synchronized (this) {
                if (!running) { shutdownDropped.incrementAndGet(); return; }
                if (endpoint.isEmpty()) { pushFail.incrementAndGet(); return; }
                connection = (HttpURLConnection) URI.create(endpoint).toURL().openConnection();
                activeConnection = connection;
            }
            connection.setRequestMethod("POST");
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setRequestProperty("X-Signature", Hmac.sign(payload, token));
            connection.setConnectTimeout(1500);
            connection.setReadTimeout(1500);
            connection.setDoOutput(true);
            byte[] bytes = payload.getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream out = connection.getOutputStream()) { out.write(bytes); }
            int code = connection.getResponseCode();
            if (code < 200 || code >= 300) {
                pushFail.incrementAndGet();
                log.accept("EVENT_DELIVERY_REJECTED code=" + code);
            } else { pushOk.incrementAndGet(); }
        } catch (Exception error) {
            pushFail.incrementAndGet();
            log.accept("EVENT_DELIVERY_FAILED " + error.getClass().getSimpleName());
        } finally {
            if (connection != null) connection.disconnect();
            synchronized (this) {
                if (activeConnection == connection) activeConnection = null;
            }
        }
    }

    @Override
    public void close() {
        HttpServer server;
        ExecutorService handlers;
        Thread sender;
        HttpURLConnection connection;
        synchronized (this) {
            if (closed) return;
            closed = true;
            running = false;
            server = http;
            handlers = executor;
            sender = worker;
            connection = activeConnection;
            int removed = 0;
            while (outbox.poll() != null) removed++;
            shutdownDropped.addAndGet(removed);
        }
        // Never hold the owner monitor while waiting for the worker's finally block.
        if (server != null) server.stop(0);
        if (handlers != null) handlers.shutdownNow();
        if (sender != null) sender.interrupt();
        if (connection != null) connection.disconnect();
        boolean interrupted = false;
        try {
            if (sender != null && sender != Thread.currentThread()) sender.join(2000);
            if (handlers != null) handlers.awaitTermination(500, TimeUnit.MILLISECONDS);
        } catch (InterruptedException error) { interrupted = true; }
        finally { if (interrupted) Thread.currentThread().interrupt(); }
        log.accept("RESOURCES_STOPPED dropped=" + shutdownDropped.get() + " push_ok=" + pushOk.get()
                + " push_fail=" + pushFail.get() + " worker_exited=" + workerExited()
                + " http_executor_terminated=" + executorTerminated());
    }

    boolean workerExited() { return worker == null || !worker.isAlive(); }
    boolean executorTerminated() { return executor == null || executor.isTerminated(); }
    int pendingEvents() { return outbox.size(); }
    synchronized boolean deliveryInFlight() { return activeConnection != null; }
}
