# AutoCode

**One conversation for engineering work. Evidence before “done.”**

AutoCode is a local, conversation-first system for coordinating AI-assisted software engineering. It turns a rough request into a reviewed, explicitly approved plan, runs bounded implementation work, independently checks the result, and preserves the findings and evidence needed to decide what happens next.

Today, it provides a working planning/build/review runner, local dashboard, provider adapters, and recovery infrastructure. The larger vision is one engineering workspace for discussing, designing, building, debugging, reviewing, and verifying software—not just generating code.

> **Our north star:** Describe the engineering outcome you want in one continuous conversation. AutoCode helps make the requirements clear, breaks the work down appropriately, coordinates specialists, combines their results, and shows what is happening, why work is still open, and what evidence supports calling it ready.
>
> **Chat tells the story. State shows the current record. Evidence earns “ready.”**

**Status:** Active development. “Implemented” below means present in the repository, not proven reliable for every project or provider. This status snapshot is based on [`372e3f2`](https://github.com/charlieanna/autocode/tree/372e3f2adbd7e1f2267e65b3d8a8cd61be508ff0), reviewed on September 25, 2026. See [validation records](VALIDATION.md) and [reliability priorities](RELIABILITY.md) for the distinction between fixture coverage and live delivery evidence.

## Why we are building this

The problem is not simply getting a model to write code. It is preserving intent and control while work moves between planning, implementation, testing, review, correction, and integration.

An implementation can look finished while a requirement is missing. Tests can pass against an older candidate. A reviewer can identify a defect that disappears from the next summary. Several components can pass individually and fail together. A long correction run can make progress without explaining what remains.

AutoCode is being built to make those handoffs explicit and inspectable. The user should not have to relay prompts between agents, reconcile contradictory completion claims, or watch several terminals to understand one task.

**We optimize for the requested engineering outcome, not the number of agents, model calls, files changed, or lines generated.** We are building it for our own projects first; dependable everyday use comes before adding more modes or integrations.

## What is implemented today

The detailed guides describe supported paths, defaults, and limitations. They are not interchangeable with roadmap promises.

| Capability | Present behavior | Details |
| --- | --- | --- |
| Requirements and reviewed planning | Separate requirements, planner, and plan-reviewer stages produce a versioned brief with constraints, acceptance criteria, milestones, dependencies, and an initial task. Questions and revisions remain explicit. | [Workflow](docs/workflow.md) |
| Human approval | Implementation requires approval of the exact displayed plan revision. Answering a question or resuming a run is not plan approval. Revised briefs require renewed approval. | [Approval](docs/workflow.md#conversation-and-approval) |
| Bounded implementation | Builders receive an approved task and relevant context. New top-level tasks use separate Git worktrees by default; existing runs retain their saved workspace. | [Workspaces](docs/task-lanes.md#multiple-tasks-in-one-project) |
| Parallel milestone Builders | Eligible milestones with satisfied dependencies and disjoint ownership can run in isolated worktrees. Their patches are combined only against the expected parent baseline, then independently validated. Unsafe or insufficiently specified batches fall back to serial execution. | [Execution](docs/execution.md#independent-milestone-builders) |
| Independent review and completion checks | A Validator checks the candidate; a separate Completion Owner proposes the next outcome. AutoPilot applies the authoritative transition and completion checks. | [Completion](docs/execution.md#completion-gate) |
| Durable findings and evidence | Reviewer findings have runner-owned identities. Omission does not close them. Explicit, scoped dispositions and evidence are required; blocked reviews retain defects already discovered. | [Findings code](tools/autocode_findings.py), [Evidence rules](docs/testing.md) |
| Repair, retries, and recovery | Read-only repair diagnosis, report-format recovery, persisted retry/escalation policies, process supervision, locking, and checkpoints support bounded recovery. Unsafe or uncertain situations can require inspection and an explicit resume. | [Execution](docs/execution.md), [Models](docs/models.md) |
| Local conversation and monitoring | CLI conversations and a browser dashboard support planning, approvals, task visibility, findings, checkpoints, and safe-boundary feedback. A native macOS host wraps the dashboard. | [Dashboard](docs/dashboard.md), [macOS](docs/macos-app.md) |
| Figma workflow | A Codex/plugin-backed design → review → implementation-handoff path exists. It requires the relevant Figma tools to be available to the CLI; it does not assume a Figma Make API. | [Figma](docs/figma.md) |
| Configurable runtimes and models | OpenCode is the default engine; Codex and configured command-tool adapters are available, including a bundled KiloCode configuration. Roles and supported reasoning settings are configurable. | [Providers](docs/providers.md), [Models](docs/models.md) |
| Multiple task lanes | Tasks in a lane run sequentially; separate lanes can run concurrently in separate worktrees. Lane branches are **not** automatically merged into one product. | [Task lanes](docs/task-lanes.md) |

**Important boundaries:** Parallel milestone integration is not automatic merging into `master`. Task lanes are not yet hierarchical product planning. Existing conversations and checkpoints are the foundation for the broader one-conversation workspace—not a claim that all planned engineering workflows already exist.

## How the current workflow fits together

```text
Rough idea + repository context
              |
              v
      Requirements gathering
              |
              v
    Plan -> challenge -> revision -> final planning review
              |
              v
       You approve the exact brief
              |
              v
    Eligible bounded task / milestone batch
              |
              v
      Builder(s) -> combined candidate
              |
              v
       Independent Validator
              |
              v
          Completion Owner
              |
              v
     AutoPilot applies the next transition
          /             |              \
     correction      needs input       ready
         |               |               |
   AutoResolver       pause with      evidence-backed
         |            explanation     completion record
   bounded repair
         |
   Builder(s) -> independent review again
```

This describes the standard code workflow. Saved runs and explicitly selected alternate workflows can retain different routing; they must not be silently upgraded or stripped of their existing approval rules. See [workflow details](docs/workflow.md).

### One controller, four bounded units

| Component | Responsibility | Output—not authority to do everything |
| --- | --- | --- |
| **AutoPilot** | Deterministic workflow control, state transitions, dispatch, approval boundaries, recovery, and completion checks. | The next permitted action and current authoritative run state. |
| **AutoPlanner** | Understand the request and develop a reviewed executable plan. | A plan candidate; user approval remains explicit. |
| **AutoCode — build unit** | Schedule approved work, run Builders, and integrate eligible results. | A source-identified implementation candidate, not product acceptance. |
| **AutoReview** | Independently validate the candidate and provide completion-owner review. | Evidence, criterion outcomes, findings, and a proposed outcome. |
| **AutoResolver** | Diagnose reviewer-requested rework without changing application source. | A bounded repair task and retests, or a blocker requiring a decision. |

**AutoCode** is also the name of the whole project. The internal build unit retains its existing name; **AutoPilot** is the controller above it. These are modules in one application, not a requirement to deploy separate services.

The current AutoResolver emits one bounded repair task, represented as a one-node repair graph. It does not implement the fix, close findings, grant permissions, or approve completion. This model-assisted diagnosis is distinct from the runner's bounded recovery-policy machinery. See [unit handoffs](docs/workflow.md#four-units-controlled-by-autopilot).

### One conversation is not one model session

The user-facing conversation can remain continuous while requirements, planning, building, and review run in separate contexts. Existing sessions can be resumed where supported; a role change must not silently erase its boundaries.

Run memory lives in saved state and artifacts—not solely in a model's conversation history. The shared run directory includes authoritative `state.json`, report/evidence artifacts, and versioned unit handoffs. Exported handoffs are inspectable snapshots, not standalone authorization tokens.

Separate sessions provide context separation. They do not make models infallible or eliminate shared blind spots.

## What “ready” means

For the standard full-task completion path, AutoPilot checks the current approved contract, task identity, candidate source revision, independent validation, required criterion evidence, end-to-end evidence, open blockers, required human approvals, and the completion request. See the [completion gate](docs/execution.md#completion-gate).

The rules we preserve are:

| Rule | Practical meaning |
| --- | --- |
| Evidence belongs to a candidate. | A check against C17 does not automatically approve C18. Dirty source changes matter, not only Git commits. |
| Missing verification stays missing. | Unknown, skipped, unavailable, and untested are not synonyms for passed. |
| Findings survive handoffs. | A fix claim or omitted finding is not an independently verified resolution. |
| Approval has a scope. | Plan approval, artifact acceptance, permission to access an external system, and permission to deploy are distinct. |
| Partial progress is not final completion. | A milestone can unlock downstream work without proving the entire product. |
| Limits and failures do not mean success. | A timeout, exhausted retry budget, or successful provider exit cannot by itself establish completion. |

“Ready” means the required gates are satisfied for the identified candidate. **It does not mean zero bugs, permission to merge, or permission to deploy.** Code and model reviews can still miss defects; the adequacy of requirements and verification matters as much as the checks' execution.

## Where we are going

The following is the intended direction. It is **not** a list of features already shipped, and it does not authorize unattended implementation of the whole roadmap.

### 1. Strengthen AutoReview first — immediate non-UX priority

**Current:** Validator + Completion Owner, candidate-bound checks, a findings ledger, and controller-owned gates.

**Next:** Make the review obligation explicit before carrying out the checks:

```text
Approved contract + exact candidate + open findings
                         |
                         v
                 Review coverage plan
                         |
                         v
            Tester produces executed evidence
                         |
                         v
             Independent technical review
                         |
                         v
        Deterministic assessment of gaps and findings
                         |
                         v
            AutoPilot selects the permitted next action
```

The planned review model should identify which criteria were checked, how, against what source and environment, with what evidence, and what remains unverified. A result may contain **both** confirmed defects and missing verification; neither should hide the other.

Start by strengthening existing command evidence and review contracts. Add domain-specific verification only where a real task requires it: browser interactions, design comparison, migration parity, or behavioral simulations. Testing generates observations; review interprets them; the controller enforces advancement. A schema-complete review plan is not proof that its tests are sufficient.

Evolve the existing units and compatibility paths. Do not create a second findings database or silently remove required reviews from saved runs.

### 2. Make one conversation work across the engineering lifecycle — UX track

**Current:** Planning conversations, task views, checkpoint history, and feedback/control surfaces exist.

**Target:** One continuous conversation for a piece of engineering work, with project-wide visibility and relevant artifacts alongside it. While the human clarifies the request, a Planner maintains a versioned draft; a Plan Reviewer checks it before approval. Questions, status requests, proposed scope changes, and control actions have distinct effects.

The user should be able to ask “Why is this still open?” and see the current blocking findings and missing evidence—not read every agent transcript. A live preview makes UI work tangible. Task, plan, finding, and evidence details remain available without losing the conversation.

Show concise explanations of actions, decisions, and observations—not private model reasoning or invented progress percentages. UX work can proceed separately from backend review improvements.

### 3. Support the engineering outcome, not one compulsory coding pipeline

**Current:** The main approved-build workflow, callable units, and a dedicated Figma path exist.

**Planned:** Composable workflows that produce the artifact the user actually requested:

| Request | Intended result |
| --- | --- |
| “Build this feature.” | An integrated candidate with requirement-linked verification. |
| “Find and fix this bug.” | Reproduction, diagnosis, bounded correction, and regression evidence. |
| “Review this PR.” | Findings and check results without changing the code; fixing it is a separate authorized action. |
| “Review or implement this architecture.” | A design assessment, or a plan that faithfully implements the approved design. |
| “Discuss these tradeoffs.” | Options, assumptions, consequences, and a decision record—not unsolicited implementation. |
| “Review or build this UI.” | A design assessment or implementation, with rendered and interaction evidence. |

These are target workflows, not claims that today's CLI automatically classifies and implements every intent. Reuse the same units and approval rules instead of building a different engine for every request.

### 4. Grow from bounded tasks to large projects

**Current:** Milestones, dependency-aware Builder batches, and manually defined task lanes.

**Planned:** A bounded hierarchy:

```text
Project -> Workstream -> Milestone -> Task
```

A large adaptive-learning platform might need domain modeling, backend APIs, frontend, learning policies, content, evaluation, and deployment work. It should not become one enormous prompt or an unreviewable list of hundreds of tasks.

The parent plan should establish product requirements and shared interfaces. Each workstream should inherit those constraints, develop its own reviewed plan, and produce an identifiable result. Cross-workstream contract changes must be explicit; child agents must not redefine the product independently.

Integrate early through small end-to-end slices—for example, one learner studies one concept, submits an attempt, and receives a next activity—rather than waiting for every subsystem to be “finished.” Component checks and simulations do not by themselves establish real learning effectiveness. Final product verification must return to the original user journey.

Keep small jobs small. A typo fix does not need a program hierarchy. Deployment, credentials, and external mutations remain separately authorized.

### 5. Add specialist delegation only when it helps

**Exploratory, not a V1 dependency:** A unit may eventually request bounded specialists for independent investigation, security review, compatibility checks, or other task-specific questions.

AutoPilot must still control allowed execution, workspace ownership, required reviews, budgets, and completion. A supervisor cannot skip an acceptance gate because it considers itself confident.

Model choice is configuration, not the product. Keep explicit defaults and bounded escalation; do not make the user benchmark hundreds of models or build unrestricted recursive delegation before the basic workflow is dependable.

## Principles that keep us on course

**Make the human's outcome the primary object.** Tasks, candidates, requirements, findings, and evidence matter more than agent identities. A collection of terminal windows is not the desired product.

**Preserve intent before optimizing execution.** Shared interfaces and explicit boundaries come before parallelism. More agents are useful only when their outputs can fit together safely.

**Keep workflow authority in code.** Models propose plans, changes, diagnoses, and review judgments. AutoPilot validates and applies transitions; models do not manufacture human approval or broaden permission.

**Prefer honest partial results to false completion.** “The implementation exists, but this criterion could not be verified” is useful. Quietly treating it as passed is not.

**Build on existing infrastructure.** Use coding runtimes and provider adapters underneath AutoCode. Do not rebuild a generic editor, model gateway, or general-purpose agent framework merely to add another layer.

**Improve reliability before expanding the surface area.** Fix reproducible handoff defects, preserve regression coverage, and prove useful bounded flows before recursive planning, distributed execution, or more integrations. The operational priority remains [RELIABILITY.md](RELIABILITY.md).

## Run AutoCode today

### Requirements

Python 3.11+, Git, and macOS/Linux or WSL. The default route uses OpenCode 1.x with the configured provider connections; the documented stock setup uses ChatGPT and Z.ai connections. Codex and custom command-tool configurations are alternatives. The current adapter does not support OpenCode 2.x.

Configure the selected runtime's credentials and model access first. A model appearing in a catalog is not proof that your account can use it. AutoCode does not provide inference credits. Consult [installation](docs/install.md), [providers](docs/providers.md), and [model settings](docs/models.md) before launching paid or subscription-backed work.

### Install and start

```sh
git clone https://github.com/charlieanna/autocode.git
cd autocode
pipx install --editable "$PWD"

autocode --help

# Use an existing Git project with a committed HEAD.
autocode "I want a small notes tool that keeps my notes between runs" \
  --workspace /absolute/path/to/project --chat
```

Review and approve the displayed brief before implementation starts. Keep the task workspace and run paths printed by the runner. New task worktrees start from the project's committed HEAD; ignored environment files and dependencies are not automatically copied. See [workspace behavior](docs/task-lanes.md).

```sh
# Local dashboard; open the loopback URL it prints.
autocode-dashboard --port 8767

# Inspect a specific saved run using its printed paths.
autocode --workspace /printed/task/workspace --run-dir /printed/run/path --status
```

`--status` is inspection. `--resume-paused` is an explicit execution action, not a substitute for resolving a blocker or approving a new plan. Use the [CLI reference](docs/cli.md) for resume, feedback, review approval, and unit-by-unit commands.

## Testing and confidence

Test each unit's contract independently, test handoff compatibility, and test the complete controller with deterministic fake providers. Exercise both safety and progress: refusing false completion is necessary, but correctly completing valid work is necessary too.

The test strategy includes stale results, reviewer disagreement, findings retention, evidence integrity, interruptions, duplicate operations, integration drift, and user approval boundaries. Keep generated sequences and targeted broken-guard tests as additions to real boundary tests—not substitutes for them.

A documented starting suite is:

```sh
python3 -m unittest tools/test_escalation.py tools/test_autocode.py \
  tools/test_goals.py tools/test_subprocess.py tools/test_opencode.py \
  tools/test_process.py
```

This is not the entire suite. See [testing](docs/testing.md), [dashboard tests](docs/dashboard.md#dashboard-verification), [recorded validation](VALIDATION.md), and [audit artifacts](audits/).

**Fixture tests establish behavior under the exercised conditions, not model quality or universal correctness.** Test counts and historical results must be tied to their recorded source/environment. Do not present them as a fresh run of current master. The reliability plan also calls for bounded real-model delivery trials: a small application, a feature in an existing project, and a bug fix.

## Limitations and trust boundary

AutoCode is currently a local, trusted-workspace tool—not a production-hardened multi-tenant execution service. Provider tool permissions and after-the-fact source checks are not a universal OS sandbox. OpenCode restrictions, Codex sandboxing, external tools, and operator permissions have different boundaries; consult the provider guide rather than assuming identical isolation.

State and evidence files support integrity checks inside this model; they are not a tamper-proof security boundary against a hostile process with write access to the same files. Recovery can pause for reconciliation instead of automatically retrying. Installing an update does not hot-reload a running process or certify existing results under the new version.

There is no promise of bug-free output, arbitrary exactly-once external effects, fully autonomous deployment, universal model support, or completed hierarchical project delivery. Each of those requires its own scope, implementation, and evidence.

## Repository map and guides

```text
tools/autopilot.py       overall workflow controller
tools/units/            planning, build, review, and repair units
tools/autocode_*.py     contracts, findings, evidence, execution, and recovery
tools/providers/        runtime adapters and bundled command-tool configs
tools/dashboard/        local browser interface
macos-app/              native host for the dashboard
docs/                   operational guides
audits/                 recorded verification artifacts
RELIABILITY.md          current reliability priorities
VALIDATION.md           recorded results and limitations
```

| Topic | Guide |
| --- | --- |
| Setup and commands | [Install](docs/install.md) · [CLI](docs/cli.md) |
| Planning, approval, and handoffs | [Workflow](docs/workflow.md) |
| Models and runtime integration | [Models](docs/models.md) · [Providers](docs/providers.md) |
| Build, recovery, and completion | [Execution](docs/execution.md) · [Interventions](docs/interventions.md) |
| Conversation and monitoring | [Dashboard](docs/dashboard.md) · [Registry API](docs/registry-api.md) · [macOS app](docs/macos-app.md) |
| Visual work and multi-task runs | [Figma](docs/figma.md) · [Task lanes](docs/task-lanes.md) |
| Verification and project priorities | [Testing](docs/testing.md) · [Validation](VALIDATION.md) · [Reliability](RELIABILITY.md) |

## Keep this README honest

Move a capability from **planned** to **implemented** only when the relevant code, supported entry point, and regression coverage exist. Link its validation evidence and known limits; label incomplete or environment-blocked verification explicitly. Update the status snapshot when reviewing a newer revision.

A conversation, design, prompt, schema, or test specification is not by itself a shipped capability. Changes to this document do not grant permission to run tasks or deploy software.

When deciding what to build next, ask:

> **Does this help a person get their intended engineering outcome with less coordination work, clearer control, and better evidence?**

If not, it is probably not the next priority.

## License

No root-level license file is present in the status snapshot above, and the package metadata does not specify a license. The maintainer still needs to document the project's licensing; this README does not choose a license.
