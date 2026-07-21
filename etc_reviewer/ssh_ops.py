"""Remote /etc git operations over SSH.

All remote commands are executed by shelling out to the system `ssh`
binary, relying on the user's existing SSH agent / keys / ~/.ssh/config.

SSH does not preserve argv arrays for the remote command: it joins the
trailing arguments with spaces and hands the result to the login shell on
the remote end. Any value that did not originate as a fixed string
literal in this file (filenames, commit messages) is therefore run
through shlex.quote() before being folded into that string. Static
scripts (no interpolated data) are sent as-is.
"""
from __future__ import annotations

import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

SSH_BASE = [
    "ssh",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=8",
    "-o", "StrictHostKeyChecking=accept-new",
]

STATUS_TIMEOUT = 15
DIFF_TIMEOUT = 15
COMMIT_TIMEOUT = 20

OS_MARKER = b"@@ETCREV_OS@@\n"
GIT_MARKER = b"@@ETCREV_GIT@@\n"

STATUS_SCRIPT = (
    "hostname\n"
    "echo '@@ETCREV_OS@@'\n"
    "cat /etc/os-release 2>/dev/null || uname -a\n"
    "echo '@@ETCREV_GIT@@'\n"
    "cd /etc && git status --porcelain=v1 -z --untracked-files=all\n"
)


def read_machines(path: str) -> list[str]:
    """Parse a plain text file, one hostname per line. Blank lines and
    lines starting with # are ignored."""
    hosts: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                hosts.append(line)
    return hosts


def _ssh_exec(host: str, remote_cmd: str, input_bytes: bytes | None = None,
              timeout: float = STATUS_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*SSH_BASE, f"root@{host}", remote_cmd],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def parse_os_release(blob: str) -> str:
    fields: dict[str, str] = {}
    for line in blob.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        fields[key] = value
    if "PRETTY_NAME" in fields:
        return fields["PRETTY_NAME"]
    if fields:
        name = fields.get("NAME", "Unknown")
        version = fields.get("VERSION", "")
        return f"{name} {version}".strip()
    stripped = blob.strip()
    return stripped.splitlines()[0] if stripped else "Unknown"


def parse_porcelain_z(data: bytes) -> list[dict]:
    """Parse `git status --porcelain=v1 -z` output.

    Each record is `XY PATH\\0`, or for renames/copies
    `XY NEWPATH\\0ORIGPATH\\0` (the extra original-path record only
    appears when X or Y is 'R' or 'C').
    """
    records = data.split(b"\0")
    changes = []
    i = 0
    while i < len(records):
        rec = records[i]
        i += 1
        if not rec:
            continue
        status = rec[:2].decode(errors="replace")
        path = rec[3:].decode(errors="replace")
        orig_path = None
        if ("R" in status or "C" in status) and i < len(records) and records[i]:
            orig_path = records[i].decode(errors="replace")
            i += 1
        changes.append({"status": status, "path": path, "orig_path": orig_path})
    return changes


def is_untracked(status: str) -> bool:
    return status[0] == "?" or status[1] == "?"


@dataclass
class MachineStatus:
    host: str
    reachable: bool = False
    hostname: str | None = None
    os: str | None = None
    changes: list[dict] = field(default_factory=list)
    error: str | None = None


def get_machine_status(host: str) -> MachineStatus:
    result = MachineStatus(host=host)
    try:
        proc = _ssh_exec(host, STATUS_SCRIPT, timeout=STATUS_TIMEOUT)
    except subprocess.TimeoutExpired:
        result.error = "SSH connection timed out"
        return result
    except OSError as exc:
        result.error = f"Failed to run ssh: {exc}"
        return result

    stdout = proc.stdout
    if OS_MARKER not in stdout:
        stderr = proc.stderr.decode(errors="replace").strip()
        result.error = stderr or f"ssh exited {proc.returncode} with no output"
        return result

    hostname_part, rest = stdout.split(OS_MARKER, 1)
    if GIT_MARKER in rest:
        os_blob, git_blob = rest.split(GIT_MARKER, 1)
    else:
        os_blob, git_blob = rest, b""

    result.reachable = True
    result.hostname = hostname_part.strip().decode(errors="replace") or host
    result.os = parse_os_release(os_blob.decode(errors="replace"))

    stderr_text = proc.stderr.decode(errors="replace")
    if "fatal: not a git repository" in stderr_text:
        result.error = "/etc is not a git repository on this machine"
        return result

    result.changes = parse_porcelain_z(git_blob)
    return result


