#!/usr/bin/env python3
"""Compare OpenSSD Core3 normal-I/O budgets for two CSGC workloads."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


FIO_BW_BYTES_RE = re.compile(
    r'"write"\s*:\s*\{.*?"bw_bytes"\s*:\s*([0-9.]+)', re.DOTALL
)
FIO_IO_BYTES_RE = re.compile(
    r'"write"\s*:\s*\{.*?"io_bytes"\s*:\s*([0-9.]+)', re.DOTALL
)
FIO_BW_RE = re.compile(r"WRITE: bw=([0-9.]+)([KMG]iB/s)")
FIO_LATENCY_RE = {
    name: re.compile(
        rf'"{name}_ns"\s*:\s*\{{.*?"mean"\s*:\s*([0-9.]+)',
        re.DOTALL,
    )
    for name in ("slat", "clat", "lat")
}
STAT_RE = re.compile(
    r"^(?P<name>[^:]+): count=(?P<count>\d+) "
    r"mean=(?P<mean>-?[0-9.]+) median=(?P<median>-?[0-9.]+) "
    r"p95=(?P<p95>-?[0-9.]+) p99=(?P<p99>-?[0-9.]+) "
    r"min=(?P<min>-?[0-9.]+) max=(?P<max>-?[0-9.]+) "
    r"sum=(?P<sum>-?[0-9.]+)$"
)
MOVE_PLAN_PATTERNS = {
    "requests": re.compile(r"csgc_mp:\s+req=(\d+)"),
    "moves": re.compile(r"csgc_mp:.*?done=(\d+)"),
    "queue_mean_us": re.compile(r"csgc_mp_life_us:.*?\bq=\d+/(\d+)/\d+"),
    "exec_mean_us": re.compile(r"csgc_mp_life_us:.*?\bexec=\d+/(\d+)/\d+"),
    "flush_mean_us": re.compile(r"csgc_mp_phase_us:.*?\bflush=\d+/(\d+)/\d+"),
    "channel_supply": re.compile(r"csgc_mp_channel_x10000:\s+supply=(\d+)"),
    "channel_service": re.compile(r"csgc_mp_channel_x10000:.*?\bservice=(\d+)"),
    "channel_dma": re.compile(r"csgc_mp_channel_x10000:.*?\bdma=(\d+)"),
    "service_mib_s": re.compile(r"csgc_mp_channel_x10000:.*?\bsrv_mib_s=(\d+)"),
    "good_mib_s": re.compile(r"csgc_mp_channel_x10000:.*?\bgood_mib_s=(\d+)"),
    "logical_waf_cs": re.compile(r"logical WAF\(CS\):\s*(\d+)"),
    "physical_waf": re.compile(r"physical WAF:\s*(\d+)"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    for workload in ("big", "small"):
        for budget in (0, 4, 8):
            parser.add_argument(
                f"--b{budget}-{workload}", required=True, type=Path
            )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def last_number(pattern: re.Pattern[str], text: str) -> float | None:
    matches = pattern.findall(text)
    return float(matches[-1]) if matches else None


def parse_fio(path: Path) -> dict[str, float]:
    text = path.read_text(errors="replace")
    bandwidth = last_number(FIO_BW_BYTES_RE, text)
    if bandwidth is not None:
        bandwidth /= 1024.0 * 1024.0
    else:
        matches = FIO_BW_RE.findall(text)
        if not matches:
            raise ValueError(f"No fio write bandwidth in {path}")
        value_text, unit = matches[-1]
        bandwidth = float(value_text)
        if unit == "KiB/s":
            bandwidth /= 1024.0
        elif unit == "GiB/s":
            bandwidth *= 1024.0
    result = {
        "fio_bw_mib_s": bandwidth,
        "fio_io_bytes": last_number(FIO_IO_BYTES_RE, text) or 0.0,
    }
    for name, pattern in FIO_LATENCY_RE.items():
        result[f"fio_{name}_mean_us"] = (last_number(pattern, text) or 0.0) / 1000.0
    return result


def parse_breakdown(path: Path) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    scalars: dict[str, float] = {}
    stats: dict[str, dict[str, float]] = {}
    if not path.is_file():
        return scalars, stats
    for line in path.read_text(errors="replace").splitlines():
        match = STAT_RE.match(line)
        if match:
            fields = match.groupdict()
            name = fields.pop("name")
            stats[name] = {key: float(value) for key, value in fields.items()}
            continue
        if "=" in line:
            name, value = line.split("=", 1)
            try:
                scalars[name] = float(value)
            except ValueError:
                pass
    return scalars, stats


def parse_ssd(path: Path) -> dict[str, float]:
    text = path.read_bytes().replace(b"\0", b"").decode(errors="replace")
    values: dict[str, float] = {}
    for name, pattern in MOVE_PLAN_PATTERNS.items():
        match = pattern.search(text)
        if match:
            values[name] = float(match.group(1))
    for name in ("channel_supply", "channel_service", "channel_dma"):
        if name in values:
            values[name] /= 100.0
    for name in ("logical_waf_cs", "physical_waf"):
        if name in values:
            values[name] /= 1000.0
    return values


def nested(mapping: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return float(value)


def load_run(run_dir: Path) -> dict[str, Any]:
    required = (
        "fio.log",
        "csgc-supply-analysis.json",
        "ssd-workload-stat.log",
        "core3-scheduler-provenance.log",
    )
    missing = [name for name in required if not (run_dir / name).is_file()]
    if missing:
        raise ValueError(f"Missing {', '.join(missing)} in {run_dir}")
    analysis = json.loads(
        (run_dir / "csgc-supply-analysis.json").read_text(encoding="ascii")
    )
    scalars, stats = parse_breakdown(
        run_dir / "gc-breakdown-diagnostic-result.txt"
    )
    return {
        "run_dir": str(run_dir),
        "fio": parse_fio(run_dir / "fio.log"),
        "supply": analysis,
        "ssd": parse_ssd(run_dir / "ssd-workload-stat.log"),
        "scalars": scalars,
        "stats": stats,
        "provenance": dict(
            line.split("=", 1)
            for line in (run_dir / "core3-scheduler-provenance.log")
            .read_text(errors="replace").splitlines()
            if "=" in line
        ),
    }


def metric(run: dict[str, Any]) -> dict[str, float]:
    analysis = run["supply"]
    scheduler = analysis["device"]["scheduler"]
    distributions = scheduler["distributions"]
    timeline = analysis["device"]["timeline_summary"]
    lifecycle = analysis["device"]["request_lifecycle"]
    fio = run["fio"]
    ssd = run["ssd"]
    stats = run["stats"]
    scalars = run["scalars"]

    def stat(name: str, field: str = "sum") -> float:
        return float(stats.get(name, {}).get(field, 0.0))

    io_bytes = fio["fio_io_bytes"]
    moves = stat("modern_section_critical_total_moves")
    return {
        **fio,
        "host_supply_coverage_pct": nested(analysis, "host", "supply_coverage_pct"),
        "host_outstanding_ge2_pct": nested(
            analysis, "host", "outstanding_ge_2_coverage_pct"
        ),
        "csio_poll_mean_us": nested(
            distributions, "csio_poll_gap_ns", "mean_us"
        ),
        "csio_poll_p50_us": nested(
            distributions, "csio_poll_gap_ns", "p50_us"
        ),
        "csio_poll_p95_us": nested(
            distributions, "csio_poll_gap_ns", "p95_us"
        ),
        "csio_poll_p99_us": nested(
            distributions, "csio_poll_gap_ns", "p99_us"
        ),
        "csio_poll_max_us": nested(
            distributions, "csio_poll_gap_ns", "max_us"
        ),
        "csio_poll_over_1ms_count": float(scheduler["csio_poll_long_count"]),
        "csio_poll_over_1ms_total_ms": float(scheduler["csio_poll_long_ns"]) / 1e6,
        "csgc_pending_wait_mean_us": nested(
            distributions, "csgc_pending_wait_ns", "mean_us"
        ),
        "csgc_pending_wait_p95_us": nested(
            distributions, "csgc_pending_wait_ns", "p95_us"
        ),
        "other_pending_wait_mean_us": nested(
            distributions, "other_pending_wait_ns", "mean_us"
        ),
        "normal_sq_batch_mean_requests": nested(
            distributions, "normal_sq_batch_size", "mean_requests"
        ),
        "normal_sq_batch_p95_requests": nested(
            distributions, "normal_sq_batch_size", "p95_requests"
        ),
        "normal_sq_batch_mean_us": nested(
            distributions, "normal_sq_batch_ns", "mean_us"
        ),
        "normal_cq_batch_mean_requests": nested(
            distributions, "normal_cq_batch_size", "mean_requests"
        ),
        "normal_cq_batch_mean_us": nested(
            distributions, "normal_cq_batch_ns", "mean_us"
        ),
        "normal_sq_yields": float(scheduler["normal_sq_yield_count"]),
        "normal_cq_yields": float(scheduler["normal_cq_yield_count"]),
        "csgc_sq_batch_mean_requests": nested(
            distributions, "csgc_sq_batch_size", "mean_requests"
        ),
        "device_request_queue_mean_us": nested(
            lifecycle, "enqueue_wait_ns", "mean_us"
        ),
        "device_worker_start_wait_mean_us": nested(
            lifecycle, "worker_start_wait_ns", "mean_us"
        ),
        "device_leader_mean_us": nested(lifecycle, "leader_ns", "mean_us"),
        "device_request_total_mean_us": nested(lifecycle, "total_ns", "mean_us"),
        "core3_normal_io_pct": nested(
            timeline, "core3_state_pct", "C3_NORMAL_EMU_IO"
        ),
        "core3_normal_cq_pct": nested(
            timeline, "core3_state_pct", "C3_NORMAL_CQ"
        ),
        "core3_csio_sched_pct": nested(
            timeline, "core3_state_pct", "C3_CSIO_SCHED"
        ),
        "core3_cdma_pct": nested(timeline, "core3_state_pct", "C3_CDMA"),
        "timeline_cdma_busy_pct": nested(timeline, "cdma_busy_pct"),
        "timeline_normal_io_pending_pct": nested(
            timeline, "normal_io_pending_pct"
        ),
        "timeline_normal_io_active_pct": nested(
            timeline, "normal_io_active_pct"
        ),
        "move_plan_queue_mean_us": float(ssd.get("queue_mean_us", 0.0)),
        "move_plan_exec_mean_us": float(ssd.get("exec_mean_us", 0.0)),
        "move_plan_flush_mean_us": float(ssd.get("flush_mean_us", 0.0)),
        "move_plan_supply_pct": float(ssd.get("channel_supply", 0.0)),
        "move_plan_service_pct": float(ssd.get("channel_service", 0.0)),
        "move_plan_dma_pct": float(ssd.get("channel_dma", 0.0)),
        "move_plan_service_mib_s": float(ssd.get("service_mib_s", 0.0)),
        "move_plan_good_mib_s": float(ssd.get("good_mib_s", 0.0)),
        "csgc_sections": float(scalars.get("timeline_sections", 0.0)),
        "migrated_blocks": moves,
        "host_migration_waf": moves * 4096.0 / io_bytes if io_bytes else 0.0,
        "logical_waf_cs": float(ssd.get("logical_waf_cs", 0.0)),
        "physical_waf": float(ssd.get("physical_waf", 0.0)),
    }


def change_pct(reference: float, value: float) -> float:
    return (value - reference) * 100.0 / reference if reference else 0.0


def reduction_pct(reference: float, value: float) -> float:
    return -change_pct(reference, value)


def render_workload(name: str, runs: dict[int, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    values = {budget: metric(run) for budget, run in runs.items()}
    metric_order = list(values[0])
    lines = [
        f"## {name}",
        "",
        "| 指标 | budget=0 | budget=4 | budget=8 | b4 相对 b0 | b8 相对 b0 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key in metric_order:
        base = values[0][key]
        b4 = values[4][key]
        b8 = values[8][key]
        lines.append(
            f"| `{key}` | {base:.3f} | {b4:.3f} | {b8:.3f} | "
            f"{change_pct(base, b4):+.2f}% | {change_pct(base, b8):+.2f}% |"
        )

    acceptance: dict[str, Any] = {}
    for budget in (4, 8):
        base = values[0]
        current = values[budget]
        poll_reduction = max(
            reduction_pct(base["csio_poll_p95_us"], current["csio_poll_p95_us"]),
            reduction_pct(
                base["csio_poll_over_1ms_total_ms"],
                current["csio_poll_over_1ms_total_ms"],
            ),
        )
        queue_reduction = reduction_pct(
            base["move_plan_queue_mean_us"], current["move_plan_queue_mean_us"]
        )
        flush_reduction = reduction_pct(
            base["move_plan_flush_mean_us"], current["move_plan_flush_mean_us"]
        )
        fio_gain = change_pct(base["fio_bw_mib_s"], current["fio_bw_mib_s"])
        yielded = current["normal_sq_yields"] + current["normal_cq_yields"] > 0
        acceptance[str(budget)] = {
            "poll_reduction_pct": poll_reduction,
            "queue_reduction_pct": queue_reduction,
            "flush_reduction_pct": flush_reduction,
            "fio_gain_pct": fio_gain,
            "budget_yield_observed": yielded,
            "mechanism_pass": yielded and poll_reduction >= 25.0 and
            max(queue_reduction, flush_reduction) >= 20.0,
            "performance_pass": fio_gain > 5.0,
        }

    best = max(values, key=lambda budget: values[budget]["fio_bw_mib_s"])
    lines.extend((
        "",
        f"端到端最高带宽来自 `budget={best}`："
        f"{values[best]['fio_bw_mib_s']:.3f} MiB/s。",
        "",
        "机制验收同时要求预算让出真实发生、CSIO poll gap 至少下降 25%，"
        "且 Move Plan queue 或 flush 至少下降 20%。性能验收要求 fio 提升超过 5%。",
        "",
    ))
    return "\n".join(lines), {
        "runs": {str(budget): runs[budget]["run_dir"] for budget in runs},
        "metrics": {str(budget): values[budget] for budget in values},
        "acceptance": acceptance,
        "best_budget": best,
    }


def main() -> None:
    args = parse_args()
    run_paths = {
        "大文件": {0: args.b0_big, 4: args.b4_big, 8: args.b8_big},
        "小文件": {0: args.b0_small, 4: args.b4_small, 8: args.b8_small},
    }
    summaries: dict[str, Any] = {}
    sections = []
    for workload, paths in run_paths.items():
        section, summary = render_workload(
            workload, {budget: load_run(path) for budget, path in paths.items()}
        )
        sections.append(section)
        summaries[workload] = summary

    report = "\n".join((
        "# mCSGC Core3 公平调度 A/B 分析",
        "",
        "同一 Host、同一 SSD1t 固件和同一负载下，仅改变普通 I/O 的 Core3 每轮预算。",
        "`budget=0` 保持原 drain-to-empty 行为，`4/8` 为 treatment。",
        "",
        *sections,
    ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report + "\n", encoding="utf-8")
    json_path = args.json_output or args.output.with_suffix(".json")
    json_path.write_text(
        json.dumps(summaries, indent=2, sort_keys=True) + "\n", encoding="ascii"
    )
    print(report)


if __name__ == "__main__":
    main()
