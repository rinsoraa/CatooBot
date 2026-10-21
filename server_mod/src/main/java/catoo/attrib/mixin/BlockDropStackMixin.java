package catoo.attrib.mixin;

import catoo.attrib.CatooAttributionMod;
import com.llamalad7.mixinextras.injector.wrapmethod.WrapMethod;
import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import com.llamalad7.mixinextras.injector.wrapoperation.WrapOperation;
import net.minecraft.class_1297;
import net.minecraft.class_1799;
import net.minecraft.class_1937;
import net.minecraft.class_2248;
import net.minecraft.class_2338;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;

/** Binds only vanilla drops emitted inside the currently active break call. */
@Mixin(class_2248.class)
public abstract class BlockDropStackMixin {
    @WrapMethod(method = "method_9577", remap = false)
    private static void catoo$dropScope(class_1937 world, class_2338 pos, class_1799 stack,
            Operation<Void> original) {
        String posText = pos.method_10263() + "," + pos.method_10264() + "," + pos.method_10260();
        boolean entered = CatooAttributionMod.beginCollectionDrop(world, posText);
        try {
            original.call(world, pos, stack);
        } finally {
            CatooAttributionMod.endCollectionDrop(entered);
        }
    }

    @WrapOperation(
            method = "method_36993",
            at = @At(value = "INVOKE",
                    target = "Lnet/minecraft/class_1937;method_8649(Lnet/minecraft/class_1297;)Z"),
            remap = false)
    private static boolean catoo$captureAcceptedSpawn(class_1937 world, class_1297 entity,
            Operation<Boolean> original) {
        boolean accepted = Boolean.TRUE.equals(original.call(world, entity));
        CatooAttributionMod.captureCollectionDrop(world, entity, accepted);
        return accepted;
    }
}
