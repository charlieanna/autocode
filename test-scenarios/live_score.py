#!/usr/bin/env python3
"""Score implementation/test mutants against a LIVE model Validator.

Uses `opencode run` (real credentials) as an independent Validator on the
mutant tree. Same mutants and same diagnosis regexes as scenarios 09/11.
This measures model judgment, not runner plumbing (that is the offline suite).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MODEL = os.environ.get("LIVE_MODEL", "zai-coding-plan/glm-5.3")
TIMEOUT = int(os.environ.get("LIVE_TIMEOUT", "180"))

IMPL = {
    "offbyone_empty": (
        "total([]) returns 1 instead of 0",
        r"empty|off.by.one|sum\(items, *[1-9]|default|total\(\[\]\)|returns? 1|start value",
        "def total(items):\n    return sum(items, 1)\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return amount * 0.9 if amount > 100 else amount\n",
    ),
    "boundary_ge": (
        "discount() uses >= instead of >",
        r"boundar|exactly 100|>=|discount\(100\)|off.by.one|inclusive",
        "def total(items):\n    for i in items:\n"
        "        if not isinstance(i, (int, float)):\n            raise TypeError(\"non-numeric\")\n"
        "    return sum(items)\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return amount * 0.9 if amount >= 100 else amount\n",
    ),
    "typeerror_swallow": (
        "non-numeric input returns 0 instead of raising TypeError",
        r"TypeError|not raised|swallow|except|non.numeric|silently",
        "def total(items):\n    t = 0\n    for i in items:\n"
        "        try:\n            t += i\n        except TypeError:\n            return 0\n"
        "    return t\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return amount * 0.9 if amount > 100 else amount\n",
    ),
    "inverted_discount": (
        "discount() condition inverted",
        r"invert|wrong (side|direction)|small order|under 100|discount\(10|condition|exclusiv",
        "def total(items):\n    for i in items:\n"
        "        if not isinstance(i, (int, float)):\n            raise TypeError(\"non-numeric\")\n"
        "    return sum(items)\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return amount * 0.9 if amount < 100 else amount\n",
    ),
    "rounded_discount": (
        "discount() rounds to whole currency",
        r"round|precision|90\.9|decimal|float|truncat|91",
        "def total(items):\n    for i in items:\n"
        "        if not isinstance(i, (int, float)):\n            raise TypeError(\"non-numeric\")\n"
        "    return sum(items)\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return round(amount * 0.9, 0) if amount > 100 else amount\n",
    ),
    "negative_allowed": (
        "negative amounts accepted instead of raising ValueError",
        r"ValueError|negative|not raised|missing.*(raise|check)",
        "def total(items):\n    for i in items:\n"
        "        if not isinstance(i, (int, float)):\n            raise TypeError(\"non-numeric\")\n"
        "    return sum(items)\n\ndef discount(amount):\n"
        "    return amount * 0.9 if amount > 100 else amount\n",
    ),
    "string_concat": (
        "total() concatenates string prices",
        r"concatenat|string|type coerc|\"\\+\"|join|str",
        "def total(items):\n    out = 0\n    for i in items:\n"
        "        if isinstance(out, (int, float)) and isinstance(i, (int, float)):\n"
        "            out += i\n        else:\n            out = str(out) + str(i)\n"
        "    return out\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return amount * 0.9 if amount > 100 else amount\n",
    ),
}

CORRECT = (
    "def total(items):\n    for i in items:\n"
    "        if not isinstance(i, (int, float)):\n            raise TypeError(\"non-numeric\")\n"
    "    return sum(items)\n\ndef discount(amount):\n"
    "    if amount < 0:\n        raise ValueError(\"negative\")\n"
    "    return amount * 0.9 if amount > 100 else amount\n"
)

SHALLOW = (
    "from cart import total, discount\n\n"
    "def test_total_smoke():\n    total([1, 2, 3])\n    total([10])\n    total([1])\n\n"
    "def test_discount_smoke():\n    discount(50)\n    discount(100)\n    discount(101)\n    discount(200)\n\n"
    "def test_no_crash():\n    total([0])\n    discount(1)\n"
)

STRONG = (
    "import pytest\nfrom cart import total, discount\n\n"
    "def test_total_empty():\n    assert total([]) == 0\n\n"
    "def test_total_sums():\n    assert total([1, 2, 3]) == 6\n\n"
    "def test_total_non_numeric_raises():\n"
    "    with pytest.raises(TypeError):\n        total([1, \"2\", 3])\n\n"
    "def test_discount_below_threshold():\n    assert discount(50) == 50\n\n"
    "def test_discount_at_boundary():\n    assert discount(100) == 100\n\n"
    "def test_discount_above_threshold():\n    assert discount(101) == 90.9\n\n"
    "def test_discount_negative_raises():\n"
    "    with pytest.raises(ValueError):\n        discount(-1)\n"
)

TEST_MUT = {
    "drop_empty_case": ({"R1"}, lambda s: re.sub(r"\n?def test_total_empty\(.*?(?=\ndef |\Z)", "\n", s, flags=re.S)),
    "drop_boundary_case": ({"R4"}, lambda s: re.sub(r"\n?def test_discount_at_boundary\(.*?(?=\ndef |\Z)", "\n", s, flags=re.S)),
    "drop_negative_case": ({"R6"}, lambda s: re.sub(r"\n?def test_discount_negative_raises\(.*?(?=\ndef |\Z)", "\n", s, flags=re.S)),
    "weaken_type_check": ({"R3"}, lambda s: re.sub(
        r"(\ndef test_total_non_numeric_raises\(.*?:\n)(.*?)(?=\ndef |\Z)",
        r"\1    total([1, 2, 3])\n", s, flags=re.S)),
    "weaken_assertion": ({"R5"}, lambda s: re.sub(
        r"(\ndef test_discount_above_threshold\(.*?:\n)(.*?)(?=\ndef |\Z)",
        r"\1    assert discount(101) is not None\n", s, flags=re.S)),
    "vacuous_placeholder": ({"R1"}, lambda s: re.sub(
        r"(\ndef test_total_empty\(.*?:\n)(.*?)(?=\ndef |\Z)",
        r"\1    assert True\n", s, flags=re.S)),
    "keep_only_one": (
        {"R1", "R3", "R4", "R5", "R6"},
        lambda s: "\n".join(
            [ln for ln in s.splitlines() if ln.startswith(("import", "from", "def test_total_sums", "    assert total([1, 2, 3])"))
             or ln == "" or ln.startswith("    assert total([1, 2, 3])")]
        ) + "\n",
    ),
}

REQ_HINT = {
    "R1": r"empty|total\(\[\]\)|sum.*default|assert True|placeholder",
    "R2": r"1, *2, *3|sums",
    "R3": r"TypeError|non.numeric",
    "R4": r"boundar|exactly 100|discount\(100",
    "R5": r"90\.9|discount\(101|round|precision|above threshold",
    "R6": r"ValueError|negative",
}

PROMPT = """You are an independent Validator for a Python module. Do NOT modify cart.py or tests/.

