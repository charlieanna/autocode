# Page count

A small Java 17 pagination library. Partial final pages are not counted yet.
Use Maven 3.9.9, JUnit Jupiter 5.11.4 and Surefire 3.5.2 (pinned in pom.xml).
After dependency setup, run:

    mvn -o -B -q -Dtest=PageCountTest test

Surefire writes one report: target/surefire-reports/TEST-example.PageCountTest.xml.
The existing exact-multiple test is a guard; add the requested partial-page case.
