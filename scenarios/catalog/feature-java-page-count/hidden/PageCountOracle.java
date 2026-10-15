import example.PageCount;

public final class PageCountOracle {
    private static void expect(int expected, int items, int size) {
        int actual = PageCount.count(items, size);
        if (actual != expected) throw new AssertionError(items + "/" + size + ": " + actual + " != " + expected);
    }

    public static void main(String[] args) {
        expect(0, 0, 10);
        expect(1, 1, 10);
        expect(2, 20, 10);
        expect(3, 21, 10);
        expect(3, 29, 10);
        expect(1073741824, Integer.MAX_VALUE, 2);
        for (int[] input : new int[][]{{-1, 10}, {1, 0}, {1, -1}}) {
            try {
                PageCount.count(input[0], input[1]);
                throw new AssertionError("invalid input accepted");
            } catch (IllegalArgumentException expected) {
                // The public contract requires this exact exception category.
            }
        }
        System.out.println("all hidden Java cases passed");
    }
}
