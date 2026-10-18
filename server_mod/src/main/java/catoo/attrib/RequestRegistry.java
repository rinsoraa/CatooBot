package catoo.attrib;

import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.Set;
import java.util.function.Function;

/** The admission lock is never held during world reads, scheduling or resource shutdown. */
final class RequestRegistry<T> {
    private boolean accepting;
    private Object owner;
    private final Set<RequestState<T>> pending = Collections.newSetFromMap(new IdentityHashMap<>());

    synchronized void open(Object owner) {
        if (accepting || !pending.isEmpty()) throw new IllegalStateException("registry already open");
        accepting = true;
        this.owner = owner;
    }

    synchronized RequestState<T> begin(long timeoutMs, Function<String, T> failure, Object owner) {
        if (!accepting || this.owner != owner) return null;
        RequestState<T> request = new RequestState<>(timeoutMs, failure);
        pending.add(request);
        return request;
    }

    synchronized void finish(RequestState<T> request) { pending.remove(request); }

    synchronized void close() {
        accepting = false;
        for (RequestState<T> request : pending) request.cancel("server_stopping");
        pending.clear();
    }
}
