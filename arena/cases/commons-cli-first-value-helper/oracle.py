"""Independent checks of a behavior-preserving extraction in CommandLine; compiles only a disposable copy.

Usage: python3 oracle.py WORKSPACE. Compiles the candidate's main sources with the ``javac`` on PATH
(Commons CLI has no runtime dependencies) and runs the evaluator's own probe against them. The probe
reaches the private helper by reflection, so it compiles against the original code too.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

workspace = Path(sys.argv[1]).resolve()
CHECKS = ["option_value_unchanged", "group_value_unchanged", "defaults_unchanged", "private_first_helper",
          "first_or_null_written_once"]  # fmt: skip

PROBE = r'''
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
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

    public static void main(String[] args) {
        Option file = Option.builder("f").longOpt("file").hasArgs().build();
        Option alpha = Option.builder("a").hasArg().build();
        Option beta = Option.builder("b").hasArg().build();
        OptionGroup group = new OptionGroup().addOption(alpha).addOption(beta);
        Options options = new Options().addOption(file).addOptionGroup(group).addOption(Option.builder("x").build());
        check("option_value_unchanged", () -> {
            CommandLine line = new DefaultParser().parse(options, new String[] {"-f", "one", "-f", "two", "-x"});
            equal(line.getOptionValue(file), "one");
            equal(line.getOptionValue("file"), "one");
            equal(line.getOptionValue(options.getOption("x")), null);
            equal(new DefaultParser().parse(options, new String[] {}).getOptionValue(file), null);
        });
        check("group_value_unchanged", () -> {
            equal(new DefaultParser().parse(options, new String[] {"-b", "bee"}).getOptionValue(group), "bee");
            equal(new DefaultParser().parse(options, new String[] {"-x"}).getOptionValue(group), null);
        });
        check("defaults_unchanged", () -> {
            CommandLine empty = new DefaultParser().parse(options, new String[] {});
            equal(empty.getOptionValue(file, "fallback"), "fallback");
            equal(empty.getOptionValue(group, "dflt"), "dflt");
            equal(empty.getOptionValue("f", () -> "lazy"), "lazy");
        });
        check("private_first_helper", () -> {
            Method first = CommandLine.class.getDeclaredMethod("first", String[].class);
            if (!Modifier.isPrivate(first.getModifiers())) throw new AssertionError("first(String[]) is not private");
            equal(first.getReturnType(), String.class);
            first.setAccessible(true);
            CommandLine line = new DefaultParser().parse(options, new String[] {});
            equal(first.invoke(line, (Object) null), null);
            equal(first.invoke(line, (Object) new String[] {"p", "q"}), "p");
        });
        System.out.println("{\"checks\":[" + String.join(",", results) + "]}");
    }
}
'''


def written_once():
    source = (workspace / "src/main/java/org/apache/commons/cli/CommandLine.java").read_text()
    found = re.findall(r"\?\s*null\s*:\s*\w+\s*\[\s*0\s*\]", source)
    if len(found) > 1:
        return {"name": "first_or_null_written_once", "ok": False, "detail": f"written {len(found)} times"}
    return {"name": "first_or_null_written_once", "ok": True}

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
    result = {"checks": [{"name": name, "ok": False, "detail": detail} for name in CHECKS[:-1]]}
print(json.dumps({"checks": [*result["checks"], written_once()]}))
