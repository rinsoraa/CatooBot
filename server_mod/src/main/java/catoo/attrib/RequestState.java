package catoo.attrib;

import java.util.Objects;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Function;
import java.util.function.LongSupplier;

/** One outcome wins: cancellation and reservation commit share the same CAS.
 * No lock or game-world access is performed here. A completed outcome is immutable.
 */
final class RequestState<T> {
    private final AtomicReference<T> outcome = new AtomicReference<>();
    private final CountDownLatch settled = new CountDownLatch(1);
    private final Function<String, T> failure;
    private final LongSupplier clock;
    private final long deadline;

    RequestState(long timeoutMs, Function<String, T> failure) {
        this(timeoutMs, failure, System::nanoTime);
    }

    RequestState(long timeoutMs, Function<String, T> failure, LongSupplier clock) {
        this.failure = failure;
        this.clock = clock;
        this.deadline = clock.getAsLong() + TimeUnit.MILLISECONDS.toNanos(timeoutMs);
    }

    boolean isOpen() {
        if (outcome.get() != null) return false;
        if (clock.getAsLong() - deadline >= 0) {
            cancel("reserve_timeout");
            return false;
        }
        return outcome.get() == null;
    }

    boolean complete(T reply) {
        if (!isOpen() || !outcome.compareAndSet(null, Objects.requireNonNull(reply))) return false;
        return true;
    }

    T cancel(String reason) {
        outcome.compareAndSet(null, Objects.requireNonNull(failure.apply(reason)));
        settled.countDown();
        return outcome.get();
    }

    boolean await(long timeoutMs) throws InterruptedException {
        return settled.await(timeoutMs, TimeUnit.MILLISECONDS);
    }

    void finishWork() { settled.countDown(); }

    T outcome() {
        return outcome.get();
    }
}
