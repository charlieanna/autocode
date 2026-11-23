"""One private Linux verification view. The input tree is read-only, and new names live in per-command scratch."""

import contextlib
import ctypes
import errno
import hashlib
import os
import select
import signal
import stat
import subprocess
import sys
from pathlib import Path


def launcher(tree):
    """Enter a private mount namespace without changing the supervised process's PID."""
    tree = Path(tree)
    control = tree.parent.parent
    script = Path(__file__).resolve()
    # The generated shell and client command pin the exact filesystem implementation.
    # Read once so a replacement between validation and execution cannot select different bytes.
    pinned = (
        "import hashlib,sys; from pathlib import Path; "
        "path=Path(sys.argv.pop(1)); expected=sys.argv.pop(1); source=path.read_bytes(); "
        "hashlib.sha256(source).hexdigest()==expected or sys.exit('Linux verification helper changed'); "
        "exec(compile(source,str(path),'exec'))"
    )
    return [
        "/usr/bin/unshare",
        "--user",
        "--map-current-user",
        "--keep-caps",
        "--mount",
        "--propagation",
        "private",
        sys.executable,
        "-I",
        "-c",
        pinned,
        str(script),
        hashlib.sha256(script.read_bytes()).hexdigest(),
        str(tree),
        str(control / "verification-view"),
        str(control / "verification-outputs"),
    ]


def bind(tree):
    return ["--bind", str(Path(tree).parent.parent / "verification-view"), str(tree)]


def _exists(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _entry(root, parts):
    # The only writer of the output tree is this single-threaded server. Never traverse a
    # link in either backing tree: Linux resolves links in the check's own mount namespace.
    parent = root
    for name in parts[:-1]:
        parent = parent / name
        if parent.is_symlink():
            raise OSError(errno.ELOOP, name)
    return root.joinpath(*parts)


class CopyView:
    use_ns = True

    def __init__(self, inputs, outputs, ready):
        self.inputs = inputs
        self.outputs = outputs
        self.ready = ready

    def init(self, path):
        os.write(self.ready, b"1")
        os.close(self.ready)

    def paths(self, path):
        parts = Path(path).parts
        if parts[:1] != ("/",) or ".." in parts:
            raise OSError(errno.EINVAL, path)
        return _entry(self.inputs, parts[1:]), _entry(self.outputs, parts[1:])

    def read_path(self, path):
        source, output = self.paths(path)
        return source if _exists(source) else output

    def output(self, path):
        source, output = self.paths(path)
        if _exists(source):
            raise OSError(errno.EROFS, path)
        return output

    def destination(self, path):
        target = self.output(path)
        # FUSE has already resolved the parent. Mirror input directories in the private
        # output tree only when a build first creates a name there.
        target.parent.mkdir(exist_ok=True, parents=True)
        return target

    def getattr(self, path, fh=None):
        info = os.fstat(fh) if fh is not None else self.read_path(path).lstat()
        times = info
        if stat.S_ISDIR(info.st_mode):
            # Build tools cache directory listings against mtime. Once new entries exist
            # the private directory records changes to those names, including removals.
            _, output = self.paths(path)
            with contextlib.suppress(FileNotFoundError):
                times = output.lstat()
        return {
            name: getattr(info, name)
            for name in ("st_mode", "st_nlink", "st_uid", "st_gid", "st_size", "st_ino", "st_blocks")
        } | {"st_atime": info.st_atime_ns, "st_mtime": times.st_mtime_ns, "st_ctime": times.st_ctime_ns}

    def readdir(self, path, fh):
        source, output = self.paths(path)
        names = set()
        for directory in (source, output):
            if directory.is_symlink():
                raise OSError(errno.ELOOP, path)
            with contextlib.suppress(FileNotFoundError):
                names.update(os.listdir(directory))
        return [".", "..", *sorted(names)]

    def readlink(self, path):
        return os.readlink(self.read_path(path))

    def open(self, path, flags):
        target = self.output(path) if flags & (os.O_ACCMODE | os.O_TRUNC) else self.read_path(path)
        return os.open(target, flags | os.O_NOFOLLOW)

    def create(self, path, mode, fi=None):
        return os.open(self.destination(path), os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode & 0o777)

    def read(self, path, size, offset, fh):
        return os.pread(fh, size, offset)

    def write(self, path, data, offset, fh):
        # A handle to a copied input was opened O_RDONLY by open().
        return os.pwrite(fh, data, offset)

    def release(self, path, fh):
        os.close(fh)
        return 0

    def flush(self, path, fh):
        os.close(os.dup(fh))
        return 0

    def fsync(self, path, datasync, fh):
        (os.fdatasync if datasync else os.fsync)(fh)
        return 0

    def truncate(self, path, length, fh=None):
        if fh is not None:
            os.ftruncate(fh, length)
        else:
            fd = os.open(self.output(path), os.O_WRONLY | os.O_NOFOLLOW)
            try:
                os.ftruncate(fd, length)
            finally:
                os.close(fd)
        return 0

    def mkdir(self, path, mode):
        os.mkdir(self.destination(path), mode & 0o1777)
        return 0

    def rmdir(self, path):
        os.rmdir(self.output(path))
        return 0

    def unlink(self, path):
        os.unlink(self.output(path))
        return 0

    def rename(self, old, new, flags=0):
        source, dest = self.output(old), self.destination(new)
        if flags:
            # libfuse supplies renameat2 flags (including atomic no-replace). Preserve them.
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(dest), flags):
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code))
        else:
            os.rename(source, dest)
        return 0

    def link(self, target, source):
        os.link(self.output(source), self.destination(target), follow_symlinks=False)
        return 0

    def symlink(self, target, source):
        os.symlink(source, self.destination(target))
        return 0

    def chmod(self, path, mode):
        target = self.output(path)
        if target.is_symlink():
            raise OSError(errno.EOPNOTSUPP, path)
        os.chmod(target, mode & 0o1777)
        return 0

    def chown(self, path, uid, gid):
        os.chown(self.output(path), uid, gid, follow_symlinks=False)
        return 0

    def utimens(self, path, times=None):
        options = {} if times is None else {"ns": times}
        os.utime(self.output(path), follow_symlinks=False, **options)
        return 0

    def mknod(self, path, mode, dev):
        if stat.S_ISFIFO(mode):
            os.mkfifo(self.destination(path), mode & 0o777)
            return 0
        raise OSError(errno.EPERM, path)

    def statfs(self, path):
        fs = os.statvfs(self.outputs)
        return {
            name: getattr(fs, name)
            for name in (
                "f_bavail",
                "f_bfree",
                "f_blocks",
                "f_bsize",
                "f_favail",
                "f_ffree",
                "f_files",
                "f_frsize",
                "f_namemax",
            )
        }


