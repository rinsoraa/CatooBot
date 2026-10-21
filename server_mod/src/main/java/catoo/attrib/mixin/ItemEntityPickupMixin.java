package catoo.attrib.mixin;

import catoo.attrib.CatooAttributionMod;
import com.llamalad7.mixinextras.injector.wrapmethod.WrapMethod;
import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import net.minecraft.class_1542;
import net.minecraft.class_1657;
import net.minecraft.class_1799;
import net.minecraft.class_1792;
import org.spongepowered.asm.mixin.Mixin;

/** Observes exact player pickup and inventory delta; it never alters the vanilla pickup. */
@Mixin(class_1542.class)
public abstract class ItemEntityPickupMixin {
    @WrapMethod(method = "method_5694", remap = false)
    private void catoo$collectionPickup(class_1657 player, Operation<Void> original) {
        class_1542 entity = (class_1542) (Object) this;
        class_1799 stack = null;
        class_1792 item = null;
        int before = -1;
        int inventoryBefore = -1;
        try {
            stack = entity.method_6983();
            item = stack == null ? null : stack.method_7909();
            before = stack == null ? -1 : stack.method_7947();
            inventoryBefore = item == null ? -1
                    : CatooAttributionMod.collectionInventoryCount(player, item);
        } catch (Throwable ignored) {
            // Missing facts stay negative so they cannot look like an empty baseline.
        }
        original.call(player);
        try {
            // Vanilla restores the removed stack's original count for statistics.
            // A discarded entity has zero remaining world items, even when that
            // detached ItemStack reports its pre-pickup count again.
            int after = entity.method_31481() ? 0 : entity.method_6983().method_7947();
            int inventoryAfter = item == null ? -1
                    : CatooAttributionMod.collectionInventoryCount(player, item);
            if (after == before && inventoryAfter == inventoryBefore) return;
            CatooAttributionMod.recordCollectionPickup(entity, player, item, before, after,
                    inventoryBefore, inventoryAfter);
        } catch (Throwable ignored) {
            CatooAttributionMod.logCollectionPickupFailure(entity);
        }
    }

    @WrapMethod(method = "method_18006", remap = false)
    private static void catoo$collectionMerge(class_1542 first, class_1799 firstStack,
            class_1542 second, class_1799 secondStack, Operation<Void> original) {
        CatooAttributionMod.recordCollectionMerge(first, second);
        original.call(first, firstStack, second, secondStack);
    }
}
