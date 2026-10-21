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
import net.minecraft.class_1937;
import net.minecraft.class_1657;
import net.minecraft.class_1792;
import net.minecraft.class_1799;
import net.minecraft.class_1542;
import net.minecraft.class_1297;
import net.minecraft.class_7923;
import net.minecraft.class_3218;
import net.minecraft.class_3222;

/** P2 shadow v7: server-owned resources and cancellation on shutdown; default off. */
public final class CatooAttributionMod implements ModInitializer {
    static final String VER = "r3-collection-negotiated-1";
    static final Booking BOOK = new Booking();
    static final Properties CFG = new Properties();
    static volatile boolean ENABLED = false;

    static volatile Executor SERVER_EXECUTOR = null;
    static volatile Thread SERVER_THREAD = null;
    static final long RESERVE_TIMEOUT_MS = 2500L;

    static final int OUTBOX_CAPACITY = 1024;
    static volatile ShadowResources RESOURCES;
    static volatile CollectionLedger COLLECTIONS;
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
                Booking.Session closing = BOOK.currentSessionOf(player.method_5477().getString());
                invalidateCollectionSession(closing);
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
                CollectionLedger ledger = COLLECTIONS;
                CollectionLedger.Frame frame = ledger == null ? null : ledger.currentFrame();
                if (frame != null && match.reservation != null
                        && CollectionProtocol.VERSION.equals(match.reservation.collectionProtocol)) {
                    ledger.bindBreak(frame, world, player, new CollectionLedger.BreakProof(
                            eventId, match.reservation.id, match.reservation.actionId, frame.identity));
                }
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
        return "{\"schema\":1,\"source\":\"server_block_break\",\"event_id\":\"" + eventId + "\",\"reservation_id\":\"" + reservationId
                + "\",\"action_id\":\"" + actionId + "\",\"session_ref\":\"" + sessionRef
                + "\",\"session_seq\":" + sessionSeq + ",\"player_uuid\":\"" + uuid
                + "\",\"dimension\":\"" + dimensionId + "\",\"pos\":\"" + pos
                + "\",\"block_before\":\"" + Booking.canonicalBlock(before)
                + "\",\"block_after\":\"" + Booking.canonicalBlock(after)
                + "\",\"shadow\":\"" + match.state + "\",\"reason\":\"" + match.reason + "\"}";
    }

    /** Begins an exact server-thread break scope; a missing session or fact means no evidence, never a guess. */
    public static CollectionLedger.Frame beginCollectionBreak(class_3218 world, class_3222 player, class_2338 pos) {
        CollectionLedger ledger = COLLECTIONS;
        if (!accepting() || ledger == null || world == null || player == null || pos == null) return null;
        try {
            String name = player.method_5477().getString();
            Booking.Session session = BOOK.currentSessionOf(name);
            if (session == null || !session.valid || session.player != player) return null;
            String uuid = String.valueOf(player.method_5667());
            String dimension = Booking.normalizeDimension(String.valueOf(world.method_27983().method_29177()));
            String posText = pos.method_10263() + "," + pos.method_10264() + "," + pos.method_10260();
            String block = Booking.canonicalBlock(String.valueOf(world.method_8320(pos)));
            CollectionLedger.Identity identity = new CollectionLedger.Identity(
                    session.ref, session.seq, uuid, dimension, posText, block);
            return ledger.beginBreak(world, player, identity);
        } catch (Throwable error) {
            log("COLLECTION_BREAK_BEGIN_UNKNOWN " + error.getClass().getSimpleName());
            return null;
        }
    }

    public static void finishCollectionBreak(CollectionLedger.Frame frame, boolean broken) {
        CollectionLedger ledger = COLLECTIONS;
        if (ledger == null || frame == null) return;
        CollectionLedger.BreakResult result = ledger.finishBreak(frame, broken);
        if (result == null || !result.confirmed()) {
            if (result != null && !result.reason().isEmpty()) {
                log("COLLECTION_BREAK_UNCONFIRMED reason=" + result.reason());
            }
            return;
        }
        ShadowResources resources = RESOURCES;
        String payload = collectionBreakJson(result);
        boolean queued = resources != null && resources.offer(payload);
        log("COLLECTION_BREAK_CONFIRMED action=" + result.proof().actionId()
                + " drops=" + result.drops().size() + " queued=" + queued);
    }

    public static boolean beginCollectionDrop(class_1937 world, String pos) {
        CollectionLedger ledger = COLLECTIONS;
        return ledger != null && ledger.beginDrop(world, pos);
    }

    public static void endCollectionDrop(boolean entered) {
        CollectionLedger ledger = COLLECTIONS;
        if (ledger != null) ledger.endDrop(entered);
    }

    public static boolean captureCollectionDrop(class_1937 world, class_1297 entity, boolean accepted) {
        CollectionLedger ledger = COLLECTIONS;
        if (ledger == null || !accepted || !(entity instanceof class_1542 itemEntity)) return false;
        try {
            class_1799 stack = itemEntity.method_6983();
            if (stack == null || stack.method_7960()) return false;
            String item = String.valueOf(class_7923.field_41178.method_10221(stack.method_7909()));
            CollectionLedger.Drop drop = new CollectionLedger.Drop(
                    String.valueOf(itemEntity.method_5667()), itemEntity.method_5628(), item, stack.method_7947());
            return ledger.spawned(world, drop, true);
        } catch (Throwable error) {
            log("COLLECTION_DROP_CAPTURE_UNKNOWN " + error.getClass().getSimpleName());
            return false;
        }
    }

    static String collectionBreakJson(CollectionLedger.BreakResult result) {
        CollectionLedger.BreakProof proof = result.proof();
        CollectionLedger.Identity id = proof.identity();
        StringBuilder drops = new StringBuilder("[");
        for (CollectionLedger.Drop drop : result.drops()) {
            if (drops.length() > 1) drops.append(',');
            drops.append("{\"uuid\":").append(Json.quote(drop.uuid()))
                    .append(",\"entity_id\":").append(drop.entityId())
                    .append(",\"item\":").append(Json.quote(drop.item()))
                    .append(",\"count\":").append(drop.count()).append('}');
        }
        drops.append(']');
        return "{\"schema\":1,\"source\":\"server_drop_evidence\"" + CollectionProtocol.field() + ",\"event_id\":"
                + Json.quote(UUID.randomUUID().toString())
                + ",\"break_event_id\":" + Json.quote(proof.eventId())
                + ",\"reservation_id\":" + Json.quote(proof.reservationId())
                + ",\"action_id\":" + Json.quote(proof.actionId())
                + ",\"session_ref\":" + Json.quote(id.sessionRef())
                + ",\"session_seq\":" + id.sessionSeq()
                + ",\"player_uuid\":" + Json.quote(id.playerUuid())
                + ",\"dimension\":" + Json.quote(id.dimension())
                + ",\"pos\":" + Json.quote(id.pos())
                + ",\"block_before\":" + Json.quote(id.block())
                + ",\"drops\":" + drops + "}";
    }

    static CollectionLedger.Identity pickupIdentity(class_1657 raw) {
        if (!(raw instanceof class_3222 player)) return null;
        try {
            Booking.Session session = BOOK.currentSessionOf(player.method_5477().getString());
            if (session == null || !session.valid || session.player != player) return null;
            class_3218 world = player.method_51469();
            String dimension = Booking.normalizeDimension(String.valueOf(world.method_27983().method_29177()));
            int x = (int) Math.floor(player.method_23317());
            int y = (int) Math.floor(player.method_23318());
            int z = (int) Math.floor(player.method_23321());
            return new CollectionLedger.Identity(session.ref, session.seq,
                    String.valueOf(player.method_5667()), dimension, x + "," + y + "," + z, "minecraft:air");
        } catch (Throwable error) {
            return null;
        }
    }

    public static int collectionInventoryCount(class_1657 player, class_1792 item) {
        int total = 0;
        for (class_1799 stack : player.method_31548().field_7547) {
            if (stack != null && !stack.method_7960() && stack.method_7909() == item) {
                total = Math.min(Integer.MAX_VALUE, total + stack.method_7947());
            }
        }
        return total;
    }

    public static void recordCollectionMerge(class_1542 first, class_1542 second) {
        CollectionLedger ledger = COLLECTIONS;
        if (ledger == null || first == null || second == null) return;
        ledger.merged(String.valueOf(first.method_5667()), String.valueOf(second.method_5667()));
    }

    public static void recordCollectionPickup(class_1542 entity, class_1657 player, class_1792 originalItem,
            int before, int after,
            int inventoryBefore, int inventoryAfter) {
        CollectionLedger ledger = COLLECTIONS;
        if (ledger == null || entity == null || player == null) return;
        try {
            CollectionLedger.Identity actor = pickupIdentity(player);
            if (actor == null || originalItem == null) return;
            String item = String.valueOf(class_7923.field_41178.method_10221(originalItem));
            CollectionLedger.PickupResult result = ledger.pickedUp(actor, String.valueOf(entity.method_5667()),
                    item, before, after, inventoryBefore, inventoryAfter);
            if (result == null) return;
            ShadowResources resources = RESOURCES;
            String payload = collectionPickupJson(result, inventoryBefore, inventoryAfter);
            boolean queued = resources != null && resources.offer(payload);
            log("COLLECTION_PICKUP dropped=" + entity.method_5667() + " confirmed=" + result.confirmed()
                    + " reason=" + result.reason() + " queued=" + queued);
        } catch (Throwable error) {
            log("COLLECTION_PICKUP_UNKNOWN " + error.getClass().getSimpleName());
        }
    }

    public static void logCollectionPickupFailure(class_1542 entity) {
        try {
            log("COLLECTION_PICKUP_UNKNOWN dropped=" + (entity == null ? "" : entity.method_5667()));
        } catch (Throwable ignored) {
            log("COLLECTION_PICKUP_UNKNOWN");
        }
    }

    static String collectionPickupJson(CollectionLedger.PickupResult result,
            int inventoryBefore, int inventoryAfter) {
        CollectionLedger.PickupBooking booking = result.booking();
        CollectionLedger.Drop drop = result.drop();
        CollectionLedger.BreakProof parent = result.parent();
        CollectionLedger.Identity id = parent.identity();
        return "{\"schema\":1,\"source\":\"server_drop_pickup\"" + CollectionProtocol.field() + ",\"event_id\":"
                + Json.quote(UUID.randomUUID().toString())
                + ",\"booking_id\":" + Json.quote(booking == null ? "" : booking.id())
                + ",\"pickup_action_id\":" + Json.quote(booking == null ? "" : booking.actionId())
                + ",\"pickup_nonce\":" + Json.quote(booking == null ? "" : booking.nonce())
                + ",\"break_event_id\":" + Json.quote(parent.eventId())
                + ",\"break_reservation_id\":" + Json.quote(parent.reservationId())
                + ",\"break_action_id\":" + Json.quote(parent.actionId())
                + ",\"session_ref\":" + Json.quote(id.sessionRef())
                + ",\"session_seq\":" + id.sessionSeq()
                + ",\"player_uuid\":" + Json.quote(id.playerUuid())
                + ",\"dimension\":" + Json.quote(id.dimension())
                + ",\"drop_uuid\":" + Json.quote(drop.uuid())
                + ",\"item\":" + Json.quote(drop.item())
                + ",\"count\":" + drop.count()
                + ",\"inventory_before\":" + inventoryBefore
                + ",\"inventory_after\":" + inventoryAfter
                + ",\"collected\":" + result.collected()
                + ",\"confirmed\":" + result.confirmed()
                + ",\"reason\":" + Json.quote(result.reason()) + "}";
    }

    static void invalidateCollectionSession(Booking.Session session) {
        CollectionLedger ledger = COLLECTIONS;
        if (ledger != null && session != null) ledger.invalidateSession(session.ref, session.seq);
    }

    static boolean accepting() {
        ShadowResources resources = RESOURCES;
        return resources != null && resources.isRunning();
    }

    static boolean collectionAvailable() {
        CollectionLedger ledger = COLLECTIONS;
        return accepting() && ledger != null && ledger.isOpen();
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
            COLLECTIONS = new CollectionLedger(serverThread, System::nanoTime);
            REQUESTS.open(resources);
            log("RUNTIME_STARTED version=" + VER);
        } catch (Exception | Error error) {
            if (resources != null) resources.close();
            REQUESTS.close();
            if (COLLECTIONS != null) COLLECTIONS.close();
            COLLECTIONS = null;
            SERVER_EXECUTOR = null;
            SERVER_THREAD = null;
            log("RUNTIME_START_FAILED " + error.getClass().getSimpleName());
        }
    }

    static void stopRuntime(Object server) {
        if (OWNER_SERVER != server) return;
        // Cancel before stopping HTTP: no handler may wait for this server thread.
        REQUESTS.close();
        if (COLLECTIONS != null) COLLECTIONS.close();
        COLLECTIONS = null;
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
        context(server, owner, "/collection/pickup-reserve", exchange -> handlePickupReserve(exchange, owner));
        context(server, owner, "/health", exchange -> respond(exchange, 200, "{\"ok\":true}"));
        context(server, owner, "/stats", exchange -> respond(exchange, 200, statsJson()));
        context(server, owner, "/session", exchange -> {
            if (!"GET".equals(exchange.getRequestMethod())) {
                respond(exchange, 405, "{\"ok\":false,\"reason\":\"method\"}"); return;
            }
            String query = exchange.getRequestURI().getQuery();
            Map<String, String> queryFields = new HashMap<>();
            if (query != null) for (String pair : query.split("&")) {
                String[] parts = pair.split("=", 2);
                if (parts.length == 2) queryFields.put(parts[0], java.net.URLDecoder.decode(parts[1], StandardCharsets.UTF_8));
            }
            String name = queryFields.getOrDefault("name", "");
            String nonce = queryFields.getOrDefault("nonce", "");
            if (!name.matches("[A-Za-z0-9_]{1,16}") || !nonce.matches("[A-Za-z0-9-]{0,128}")) {
                respond(exchange, 400, "{\"ok\":false,\"reason\":\"query\"}"); return;
            }
            String given = exchange.getRequestHeaders().getFirst("X-Signature");
            if (!Hmac.verify(nonce.isEmpty() ? name : name + "\n" + nonce, CFG.getProperty("token", ""), given)) {
                respond(exchange, 401, "{\"ok\":false,\"reason\":\"hmac\"}");
                return;
            }
            Booking.Session session = BOOK.currentSessionOf(name);
            if (session == null) {
                respond(exchange, 404, "{\"ok\":false,\"reason\":\"no_session\"}");
                return;
            }
            respond(exchange, 200, "{\"ok\":true,\"schema\":1,\"nonce\":\"" + nonce + "\",\"session_ref\":\"" + session.ref + "\",\"session_seq\":"
                    + session.seq + ",\"player_uuid\":\"" + session.uuid + "\""
                    + (collectionAvailable() ? CollectionProtocol.field() : "") + "}");
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
                + ",\"retained_nonces\":" + BOOK.retainedNonces() + ",\"retained_reservations\":" + BOOK.retainedReservations()
                + ",\"delivery_in_flight\":" + (resources != null && resources.deliveryInFlight()) + "}";
    }

    static void handleReserve(HttpExchange exchange, ShadowResources owner) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) {
            respond(exchange, 405, "{\"error\":\"method\"}");
            return;
        }
        byte[] bytes = exchange.getRequestBody().readNBytes(8193);
        if (bytes.length > 8192) { respond(exchange, 413, "{\"ok\":false,\"reason\":\"body_size\"}"); return; }
        String body = new String(bytes, StandardCharsets.UTF_8);
        String signature = exchange.getRequestHeaders().getFirst("X-Signature");
        if (!Hmac.verify(body, CFG.getProperty("token", ""), signature)) {
            log("RESERVE_REJECTED reason=hmac");
            respond(exchange, 401, "{\"ok\":false,\"reason\":\"hmac\"}");
            return;
        }
        Map<String, String> fields;
        try { fields = Json.flat(body); }
        catch (IllegalArgumentException error) { respond(exchange, 400, "{\"ok\":false,\"reason\":\"json\"}"); return; }
        String protocolError = CollectionProtocol.check(fields, collectionAvailable(), false);
        if (!protocolError.isEmpty()) { respond(exchange, 409, refuse(409, protocolError).body); return; }
        String pos = fields.getOrDefault("pos", "");
        try {
            int x = Integer.parseInt(fields.getOrDefault("x", ""));
            int y = Integer.parseInt(fields.getOrDefault("y", ""));
            int z = Integer.parseInt(fields.getOrDefault("z", ""));
            if (Math.abs((long) x) > 30000000 || Math.abs((long) z) > 30000000 || y < -512 || y > 2048
                    || !pos.equals(x + "," + y + "," + z)) throw new IllegalArgumentException();
        } catch (IllegalArgumentException error) { respond(exchange, 400, "{\"ok\":false,\"reason\":\"position\"}"); return; }
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

    static void handlePickupReserve(HttpExchange exchange, ShadowResources owner) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) {
            respond(exchange, 405, "{\"ok\":false,\"reason\":\"method\"}");
            return;
        }
        byte[] bytes = exchange.getRequestBody().readNBytes(8193);
        if (bytes.length > 8192) {
            respond(exchange, 413, "{\"ok\":false,\"reason\":\"body_size\"}");
            return;
        }
        String body = new String(bytes, StandardCharsets.UTF_8);
        if (!Hmac.verify(body, CFG.getProperty("token", ""), exchange.getRequestHeaders().getFirst("X-Signature"))) {
            log("PICKUP_RESERVE_REJECTED reason=hmac");
            respond(exchange, 401, "{\"ok\":false,\"reason\":\"hmac\"}");
            return;
        }
        Map<String, String> fields;
        try {
            fields = Json.flat(body);
        } catch (IllegalArgumentException error) {
            respond(exchange, 400, "{\"ok\":false,\"reason\":\"json\"}");
            return;
        }
        String protocolError = CollectionProtocol.check(fields, collectionAvailable(), true);
        if (!protocolError.isEmpty()) { respond(exchange, 409, refuse(409, protocolError).body); return; }
        String actionId = fields.getOrDefault("action_id", "");
        String nonce = fields.getOrDefault("nonce", "");
        String dropUuid = fields.getOrDefault("drop_uuid", "");
        String expectedBreakAction = fields.getOrDefault("expected_break_action", "");
        String sessionRef = fields.getOrDefault("session_ref", "");
        if (actionId.isEmpty() || actionId.length() > 128 || nonce.length() < 16 || nonce.length() > 128
                || dropUuid.length() != 36) {
            respond(exchange, 400, "{\"ok\":false,\"reason\":\"fields\"}");
            return;
        }
        Reply reply = runOnServerThread(request -> {
            if (!request.isOpen()) return request.outcome();
            Booking.Session session = BOOK.sessionOfRef(sessionRef);
            if (session == null || !session.valid || !(session.player instanceof class_3222 player)) {
                return refuse(409, "session_not_current");
            }
            CollectionLedger ledger = COLLECTIONS;
            CollectionLedger.Identity actor = pickupIdentity(player);
            CollectionLedger.PickupBooking booking = ledger == null ? null
                    : ledger.reservePickup(actor, actionId, nonce, dropUuid, expectedBreakAction,
                            candidate -> request.complete(pickupReply(candidate)));
            if (request.outcome() != null) return request.outcome();
            if (booking == null) return refuse(409, "collection_lineage_unavailable");
            return pickupReply(booking);
        }, RESERVE_TIMEOUT_MS, owner);
        respond(exchange, reply.code, reply.body);
        log("PICKUP_RESERVE code=" + reply.code + " reason=" + reply.reason + " action=" + actionId);
    }

    static Reply pickupReply(CollectionLedger.PickupBooking booking) {
        return new Reply(200, "{\"ok\":true,\"schema\":1,\"booking_id\":"
                + Json.quote(booking.id()) + ",\"action_id\":" + Json.quote(booking.actionId())
                + ",\"nonce\":" + Json.quote(booking.nonce()) + ",\"session_ref\":" + Json.quote(booking.identity().sessionRef())
                + ",\"drop_uuid\":" + Json.quote(booking.dropUuid())
                + ",\"break_event_id\":" + Json.quote(booking.breakEventId())
                + ",\"reservation_id\":" + Json.quote(booking.breakReservationId())
                + ",\"break_action_id\":" + Json.quote(booking.breakActionId())
                + CollectionProtocol.field() + "}", "ok");
    }

    static Reply reserveForRequest(Map<String, String> fields,
            java.util.function.Function<Map<String, String>, String> blockAt, RequestState<Reply> request) {
        if (!request.isOpen()) return request.outcome();
        String protocolError = CollectionProtocol.check(fields, collectionAvailable(), false);
        if (!protocolError.isEmpty()) return refuse(409, protocolError);
        Booking.ReserveResult result = BOOK.reserve(fields, blockAt,
                candidate -> request.complete(reserveReply(candidate, fields)));
        Reply winner = request.outcome();
        return winner != null ? winner : reserveReply(result, fields);
    }

    static Reply reserveReply(Booking.ReserveResult result) {
        return new Reply(result.httpCode, "{\"ok\":" + result.ok + ",\"reason\":\"" + result.reason
                + "\",\"reservation_id\":\"" + result.id + "\"}", result.reason);
    }

    static Reply reserveReply(Booking.ReserveResult result, Map<String, String> fields) {
        return new Reply(result.httpCode, "{\"ok\":" + result.ok + ",\"schema\":1,\"reason\":\"" + result.reason
                + "\",\"reservation_id\":" + Json.quote(result.id) + ",\"action_id\":" + Json.quote(fields.getOrDefault("action_id", ""))
                + ",\"nonce\":" + Json.quote(fields.getOrDefault("nonce", "")) + ",\"session_ref\":"
                + Json.quote(fields.getOrDefault("session_ref", ""))
                + (result.ok && CollectionProtocol.VERSION.equals(fields.get("collection_protocol"))
                    ? CollectionProtocol.field() : "") + "}", result.reason);
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
        exchange.getResponseHeaders().set("X-Signature", Hmac.sign(body, CFG.getProperty("token", "")));
        exchange.sendResponseHeaders(code, bytes.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(bytes);
        }
    }
}
