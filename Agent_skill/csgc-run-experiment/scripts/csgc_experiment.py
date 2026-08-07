#!/usr/bin/env python3
"""Validate, execute, monitor, and record CSGC experiment batches."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import traceback
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[3]
HOST_REPO = Path("/home/xin/work-xie/mcsgc-real/linux-cs")
HOST_BUILD_SCRIPT = HOST_REPO / "build_f2fs.sh"
HOST_MODULE = HOST_REPO / "fs/f2fs/f2fs.ko"
BENCHMARK_DIR = REPO_ROOT / "host/benchmarks/scripts"
TEST_SCRIPT = BENCHMARK_DIR / "test.sh"
KERNEL_LOG_DIR = BENCHMARK_DIR / "TOSTUDY/mcsgc-0309"
COLLECTOR_SCRIPT = KERNEL_LOG_DIR / "old-mydmesg.sh"
ANALYSIS_SCRIPT = BENCHMARK_DIR / "draw-xie/breakdown.py"
ANALYSIS_ROOT = BENCHMARK_DIR / "draw-xie/breakdown-result"
RECORD_ROOT = REPO_ROOT / "experiment_records"
REMOTE_HOST = "192.168.98.31"
REMOTE_REPO = Path("/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc")
SSH_OPTIONS = (
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "StrictHostKeyChecking=yes",
)

EXPECTED_DEVICE_FIELDS = ("nvme0n1", "259:0", "0", "59.8G", "0", "disk")
AUTOMATION_LOCK = Path("/tmp/csgc-experiment-agent.lock")
ACTIVE_PROCESS_PATTERN = (
    r"[/](test|run_fio|run_filebench|run_ycsb|build_f2fs)\.sh|"
    r"[o]ld-mydmesg\.sh|(^|/)(fio|filebench)( |$)|[b]in/ycsb |"
    r"[f]ile_writer|[m]kfs\.f2fs|[f]sck\.f2fs|[m]ake .*M=fs/f2fs"
)
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
ANOMALY_RE = re.compile(
    r"FATAL:|ERROR:|Kernel panic|watchdog: BUG|BUG:|Oops:|Call Trace:|"
    r"I/O error|F2FS-fs.*(?:ERROR|error)",
    re.IGNORECASE,
)
MAX_INCREMENTAL_READ = 8 * 1024 * 1024


class ExperimentError(RuntimeError):
    """Represent a controlled validation or experiment failure."""


@dataclasses.dataclass(frozen=True)
class VersionExpectation:
    """Store optional Git version constraints for one repository."""

    branch: str | None = None
    commit: str | None = None
    allow_dirty: bool = True


@dataclasses.dataclass(frozen=True)
class TestCase:
    """Store one test.sh request, expected SSD mode, and repetition policy."""

    mode: str
    ssd_thread_mode: str
    config: str
    repetitions: int
    test_info: str
    other_info: str | None = None


@dataclasses.dataclass(frozen=True)
class ExperimentPlan:
    """Store a validated batch plan and its execution policy."""

    experiment_id: str
    description: str
    expected_host: VersionExpectation
    expected_ssd: VersionExpectation
    monitor_interval_seconds: int
    collector_start_timeout_seconds: int
    collector_stop_timeout_seconds: int
    test_stop_timeout_seconds: int
    analyze: bool
    tests: tuple[TestCase, ...]


def now_iso() -> str:
    """Return the current local timestamp in an unambiguous ISO format."""

    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def command_text(command: Sequence[str]) -> str:
    """Render an argument vector for logs without changing execution semantics."""

    return shlex.join(str(part) for part in command)


def require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    """Validate that one plan value is a JSON object."""

    if not isinstance(value, Mapping):
        raise ExperimentError(f"{field} must be a JSON object")
    return value


def reject_unknown_keys(
    data: Mapping[str, Any], allowed: set[str], field: str
) -> None:
    """Reject plan keys that would otherwise hide spelling or schema mistakes."""

    unknown = sorted(str(key) for key in data if key not in allowed)
    if unknown:
        raise ExperimentError(f"{field} contains unknown keys: {', '.join(unknown)}")


def require_bool(value: Any, field: str) -> bool:
    """Validate that one plan value is a JSON boolean."""

    if not isinstance(value, bool):
        raise ExperimentError(f"{field} must be true or false")
    return value


def require_ascii_line(value: Any, field: str, maximum: int = 512) -> str:
    """Validate a bounded single-line ASCII description."""

    if not isinstance(value, str):
        raise ExperimentError(f"{field} must be a string")
    if "\n" in value or "\r" in value:
        raise ExperimentError(f"{field} must be a single line")
    if len(value) > maximum:
        raise ExperimentError(f"{field} must contain at most {maximum} characters")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ExperimentError(f"{field} must contain ASCII characters only") from exc
    return value


def require_bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    """Validate an integer plan value against an inclusive range."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ExperimentError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ExperimentError(f"{field} must be in [{minimum}, {maximum}], got {value}")
    return value


def require_safe_token(value: Any, field: str) -> str:
    """Validate a token before using it in labels, paths, or tmux names."""

    if not isinstance(value, str) or not SAFE_TOKEN_RE.fullmatch(value):
        raise ExperimentError(
            f"{field} must match {SAFE_TOKEN_RE.pattern}, got {value!r}"
        )
    return value


def optional_safe_token(value: Any, field: str) -> str | None:
    """Validate an optional label token."""

    if value is None or value == "":
        return None
    return require_safe_token(value, field)


def require_branch_name(value: Any, field: str) -> str:
    """Validate an expected Git branch name, including common slash-separated names."""

    if not isinstance(value, str) or not BRANCH_RE.fullmatch(value):
        raise ExperimentError(f"{field} must match {BRANCH_RE.pattern}, got {value!r}")
    if ".." in value or "//" in value or value.endswith(("/", ".")):
        raise ExperimentError(f"{field} is not a valid branch name: {value!r}")
    return value


def parse_version_expectation(value: Any, field: str) -> VersionExpectation:
    """Parse optional branch, commit, and dirty-worktree constraints."""

    if value is None:
        return VersionExpectation()
    data = require_mapping(value, field)
    reject_unknown_keys(data, {"branch", "commit", "allow_dirty"}, field)
    branch = data.get("branch")
    commit = data.get("commit")
    allow_dirty = data.get("allow_dirty", True)
    if branch is not None:
        branch = require_branch_name(branch, f"{field}.branch")
    if commit is not None:
        if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
            raise ExperimentError(f"{field}.commit must be a 7-40 digit hex commit ID")
        commit = commit.lower()
    return VersionExpectation(
        branch=branch,
        commit=commit,
        allow_dirty=require_bool(allow_dirty, f"{field}.allow_dirty"),
    )


