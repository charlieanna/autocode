from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autocode_gocode_adapter.compatibility import CompatibilityError, CompatibilityManifest
from autocode_gocode_adapter.launcher import main
from autocode_gocode_adapter.sync import SyncError, UpstreamSynchronizer


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[Path, str]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    source = tmp_path / "source"
    subprocess.run(["git", "clone", str(remote), str(source)], check=True, capture_output=True)
    git(source, "config", "user.email", "fixture@example.test")
    git(source, "config", "user.name", "Fixture")
    (source / "runner.py").write_text("class Runner: pass\n")
    (source / "dashboard.py").write_text("def create_app(): pass\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "fixture upstream")
    git(source, "branch", "-M", "master")
    git(source, "push", "-u", "origin", "master")
    return source, git(source, "rev-parse", "HEAD")


def manifest() -> CompatibilityManifest:
    return CompatibilityManifest.from_dict(
        {
            "version": 1,
            "probes": [
                {"path": "runner.py", "contains": "class Runner"},
                {"path": "dashboard.py", "contains": "create_app"},
            ],
        }
    )


def test_fetches_only_tracking_ref_and_creates_detached_pinned_worktree(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    source, commit = upstream
    before_branch = git(source, "branch", "--show-current")
    before_remotes = git(source, "remote", "-v")
    invocations: list[tuple[str, ...]] = []
    sync = UpstreamSynchronizer(manifest(), invocations.append)

    result = sync.prepare(source, tmp_path / "pinned")

    assert result.commit == commit
    assert git(source, "branch", "--show-current") == before_branch
    assert git(source, "remote", "-v") == before_remotes
    assert git(source, "status", "--porcelain") == ""
    assert git(result.checkout, "rev-parse", "HEAD") == commit
    assert subprocess.run(
        ["git", "-C", str(result.checkout), "symbolic-ref", "-q", "HEAD"],
        text=True,
        capture_output=True,
    ).returncode == 1
    commands = [" ".join(call) for call in invocations]
    assert any("fetch --no-tags origin +refs/heads/master:refs/remotes/origin/master" in call for call in commands)
    assert not any(any(word in call for word in (" push", " merge", " reset", " checkout", " switch", " remote ")) for call in commands)


def test_reuses_matching_clean_detached_checkout_and_records_pin(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    source, commit = upstream
    destination = tmp_path / "pinned"
    sync = UpstreamSynchronizer(manifest())
    first = sync.prepare(source, destination)
    record = tmp_path / "pin.json"
    second = sync.prepare(source, destination, record_path=record)

    assert second.checkout == first.checkout
    assert second.commit == commit
    assert json.loads(record.read_text()) == {
        "compatibility_manifest": manifest().identity,
        "upstream_commit": commit,
    }


@pytest.mark.parametrize("checkout_target", [False, True])
def test_refuses_record_paths_inside_source_or_pinned_checkout_without_changes(
    upstream: tuple[Path, str], tmp_path: Path, checkout_target: bool
) -> None:
    source, _ = upstream
    destination = tmp_path / "pinned"
    sync = UpstreamSynchronizer(manifest())
    checkout = sync.prepare(source, destination).checkout
    target = (checkout / "dashboard.py") if checkout_target else (source / "runner.py")
    before_contents = target.read_bytes()
    before_source_status = git(source, "status", "--porcelain")
    before_checkout_status = git(checkout, "status", "--porcelain")

    with pytest.raises(SyncError, match="outside both"):
        sync.prepare(source, destination, record_path=target)

    assert target.read_bytes() == before_contents
    assert git(source, "status", "--porcelain") == before_source_status
    assert git(checkout, "status", "--porcelain") == before_checkout_status


def test_refuses_unrelated_existing_record_without_overwriting_it(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    source, _ = upstream
    destination = tmp_path / "pinned"
    record = tmp_path / "operator-record.json"
    record.write_text("operator-owned contents\n", encoding="utf-8")
    before = record.read_bytes()
    sync = UpstreamSynchronizer(manifest())
    checkout = sync.prepare(source, destination).checkout
    before_source_status = git(source, "status", "--porcelain")
    before_checkout_status = git(checkout, "status", "--porcelain")

    with pytest.raises(SyncError, match="non-adapter-owned"):
        sync.prepare(source, destination, record_path=record)

    assert record.read_bytes() == before
    assert git(source, "status", "--porcelain") == before_source_status
    assert git(checkout, "status", "--porcelain") == before_checkout_status


def test_refuses_record_path_through_symlinked_parent_without_changes(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    source, _ = upstream
    destination = tmp_path / "pinned"
    symlink_parent = tmp_path / "linked-source"
    symlink_parent.symlink_to(source, target_is_directory=True)
    sync = UpstreamSynchronizer(manifest())
    checkout = sync.prepare(source, destination).checkout
    before_source_status = git(source, "status", "--porcelain")
    before_checkout_status = git(checkout, "status", "--porcelain")

    with pytest.raises(SyncError, match="outside both"):
        sync.prepare(source, destination, record_path=symlink_parent / "record.json")

    assert not (source / "record.json").exists()
    assert git(source, "status", "--porcelain") == before_source_status
    assert git(checkout, "status", "--porcelain") == before_checkout_status


def test_refuses_final_component_symlink_without_overwriting_its_target(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    source, _ = upstream
    destination = tmp_path / "pinned"
    target = tmp_path / "operator-owned.json"
    target.write_text("operator-owned contents\n", encoding="utf-8")
    record = tmp_path / "pin.json"
    record.symlink_to(target)
    sync = UpstreamSynchronizer(manifest())
    checkout = sync.prepare(source, destination).checkout
    before_source_status = git(source, "status", "--porcelain")
    before_checkout_status = git(checkout, "status", "--porcelain")

    with pytest.raises(SyncError, match="non-adapter-owned"):
        sync.prepare(source, destination, record_path=record)

    assert target.read_text(encoding="utf-8") == "operator-owned contents\n"
    assert record.is_symlink()
    assert git(source, "status", "--porcelain") == before_source_status
    assert git(checkout, "status", "--porcelain") == before_checkout_status


def test_creates_new_record_and_reuses_only_the_exact_canonical_record(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    source, commit = upstream
    destination = tmp_path / "pinned"
    record = tmp_path / "new-records" / "pin.json"
    sync = UpstreamSynchronizer(manifest())
    checkout = sync.prepare(source, destination).checkout
    source_status = git(source, "status", "--porcelain")
    checkout_status = git(checkout, "status", "--porcelain")

    sync.prepare(source, destination, record_path=record)
    expected = (
        json.dumps(
            {"compatibility_manifest": manifest().identity, "upstream_commit": commit}, sort_keys=True
        )
        + "\n"
    )
    assert record.read_text(encoding="utf-8") == expected
    sync.prepare(source, destination, record_path=record)

    assert record.read_text(encoding="utf-8") == expected
    assert git(source, "status", "--porcelain") == source_status
    assert git(checkout, "status", "--porcelain") == checkout_status


def test_refuses_dirty_source_or_reused_checkout(upstream: tuple[Path, str], tmp_path: Path) -> None:
    source, _ = upstream
    (source / "uncommitted.txt").write_text("dirty")
    with pytest.raises(SyncError, match="dirty"):
        UpstreamSynchronizer(manifest()).prepare(source, tmp_path / "pinned")
    (source / "uncommitted.txt").unlink()
    destination = tmp_path / "pinned"
    result = UpstreamSynchronizer(manifest()).prepare(source, destination)
    (result.checkout / "uncommitted.txt").write_text("dirty")
    with pytest.raises(SyncError, match="dirty"):
        UpstreamSynchronizer(manifest()).prepare(source, destination)


def test_reports_missing_ref_and_incompatible_interface(upstream: tuple[Path, str], tmp_path: Path) -> None:
    source, _ = upstream
    with pytest.raises(SyncError, match="does not resolve"):
        UpstreamSynchronizer(manifest(), branch="absent").prepare(source, tmp_path / "missing")
    incompatible = CompatibilityManifest.from_dict(
        {"version": 1, "probes": [{"path": "runner.py", "contains": "missing_seam"}]}
    )
    with pytest.raises(CompatibilityError, match="runner.py"):
        UpstreamSynchronizer(incompatible).prepare(source, tmp_path / "incompatible")


def test_launcher_runs_local_fixture_without_provider_runtime(
    upstream: tuple[Path, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, commit = upstream
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "version": 1,
        "probes": [{"path": "runner.py", "contains": "class Runner"}],
    }))
    record = tmp_path / "record.json"

    assert main([
        "--upstream", str(source), "--checkout", str(tmp_path / "pinned"),
        "--manifest", str(manifest_path), "--record", str(record),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["commit"] == commit
    assert json.loads(record.read_text())["upstream_commit"] == commit
