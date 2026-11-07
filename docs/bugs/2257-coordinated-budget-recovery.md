# Recovering a shared coordination cost cap from terminal chat

An ordinary member cost-pause action can change that member's cap, but the
coordinator still conserves the original whole-set envelope. Without a shared
action, chat cannot authorize a larger envelope through the public interface.

The core now offers a finite shared-cap decision only at a settled cost
checkpoint. The terminal shows known cumulative spend and incomplete accounting,
asks for the person's amount and confirmation, and submits the exact fresh named
action. The core retains its original initial-spend record, all member and joint
attempts, failed independent verifier costs and every other setting. Unknown or
legacy accounting coverage remains incomplete. A sufficient existing cap may be
reasserted; this never resets spent money.

For a member cost stop, the coordinator derives that member's allowance from the
new total after charging all other retained costs. TaskRun consumes the existing
public recovery-token action. The shared decision approves no plan, waives no
proof and authorizes no unrelated recovery. An interrupted resume leaves durable
intent for inspection; reopening never repeats the uncertain call.

`tests.test_coordination_budget` drives the public CLI with controlled TaskRun
responses, including stale accounting/checkpoints, active or cancelled sets,
insufficient and nonfinite caps, retained unknown costs and interrupted calls.
`tests.test_chat_coordination` verifies explicit confirmation, the exact named
action, private token transport, decline and stale-screen refusal. These checks
exercise accounting and admission without live model calls. Full native
multi-repository delivery remains a separate qualification requirement.

`tests.test_coordination_budget_cli` additionally creates a real cost pause with
supervised offline providers on supported Linux. The named shared-cap action
crosses actual parsing, recovery-token admission, settings update and TaskRun
resume, preserves the peer's pending plan, and reaches a new plan-approval gate
without authorizing a Builder. A provider-launch probe rejects duplicate dispatch
from the stale budget action.