def validate_config_path(config: Any, field: str) -> str:
    """Validate that a config is a relative shell file contained in configs/."""

    if not isinstance(config, str) or not config:
        raise ExperimentError(f"{field} must be a nonempty string")
    try:
        config.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ExperimentError(f"{field} must contain ASCII characters only") from exc
    relative = Path(config)
    if relative.is_absolute() or ".." in relative.parts:
        raise ExperimentError(f"{field} must be a safe relative path")
    if not relative.parts or relative.parts[0] != "configs" or relative.suffix != ".sh":
        raise ExperimentError(f"{field} must name a .sh file under configs/")
    resolved = (BENCHMARK_DIR / relative).resolve()
    config_root = (BENCHMARK_DIR / "configs").resolve()
    if not resolved.is_relative_to(config_root):
        raise ExperimentError(f"{field} resolves outside configs/: {resolved}")
    if not resolved.is_file():
        raise ExperimentError(f"config file does not exist: {resolved}")
    return relative.as_posix()


def parse_test_case(value: Any, index: int) -> TestCase:
    """Parse and validate one requested test tuple."""

    field = f"tests[{index}]"
    data = require_mapping(value, field)
    reject_unknown_keys(
        data,
        {
            "mode",
            "ssd_thread_mode",
            "config",
            "repetitions",
            "test_info",
            "other_info",
        },
        field,
    )
    mode = require_safe_token(data.get("mode"), f"{field}.mode")
    if mode not in {"ori", "iplfs"} and "csgc" not in mode:
        raise ExperimentError(
            f"{field}.mode must be ori, iplfs, or contain case-sensitive 'csgc'"
        )
    ssd_thread_mode = require_safe_token(
        data.get("ssd_thread_mode"), f"{field}.ssd_thread_mode"
    )
    if ssd_thread_mode not in {"ssd1t", "ssd2t"}:
        raise ExperimentError(f"{field}.ssd_thread_mode must be ssd1t or ssd2t")
    return TestCase(
        mode=mode,
        ssd_thread_mode=ssd_thread_mode,
        config=validate_config_path(data.get("config"), f"{field}.config"),
        repetitions=require_bounded_int(
            data.get("repetitions"), f"{field}.repetitions", 1, 100
        ),
        test_info=require_safe_token(data.get("test_info"), f"{field}.test_info"),
        other_info=optional_safe_token(data.get("other_info"), f"{field}.other_info"),
    )