def main():
    tree, mountpoint, outputs = map(Path, sys.argv[1:4])
    if not Path("/dev/fuse").is_char_device():
        raise SystemExit("Linux verification copies require the FUSE 3 runtime and /dev/fuse in the launcher")
    safe_env = {"PATH": "/usr/bin:/bin", "FUSE_LIBRARY_PATH": "libfuse3.so.3"}
    subprocess.run(
        ["/usr/bin/mount", "-t", "tmpfs", "-o", "mode=0700,nosuid,nodev", "tmpfs", outputs], check=True, env=safe_env
    )
    read_fd, write_fd = os.pipe()
    owner = os.getpid()
    if not os.fork():
        try:
            # The server dies with the original bwrap process, including on cancellation.
            # Check the parent after arming the signal to cover an exit during fork.
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != owner:
                raise RuntimeError("verification mount owner exited")
            for fd in os.listdir("/proc/self/fd"):
                if int(fd) > 2 and int(fd) != write_fd:
                    os.closerange(int(fd), int(fd) + 1)
            os.environ.clear()
            os.environ.update(safe_env)
            os.chdir("/")
            import mfusepy

            class Operations(CopyView, mfusepy.Operations):
                pass

            class Filesystem(mfusepy.FUSE):
                # mfusepy 3.1.1 otherwise drops libfuse3's rename flags before calling Operations.
                def rename_fuse_3(self, old, new, flags):
                    return self.operations.rename(os.fsdecode(old), os.fsdecode(new), flags)

            Filesystem(
                Operations(tree, outputs, write_fd),
                str(mountpoint),
                foreground=True,
                nothreads=True,
                default_permissions=True,
                nosuid=True,
                nodev=True,
            )
        except Exception as error:
            print(f"Linux verification mount failed: {error}", file=sys.stderr, flush=True)
        finally:
            os._exit(1)
    os.close(write_fd)
    try:
        if not select.select([read_fd], [], [], 5)[0] or os.read(read_fd, 1) != b"1":
            raise RuntimeError("Linux verification filesystem did not mount")
    finally:
        os.close(read_fd)
    drop = "/usr/bin/setpriv"
    os.execv(
        drop, [drop, "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all", "--no-new-privs", *sys.argv[4:]]
    )


if __name__ == "__main__":
    main()
