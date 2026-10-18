package catoo.attrib;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.minecraft.class_2338;
import net.minecraft.class_3218;

/** P2 影子模式：只记录证据，不参与任何任务终态。修复轮：会话显式绑定、维度/精确方块/身份参与匹配、异步有界 OUTBOX。 */
public final class CatooAttributionMod implements ModInitializer {
    static final String VER = "p2-shadow-2";
    static final Booking BOOK = new Booking();
    static final Properties CFG = new Properties();
    static volatile boolean ENABLED = false;
    static final java.util.List<Booking.Session> SESSIONS = Collections.synchronizedList(new ArrayList<>());

    static final int OUTBOX_CAPACITY = 1024;
    static final BlockingQueue<String> OUTBOX = new ArrayBlockingQueue<>(OUTBOX_CAPACITY);
    static final java.util.concurrent.atomic.AtomicLong OUTBOX_OVERFLOW = new java.util.concurrent.atomic.AtomicLong();
    static final java.util.concurrent.atomic.AtomicLong PUSH_OK = new java.util.concurrent.atomic.AtomicLong();
    static final java.util.concurrent.atomic.AtomicLong PUSH_FAIL = new java.util.concurrent.atomic.AtomicLong();

    static void log(String line) {
        String text = java.time.Instant.now().toString() + " " + line;
        System.out.println("[catoo-attrib] " + line);
        try (FileWriter writer = new FileWriter(new File(System.getProperty("user.dir"),
                "logs/catoobot-attribution.log"), true)) {
            writer.write(text + System.lineSeparator());
        } catch (Throwable ignored) {
            // 只读实验：日志失败不影响服务器
        }
    }

    static void loadConfig() {
        File file = new File(System.getProperty("user.dir"), "config/catoo-attribution.properties");
        if (file.isFile()) {
            try (Reader reader = new InputStreamReader(new FileInputStream(file), StandardCharsets.UTF_8)) {
                CFG.load(reader);
            } catch (Throwable error) {
                log("CONFIG_ERROR " + error.getClass().getSimpleName());
            }
        }
        ENABLED = Boolean.parseBoolean(CFG.getProperty("enabled", "false"));
    }

    @Override
    public void onInitialize() {
        loadConfig();
        log("INIT mod=catoobot-attribution version=" + VER + " shadow_enabled=" + ENABLED
                + " outbox_capacity=" + OUTBOX_CAPACITY);
        ServerPlayConnectionEvents.JOIN.register((handler, sender, server) -> {
            try {
                var player = handler.field_14140;
                String name = player.method_5477().getString();
                String uuid;
                try {
                    uuid = String.valueOf(player.method_5667());
                } catch (Throwable error) {
                    uuid = "unknown";
                }
                String ref = UUID.randomUUID().toString();
                Booking.Session session = BOOK.newSession(name, uuid, ref, player.method_51469());
                SESSIONS.add(session);
                log("JOIN name=" + name + " uuid=" + uuid + " session_ref=" + ref + " session_seq=" + session.seq);
            } catch (Throwable error) {
                log("JOIN_ERROR " + error.getClass().getName() + ":" + error.getMessage());
            }
        });
        PlayerBlockBreakEvents.AFTER.register((world, player, pos, state, blockEntity) -> {
            try {
                String name = player.method_5477().getString();
                Booking.Session session = BOOK.currentSessionOf(name);
                String uuid = session == null ? "unknown" : session.uuid;
                String stateNow;
                try {
                    stateNow = String.valueOf(world.method_8320(pos));
                } catch (Throwable error) {
                    stateNow = "UNKNOWN";
                }
                String posText = pos.method_10263() + "," + pos.method_10264() + "," + pos.method_10260();
                Booking.MatchResult match = BOOK.match(session, uuid, posText, String.valueOf(state));
                String eventId = UUID.randomUUID().toString();
                String payload = eventJson(eventId, match, session, uuid, posText, String.valueOf(state), stateNow);
                boolean queued = OUTBOX.offer(payload);
                if (!queued) OUTBOX_OVERFLOW.incrementAndGet();
                log("AFTER name=" + name + " uuid=" + uuid + " session_ref="
                        + (session == null ? "" : session.ref) + " session_seq=" + (session == null ? -1 : session.seq)
                        + " pos=" + posText + " state_before=" + Booking.canonicalBlock(String.valueOf(state))
                        + " state_now=" + Booking.canonicalBlock(stateNow) + " shadow=" + match.state
                        + " reason=" + match.reason + " event=" + eventId + " queued=" + queued
                        + " reservation=" + (match.reservation == null ? "null" : match.reservation.id));
            } catch (Throwable error) {
                log("AFTER_ERROR " + error.getClass().getName() + ":" + error.getMessage());
            }
        });
        if (ENABLED) {
            startHttp();
            startOutbox();
        }
        log("LISTENER_REGISTERED PlayerBlockBreakEvents.AFTER; ServerPlayConnectionEvents.JOIN");
    }

