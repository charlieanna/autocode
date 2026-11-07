"""Viewer read receipts and core snapshot metadata; neither is work progress.

No action tokens or ordering authority are cached here. Snapshot hashes are
opaque identities; local monotonic time measures receipt age, never core age.
"""

from datetime import datetime


class Read:
    def __init__(self):
        self.received = None
        self.error = None
        self.snapshot = None

    def accept(self, view, now):
        self.received, self.error = now, None
        sample = view.get("observation") or {}
        self.snapshot = None
        if not isinstance(sample, dict) or sample.get("version") != 1:
            return
        if any(
            not isinstance(sample.get(key), str) or not sample[key] or sample[key] != view.get(key)
            for key in ("workspace", "run_dir")
        ):
            return
        token, timestamp = sample.get("checkpoint_token"), sample.get("sampled_at")
        if not isinstance(token, str) or not token or not isinstance(timestamp, str):
            return
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return
        except ValueError:
            return
        self.snapshot = {**{key: sample[key] for key in ("workspace", "run_dir")}, "token": token, "time": parsed}

    def fail(self, error):
        self.error = error

    def age(self, now):
        return "never" if self.received is None else f"{max(0, int(now - self.received))}s ago"

    def outage(self) -> str:
        """A failed read has a snapshot to retain only once a read has shown one (#1998)."""
        return "last snapshot retained" if self.received is not None else "no previous read"

    def compact(self, now):
        if self.received is None:
            return "status not read yet"
        sampled = f", run state at {self.snapshot['time'].strftime('%H:%M:%S %Z')}" if self.snapshot else ""
        return f"status read {self.age(now)}{sampled}"

    def details(self, now):
        sample = self.snapshot
        rows = [
            f"Viewer successful read: {self.age(now)}",
            "Read availability: "
            + (
                f"unavailable — {self.outage()}"
                if self.error
                else "available"
                if self.received is not None
                else "unknown"
            ),
            f"Core snapshot sampled at: {sample['time'].isoformat() if sample else 'unknown'}",
            f"Core checkpoint identity: {sample['token'] if sample else 'unknown'}",
        ]
        if sample:
            rows.extend((f"Snapshot workspace: {sample['workspace']}", f"Snapshot run: {sample['run_dir']}"))
        rows.append("A read or snapshot timestamp does not measure meaningful work progress or accept evidence.")
        return "\n".join(rows)
