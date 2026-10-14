package example;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class PageCountTest {
    @Test
    void test_c2_exact_multiple_remains_two_pages() {
        assertEquals(2, PageCount.count(20, 10));
    }
}