def load_plan(path: Path) -> ExperimentPlan:
    """Load and fully validate an experiment plan from JSON."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExperimentError(f"plan does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ExperimentError(f"invalid JSON in {path}: {exc}") from exc
    data = require_mapping(raw, "plan")
    reject_unknown_keys(
        data,
        {
            "experiment_id",
            "description",
            "expected_host",
            "expected_ssd",
            "monitor_interval_seconds",
            "collector_start_timeout_seconds",
            "collector_stop_timeout_seconds",
            "test_stop_timeout_seconds",
            "analyze",
            "tests",
        },
        "plan",
    )
    raw_tests = data.get("tests")
    if not isinstance(raw_tests, list) or not raw_tests:
        raise ExperimentError("tests must be a nonempty JSON array")
    description = require_ascii_line(data.get("description", ""), "description")
    return ExperimentPlan(
        experiment_id=require_safe_token(data.get("experiment_id"), "experiment_id"),
        description=description,
        expected_host=parse_version_expectation(
            data.get("expected_host"), "expected_host"
        ),
        expected_ssd=parse_version_expectation(data.get("expected_ssd"), "expected_ssd"),
        monitor_interval_seconds=require_bounded_int(
            data.get("monitor_interval_seconds", 30),
            "monitor_interval_seconds",
            5,
            600,
        ),
        collector_start_timeout_seconds=require_bounded_int(
            data.get("collector_start_timeout_seconds", 30),
            "collector_start_timeout_seconds",
            5,
            300,
        ),
        collector_stop_timeout_seconds=require_bounded_int(
            data.get("collector_stop_timeout_seconds", 180),
            "collector_stop_timeout_seconds",
            10,
            900,
        ),
        test_stop_timeout_seconds=require_bounded_int(
            data.get("test_stop_timeout_seconds", 60),
            "test_stop_timeout_seconds",
            10,
            600,
        ),
        analyze=require_bool(data.get("analyze", True), "analyze"),
        tests=tuple(parse_test_case(item, index) for index, item in enumerate(raw_tests)),
    )


def plan_as_dict(plan: ExperimentPlan) -> dict[str, Any]:
    """Convert a validated plan into a stable JSON-serializable dictionary."""

    return dataclasses.asdict(plan)


def run_capture(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 30,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a short command and capture its complete text output."""

    try:
        result = subprocess.run(
            [str(part) for part in command],
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        raise ExperimentError(
            f"command timed out after {timeout}s: {command_text(command)}\n{output.rstrip()}"
        ) from exc
    except OSError as exc:
        raise ExperimentError(
            f"cannot start command: {command_text(command)}: {exc}"
        ) from exc
    if check and result.returncode != 0:
        raise ExperimentError(
            f"command failed ({result.returncode}): {command_text(command)}\n"
            f"{result.stdout.rstrip()}"
        )
    return result


def tail_text(path: Path, max_bytes: int = 32768) -> str:
    """Read a bounded tail from a text file for failure reporting."""

    if not path.exists():
        return ""
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        stream.seek(max(0, size - max_bytes))
        return stream.read().decode("utf-8", errors="replace")


def run_logged_command(
    command: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    heartbeat_seconds: int = 30,
) -> int:
    """Run a potentially long command while logging output and printing heartbeats."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    last_heartbeat = started
    with log_path.open("a", encoding="utf-8") as log_stream:
        log_stream.write(f"$ {command_text(command)}\n")
        log_stream.flush()
        process = subprocess.Popen(
            [str(part) for part in command],
            cwd=str(cwd),
            text=True,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
        )
        while process.poll() is None:
            time.sleep(1)
            current = time.monotonic()
            if current - last_heartbeat >= heartbeat_seconds:
                elapsed = int(current - started)
                print(f"Command still running after {elapsed}s: {command_text(command)}")
                sys.stdout.flush()
                last_heartbeat = current
        return int(process.returncode)


def sha256_file(path: Path) -> str:
    """Compute a SHA-256 digest without loading the complete file into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def markdown_cell(value: Any) -> str:
    """Escape a value for a compact Markdown table cell."""

    if value is None or value == "":
        return "-"
    return str(value).replace("|", "\\|").replace("\n", "<br>")


class ExperimentRecorder:
    """Persist append-only events plus current JSON and Markdown summaries."""

    def __init__(self, plan: ExperimentPlan, source_plan: Path) -> None:
        """Create a new record directory without overwriting an older experiment."""

        self.plan = plan
        self.directory = RECORD_ROOT / plan.experiment_id
        self.state_path = self.directory / "state.json"
        self.events_path = self.directory / "events.jsonl"
        self.record_path = self.directory / "record.md"
        RECORD_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise ExperimentError(
                f"experiment record already exists; choose a new experiment_id: {self.directory}"
            ) from exc
        (self.directory / "plan.json").write_text(
            source_plan.read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.state: dict[str, Any] = {
            "experiment_id": plan.experiment_id,
            "description": plan.description,
            "status": "initializing",
            "started_at": now_iso(),
            "finished_at": None,
            "environment": {},
            "runs": [],
            "events": [],
            "failure": None,
        }
        self._persist()
        self.event("INFO", "Experiment record initialized")

    def event(self, level: str, message: str, **details: Any) -> None:
        """Append one timestamped event and refresh current summaries."""

        event = {
            "timestamp": now_iso(),
            "level": level,
            "message": message,
            "details": details,
        }
        self.state["events"].append(event)
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
        print(f"[{event['timestamp']}] {level}: {message}")
        sys.stdout.flush()
        self._persist()

    def set_status(self, status: str) -> None:
        """Update the batch status and persist the summaries."""

        self.state["status"] = status
        self._persist()

    def set_environment(self, environment: Mapping[str, Any]) -> None:
        """Record the preflight environment snapshot."""

        self.state["environment"] = dict(environment)
        self._persist()

    def add_run(self, run: Mapping[str, Any]) -> int:
        """Add one repetition record and return its list index."""

        self.state["runs"].append(dict(run))
        self._persist()
        return len(self.state["runs"]) - 1

    def update_run(self, index: int, **updates: Any) -> None:
        """Update one repetition record with newly observed facts."""

        self.state["runs"][index].update(updates)
        self._persist()

    def fail(self, message: str) -> None:
        """Mark the batch failed and preserve its first failure reason."""

        self.state["status"] = "failed"
        self.state["finished_at"] = now_iso()
        if self.state["failure"] is None:
            self.state["failure"] = message
        self.event("ERROR", message)

    def complete(self, analyzed: bool) -> None:
        """Mark the batch complete after all tests and analyses succeed."""

        self.state["status"] = "completed"
        self.state["finished_at"] = now_iso()
        message = "All requested tests completed"
        if analyzed:
            message += " and deterministic log analyses completed"
        self.event("INFO", message)

    def _persist(self) -> None:
        """Write current machine-readable and human-readable state."""

        self.state_path.write_text(
            json.dumps(self.state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.record_path.write_text(self._render_markdown(), encoding="utf-8")

    def _render_markdown(self) -> str:
        """Render the current experiment state as a reviewable Markdown record."""

        environment = self.state.get("environment", {})
        host = environment.get("host_git", {})
        ssd = environment.get("ssd_git", {})
        lines = [
            "# CSGC Experiment Record",
            "",
            f"- Experiment ID: `{self.state['experiment_id']}`",
            f"- Description: {markdown_cell(self.state.get('description'))}",
            f"- Status: `{self.state['status']}`",
            f"- Started: `{self.state['started_at']}`",
            f"- Finished: `{self.state.get('finished_at') or '-'}`",
            f"- Failure: {self.state.get('failure') or '-'}",
            "",
            "## Environment",
            "",
            f"- Host branch: `{host.get('branch', '-')}`",
            f"- Host commit: `{host.get('commit', '-')}`",
            f"- Host dirty paths: `{', '.join(host.get('dirty_paths', [])) or '-'}`",
            f"- Server-31 branch: `{ssd.get('branch', '-')}`",
            f"- Server-31 commit: `{ssd.get('commit', '-')}`",
            f"- Server-31 dirty paths: `{', '.join(ssd.get('dirty_paths', [])) or '-'}`",
            f"- Detected SSD thread mode: `{environment.get('ssd_thread_mode', '-')}`",
            f"- Device: `{environment.get('device_line', '-')}`",
            f"- Kernel: `{environment.get('kernel_release', '-')}`",
            "",
            "The server-31 checkout is provenance evidence, not proof of the flashed SSD binary.",
            "",
            "## Runs",
            "",
            "| # | Mode | SSD | Config | Rep | Status | Kernel log | Output root | Analysis |",
            "|---:|---|---|---|---:|---|---|---|---|",
        ]
        for run in self.state.get("runs", []):
            lines.append(
                "| {sequence} | {mode} | {ssd} | {config} | {repetition} | {status} | "
                "{kernel_log} | {output_root} | {analysis_dir} |".format(
                    sequence=markdown_cell(run.get("sequence")),
                    mode=markdown_cell(run.get("mode")),
                    ssd=markdown_cell(run.get("ssd_thread_mode")),
                    config=markdown_cell(run.get("config")),
                    repetition=markdown_cell(run.get("repetition")),
                    status=markdown_cell(run.get("status")),
                    kernel_log=markdown_cell(run.get("kernel_log")),
                    output_root=markdown_cell(run.get("output_root")),
                    analysis_dir=markdown_cell(run.get("analysis_dir")),
                )
            )
        lines.extend(
            [
                "",
                "## Event Log",
                "",
                "| Time | Level | Event |",
                "|---|---|---|",
            ]
        )
        for event in self.state.get("events", []):
            lines.append(
                f"| {markdown_cell(event['timestamp'])} | {markdown_cell(event['level'])} | "
                f"{markdown_cell(event['message'])} |"
            )
        lines.extend(["", "## Agent Analysis", "", "Pending agent analysis.", ""])
        return "\n".join(lines)


class ExperimentLock:
    """Hold a nonblocking host-wide lock for one automation batch."""

    def __init__(self, path: Path = AUTOMATION_LOCK) -> None:
        """Initialize the lock file handle container."""

        self.path = path
        self.stream: Any = None

    def __enter__(self) -> "ExperimentLock":
        """Acquire the automation lock or fail without waiting."""

        self.stream = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.stream.close()
            self.stream = None
            raise ExperimentError(
                f"another CSGC automation batch holds {self.path}"
            ) from exc
        self.stream.seek(0)
        self.stream.truncate()
        self.stream.write(f"pid={os.getpid()} started={now_iso()}\n")
        self.stream.flush()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Release the lock while preserving the lock file on disk."""

        if self.stream is not None:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()


def ensure_required_paths() -> None:
    """Verify all fixed local scripts and directories before preflight."""

    required = (
        REPO_ROOT,
        HOST_REPO,
        HOST_BUILD_SCRIPT,
        BENCHMARK_DIR,
        TEST_SCRIPT,
        KERNEL_LOG_DIR,
        COLLECTOR_SCRIPT,
        ANALYSIS_SCRIPT,
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ExperimentError(f"required paths are missing: {', '.join(missing)}")
    if REPO_ROOT != Path("/home/xin/artifact-csgc"):
        raise ExperimentError(f"unexpected repository root derived from skill path: {REPO_ROOT}")
    not_executable = [
        str(path)
        for path in (HOST_BUILD_SCRIPT, TEST_SCRIPT, COLLECTOR_SCRIPT)
        if not os.access(path, os.X_OK)
    ]
    if not_executable:
        raise ExperimentError(f"required scripts are not executable: {', '.join(not_executable)}")


def ensure_required_commands() -> None:
    """Verify external command availability without installing during an experiment."""

    commands = ("findmnt", "git", "lsblk", "pgrep", "python3", "ssh", "sudo", "tee", "tmux")
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise ExperimentError(f"required commands are missing: {', '.join(missing)}")


def parse_git_status(status_text: str) -> list[str]:
    """Extract dirty path entries from porcelain status output."""

    paths: list[str] = []
    for line in status_text.splitlines():
        if line.startswith("##") or not line.strip():
            continue
        paths.append(line.rstrip())
    return paths


def local_git_info(repo: Path) -> dict[str, Any]:
    """Collect the local repository branch, commit, and dirty paths."""

    branch = run_capture(
        ("git", "--no-optional-locks", "-C", str(repo), "branch", "--show-current")
    ).stdout.strip()
    commit = run_capture(
        ("git", "--no-optional-locks", "-C", str(repo), "rev-parse", "HEAD")
    ).stdout.strip()
    status = run_capture(
        (
            "git",
            "--no-optional-locks",
            "-C",
            str(repo),
            "status",
            "--short",
            "--branch",
        )
    ).stdout
    return {
        "branch": branch or "(detached)",
        "commit": commit,
        "dirty_paths": parse_git_status(status),
        "status": status.rstrip(),
    }


def remote_git_command(*git_args: str) -> str:
    """Run one fixed read-only Git query against the server-31 repository."""

    allowed = {
        ("branch", "--show-current"),
        ("rev-parse", "HEAD"),
        ("status", "--short", "--branch"),
    }
    if tuple(git_args) not in allowed:
        raise ExperimentError(f"refusing unsupported server-31 Git query: {git_args}")
    command = (
        "ssh",
        *SSH_OPTIONS,
        REMOTE_HOST,
        "git",
        "--no-optional-locks",
        "-C",
        str(REMOTE_REPO),
        *git_args,
    )
    return run_capture(command, timeout=20).stdout


def remote_git_info() -> dict[str, Any]:
    """Collect server-31 Git provenance without modifying remote state."""

    branch = remote_git_command("branch", "--show-current").strip()
    commit = remote_git_command("rev-parse", "HEAD").strip()
    status = remote_git_command("status", "--short", "--branch")
    return {
        "host": REMOTE_HOST,
        "repo": str(REMOTE_REPO),
        "branch": branch or "(detached)",
        "commit": commit,
        "dirty_paths": parse_git_status(status),
        "status": status.rstrip(),
        "proves_flashed_binary": False,
    }


def detect_ssd_thread_mode() -> dict[str, str]:
    """Run test.sh's read-only Vitis detector and preserve its diagnostics."""

    try:
        result = subprocess.run(
            (str(TEST_SCRIPT), "--detect-ssd-thread-mode"),
            cwd=BENCHMARK_DIR,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExperimentError("SSD thread-mode detection timed out") from exc
    except OSError as exc:
        raise ExperimentError(f"cannot run SSD thread-mode detection: {exc}") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostics"
        raise ExperimentError(
            f"SSD thread-mode detection failed with status {result.returncode}: {detail}"
        )
    mode = result.stdout.strip()
    if mode not in {"ssd1t", "ssd2t"}:
        raise ExperimentError(f"SSD thread-mode detector returned invalid output: {mode!r}")
    return {"mode": mode, "diagnostics": result.stderr.strip()}


def check_version_expectation(
    label: str, actual: Mapping[str, Any], expected: VersionExpectation
) -> None:
    """Fail when observed Git state violates an explicit plan constraint."""

    if expected.branch is not None and actual.get("branch") != expected.branch:
        raise ExperimentError(
            f"{label} branch mismatch: expected {expected.branch}, got {actual.get('branch')}"
        )
    if expected.commit is not None and not str(actual.get("commit", "")).startswith(
        expected.commit
    ):
        raise ExperimentError(
            f"{label} commit mismatch: expected prefix {expected.commit}, "
            f"got {actual.get('commit')}"
        )
    dirty_paths = actual.get("dirty_paths", [])
    if dirty_paths and not expected.allow_dirty:
        raise ExperimentError(f"{label} worktree is dirty: {dirty_paths}")


def check_tmux_visibility() -> str:
    """Ensure preflight can see the real user tmux server rather than a sandbox view."""

    result = run_capture(
        (
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{session_name}|#{pane_pid}|#{pane_current_command}|#{pane_dead}",
        ),
        check=False,
    )
    output = result.stdout.strip()
    if result.returncode == 0:
        return output
    lower = output.lower()
    if "no server running" in lower or "no sessions" in lower:
        return ""
    raise ExperimentError(
        "cannot inspect the host tmux server; run preflight outside the restricted sandbox: "
        + output
    )


def active_processes() -> str:
    """Return exact benchmark and collector processes that block a new run."""

    result = run_capture(("pgrep", "-af", ACTIVE_PROCESS_PATTERN), check=False)
    if result.returncode == 1:
        return ""
    if result.returncode != 0:
        raise ExperimentError(f"pgrep failed: {result.stdout.rstrip()}")
    return result.stdout.strip()


def mounted_f2fs() -> str:
    """Return active F2FS mounts that block module rebuild or a new test."""

    result = run_capture(
        ("findmnt", "-rn", "-t", "f2fs", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS"),
        check=False,
    )
    if result.returncode == 1:
        return ""
    if result.returncode != 0:
        raise ExperimentError(f"findmnt failed: {result.stdout.rstrip()}")
    return result.stdout.strip()


def ensure_no_active_experiment() -> dict[str, str]:
    """Reject a new run when a benchmark, collector, or F2FS mount is active."""

    panes = check_tmux_visibility()
    processes = active_processes()
    mounts = mounted_f2fs()
    if processes:
        raise ExperimentError(f"an experiment-related process is already active:\n{processes}")
    if mounts:
        raise ExperimentError(f"an F2FS filesystem is already mounted:\n{mounts}")
    return {"tmux_panes": panes, "active_processes": processes, "f2fs_mounts": mounts}


def device_identity() -> str:
    """Require the exact OpenSSD lsblk identity specified by the experiment workflow."""

    result = run_capture(("lsblk", "-dn", "-o", "NAME,MAJ:MIN,RM,SIZE,RO,TYPE"))
    lines = [line.split() for line in result.stdout.splitlines() if line.strip()]
    if list(EXPECTED_DEVICE_FIELDS) not in lines:
        rendered = " ".join(EXPECTED_DEVICE_FIELDS)
        raise ExperimentError(
            f"expected OpenSSD device line is absent: {rendered}\nObserved:\n"
            f"{result.stdout.rstrip()}"
        )
    return " ".join(EXPECTED_DEVICE_FIELDS)


def ensure_noninteractive_sudo() -> None:
    """Require sudo commands to work without a prompt inside detached tmux sessions."""

    result = run_capture(("sudo", "-n", "true"), check=False)
    if result.returncode != 0:
        raise ExperimentError(
            "non-interactive sudo is unavailable; detached tmux sessions cannot answer a "
            "password prompt. Authorize the exact experiment commands before execution."
        )


def preflight_environment(plan: ExperimentPlan, require_sudo: bool) -> dict[str, Any]:
    """Collect and validate all read-only facts needed before state changes."""

    ensure_required_paths()
    ensure_required_commands()
    conflicts = ensure_no_active_experiment()
    host = local_git_info(HOST_REPO)
    ssd = remote_git_info()
    ssd_thread = detect_ssd_thread_mode()
    check_version_expectation("Host", host, plan.expected_host)
    check_version_expectation("server-31 SSD", ssd, plan.expected_ssd)
    if require_sudo:
        ensure_noninteractive_sudo()
    return {
        "checked_at": now_iso(),
        "kernel_release": os.uname().release,
        "device_line": device_identity(),
        "host_git": host,
        "ssd_git": ssd,
        "ssd_thread_mode": ssd_thread["mode"],
        "ssd_thread_detection": ssd_thread["diagnostics"],
        "sudo_noninteractive": require_sudo,
        **conflicts,
    }


def module_loaded() -> bool:
    """Check whether the host F2FS module is currently loaded."""

    try:
        modules = Path("/proc/modules").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ExperimentError(f"cannot read /proc/modules: {exc}") from exc
    return any(line.startswith("f2fs ") for line in modules.splitlines())


def prepare_host_module(recorder: ExperimentRecorder, run_index: int) -> str:
    """Remove an idle F2FS module, rebuild it, insert it, and record its digest."""

    build_log = recorder.directory / f"run-{run_index + 1:03d}-host-build.log"
    if module_loaded():
        recorder.event("INFO", "Removing the existing idle F2FS module", run=run_index + 1)
        result = run_capture(("sudo", "-n", "rmmod", "f2fs"), check=False, timeout=60)
        if result.returncode != 0:
            raise ExperimentError(
                f"sudo rmmod f2fs failed ({result.returncode}): {result.stdout.rstrip()}"
            )
    else:
        recorder.event("INFO", "F2FS module was already absent", run=run_index + 1)
    recorder.event("INFO", "Starting Host F2FS build", run=run_index + 1)
    status = run_logged_command(
        ("sudo", "-n", str(HOST_BUILD_SCRIPT)),
        cwd=HOST_REPO,
        log_path=build_log,
    )
    if status != 0:
        raise ExperimentError(
            f"Host build failed with status {status}; see {build_log}\n{tail_text(build_log)}"
        )
    if not module_loaded():
        raise ExperimentError(f"Host build exited zero but F2FS is not loaded; see {build_log}")
    if not HOST_MODULE.is_file():
        raise ExperimentError(f"built module is missing after build: {HOST_MODULE}")
    digest = sha256_file(HOST_MODULE)
    recorder.event(
        "INFO",
        "Host F2FS build completed",
        run=run_index + 1,
        module_sha256=digest,
        build_log=str(build_log),
    )
    return digest


def next_kernel_log(case: TestCase) -> Path:
    """Apply old-mydmesg.sh naming rules to allocate a new per-test log."""

    fields = [dt.datetime.now().strftime("%m%d"), case.mode, case.ssd_thread_mode, case.test_info]
    if case.other_info:
        fields.append(case.other_info)
    stem = "-".join(fields)
    prefix = stem
    number = 1
    numeric_match = re.fullmatch(r"(.*)-([0-9]+)", stem)
    if numeric_match:
        prefix = numeric_match.group(1)
        number = int(numeric_match.group(2))
    while True:
        candidate = KERNEL_LOG_DIR / f"{prefix}-{number}.log"
        if not candidate.exists():
            return candidate
        number += 1


def make_tmux_name(kind: str, experiment_id: str, sequence: int) -> str:
    """Build a bounded unique tmux name owned by this experiment only."""

    raw = f"csgc-auto-{kind}-{experiment_id}-{sequence:03d}"
    digest = hashlib.sha256(raw.encode("ascii")).hexdigest()[:8]
    return f"{raw[:64]}-{digest}"


def tmux_session_exists(name: str) -> bool:
    """Check one exact tmux session name without matching other sessions."""

    result = run_capture(("tmux", "has-session", "-t", f"={name}"), check=False)
    return result.returncode == 0


def build_session_script(
    command: Sequence[str], cwd: Path, console_path: Path, status_path: Path
) -> str:
    """Create a Bash payload that records console output and the exact command status."""

    return "\n".join(
        (
            "set +e",
            f"cd -- {shlex.quote(str(cwd))}",
            f"exec > >(tee -a -- {shlex.quote(str(console_path))}) 2>&1",
            f"echo '$ {command_text(command)}'",
            command_text(command),
            "command_status=$?",
            f"printf '%s\\n' \"$command_status\" > {shlex.quote(str(status_path))}",
            "exit \"$command_status\"",
        )
    )


def start_tmux_session(name: str, script: str, cwd: Path) -> None:
    """Start one detached tmux session with an explicit Bash payload."""

    if tmux_session_exists(name):
        raise ExperimentError(f"refusing to reuse existing tmux session: {name}")
    run_capture(
        ("tmux", "new-session", "-d", "-s", name, "-c", str(cwd), "/bin/bash", "-lc", script),
        timeout=30,
    )


def read_status_file(path: Path) -> int:
    """Read a detached command status file and validate its integer format."""

    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ExperimentError(f"tmux command exited without a status file: {path}") from exc
    if not re.fullmatch(r"[0-9]+", text):
        raise ExperimentError(f"invalid status file {path}: {text!r}")
    return int(text)


def wait_for_collector_ready(
    session: str, console_path: Path, timeout_seconds: int
) -> None:
    """Wait for the collector readiness line or fail if its session exits."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if "Tracing dmesg to:" in tail_text(console_path):
            return
        if not tmux_session_exists(session):
            raise ExperimentError(
                f"collector session exited before readiness: {session}\n"
                f"{tail_text(console_path)}"
            )
        time.sleep(0.5)
    raise ExperimentError(
        f"collector did not become ready within {timeout_seconds}s: {session}\n"
        f"{tail_text(console_path)}"
    )


def send_ctrl_c(session: str) -> None:
    """Send Ctrl+C to one exact tmux session owned by the current run."""

    result = run_capture(
        ("tmux", "send-keys", "-t", f"={session}", "C-c"),
        timeout=30,
        check=False,
    )
    if result.returncode != 0 and tmux_session_exists(session):
        raise ExperimentError(
            f"failed to send Ctrl+C to {session}: {result.stdout.rstrip()}"
        )


def wait_session_exit(session: str, timeout_seconds: int) -> bool:
    """Wait a bounded interval for one tmux session to exit naturally."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not tmux_session_exists(session):
            return True
        time.sleep(1)
    return not tmux_session_exists(session)


def read_increment(path: Path, offset: int) -> tuple[str, int, bool]:
    """Read only newly appended bytes, bounding work for unexpectedly large bursts."""

    if not path.exists():
        return "", offset, False
    size = path.stat().st_size
    if size < offset:
        offset = 0
    skipped = size - offset > MAX_INCREMENTAL_READ
    start = max(offset, size - MAX_INCREMENTAL_READ) if skipped else offset
    with path.open("rb") as stream:
        stream.seek(start)
        data = stream.read()
    return data.decode("utf-8", errors="replace"), size, skipped


def anomaly_excerpt(text: str) -> str | None:
    """Return a concise fatal-signature excerpt from newly appended text."""

    match = ANOMALY_RE.search(text)
    if match is None:
        return None
    start = max(0, match.start() - 200)
    end = min(len(text), match.end() + 500)
    return text[start:end].replace("\x00", "")


def monitor_test(
    session: str,
    console_path: Path,
    kernel_log: Path,
    interval_seconds: int,
) -> None:
    """Monitor appended output until the test session exits or a fatal signature appears."""

    offsets = {console_path: 0, kernel_log: 0}
    last_heartbeat = time.monotonic()
    while tmux_session_exists(session):
        for path in (console_path, kernel_log):
            text, offsets[path], skipped = read_increment(path, offsets[path])
            if skipped:
                raise ExperimentError(
                    f"incremental monitor fell behind by more than "
                    f"{MAX_INCREMENTAL_READ} bytes for {path}"
                )
            excerpt = anomaly_excerpt(text)
            if excerpt is not None:
                raise ExperimentError(f"fatal signature detected in {path}:\n{excerpt}")
        current = time.monotonic()
        if current - last_heartbeat >= 300:
            print(f"Test session remains active: {session}")
            sys.stdout.flush()
            last_heartbeat = current
        time.sleep(interval_seconds)
    for path in (console_path, kernel_log):
        text, offsets[path], _ = read_increment(path, offsets[path])
        excerpt = anomaly_excerpt(text)
        if excerpt is not None:
            raise ExperimentError(f"fatal signature detected in {path}:\n{excerpt}")


def file_contains(path: Path, needle: str) -> bool:
    """Search a completed text file after the measured workload has ended."""

    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            return any(needle in line for line in stream)
    except FileNotFoundError:
        return False


def snapshot_output_roots(case: TestCase) -> tuple[Path, set[Path]]:
    """Snapshot existing timestamp roots for the exact mode and SSD label."""

    root = BENCHMARK_DIR / f"outputs-{case.mode}-{case.ssd_thread_mode}"
    existing = (
        {path.resolve() for path in root.iterdir() if path.is_dir()}
        if root.is_dir()
        else set()
    )
    return root, existing


def discover_output_root(root: Path, before: set[Path]) -> Path:
    """Require exactly one new timestamp output root after one test.sh invocation."""

    after = (
        {path.resolve() for path in root.iterdir() if path.is_dir()}
        if root.is_dir()
        else set()
    )
    created = sorted(after - before)
    if len(created) != 1:
        raise ExperimentError(
            f"expected exactly one new output root under {root}, found {len(created)}: {created}"
        )
    return created[0]


def stop_owned_test(
    session: str, timeout_seconds: int, recorder: ExperimentRecorder, run_number: int
) -> bool:
    """Request a graceful Ctrl+C stop for the current test without force-killing it."""

    if not tmux_session_exists(session):
        return True
    recorder.event("WARNING", "Sending Ctrl+C to the owned test session", run=run_number)
    send_ctrl_c(session)
    stopped = wait_session_exit(session, timeout_seconds)
    if not stopped:
        recorder.event(
            "ERROR",
            "Owned test session did not exit; leaving it and its collector active",
            run=run_number,
            session=session,
        )
    return stopped


def stop_collector(
    session: str,
    status_path: Path,
    console_path: Path,
    timeout_seconds: int,
) -> int:
    """Send Ctrl+C to the collector and wait for its scripted cleanup to finish."""

    if tmux_session_exists(session):
        send_ctrl_c(session)
        if not wait_session_exit(session, timeout_seconds):
            raise ExperimentError(
                f"collector did not exit within {timeout_seconds}s; session remains: {session}"
            )
    status = read_status_file(status_path)
    if status != 0:
        raise ExperimentError(
            f"collector cleanup failed with status {status}: {session}\n{tail_text(console_path)}"
        )
    if "Running: python3" not in tail_text(console_path, max_bytes=131072):
        raise ExperimentError(
            f"collector exited without recorded finderror.py cleanup: {console_path}"
        )
    return status


def run_one_repetition(
    plan: ExperimentPlan,
    case: TestCase,
    repetition: int,
    sequence: int,
    recorder: ExperimentRecorder,
) -> None:
    """Execute one complete Host-build, collector, test, and collector-cleanup lifecycle."""

    run_data: dict[str, Any] = {
        "sequence": sequence,
        "mode": case.mode,
        "ssd_thread_mode": case.ssd_thread_mode,
        "config": case.config,
        "repetition": repetition,
        "status": "preflight",
        "started_at": now_iso(),
        "finished_at": None,
        "kernel_log": None,
        "output_root": None,
        "analysis_dir": None,
    }
    run_state_index = recorder.add_run(run_data)
    run_number = run_state_index + 1
    collector_session = make_tmux_name("log", plan.experiment_id, sequence)
    test_session = make_tmux_name("lab", plan.experiment_id, sequence)
    collector_console = recorder.directory / f"run-{sequence:03d}-collector-console.log"
    collector_status_path = recorder.directory / f"run-{sequence:03d}-collector.status"
    test_console = recorder.directory / f"run-{sequence:03d}-test-console.log"
    test_status_path = recorder.directory / f"run-{sequence:03d}-test.status"
    kernel_log = next_kernel_log(case)
    test_started = False
    collector_started = False
    collector_stop_requested = False

    try:
        recorder.event("INFO", "Running per-test preflight", run=run_number)
        environment = preflight_environment(plan, require_sudo=True)
        recorder.update_run(run_state_index, environment=environment)
        detected_ssd_mode = environment["ssd_thread_mode"]
        if detected_ssd_mode != case.ssd_thread_mode:
            raise ExperimentError(
                "server-31 Vitis SSD thread mode mismatch: "
                f"expected {case.ssd_thread_mode}, detected {detected_ssd_mode}"
            )
        module_digest = prepare_host_module(recorder, run_state_index)
        recorder.update_run(
            run_state_index,
            status="host_module_ready",
            module_sha256=module_digest,
            host_build_log=str(recorder.directory / f"run-{run_number:03d}-host-build.log"),
            kernel_log=str(kernel_log),
            collector_session=collector_session,
            test_session=test_session,
            collector_console=str(collector_console),
            test_console=str(test_console),
        )

        collector_command = ("sudo", "-n", str(COLLECTOR_SCRIPT), str(kernel_log))
        collector_payload = build_session_script(
            collector_command, KERNEL_LOG_DIR, collector_console, collector_status_path
        )
        start_tmux_session(collector_session, collector_payload, KERNEL_LOG_DIR)
        collector_started = True
        wait_for_collector_ready(
            collector_session, collector_console, plan.collector_start_timeout_seconds
        )
        recorder.event(
            "INFO",
            "Kernel-log collector is ready",
            run=run_number,
            session=collector_session,
            log=str(kernel_log),
        )
        recorder.update_run(run_state_index, status="collector_ready")

        output_parent, output_before = snapshot_output_roots(case)
        test_command = (
            "sudo",
            "-n",
            f"CSGC_EXPECTED_SSD_THREAD_MODE={case.ssd_thread_mode}",
            str(TEST_SCRIPT),
            case.mode,
            case.config,
        )
        test_payload = build_session_script(
            test_command, BENCHMARK_DIR, test_console, test_status_path
        )
        start_tmux_session(test_session, test_payload, BENCHMARK_DIR)
        test_started = True
        recorder.event(
            "INFO",
            "Benchmark test started",
            run=run_number,
            session=test_session,
            command=command_text(test_command),
        )
        recorder.update_run(run_state_index, status="test_running")
        monitor_test(
            test_session,
            test_console,
            kernel_log,
            plan.monitor_interval_seconds,
        )
        test_started = False
        test_status = read_status_file(test_status_path)
        recorder.event(
            "INFO" if test_status == 0 else "ERROR",
            "Benchmark test exited",
            run=run_number,
            status=test_status,
        )
        recorder.update_run(
            run_state_index, status="test_exited", test_status=test_status
        )

        collector_stop_requested = True
        collector_status = stop_collector(
            collector_session,
            collector_status_path,
            collector_console,
            plan.collector_stop_timeout_seconds,
        )
        collector_started = False
        recorder.event(
            "INFO",
            "Kernel-log collector finalized",
            run=run_number,
            status=collector_status,
        )
        recorder.update_run(run_state_index, collector_status=collector_status)

        if test_status != 0:
            raise ExperimentError(
                f"test.sh failed with status {test_status}; see {test_console}"
            )
        output_root = discover_output_root(output_parent, output_before)
        if not file_contains(test_console, "Test script completed."):
            raise ExperimentError(
                f"test.sh exited zero without 'Test script completed.': {test_console}"
            )
        if not kernel_log.is_file() or kernel_log.stat().st_size == 0:
            raise ExperimentError(f"kernel log is missing or empty: {kernel_log}")
        recorder.update_run(
            run_state_index,
            status="completed",
            finished_at=now_iso(),
            test_status=test_status,
            collector_status=collector_status,
            output_root=str(output_root),
        )
        recorder.event(
            "INFO",
            "One complete test repetition succeeded",
            run=run_number,
            output_root=str(output_root),
        )
    except BaseException:
        test_stopped = True
        if test_started:
            try:
                test_stopped = stop_owned_test(
                    test_session, plan.test_stop_timeout_seconds, recorder, run_number
                )
            except ExperimentError as cleanup_error:
                test_stopped = False
                recorder.event(
                    "ERROR",
                    f"Test cleanup after failure also failed: {cleanup_error}",
                    run=run_number,
                )
        if collector_started and test_stopped and not collector_stop_requested:
            try:
                collector_stop_requested = True
                stop_collector(
                    collector_session,
                    collector_status_path,
                    collector_console,
                    plan.collector_stop_timeout_seconds,
                )
                collector_started = False
                recorder.event(
                    "INFO", "Collector finalized after test failure", run=run_number
                )
            except ExperimentError as cleanup_error:
                recorder.event(
                    "ERROR",
                    f"Collector cleanup after failure also failed: {cleanup_error}",
                    run=run_number,
                )
        elif collector_started and collector_stop_requested:
            recorder.event(
                "WARNING",
                "Collector stop was already requested; no second Ctrl+C was sent",
                run=run_number,
                session=collector_session,
            )
        recorder.update_run(
            run_state_index,
            status="failed",
            finished_at=now_iso(),
            remaining_test_session=tmux_session_exists(test_session),
            remaining_collector_session=tmux_session_exists(collector_session),
        )
        raise


def analysis_directories_for_log(log_path: Path) -> set[Path]:
    """List parser result directories associated with one kernel-log stem."""

    if not ANALYSIS_ROOT.is_dir():
        return set()
    stem = log_path.name[:-4] if log_path.name.endswith(".log") else log_path.stem
    return {path.resolve() for path in ANALYSIS_ROOT.glob(f"{stem}_*") if path.is_dir()}


def analyze_completed_runs(recorder: ExperimentRecorder) -> None:
    """Run the existing parser for every completed repetition and record result paths."""

    for index, run in enumerate(recorder.state["runs"]):
        if run.get("status") != "completed":
            continue
        log_path = Path(run["kernel_log"])
        before = analysis_directories_for_log(log_path)
        analysis_console = recorder.directory / f"run-{index + 1:03d}-analysis-console.log"
        recorder.event("INFO", "Starting deterministic log analysis", run=index + 1)
        status = run_logged_command(
            ("python3", str(ANALYSIS_SCRIPT), str(log_path)),
            cwd=ANALYSIS_SCRIPT.parent,
            log_path=analysis_console,
        )
        after = analysis_directories_for_log(log_path)
        created = sorted(after - before)
        if status != 0 or len(created) != 1:
            message = (
                f"analysis failed for run {index + 1}: status={status}, "
                f"new_result_dirs={created}; see {analysis_console}"
            )
            recorder.update_run(
                index,
                analysis_status=status,
                analysis_console=str(analysis_console),
            )
            recorder.event("ERROR", message, run=index + 1)
            raise ExperimentError(message)
        recorder.update_run(
            index,
            analysis_status=status,
            analysis_console=str(analysis_console),
            analysis_dir=str(created[0]),
        )
        recorder.event(
            "INFO",
            "Deterministic log analysis completed",
            run=index + 1,
            analysis_dir=str(created[0]),
        )


def execute_plan(plan: ExperimentPlan, plan_path: Path) -> int:
    """Execute a complete validated batch under the global automation lock."""

    with ExperimentLock():
        recorder = ExperimentRecorder(plan, plan_path)
        try:
            recorder.set_status("preflight")
            environment = preflight_environment(plan, require_sudo=True)
            recorder.set_environment(environment)
            recorder.event("INFO", "Batch preflight passed")
            sequence = 0
            for case in plan.tests:
                for repetition in range(1, case.repetitions + 1):
                    sequence += 1
                    recorder.set_status("running")
                    run_one_repetition(
                        plan, case, repetition, sequence, recorder
                    )
            if plan.analyze:
                recorder.set_status("analyzing")
                analyze_completed_runs(recorder)
            recorder.complete(analyzed=plan.analyze)
            print(f"Record: {recorder.record_path}")
            return 0
        except BaseException as exc:
            trace_path = recorder.directory / "failure-traceback.log"
            trace_path.write_text(traceback.format_exc(), encoding="utf-8")
            recorder.fail(f"{type(exc).__name__}: {exc}")
            print(f"Record: {recorder.record_path}", file=sys.stderr)
            print(f"Traceback: {trace_path}", file=sys.stderr)
            return 1


def print_preflight(plan: ExperimentPlan) -> int:
    """Run read-only readiness checks and print their structured result."""

    environment = preflight_environment(plan, require_sudo=True)
    print(json.dumps(environment, indent=2, sort_keys=True))
    return 0


def print_status(record_dir: Path) -> int:
    """Print the current state file for an existing experiment record."""

    state_path = record_dir / "state.json"
    if not state_path.is_file():
        raise ExperimentError(f"state file does not exist: {state_path}")
    print(state_path.read_text(encoding="utf-8"), end="")
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    """Create the command-line parser for non-executing and executing modes."""

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="validate plan JSON only")
    validate_parser.add_argument("--plan", type=Path, required=True)
    preflight_parser = subparsers.add_parser(
        "preflight", help="run read-only local and server-31 readiness checks"
    )
    preflight_parser.add_argument("--plan", type=Path, required=True)
    run_parser = subparsers.add_parser(
        "run", help="execute the destructive experiment workflow"
    )
    run_parser.add_argument("--plan", type=Path, required=True)
    run_parser.add_argument(
        "--execute",
        action="store_true",
        help="required explicit acknowledgement for state-changing execution",
    )
    status_parser = subparsers.add_parser("status", help="print an existing state.json")
    status_parser.add_argument("--record-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch validation, preflight, execution, or status inspection."""

    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            return print_status(args.record_dir.resolve())
        plan_path = args.plan.resolve()
        plan = load_plan(plan_path)
        if args.command == "validate":
            print(json.dumps(plan_as_dict(plan), indent=2, sort_keys=True))
            return 0
        if args.command == "preflight":
            return print_preflight(plan)
        if args.command == "run":
            if not args.execute:
                raise ExperimentError(
                    "run requires --execute; use validate or preflight for read-only checks"
                )
            return execute_plan(plan, plan_path)
        raise ExperimentError(f"unsupported command: {args.command}")
    except ExperimentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