    static String eventJson(String eventId, Booking.MatchResult match, Booking.Session session, String uuid,
            String pos, String before, String after) {
        String reservationId = match.reservation == null ? "" : match.reservation.id;
        String actionId = match.reservation == null ? "" : match.reservation.actionId;
        String sessionRef = session == null ? "" : session.ref;
        long sessionSeq = session == null ? -1 : session.seq;
        return "{\"event_id\":\"" + eventId + "\","
                + "\"reservation_id\":\"" + reservationId + "\","
                + "\"action_id\":\"" + actionId + "\","
                + "\"session_ref\":\"" + sessionRef + "\","
                + "\"session_seq\":" + sessionSeq + ","
                + "\"player_uuid\":\"" + uuid + "\","
                + "\"dimension\":\"" + CFG.getProperty("dimension_label", "minecraft:overworld") + "\","
                + "\"pos\":\"" + pos + "\","
                + "\"block_before\":\"" + Booking.canonicalBlock(before) + "\","
                + "\"block_after\":\"" + Booking.canonicalBlock(after) + "\","
                + "\"shadow\":\"" + match.state + "\","
                + "\"reason\":\"" + match.reason + "\"}";
    }

    static void startOutbox() {
        Thread worker = new Thread(() -> {
            while (true) {
                try {
                    String payload = OUTBOX.poll(1, TimeUnit.SECONDS);
                    if (payload == null) continue;
                    deliver(payload);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return;
                } catch (Throwable error) {
                    log("OUTBOX_ERROR " + error.getClass().getSimpleName());
                }
            }
        }, "catoo-attrib-outbox");
        worker.setDaemon(true);
        worker.start();
        log("OUTBOX_WORKER_STARTED");
    }

    static void deliver(String payload) {
        String url = CFG.getProperty("bot_url", "");
        if (url.isEmpty()) return;
        try {
            HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
            connection.setRequestMethod("POST");
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setRequestProperty("X-Signature", Hmac.sign(payload, CFG.getProperty("token", "")));
            connection.setConnectTimeout(1500);
            connection.setReadTimeout(1500);
            connection.setDoOutput(true);
            try (OutputStream out = connection.getOutputStream()) {
                out.write(payload.getBytes(StandardCharsets.UTF_8));
            }
            int code = connection.getResponseCode();
            if (code >= 300) {
                PUSH_FAIL.incrementAndGet();
                log("EVENT_DELIVERY_REJECTED code=" + code);
            } else {
                PUSH_OK.incrementAndGet();
            }
            connection.disconnect();
        } catch (Throwable error) {
            PUSH_FAIL.incrementAndGet();
            log("EVENT_DELIVERY_FAILED " + error.getClass().getSimpleName());
        }
    }

