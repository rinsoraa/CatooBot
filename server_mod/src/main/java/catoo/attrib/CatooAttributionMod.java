package catoo.attrib;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.minecraft.class_2338;
import net.minecraft.class_3218;
import net.minecraft.class_3222;

/** P2 shadow v7: server-owned resources and cancellation on shutdown; default off. */
public final class CatooAttributionMod implements ModInitializer {
    static final String VER = "p2-shadow-7";
    static final Booking BOOK = new Booking();
    static final Properties CFG = new Properties();
    static volatile boolean ENABLED = false;

    static volatile Executor SERVER_EXECUTOR = null;
    static volatile Thread SERVER_THREAD = null;
    static final long RESERVE_TIMEOUT_MS = 2500L;

    static final int OUTBOX_CAPACITY = 1024;
    static volatile ShadowResources RESOURCES;
    static volatile Object OWNER_SERVER;
    static final RequestRegistry<Reply> REQUESTS = new RequestRegistry<>();

    static final class Reply {
        final int code;
        final String body;
        final String reason;
        Reply(int code, String body, String reason) {
            this.code = code;
            this.body = body;
            this.reason = reason;
        }
    }

    interface Work {
        Reply run(RequestState<Reply> request);
    }

    static Reply refuse(int code, String reason) {
        return new Reply(code, "{\"ok\":false,\"reason\":\"" + reason + "\"}", reason);
    }

    /**
     * World reads stay on the server thread. Timeout/interruption and reservation commit
     * compete for ONE immutable outcome; a timeout can never overwrite a committed success.
     */
    static Reply runOnServerThread(Work work, long timeoutMs) {
        return runOnServerThread(work, timeoutMs, RESOURCES);
    }

    static Reply runOnServerThread(Work work, long timeoutMs, ShadowResources owner) {
        Executor executor = SERVER_EXECUTOR;
        if (executor == null) return refuse(503, "server_thread_unavailable");
        final RequestState<Reply> request = REQUESTS.begin(timeoutMs, reason -> refuse(503, reason), owner);
        if (request == null) return refuse(503, "server_stopping");
        try {
            if (SERVER_THREAD == Thread.currentThread()) {
                try {
                    if (request.isOpen()) request.complete(work.run(request));
                } catch (Throwable error) {
                    request.complete(refuse(500, "work_error"));
                }
                return request.cancel("reserve_timeout");
            }
            try {
                executor.execute(() -> {
                    try {
                        if (!request.isOpen()) return;
                        request.complete(work.run(request));
                    } catch (Throwable error) {
                        request.complete(refuse(500, "work_error"));
                    } finally {
                        request.finishWork();
                    }
                });
            } catch (Throwable error) {
                return request.cancel("server_thread_rejected");
            }
            try {
                if (!request.await(timeoutMs)) {
                    return request.cancel("reserve_timeout");
                }
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return request.cancel("reserve_interrupted");
            }
            return request.cancel("reserve_timeout");
        } finally { REQUESTS.finish(request); }
    }

