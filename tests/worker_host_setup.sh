#!/usr/bin/env bash
# Disposable CI host only. Production admission never enables services or linger.
set -euo pipefail

if [[ "${GITHUB_ACTIONS:-}" != "true" || "${RUNNER_OS:-}" != "Linux" ]]; then
  echo "This setup is only for the disposable GitHub Linux worker-host job." >&2
  exit 2
fi

sudo apt-get update -q
sudo apt-get install -y -q bubblewrap libseccomp2 openssh-server tmux
if sysctl -n kernel.apparmor_restrict_unprivileged_userns >/dev/null 2>&1; then
  sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
fi
/usr/bin/bwrap --unshare-all --ro-bind / / --dev /dev --proc /proc -- /bin/true

worker_uid="$(id -u)"
sudo loginctl enable-linger "$worker_uid"
sudo systemctl start "user@${worker_uid}.service"
export XDG_RUNTIME_DIR="/run/user/${worker_uid}"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
systemctl --user show --property=Version --value

worker_fixture="$(mktemp -d "${RUNNER_TEMP}/autocode-ssh.XXXXXX")"
chmod 700 "$worker_fixture"
export AUTOCODE_WORKER_EVIDENCE="${GITHUB_WORKSPACE}/.scenario-runs/worker-host"
cleanup_worker_host() {
  if [[ -f "$worker_fixture/sshd.pid" ]]; then
    sudo kill "$(cat "$worker_fixture/sshd.pid")" || true
  fi
  mkdir -p "$AUTOCODE_WORKER_EVIDENCE"
  if [[ -f "$worker_fixture/sshd.log" ]]; then
    sudo cat "$worker_fixture/sshd.log" > "$AUTOCODE_WORKER_EVIDENCE/sshd.log"
  fi
  rm -rf "$worker_fixture"
}
trap cleanup_worker_host EXIT
ssh-keygen -q -t ed25519 -N '' -f "$worker_fixture/client"
ssh-keygen -q -t ed25519 -N '' -f "$worker_fixture/host"
cp "$worker_fixture/client.pub" "$worker_fixture/authorized_keys"
chmod 600 "$worker_fixture/authorized_keys"
sudo install -d -m 0755 /run/sshd
cat > "$worker_fixture/sshd_config" <<EOF
Port 22222
ListenAddress 127.0.0.1
HostKey $worker_fixture/host
PidFile $worker_fixture/sshd.pid
AuthorizedKeysFile $worker_fixture/authorized_keys
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
UsePAM yes
StrictModes yes
LogLevel VERBOSE
EOF
sudo /usr/sbin/sshd -f "$worker_fixture/sshd_config" -E "$worker_fixture/sshd.log"
printf '[127.0.0.1]:22222 %s\n' "$(cat "$worker_fixture/host.pub")" > "$worker_fixture/known_hosts"
cat > "$worker_fixture/ssh_config" <<EOF
Host fixture
  HostName 127.0.0.1
  Port 22222
  User $(id -un)
  IdentityFile $worker_fixture/client
  IdentitiesOnly yes
  BatchMode yes
  StrictHostKeyChecking yes
  UserKnownHostsFile $worker_fixture/known_hosts
  ConnectTimeout 5
EOF
export AUTOCODE_WORKER_SSH_CONFIG="$worker_fixture/ssh_config"
ssh -F "$AUTOCODE_WORKER_SSH_CONFIG" fixture /bin/true
export AUTOCODE_REQUIRE_WORKER_HOST=1
.venv/bin/python -m unittest -v tests.test_worker_host
