package catoo.attrib;

import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.*;

/** Actual mod listener: signed session challenges, malformed bodies and failure responses. */
public final class ProtocolBridgeTest {
    static int passed;
    static final String TOKEN = "test-only-protocol-secret-0123456789";
    static void check(boolean value, String label) {
        if (!value) throw new AssertionError(label);
        passed++; System.out.println("  OK   " + label);
    }
    record Response(int code, String body, String signature) {}
    static Response call(int port, String route, String body, String signed, boolean bad) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) URI.create("http://127.0.0.1:" + port + route).toURL().openConnection();
        connection.setConnectTimeout(1000); connection.setReadTimeout(1000);
        connection.setRequestProperty("X-Signature", Hmac.sign(signed, bad ? "wrong" : TOKEN));
        if (body != null) {
            connection.setRequestMethod("POST"); connection.setDoOutput(true);
            try (var out = connection.getOutputStream()) { out.write(body.getBytes(StandardCharsets.UTF_8)); }
        }
        try {
            int code = connection.getResponseCode();
            var input = code >= 400 ? connection.getErrorStream() : connection.getInputStream();
            String response;
            try (input) { response = new String(input.readAllBytes(), StandardCharsets.UTF_8); }
            String signature = connection.getHeaderField("X-Signature");
            check(Hmac.verify(response, TOKEN, signature), "response HMAC validates status " + code);
            return new Response(code, response, signature);
        } finally { connection.disconnect(); }
    }
    public static void main(String[] args) throws Exception {
        int port;
        try (var socket = new ServerSocket(0)) { port = socket.getLocalPort(); }
        CatooAttributionMod.CFG.setProperty("port", Integer.toString(port));
        CatooAttributionMod.CFG.setProperty("token", TOKEN);
        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            CatooAttributionMod.startRuntime(executor, executor.submit(Thread::currentThread).get());
            String ref = UUID.randomUUID().toString(), uuid = UUID.randomUUID().toString();
            Object player = new Object();
            CatooAttributionMod.BOOK.newSession("ProtoBot", uuid, ref, new Object());
            CatooAttributionMod.BOOK.attachPlayer(ref, player);
            String nonce = UUID.randomUUID().toString();
            var session = call(port, "/session?name=ProtoBot&nonce=" + nonce, null, "ProtoBot\n" + nonce, false);
            check(session.code == 200 && session.body.contains("\"schema\":1") && session.body.contains(nonce)
                    && session.body.contains(ref) && session.body.contains(uuid), "signed session echoes challenge and current connection identity");
            check(session.body.contains(CollectionProtocol.field()), "active signed session advertises collection protocol");
            for (String version : new String[]{"old", "", "future"}) {
                String invalid = "{\"x\":1,\"y\":64,\"z\":3,\"pos\":\"1,64,3\",\"collection_protocol\":" + Json.quote(version) + "}";
                var response = call(port, "/reserve", invalid, invalid, false);
                check(response.code == 409 && response.body.contains("collection_protocol_unsupported")
                        && CatooAttributionMod.BOOK.activeReservations() == 0, "HTTP unknown collection contract refused before world access");
            }
            var missingPickup = call(port, "/collection/pickup-reserve", "{}", "{}", false);
            check(missingPickup.code == 409 && missingPickup.body.contains("collection_protocol_required"), "HTTP pickup cannot omit capability contract");
            for (String invalid : new String[]{"{\"collection_protocol\":1}", "{\"collection_protocol\":true}"}) {
                check(call(port, "/reserve", invalid, invalid, false).code == 400, "HTTP protocol field preserves string type");
            }
            var fields = java.util.Map.of("session_ref", ref, "action_id", "negotiated", "nonce", "negotiated-nonce-0123456", "dimension", "minecraft:overworld", "pos", "1,64,3", "expected_block", "minecraft:oak_log", "collection_protocol", CollectionProtocol.VERSION);
            var ready = CatooAttributionMod.runOnServerThread(request -> CatooAttributionMod.reserveForRequest(fields, f -> "minecraft:oak_log", request), 1000);
            check(ready.code == 200 && ready.body.contains(CollectionProtocol.field()), "real reservation commit acknowledges exact requested protocol");
            CatooAttributionMod.BOOK.invalidateAll();
            CatooAttributionMod.BOOK.newSession("ProtoBot", uuid, ref, new Object());
            CatooAttributionMod.BOOK.attachPlayer(ref, player);
            check(call(port, "/session?name=ProtoBot&nonce=" + nonce, null, "ProtoBot", false).code == 401,
                    "old request signature cannot authenticate a new challenge");
            String[] badBodies = {"[]", "{\"x\":true}", "{\"x\":1,\"x\":2}", "{\"x\":\"1\"}",
                "{\"x\":1,\"y\":64,\"z\":3,\"pos\":\"999,64,3\"}", " ".repeat(8193)};
            for (String body : badBodies) {
                var response = call(port, "/reserve", body, body, false);
                check(response.code == (body.length() > 8192 ? 413 : 400)
                        && CatooAttributionMod.BOOK.activeReservations() == 0,
                        "malformed/oversized/contradictory request fails before world access");
            }
            String body = "{}";
            check(call(port, "/reserve", body, body, true).code == 401, "bad request HMAC never creates a reservation");
            CatooAttributionMod.COLLECTIONS.close();
            var closed = call(port, "/session?name=ProtoBot&nonce=" + nonce, null, "ProtoBot\n" + nonce, false);
            check(!closed.body.contains("collection_protocol"), "closed ledger does not advertise collection capability");
            var unavailable = CatooAttributionMod.runOnServerThread(request -> CatooAttributionMod.reserveForRequest(fields, f -> { throw new AssertionError("closed ledger must not read world"); }, request), 1000);
            check(unavailable.code == 409 && unavailable.body.contains("collection_protocol_unavailable"), "closed ledger cannot acknowledge late collection reservation");
            CatooAttributionMod.BOOK.disconnect("ProtoBot", uuid, player);
            check(call(port, "/session?name=ProtoBot&nonce=" + nonce, null, "ProtoBot\n" + nonce, false).code == 404,
                    "disconnected identity cannot be reacquired from session endpoint");
        } finally { CatooAttributionMod.stopRuntime(executor); executor.shutdownNow(); }
        System.out.println("[protocol-bridge-test] " + passed + "/" + passed + " checks passed");
    }
}
