"""Seeded hidden assumptions: were the code's two product forks put to the person, and did their
decisions (against the code's grain) reach the delivered `envfile merge`, without asking about the
repository's own conventions or AutoCode's machinery?

File checks (also in `check` mode): the standard Python change checks, the hidden tests split into the
person's decisions and the repository's conventions, and the README. Run checks: each fork was asked
as a question or listed in Requirements' confirmation before any plan, no question was about a
repository convention or the machinery (tests, proofs, commands), and a bounded question count.
"""

import re
import shutil
import sys

from harness.oracle import Check, python_change_checks, run, run_checks, scratch_copy, tail

HIDDEN = "test_hidden_merge"
# The two forks, by what a question about them says (the same topics as scenario.toml [person]).
FORKS = {
    "duplicate_keys": r"duplicat|repeated|same key|more than one (input )?file|several files|multiple files|"
    r"conflict|overrid|precedence|which (file|value)|(first|last|later) (file|value)",
    "empty_input": r"\bempty\b|no entries|without entries|nothing to merge|zero entries|no keys",
}
# Facts the repository settles (show's --sorted, envfile.errors.fail) and AutoCode's own machinery.
NOISE = re.compile(
    r"--sort|\bflag\b|\boption name\b|\bspell(ing)?\b|\bnaming\b|\berror (message|format|helper|style|output)\b|"
    r"\bstderr\b|\bfail\(\)|errors\.py|\bexit code 2\b|\btest (command|runner|framework|file|suite)\b|"
    r"\bunittest\b|\bpytest\b|\bregression\b|\bproof\b|\bguard\b|\brun the tests\b|\bwhich tests\b|"
    r"\breadme (format|section|style)\b|\bstandard library\b|\bdependenc",
    re.I,
)
# Stages that come after Requirements: a fork first raised there was not cleared before planning.
AFTER_REQUIREMENTS = {
    "draft_plan",
    "review_plan",
    "revise_plan",
    "finalize_plan",
    "builder",
    "tester",
    "review_completion",
    "orchestrator",
    "plan_task",
}
MAX_QUESTIONS = 5


def hidden_class(copy, scenario, name):
    target = copy / "_oracle_hidden"
    if not target.exists():
        shutil.copytree(scenario.dir / "hidden", target)
        (target / "__init__.py").touch()
    return run([sys.executable, "-m", "unittest", f"_oracle_hidden.{HIDDEN}.{name}"], copy, timeout=300)


def file_checks(project, scenario):
    checks = python_change_checks(project, scenario, package="envfile")
    with scratch_copy(project) as copy:
        for name, label in (
            ("PersonDecisionTests", "person_decisions_in_built_behavior"),
            ("ConventionTests", "repository_conventions_followed"),
        ):
            result = hidden_class(copy, scenario, name)
            checks.append(Check(label, result.returncode == 0, tail(result)))
    readme = project / "README.md"
    checks.append(Check("readme_documents_merge", readme.is_file() and "merge" in readme.read_text()))
    return checks


def _text(row):
    return " ".join(str(row.get(key) or "") for key in ("question", "why"))


def run_level_checks(run_record):
    answers = run_record.get("answers") or []
    questions = [row for row in answers if row.get("source") != "approval" and not row.get("requirements_review")]
    corrections = [row for row in answers if row.get("requirements_review")]
    confirmations = [row for row in run_record.get("approvals") or [] if row.get("gate") == "approve_requirements"]
    checks = []
    for fork, pattern in FORKS.items():
        asked = [row for row in questions if re.search(pattern, _text(row), re.I)]
        early = [
            row
            for row in asked
            if row.get("stages_before_answer") and not (set(row["stages_before_answer"]) & AFTER_REQUIREMENTS)
        ]
        listed = [row for row in [*corrections, *confirmations] if re.search(pattern, _text(row), re.I)]
        overrode = [
            row
            for row in early
            if row.get("source") == "person"
            and row.get("proposed_default")
            and row["answer"] != row["proposed_default"]
        ]
        checks.append(
            Check(
                f"fork_cleared_with_person: {fork}",
                bool(early or listed),
                f"asked {len(asked)} ({len(early)} before planning, {len(overrode)} overriding the proposed default), "
                f"listed in {len(listed)} Requirements confirmation(s)"
                + (f"; first: {asked[0].get('question')!r} -> {asked[0].get('answer')!r}" if asked else ""),
            )
        )
    noisy = [row.get("question") for row in questions if NOISE.search(str(row.get("question") or ""))]
    checks.append(
        Check(
            "no_convention_or_machinery_questions",
            not noisy,
            f"asked: {noisy}" if noisy else f"{len(questions)} question(s), none about conventions or machinery",
        )
    )
    checks.append(
        Check(
            "question_count_bounded",
            len(questions) <= MAX_QUESTIONS,
            f"asked {len(questions)}, allowed {MAX_QUESTIONS}",
        )
    )
    return checks


def check(project, scenario, run_record=None):
    checks = file_checks(project, scenario)
    if run_record is None:
        return checks
    return checks + run_level_checks(run_record) + run_checks(run_record, workflow="build", plan_approved=True)