    static void log(String line) {
        String text = java.time.Instant.now().toString() + " " + line;
        System.out.println("[catoo-attrib] " + line);
        try (FileWriter writer = new FileWriter(new File(System.getProperty("user.dir"),
                "logs/catoobot-attribution.log"), true)) {
            writer.write(text + System.lineSeparator());
        } catch (Throwable ignored) {
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
        if (!ENABLED) {
            log("DISABLED listeners_not_registered (no JOIN/DISCONNECT/AFTER, no HTTP, no outbox, no match)");
            return;
        }
        ServerLifecycleEvents.SERVER_STARTED.register(server -> startRuntime(server, Thread.currentThread()));
        ServerLifecycleEvents.SERVER_STOPPING.register(server -> stopRuntime(server));
        ServerLifecycleEvents.SERVER_STOPPED.register(server -> stopRuntime(server));
        ServerPlayConnectionEvents.JOIN.register((handler, sender, server) -> {
            try {
                if (OWNER_SERVER != server || !accepting()) return;
                class_3222 player = handler.field_14140;
                String name = player.method_5477().getString();
                String uuid;
                try {
                    uuid = String.valueOf(player.method_5667());
                } catch (Throwable error) {
                    uuid = "unknown";
                }
                String ref = UUID.randomUUID().toString();
                Booking.Session session = BOOK.newSession(name, uuid, ref, currentWorldOfPlayer(player));
                BOOK.attachPlayer(ref, player);
                log("JOIN name=" + name + " uuid=" + uuid + " session_ref=" + ref + " session_seq=" + session.seq);
            } catch (Throwable error) {
                log("JOIN_ERROR " + error.getClass().getName() + ":" + error.getMessage());
            }
        });
        ServerPlayConnectionEvents.DISCONNECT.register((handler, server) -> {
            try {
                if (OWNER_SERVER != server || !accepting()) return;
                class_3222 player = handler.field_14140;
                boolean closed = BOOK.disconnect(player.method_5477().getString(),
                        String.valueOf(player.method_5667()), player);
                log("DISCONNECT session_closed=" + closed);
            } catch (Throwable error) {
                log("DISCONNECT_ERROR " + error.getClass().getSimpleName());
            }
        });
        PlayerBlockBreakEvents.AFTER.register((world, player, pos, state, blockEntity) -> {
            try {
                if (!accepting() || SERVER_THREAD != Thread.currentThread()) return;
                ShadowResources resources = RESOURCES;
                String name = player.method_5477().getString();
                Booking.Session session = BOOK.currentSessionOf(name);
                String eventUuid;
                try {
                    eventUuid = String.valueOf(player.method_5667());
                } catch (Throwable error) {
                    eventUuid = "unknown";
                }
                String eventDim;
                try {
                    eventDim = Booking.normalizeDimension(String.valueOf(world.method_27983().method_29177()));
                } catch (Throwable error) {
                    eventDim = "";
                }
                String stateNow;
                try {
                    stateNow = String.valueOf(world.method_8320(pos));
                } catch (Throwable error) {
                    stateNow = "UNKNOWN";
                }
                String posText = pos.method_10263() + "," + pos.method_10264() + "," + pos.method_10260();
                Booking.MatchResult match = BOOK.match(session, eventUuid, eventDim, posText, String.valueOf(state));
                String eventId = UUID.randomUUID().toString();
                String payload = eventJson(eventId, match, session, eventUuid, eventDim, posText,
                        String.valueOf(state), stateNow);
                boolean queued = resources != null && resources.offer(payload);
                log("AFTER name=" + name + " uuid=" + eventUuid + " session_ref="
                        + (session == null ? "" : session.ref) + " session_seq=" + (session == null ? -1 : session.seq)
                        + " dim=" + eventDim + " pos=" + posText + " state_before="
                        + Booking.canonicalBlock(String.valueOf(state)) + " state_now="
                        + Booking.canonicalBlock(stateNow) + " shadow=" + match.state + " reason=" + match.reason
                        + " event=" + eventId + " queued=" + queued + " reservation="
                        + (match.reservation == null ? "null" : match.reservation.id));
            } catch (Throwable error) {
                log("AFTER_ERROR " + error.getClass().getName() + ":" + error.getMessage());
            }
        });
        log("LISTENER_REGISTERED PlayerBlockBreakEvents.AFTER; ServerPlayConnectionEvents.JOIN/DISCONNECT");
    }

    static class_3218 currentWorldOfPlayer(class_3222 player) {
        try {
            return player.method_51469();
        } catch (Throwable error) {
            return null;
        }
    }

    static class_3218 currentWorldOf(Booking.Session session) {
        if (session == null || !(session.player instanceof class_3222)) return null;
        try {
            return ((class_3222) session.player).method_51469();
        } catch (Throwable error) {
            return null;
        }
    }

    static String currentDimensionOf(Booking.Session session) {
        class_3218 world = currentWorldOf(session);
        if (world == null) return "";
        try {
            return Booking.normalizeDimension(String.valueOf(world.method_27983().method_29177()));
        } catch (Throwable error) {
            return "";
        }
    }

    static String eventJson(String eventId, Booking.MatchResult match, Booking.Session session, String uuid,
            String dimensionId, String pos, String before, String after) {
        String reservationId = match.reservation == null ? "" : match.reservation.id;
        String actionId = match.reservation == null ? "" : match.reservation.actionId;
        String sessionRef = session == null ? "" : session.ref;
        long sessionSeq = session == null ? -1 : session.seq;
        return "{\"event_id\":\"" + eventId + "\",\"reservation_id\":\"" + reservationId
                + "\",\"action_id\":\"" + actionId + "\",\"session_ref\":\"" + sessionRef
                + "\",\"session_seq\":" + sessionSeq + ",\"player_uuid\":\"" + uuid
                + "\",\"dimension\":\"" + dimensionId + "\",\"pos\":\"" + pos
                + "\",\"block_before\":\"" + Booking.canonicalBlock(before)
                + "\",\"block_after\":\"" + Booking.canonicalBlock(after)
                + "\",\"shadow\":\"" + match.state + "\",\"reason\":\"" + match.reason + "\"}";
    }

    static boolean accepting() {
        ShadowResources resources = RESOURCES;
        return resources != null && resources.isRunning();
    }

    static void startRuntime(Executor server, Thread serverThread) {
        if (OWNER_SERVER != null) throw new IllegalStateException("previous server still owns resources");
        OWNER_SERVER = server;
        BOOK.invalidateAll();
        ShadowResources resources = null;
        try {
            resources = new ShadowResources(Integer.parseInt(CFG.getProperty("port", "19132")),
                    CFG.getProperty("bot_url", ""), CFG.getProperty("token", ""),
                    OUTBOX_CAPACITY, CatooAttributionMod::log);
            RESOURCES = resources;
            resources.start(CatooAttributionMod::configureHttp);
            SERVER_EXECUTOR = server;
            SERVER_THREAD = serverThread;
            REQUESTS.open(resources);
            log("RUNTIME_STARTED version=" + VER);
        } catch (Exception | Error error) {
            if (resources != null) resources.close();
            REQUESTS.close();
            SERVER_EXECUTOR = null;
            SERVER_THREAD = null;
            log("RUNTIME_START_FAILED " + error.getClass().getSimpleName());
        }
    }

    static void stopRuntime(Object server) {
        if (OWNER_SERVER != server) return;
        // Cancel before stopping HTTP: no handler may wait for this server thread.
        REQUESTS.close();
        SERVER_EXECUTOR = null;
        SERVER_THREAD = null;
        BOOK.invalidateAll();
        ShadowResources resources = RESOURCES;
        if (resources != null) resources.close();
        OWNER_SERVER = null;
        log("RUNTIME_STOPPED version=" + VER + " reservations_active=" + BOOK.activeReservations());
    }

    static void context(HttpServer server, ShadowResources owner, String path,
            com.sun.net.httpserver.HttpHandler handler) {
        server.createContext(path, exchange -> {
            try {
                if (RESOURCES != owner || !owner.isRunning()) {
                    respond(exchange, 503, "{\"ok\":false,\"reason\":\"server_stopping\"}");
                } else { handler.handle(exchange); }
            } finally { exchange.close(); }
        });
    }

    static void configureHttp(HttpServer server) {
        // This filter captures a generation, so old HTTP callbacks cannot enter a new server.
        ShadowResources owner = RESOURCES;
        context(server, owner, "/reserve", exchange -> handleReserve(exchange, owner));
        context(server, owner, "/health", exchange -> respond(exchange, 200, "{\"ok\":true}"));
        context(server, owner, "/stats", exchange -> respond(exchange, 200, statsJson()));
        context(server, owner, "/session", exchange -> {
            String query = exchange.getRequestURI().getQuery();
            String rawName = query != null && query.startsWith("name=") ? query.substring(5) : "";
            String name = java.net.URLDecoder.decode(rawName, StandardCharsets.UTF_8);
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
    }

    static String statsJson() {
        ShadowResources resources = RESOURCES;
        return "{\"reservations_active\":" + BOOK.activeReservations()
                + ",\"rejected\":" + BOOK.rejected + ",\"expired\":" + BOOK.expired
                + ",\"ambiguous\":" + BOOK.ambiguous + ",\"unknown\":" + BOOK.unknown
                + ",\"consumed\":" + BOOK.consumedCount + ",\"identity_rejected\":" + BOOK.identityRejected
                + ",\"dimension_rejected\":" + BOOK.dimensionRejected
                + ",\"push_ok\":" + (resources == null ? 0 : resources.pushOk.get()) + ",\"push_fail\":" + (resources == null ? 0 : resources.pushFail.get())
                + ",\"outbox_overflow\":" + (resources == null ? 0 : resources.overflow.get())
                + ",\"shutdown_dropped\":" + (resources == null ? 0 : resources.shutdownDropped.get())
                + ",\"outbox_pending\":" + (resources == null ? 0 : resources.pendingEvents())
                + ",\"delivery_in_flight\":" + (resources != null && resources.deliveryInFlight()) + "}";
    }

    static void handleReserve(HttpExchange exchange, ShadowResources owner) throws IOException {
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
        Reply reply = runOnServerThread(request -> {
            if (!request.isOpen()) return request.outcome();
            String sessionRef = fields.getOrDefault("session_ref", "").trim();
            Booking.Session sessionNow = BOOK.sessionOfRef(sessionRef);
            if (sessionNow == null || !sessionNow.valid) return refuse(409, "session_not_current");
            if (!(sessionNow.player instanceof class_3222)) return refuse(409, "player_missing");
            String liveUuid;
            try {
                liveUuid = String.valueOf(((class_3222) sessionNow.player).method_5667());
            } catch (Throwable error) {
                liveUuid = "";
            }
            if (liveUuid.isEmpty() || "unknown".equals(liveUuid) || !liveUuid.equals(sessionNow.uuid)) {
                return refuse(409, "identity_mismatch");
            }
            class_3218 targetWorld = currentWorldOf(sessionNow);
            if (targetWorld == null) return refuse(503, "world_unavailable");
            String actualDim = Booking.normalizeDimension(String.valueOf(targetWorld.method_27983().method_29177()));
            String dimErr = Booking.checkDimension(fields.getOrDefault("dimension", ""), actualDim);
            if (!dimErr.isEmpty()) return refuse(409, dimErr);
            if (!request.isOpen()) return request.outcome();
            return reserveForRequest(fields, f -> {
                return String.valueOf(targetWorld.method_8320(posOf(f)));
            }, request);
        }, RESERVE_TIMEOUT_MS, owner);
        respond(exchange, reply.code, reply.body);
        log("RESERVE code=" + reply.code + " reason=" + reply.reason
                + " action=" + fields.getOrDefault("action_id", ""));
    }

    static Reply reserveForRequest(Map<String, String> fields,
            java.util.function.Function<Map<String, String>, String> blockAt, RequestState<Reply> request) {
        if (!request.isOpen()) return request.outcome();
        Booking.ReserveResult result = BOOK.reserve(fields, blockAt,
                candidate -> request.complete(reserveReply(candidate)));
        Reply winner = request.outcome();
        return winner != null ? winner : reserveReply(result);
    }

    static Reply reserveReply(Booking.ReserveResult result) {
        return new Reply(result.httpCode, "{\"ok\":" + result.ok + ",\"reason\":\"" + result.reason
                + "\",\"reservation_id\":\"" + result.id + "\"}", result.reason);
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