Spec:
- total(items) sums numeric prices: total([])==0, total([1,2,3])==6, non-numeric raises TypeError
- discount(amount) is 10% off orders over 100: discount(100)==100 (exclusive boundary), discount(101)==90.9, negative raises ValueError

Inspect cart.py and tests/. Run checks yourself (you may execute Python/pytest).
Also state, for each spec clause, whether the TEST SUITE proves it (not just whether the code is correct).

Return ONLY JSON:
{"verdict":"PASS"|"FAIL","findings":[{"finding":"one line naming the actual defect or the unproven requirement in plain language"}]}
Name defects concretely (empty-list/off-by-one, boundary inclusive, TypeError swallowed, rounding, inverted condition, ValueError missing, type coercion/string concat, requirement Rn unproven, etc.).
"""


def setup(root: Path, name: str, code: str, tests: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "cart.py").write_text(code)
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_cart.py").write_text(tests)
    return repo


def run_validator(repo: Path) -> tuple[str, str]:
    try:
        p = subprocess.run(
            ["opencode", "run", "--dir", str(repo), "--format", "default",
             "--model", MODEL, "--title", "live-validator", PROMPT],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT"
    return p.stdout, p.stderr


def extract(out: str) -> tuple[str, list[str]]:
    verdict = ""
    findings = []
    m = re.search(r"\{[\s\S]*\"verdict\"[\s\S]*\}", out)
    blob = m.group(0) if m else out
    vm = re.search(r"\"verdict\"\s*:\s*\"(PASS|FAIL)\"", blob, re.I)
    if vm:
        verdict = vm.group(1).upper()
    for fm in re.finditer(r"\"finding\"\s*:\s*\"((?:\\.|[^\"\\])*)\"", blob):
        findings.append(bytes(fm.group(1), "utf-8").decode("unicode_escape"))
    if not findings:
        findings = [ln.strip() for ln in out.splitlines() if ln.strip()][:8]
    return verdict, findings


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="live-score-"))
    print(f"live model={MODEL}  workdir={root}\n")
    rows = []

    def record(name, result, verdict, findings):
        rows.append((name, result, verdict))
        print(f"{name:22s} {result:18s} verdict={verdict}")
        for f in findings[:4]:
            print(f"    - {f[:160]}")
        print()

    # --- 09 implementation mutants ---
    print("======== 09 implementation mutants ========")
    for name, (bug, symptom, code) in IMPL.items():
        repo = setup(root, f"impl-{name}", code, SHALLOW)
        out, err = run_validator(repo)
        verdict, findings = extract(out)
        blob = "\n".join(findings)
        if verdict == "FAIL" and re.search(symptom, blob, re.I):
            record(name, "CAUGHT+DIAGNOSED", verdict, findings)
        elif verdict == "FAIL":
            record(name, "CAUGHT-VAGUE", verdict, findings)
        else:
            record(name, "MISSED", verdict or "?", findings)

    # control: correct code
    repo = setup(root, "impl-control", CORRECT, SHALLOW)
    out, _ = run_validator(repo)
    verdict, findings = extract(out)
    if verdict == "FAIL":
        record("control", "FALSE-ALARM", verdict, findings)
    else:
        record("control", "ACCEPTED-OK", verdict or "PASS", findings)

    # --- 11 test-suite mutants ---
    print("======== 11 test-suite mutants ========")
    for name, (gaps, fn) in TEST_MUT.items():
        tests = fn(STRONG)
        repo = setup(root, f"test-{name}", CORRECT, tests)
        out, _ = run_validator(repo)
        verdict, findings = extract(out)
        blob = "\n".join(findings)
        named = [g for g in sorted(gaps) if re.search(REQ_HINT[g], blob, re.I)]
        if verdict != "FAIL" and not named:
            record(name, "MISSED", verdict or "?", findings)
        elif not named:
            record(name, "REJECT-VAGUE", verdict, findings)
        elif set(named) >= set(gaps):
            record(name, "CAUGHT+PRECISE", verdict, findings)
        else:
            record(name, f"CAUGHT-PARTIAL({'/'.join(named)})", verdict, findings)

    repo = setup(root, "test-control", CORRECT, STRONG)
    out, _ = run_validator(repo)
    verdict, findings = extract(out)
    if verdict == "FAIL":
        record("control", "FALSE-ALARM", verdict, findings)
    else:
        record("control", "ACCEPTED-OK", verdict or "PASS", findings)

    print("======== SUMMARY ========")
    caught = sum(1 for _, r, _ in rows if r.startswith("CAUGHT") or r == "ACCEPTED-OK" or r == "FALSE-ALARM" or r == "MISSED")
    print(f"model={MODEL}")
    for n, r, v in rows:
        print(f"  {n:22s} {r}")
    impl = [r for n, r, _ in rows if n in IMPL or n == "control"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
