package catoo.attrib.mixin;

import catoo.attrib.CatooAttributionMod;
import com.llamalad7.mixinextras.injector.wrapmethod.WrapMethod;
import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import net.minecraft.class_2338;
import net.minecraft.class_3218;
import net.minecraft.class_3222;
import net.minecraft.class_3225;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;

/** Captures the exact lifetime of one server-side player block break call. */
@Mixin(class_3225.class)
public abstract class ServerPlayerInteractionManagerMixin {
    @Shadow protected class_3218 field_14007;
    @Shadow protected class_3222 field_14008;

    @WrapMethod(method = "method_14266", remap = false)
    private boolean catoo$collectionBreak(class_2338 pos, Operation<Boolean> original) {
        var frame = CatooAttributionMod.beginCollectionBreak(field_14007, field_14008, pos);
        boolean broken = false;
        try {
            broken = Boolean.TRUE.equals(original.call(pos));
            return broken;
        } finally {
            CatooAttributionMod.finishCollectionBreak(frame, broken);
        }
    }
}
