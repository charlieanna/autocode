"""Independent behavior checks of CommandLine.getOptionCount; compiles only a disposable copy.

Usage: python3 oracle.py WORKSPACE. Compiles the candidate's main sources with the ``javac`` on PATH
(Commons CLI has no runtime dependencies), then runs the evaluator's own probe against them. The probe
reaches the new methods by reflection, so it compiles against the original code too.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

workspace = Path(sys.argv[1]).resolve()
CHECKS = ["counts_repeated_short_option", "counts_by_long_name_and_char", "counts_by_option_object",
          "absent_and_unknown_count_zero", "counts_options_with_values", "existing_queries_unchanged"]  # fmt: skip

PROBE = r'''
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import org.apache.commons.cli.*;

public class ArenaProbe {
    static final List<String> results = new ArrayList<>();

    interface Body { void run() throws Exception; }

    static void check(String name, Body body) {
        try {
            body.run();
            results.add("{\"name\":\"" + name + "\",\"ok\":true}");
        } catch (Throwable error) {
            Throwable cause = error.getCause() != null && error instanceof java.lang.reflect.InvocationTargetException ? error.getCause() : error;
            String detail = (cause.getClass().getSimpleName() + ": " + cause.getMessage()).replace("\\", "\\\\").replace("\"", "'");
            results.add("{\"name\":\"" + name + "\",\"ok\":false,\"detail\":\"" + detail + "\"}");
        }
    }

    static void equal(Object actual, Object expected) {
        if (!java.util.Objects.equals(actual, expected)) throw new AssertionError(actual + " != " + expected);
    }

    static int count(CommandLine line, Class<?> type, Object key) throws Exception {
        Method method = CommandLine.class.getMethod("getOptionCount", type);
        if (method.getReturnType() != int.class) throw new AssertionError("getOptionCount returns " + method.getReturnType());
        return (Integer) method.invoke(line, key);
    }

    static Options options() {
        Options options = new Options();
        options.addOption(Option.builder("v").longOpt("verbose").desc("more output").build());
        options.addOption(Option.builder("D").hasArgs().valueSeparator('=').build());
        options.addOption(Option.builder("q").longOpt("quiet").build());
        options.addOption(Option.builder("f").longOpt("file").hasArg().build());
        return options;
    }

    static CommandLine parse(String... args) throws ParseException {
        return new DefaultParser().parse(options(), args);
    }

    public static void main(String[] args) {
        check("counts_repeated_short_option", () -> {
            equal(count(parse("-v", "-v", "-v"), String.class, "v"), 3);
            equal(count(parse("-vvv"), String.class, "v"), 3);
        });
        check("counts_by_long_name_and_char", () -> {
            CommandLine line = parse("-v", "--verbose", "x");
            equal(count(line, String.class, "verbose"), 2);
            equal(count(line, char.class, 'v'), 2);
        });
        check("counts_by_option_object", () -> {
            Options options = options();
            CommandLine line = new DefaultParser().parse(options, new String[] {"-q", "--quiet", "-q"});
            equal(count(line, Option.class, options.getOption("q")), 3);
        });
        check("absent_and_unknown_count_zero", () -> {
            CommandLine line = parse("-v");
            equal(count(line, String.class, "q"), 0);
            equal(count(line, String.class, "nope"), 0);
            equal(count(line, char.class, 'z'), 0);
        });
        check("counts_options_with_values", () -> {
            CommandLine line = parse("-f", "a", "--file=b", "-Dx=1", "-Dy=2");
            equal(count(line, String.class, "f"), 2);
            equal(count(line, String.class, "D"), 2);
        });
        check("existing_queries_unchanged", () -> {
            CommandLine line = parse("-v", "-f", "a", "-f", "b", "rest");
            equal(line.hasOption("v"), true);
            equal(line.getOptionValue("f"), "a");
            equal(String.join(",", line.getOptionValues("file")), "a,b");
            equal(line.getOptions().length, 3);
            equal(String.join(",", line.getArgList()), "rest");
        });
        System.out.println("{\"checks\":[" + String.join(",", results) + "]}");
    }
}
'''

with tempfile.TemporaryDirectory(prefix="arena-cli-") as folder:
    root = Path(folder)
    classes = root / "classes"
    sources = sorted(str(path) for path in (workspace / "src/main/java").rglob("*.java"))
    (root / "ArenaProbe.java").write_text(PROBE)
    steps = [
        ["javac", "-nowarn", "-encoding", "UTF-8", "-d", str(classes), *sources],
        ["javac", "-encoding", "UTF-8", "-cp", str(classes), "-d", str(classes), str(root / "ArenaProbe.java")],
        ["java", "-cp", str(classes), "ArenaProbe"],
    ]
    for step in steps:
        done = subprocess.run(step, cwd=root, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
        if done.returncode != 0:
            break
try:
    result = json.loads(done.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    detail = (done.stderr or done.stdout)[-400:]
    result = {"checks": [{"name": name, "ok": False, "detail": detail} for name in CHECKS]}
print(json.dumps(result))
