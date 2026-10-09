"""Cycle-free contract identity and authenticated approval predicates."""

try:
    from . import autocode_util as util
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_util as util
    import autocode_workflows as workflows


def token(contract):
    return f"r{contract['revision']}:{contract['hash']}"


def sealed(contract):
    return contract.get("hash") == util.digest({k: contract[k] for k in ("task_id", "revision", "body")})


def approved(state):
    contract = state.get("goal_contract", {})
    approval = contract.get("approval_event") or {}
    return bool(
        contract
        and sealed(contract)
        and contract.get("approval_status") == "approved"
        and approval.get("token") == token(contract)
        and workflows.approval_actor_ok(contract.get("origin"), approval)
        and approval in state.get("user_events", [])
        and not contract["body"]["open_blocking_questions"]
    )
