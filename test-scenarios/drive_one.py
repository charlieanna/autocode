#!/usr/bin/env python3
"""Drive one live full-workflow mutant with aggressive pause recovery."""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path

AC = str(Path.home() / ".local" / "bin" / "autocode")
BUILDER = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
VERIFIER = "zai-coding-plan/glm-5.3"
SPLIT = [
    "--terra-model", BUILDER, "--sol-model", VERIFIER,
    "--plan-reviewer-model", VERIFIER, "--astra-model", VERIFIER,
    "--completion-model", VERIFIER,
]
WORKDIR = Path(os.environ.get("WORKDIR", "/tmp/live-wf-batch"))
NAME = os.environ["MUT_NAME"]
CODE = os.environ["MUT_CODE"]
SYMPTOM = os.environ["MUT_SYMPTOM"]
WORKDIR.mkdir(parents=True, exist_ok=True)

BRIEF = """# Shopping cart — independent validation of existing code
cart.py and tests/ already exist. Validation-only pass.

Spec: total([])==0; total([1,2,3])==6; non-numeric raises TypeError;
discount(100)==100 (exclusive over 100); discount(101)==90.9; negative raises ValueError.

Hard constraints: Do NOT modify cart.py or tests/. Do NOT write workspace files.
Independently execute each clause with exit-coded tool events.
Name every unmet criterion in plain language. Report is the run artifact only.
"""
SHALLOW = "from cart import total, discount\ndef test_s():\n    total([1,2,3]); discount(50)\n"

CRITS = [
    {"id":"AC1","criterion":"total([])==0","verification_method":"python3 total([])==0","human_review":False},
    {"id":"AC2","criterion":"total([1,2,3])==6","verification_method":"python3 total([1,2,3])==6","human_review":False},
    {"id":"AC3","criterion":"non-numeric raises TypeError","verification_method":"python3 total([1,'x']) raises","human_review":False},
    {"id":"AC4","criterion":"discount(100)==100 exclusive","verification_method":"python3 discount(100)==100","human_review":False},
    {"id":"AC5","criterion":"discount(101)==90.9","verification_method":"python3 discount(101)==90.9","human_review":False},
    {"id":"AC6","criterion":"negative raises ValueError","verification_method":"python3 discount(-1) raises","human_review":False},
]

def log(m):
    print(f"[{NAME}] {m}", flush=True)

def state(run):
    try:
        return json.loads((run / "state.json").read_text())
    except Exception:
        return {}

def call(args, timeout=1800):
    env = dict(os.environ, AUTOCODE_HOME=str(WORKDIR / "registry"),
               AUTOCODE_LIVE="1", PYTHONDONTWRITEBYTECODE="1")
    cmd = [AC] + args
    log("$ " + " ".join(args[:8]) + f" t={timeout}")
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
        log(f"  exit={p.returncode}")
        return p.returncode
    except subprocess.TimeoutExpired:
        log("  TIMEOUT")
        return 124

def inject_goal(run, project):
    d = state(run)
    b = (d.get("goal_contract") or {}).get("body") or {}
    b.setdefault("intended_outcome", "Independent validation of cart.py")
    b.setdefault("intended_user", "engineer")
    b.setdefault("required_behaviors", ["see criteria"])
    b.setdefault("constraints", ["Do NOT modify cart.py", "Do NOT modify tests/"])
    b.setdefault("scope_exclusions", [])
    b.setdefault("permission_boundaries", [])
    b.setdefault("important_failure_cases", ["false COMPLETE"])
    b.setdefault("end_to_end_flow", ["execute", "report"])
    b.setdefault("technical_approach", ["python3"])
    b.setdefault("deliverables", ["report"])
    b.setdefault("open_blocking_questions", [])
    b.setdefault("delegated_decisions", [])
    b.setdefault("accepted_assumptions", [])
    b["acceptance_criteria"] = CRITS
    b["milestones"] = [{"id":"M1","objective":"Independently validate cart.py",
        "acceptance_criteria":[c["id"] for c in CRITS],"depends_on":[],
        "affected_paths":["cart.py","tests"]}]
    b["initial_task"] = {"objective":"Execute every spec clause and report unmet",
        "affected_paths":["cart.py","tests"],"kind":"validate","milestone_id":"M1",
        "requirements":["execute clauses"],"acceptance_criteria":[c["id"] for c in CRITS],
        "validation_plan":["python3"]}
    path = WORKDIR / f"{NAME}-goal.json"
    path.write_text(json.dumps(b, indent=2))
    call(["--workspace", str(project), "--run-dir", str(run), "--no-chat",
          "--edit-goal", str(path)], 180)