def fetch_all_status(hosts: list[str]) -> list[MachineStatus]:
    if not hosts:
        return []
    with ThreadPoolExecutor(max_workers=min(32, len(hosts))) as pool:
        return list(pool.map(get_machine_status, hosts))


def get_diff(host: str, path: str, untracked: bool) -> tuple[str | None, str | None]:
    """Returns (diff_text, error)."""
    if untracked:
        argv = ["git", "-C", "/etc", "diff", "--no-index", "--", "/dev/null", path]
    else:
        argv = ["git", "-C", "/etc", "diff", "HEAD", "--", path]
    cmd = " ".join(shlex.quote(a) for a in argv)
    try:
        proc = _ssh_exec(host, cmd, timeout=DIFF_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "SSH connection timed out"
    except OSError as exc:
        return None, f"Failed to run ssh: {exc}"
    # `git diff --no-index` exits 1 when it found differences (normal).
    if proc.returncode not in (0, 1):
        err = proc.stderr.decode(errors="replace").strip()
        return None, err or f"git diff failed (exit {proc.returncode})"
    text = proc.stdout.decode(errors="replace")
    return (text or "(no textual diff)"), None


def commit_machine(host: str, message: str, commit_paths: list[str],
                    gitignore_paths: list[str]) -> tuple[bool, str | None]:
    """Stage + commit the given paths on one machine. Paths in
    gitignore_paths are appended to /etc/.gitignore instead of being
    committed themselves; the resulting .gitignore change is folded
    into the same commit."""
    all_paths = list(commit_paths)

    if gitignore_paths:
        content = "".join("/" + p.lstrip("/") + "\n" for p in gitignore_paths)
        try:
            proc = _ssh_exec(host, "cat >> /etc/.gitignore",
                              input_bytes=content.encode(), timeout=COMMIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            return False, "SSH connection timed out while updating .gitignore"
        except OSError as exc:
            return False, f"Failed to run ssh: {exc}"
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace").strip()
            return False, f"Failed to update .gitignore: {err}"
        if ".gitignore" not in all_paths:
            all_paths.append(".gitignore")

    if not all_paths:
        return True, None

    add_argv = ["git", "-C", "/etc", "add", "--", *all_paths]
    commit_argv = ["git", "-C", "/etc", "commit", "-m", message, "--", *all_paths]
    cmd = " ".join(shlex.quote(a) for a in add_argv) + " && " + \
          " ".join(shlex.quote(a) for a in commit_argv)
    try:
        proc = _ssh_exec(host, cmd, timeout=COMMIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "SSH connection timed out"
    except OSError as exc:
        return False, f"Failed to run ssh: {exc}"
    if proc.returncode != 0:
        out = proc.stdout.decode(errors="replace").strip()
        err = proc.stderr.decode(errors="replace").strip()
        return False, "\n".join(p for p in (out, err) if p) or f"exit {proc.returncode}"
    return True, None


def commit_all(hosts_selections: dict[str, dict], message: str) -> dict[str, dict]:
    """hosts_selections: {host: {"commit": [paths], "gitignore": [paths]}}
    Runs commits for all involved machines in parallel.
    Returns {host: {"ok": bool, "error": str|None}}"""
    def _do(item):
        host, sel = item
        ok, err = commit_machine(host, message, sel.get("commit", []), sel.get("gitignore", []))
        return host, {"ok": ok, "error": err}

    results: dict[str, dict] = {}
    items = list(hosts_selections.items())
    if not items:
        return results
    with ThreadPoolExecutor(max_workers=min(32, len(items))) as pool:
        for host, res in pool.map(_do, items):
            results[host] = res
    return results