    static void startHttp() {
        try {
            int port = Integer.parseInt(CFG.getProperty("port", "19132"));
            HttpServer server = HttpServer.create(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), port), 8);
            server.createContext("/reserve", CatooAttributionMod::handleReserve);
            server.createContext("/health", exchange -> respond(exchange, 200, "{\"ok\":true}"));
            server.createContext("/stats", exchange -> respond(exchange, 200, statsJson()));
            server.createContext("/session", exchange -> {
                String query = exchange.getRequestURI().getQuery();
                String rawName = query != null && query.startsWith("name=") ? query.substring(5) : "";
                String name = java.net.URLDecoder.decode(rawName, java.nio.charset.StandardCharsets.UTF_8);
                String expected = Hmac.sign(name, CFG.getProperty("token", ""));
                String given = exchange.getRequestHeaders().getFirst("X-Signature");
                if (expected.isEmpty() || given == null || !expected.equalsIgnoreCase(given)) {
                    respond(exchange, 401, "{\"ok\":false,\"reason\":\"hmac\"}");
                    return;
                }
                Booking.Session session = BOOK.currentSessionOf(name);
                if (session == null) {
                    respond(exchange, 404, "{\"ok\":false,\"reason\":\"no_session\"}");
                    return;
                }
                respond(exchange, 200, "{\"ok\":true,\"session_ref\":\"" + session.ref + "\",\"session_seq\":"
                        + session.seq + ",\"player_uuid\":\"" + session.uuid + "\"}");
            });

            server.start();
            log("HTTP_LISTEN 127.0.0.1:" + port);
        } catch (Throwable error) {
            log("HTTP_START_FAILED " + error.getClass().getSimpleName());
        }
    }

    static String statsJson() {
        return "{\"reservations_active\":" + BOOK.activeReservations()
                + ",\"rejected\":" + BOOK.rejected + ",\"expired\":" + BOOK.expired
                + ",\"ambiguous\":" + BOOK.ambiguous + ",\"unknown\":" + BOOK.unknown
                + ",\"consumed\":" + BOOK.consumedCount + ",\"identity_rejected\":" + BOOK.identityRejected
                + ",\"push_ok\":" + PUSH_OK.get() + ",\"push_fail\":" + PUSH_FAIL.get()
                + ",\"outbox_overflow\":" + OUTBOX_OVERFLOW.get() + "}";
    }

    static void handleReserve(HttpExchange exchange) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) {
            respond(exchange, 405, "{\"error\":\"method\"}");
            return;
        }
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        String signature = exchange.getRequestHeaders().getFirst("X-Signature");
        if (!Hmac.verify(body, CFG.getProperty("token", ""), signature)) {
            log("RESERVE_REJECTED reason=hmac");
            respond(exchange, 401, "{\"ok\":false,\"reason\":\"hmac\"}");
            return;
        }
        Map<String, String> fields = Json.flat(body);
        Booking.ReserveResult result = BOOK.reserve(fields, f -> {
            String ref = f.getOrDefault("session_ref", "").trim();
            Booking.Session session = sessionOfRef(ref);
            if (session == null || !(session.world instanceof class_3218)) return null;
            return String.valueOf(((class_3218) session.world).method_8320(posOf(f)));
        });
        respond(exchange, result.httpCode, "{\"ok\":" + result.ok + ",\"reason\":\"" + result.reason
                + "\",\"reservation_id\":\"" + result.id + "\"}");
        log("RESERVE ok=" + result.ok + " reason=" + result.reason
                + " action=" + fields.getOrDefault("action_id", ""));
    }

    static Booking.Session sessionOfRef(String ref) {
        for (Booking.Session session : SESSIONS) {
            if (session.ref.equals(ref)) return session;
        }
        return null;
    }

    static class_2338 posOf(Map<String, String> fields) {
        try {
            return new class_2338(Integer.parseInt(fields.getOrDefault("x", "0")),
                    Integer.parseInt(fields.getOrDefault("y", "0")),
                    Integer.parseInt(fields.getOrDefault("z", "0")));
        } catch (Throwable error) {
            return new class_2338(0, 0, 0);
        }
    }

    static void respond(HttpExchange exchange, int code, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(code, bytes.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(bytes);
        }
    }
}