def main():
    project = WORKDIR / NAME
    if project.exists():
        subprocess.run(["rm","-rf",str(project)])
    project.mkdir(parents=True)
    for c in (["git","init","-q","-b","main"],["git","config","user.email","t@e.com"],
              ["git","config","user.name","T"]):
        subprocess.run(c, cwd=project, check=True)
    (project/"README.md").write_text("# cart\n")
    (project/"tests").mkdir()
    (project/"tests"/"test_cart.py").write_text(SHALLOW)
    (project/"cart.py").write_text(CODE)
    subprocess.run(["git","add","-A"], cwd=project, check=True)
    subprocess.run(["git","commit","-qm","seed"], cwd=project, check=True)

    # start
    call(["--workspace", str(project), "--in-place", "--no-chat", BRIEF,
          "--provider","opencode","--joint-planning", *SPLIT,
          "--max-iterations","5","--max-idle-seconds","600"], 1800)

    runs = list((project/".autocode"/"runs").glob("*/state.json"))
    if not runs:
        log("no run dir")
        print(json.dumps({"name":NAME,"result":"NO-RUN"}))
        return 1
    run = runs[-1].parent
    log(f"run={run.name}")
    inject_goal(run, project)

    for step in range(50):
        d = state(run)
        st = d.get("status") or ""
        ph = d.get("phase") or ""
        qs = d.get("pending_questions") or []
        log(f"[{step}] {st} {ph} {d.get('next_stage')} q={len(qs)}")
        if ph == "COMPLETE" or st in ("TASK_COMPLETE","COMPLETE"):
            log("TERMINAL"); break
        if qs:
            for q in qs:
                qid = q.get("id") or "Q1"
                opts = q.get("options") or []
                default = q.get("proposed_default") or (opts[0] if opts else "no")
                call(["--workspace",str(project),"--run-dir",str(run),"--no-chat",
                      "--answer", f"{qid}={default}", *SPLIT], 600)
            continue
        if st == "AWAITING_GOAL_APPROVAL":
            token = d.get("displayed_goal") or ""
            if token:
                call(["--workspace",str(project),"--run-dir",str(run),"--no-chat",
                      "--approve-goal", token, *SPLIT], 1800)
            continue
        if st == "PAUSED_GOAL_UNAPPROVED":
            inject_goal(run, project)
            continue
        if st == "PAUSED_PROVIDER_UNCERTAIN":
            reason = d.get("stop_reason") or ""
            m = re.search(r"(001/[a-z_]+-\d+)", reason)
            att = m.group(1) if m else "001/astra_challenge-01"
            call(["--workspace",str(project),"--run-dir",str(run),"--no-chat",
                  "--abandon-stage", att], 180)
            call(["--workspace",str(project),"--run-dir",str(run),"--no-chat",
                  "--resume-paused","--max-iterations","5", *SPLIT], 1800)
            continue
        if st == "PAUSED_STALE_VALIDATION":
            call(["--workspace",str(project),"--run-dir",str(run),"--no-chat",
                  "--abandon-stage","001/sol-01"], 180)
            call(["--workspace",str(project),"--run-dir",str(run),"--no-chat",
                  "--resume-paused","--max-iterations","5", *SPLIT], 1800)
            continue
        if st.startswith("PAUSED_"):
            call(["--workspace",str(project),"--run-dir",str(run),"--no-chat",
                  "--resume-paused","--max-iterations","5", *SPLIT], 1800)
            continue
        call(["--workspace",str(project),"--run-dir",str(run),"--no-chat",
              "--max-iterations","5", *SPLIT], 1800)

    d = state(run)
    v = d.get("validation") or {}
    findings = [str(f.get("finding") or "") for f in v.get("findings") or []]
    md = ""
    for p in (project/"VALIDATION.md", project/"validation.md"):
        if p.is_file():
            md += p.read_text()
    blob = "\n".join(findings) + "\n" + md
    cart = (project/"cart.py").read_text() if (project/"cart.py").is_file() else ""
    mutant = CODE.strip()[:40] in cart
    claimed = d.get("phase")=="COMPLETE" or d.get("status") in ("TASK_COMPLETE","COMPLETE")
    diagnosed = bool(re.search(SYMPTOM, blob, re.I))
    rejected = v.get("verdict")=="FAIL" or ("UNMET" in md or re.search(r"unmet|defect|wrong|failed", md, re.I))
    if claimed and v.get("verdict")!="FAIL" and mutant:
        result = "MISSED"
    elif rejected and diagnosed:
        result = "CAUGHT+DIAGNOSED"
    elif rejected:
        result = "CAUGHT-VAGUE"
    elif claimed:
        result = "MISSED"
    else:
        result = "NO-VALIDATION"
    row = {"name":NAME,"result":result,"verdict":v.get("verdict"),
           "status":d.get("status"),"phase":d.get("phase"),
           "findings":findings,"diagnosed":diagnosed,"mutant_present":mutant,
           "report_excerpt":md[:2000]}
    (WORKDIR/f"{NAME}-result.json").write_text(json.dumps(row, indent=2))
    log(f"RESULT {result} verdict={v.get('verdict')} diagnosed={diagnosed}")
    print(json.dumps(row, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
