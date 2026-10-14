package example;

public final class PageCount {
    private PageCount() {}

    public static int count(int items, int pageSize) {
        if (items < 0 || pageSize <= 0) {
            throw new IllegalArgumentException("nonnegative items and positive page size required");
        }
        return items / pageSize;
    }
}
