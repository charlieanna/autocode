Add partial-page counting to this Java pagination library. A positive remainder
needs one final page: 21 items with a page size of 10 needs 3 pages. Preserve
existing exact-multiple behavior: 20 items with a page size of 10 needs 2 pages.
Empty input needs zero pages. Continue refusing negative item counts and
nonpositive page sizes with IllegalArgumentException. Avoid overflow for large
valid int inputs.

Keep the project's Maven/JUnit setup and its existing exact-multiple guard.
Add a real JUnit regression named test_c1_partial_page_is_counted. Keep the
existing test_c2_exact_multiple_remains_two_pages guard. Run the one PageCountTest
class; Surefire writes target/surefire-reports/TEST-example.PageCountTest.xml.
