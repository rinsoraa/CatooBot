package catoo.attrib;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
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

/** P2 影子模式：只记录证据，不参与任何任务终态。 */
public final class CatooAttributionMod implements ModInitializer {
    static final String VER = "p2-shadow-1";
    static final Booking BOOK = new Booking();
    static final Map<String, String> SESSIONS = new ConcurrentHashMap<>();
    static volatile String lastSessionRef = null;
    static volatile class_3218 worldRef = null;
    static volatile String playerUuid = null;
    static volatile boolean ENABLED = false;
    static Properties CFG = new Properties();
    static final List<String> OUTBOX = Collections.synchronizedList(new ArrayList<>());

    static void log(String line) {
        String text = java.time.Instant.now().toString() + " " + line;
        System.out.println("[catoo-attrib] " + line);
        try (FileWriter w = new FileWriter(new File(System.getProperty(
"user.dir"), "logs/catoobot-attribution.log"), true)) {
            w.write(text + System.lineSeparator());
        } catch (Throwable ignored) {
        }
    }

    static void loadConfig() {
        File file = new File(System.getProperty(
"user.dir"), "config/catoo-attribution.properties");
        if (file.isFile()) {
            try (Reader r = new InputStreamReader(new FileInputStream(file), StandardCharsets.UTF_8)) {
                CFG.load(r);
            } catch (Throwable error) {
                log("CONFIG_ERROR " + error.getClass().getSimpleName());
            }
        }
        ENABLED = Boolean.parseBoolean(CFG.getProperty("enabled", "false"));
    }

    @Override
    public void onInitialize() {
        loadConfig();
        log("INIT mod=catoobot-attribution version=" + VER + " shadow_enabled=" + ENABLED);
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
                long seq = BOOK.newSession(name, uuid, ref);
                SESSIONS.put(name, ref);
                lastSessionRef = ref;
                worldRef = player.method_51469();
                playerUuid = uuid;
                log("JOIN name=" + name + " uuid=" + uuid + " session_ref=" + ref + " session_seq=" + seq);
            } catch (Throwable error) {
                log("JOIN_ERROR " + error.getClass().getName() + ":" + error.getMessage());
            }
        });
        PlayerBlockBreakEvents.AFTER.register((world, player, pos, state, blockEntity) -> {
            try {
                String name = player.method_5477().getString();
                String stateNow;
                try {
                    stateNow = String.valueOf(world.method_8320(pos));
                } catch (Throwable error) {
                    stateNow = "UNKNOWN";
                }
                String posText = pos.method_10263() + "," + pos.method_10264() + "," + pos.method_10260();
                Booking.MatchResult match = BOOK.match(name, posText, String.valueOf(state), String.valueOf(stateNow));
                log("AFTER name=" + name + " pos=" + posText + " state_before=" + state
                        + " state_now=" + stateNow + " shadow=" + match.state + " reason=" + match.reason
                        + " reservation=" + (match.reservation == null ? "null" : match.reservation.id));
                push(match, name, posText, String.valueOf(state), stateNow);
            } catch (Throwable error) {
                log("AFTER_ERROR " + error.getClass().getName() + ":" + error.getMessage());
            }
        });
        if (ENABLED) {
            startHttp();
        }
        log("LISTENER_REGISTERED PlayerBlockBreakEvents.AFTER; ServerPlayConnectionEvents.JOIN");
    }

    static void push(Booking.MatchResult match, String name, String pos, String before, String after) {
        String body = "{\"event_id\":\"" + UUID.randomUUID() + "\",\"player\":\"" + name
                + "\",\"pos\":\"" + pos + "\",\"block_before\":\"" + before.replace("\"", "'")
                + "\",\"block_after\":\"" + after.replace("\"", "'") + "\",\"shadow\":\""
                + match.state + "\",\"reason\":\"" + match.reason + "\",\"reservation_id\":\""
                + (match.reservation == null ? "" : match.reservation.id) + "\",\"session_ref\":\""
                + (lastSessionRef == null ? "" : lastSessionRef) + "\"}";
        String url = CFG.getProperty("bot_url", "");
        if (url.isEmpty()) {
            OUTBOX.add(body);
            return;
        }
        try {
            HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setRequestProperty("X-Signature", Hmac.sign(body, CFG.getProperty("token", "")));
            conn.setConnectTimeout(1500);
            conn.setReadTimeout(1500);
            conn.setDoOutput(true);
            try (OutputStream out = conn.getOutputStream()) {
                out.write(body.getBytes(StandardCharsets.UTF_8));
            }
            int code = conn.getResponseCode();
            if (code >= 300) {
                OUTBOX.add(body);
                log("EVENT_DELIVERY_REJECTED code=" + code);
            }
            conn.disconnect();
        } catch (Throwable error) {
            OUTBOX.add(body);
            log("EVENT_DELIVERY_FAILED " + error.getClass().getSimpleName());
        }
    }

    static void startHttp() {
        try {
            int port = Integer.parseInt(CFG.getProperty("port", "19132"));
            HttpServer server = HttpServer.create(new InetSocketAddress(InetAddress.getByName(
"127.0.0.1"), port), 8);
            server.createContext("/reserve", CatooAttributionMod::handleReserve);
            server.createContext("/health", exchange -> respond(exchange, 200, "{\"ok\":true}"));
            server.start();
            log("HTTP_LISTEN 127.0.0.1:" + port);
        } catch (Throwable error) {
            log("HTTP_START_FAILED " + error.getClass().getSimpleName());
        }
    }

    static void handleReserve(HttpExchange exchange) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) { respond(exchange, 405, "{\"error\":\"method\"}"); return; }
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        String signature = exchange.getRequestHeaders().getFirst("X-Signature");
        if (!Hmac.verify(body, CFG.getProperty("token", ""), signature)) {
            log("RESERVE_REJECTED reason=hmac");
            respond(exchange, 401, "{\"ok\":false,\"reason\":\"hmac\"}"); return;
        }
        Map<String, String> fields = Json.flat(body);
        Booking.ReserveResult result = BOOK.reserve(fields,
                pos -> worldRef == null ? null : String.valueOf(worldRef.method_8320(posOf(fields))),
                SESSIONS::get, lastSessionRef);
        respond(exchange, result.httpCode,
                "{\"ok\":" + result.ok + ",\"reason\":\"" + result.reason + "\",\"reservation_id\":\""
                        + result.reservationId + "\"}");
        log("RESERVE ok=" + result.ok + " reason=" + result.reason + " action=" + fields.get("action_id"));
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
