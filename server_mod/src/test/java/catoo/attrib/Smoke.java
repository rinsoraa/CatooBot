package catoo.attrib;

/** 部署前冒烟：只验证入口类能否被加载并实例化（避免把类加载错误带到服务器上）。 */
public final class Smoke {
    public static void main(String[] args) throws Exception {
        Class<?> type = Class.forName("catoo.attrib.CatooAttributionMod");
        Object instance = type.getDeclaredConstructor().newInstance();
        System.out.println("[smoke] class_load_ok=" + (instance != null));
    }
}
