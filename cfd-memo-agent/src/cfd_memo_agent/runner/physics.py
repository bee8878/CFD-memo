"""Extract force-coefficient evidence without claiming physical validity."""
from __future__ import annotations

import math
from pathlib import Path

RE100_REFERENCE = {
    "mean_cd": {"minimum": 1.30, "maximum": 1.40},
    "cl_amplitude": {"minimum": 0.20, "maximum": 0.36},
    "strouhal": {"minimum": 0.155, "maximum": 0.175},
    "source": {
        "title": "Harichandan and Roy (2012), Table 2/3",
        "url": "https://www.jafmonline.net/article_1329_01e9565cecc4e989123f9620c1d09c09.pdf",
        "note": "Acceptance bands cover the cited Re=100 numerical results; they are not fitted to this case.",
    },
}


def _coefficient_files(case: Path) -> list[Path]:
    root = case / "postProcessing" / "forceCoeffs"
    if not root.is_dir():
        raise ValueError("Missing postProcessing/forceCoeffs output")
    paths = list(root.glob("*/coefficient.dat"))
    if not paths:
        paths = list(root.glob("*/forceCoeffs.dat"))
    if not paths:
        raise ValueError("Missing force coefficient data file")

    def start_time(path: Path) -> float:
        try:
            value = float(path.parent.name)
        except ValueError as exc:
            raise ValueError(f"Invalid force output time directory: {path.parent.name}") from exc
        if not math.isfinite(value):
            raise ValueError("Non-finite force output start time")
        return value

    return sorted(paths, key=start_time)


def read_force_coefficients(case: Path | str) -> tuple[list[dict], list[str]]:
    """Read restart-safe Foundation forceCoeffs tables, keyed by header names."""
    rows: dict[float, dict] = {}
    files = _coefficient_files(Path(case))
    for path in files:
        columns = None
        for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                possible = line[1:].strip().split()
                if possible and possible[0] == "Time" and "Cd" in possible and "Cl" in possible:
                    columns = possible
                continue
            if columns is None:
                raise ValueError(f"Missing force coefficient header: {path}")
            fields = line.split()
            if len(fields) != len(columns):
                raise ValueError(f"Wrong force coefficient column count: {path}:{line_number}")
            try:
                values = [float(value) for value in fields]
            except ValueError as exc:
                raise ValueError(f"Invalid force coefficient number: {path}:{line_number}") from exc
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"Non-finite force coefficient: {path}:{line_number}")
            record = dict(zip(columns, values))
            rows[record["Time"]] = record
    ordered = [rows[key] for key in sorted(rows)]
    if len(ordered) < 2:
        raise ValueError("Force coefficient output contains fewer than two samples")
    return ordered, [str(path) for path in files]


def _rising_crossings(times: list[float], values: list[float]) -> list[float]:
    crossings = []
    for t0, t1, y0, y1 in zip(times, times[1:], values, values[1:]):
        if y0 <= 0 < y1 and y1 != y0:
            crossings.append(t0 + (t1 - t0) * (-y0) / (y1 - y0))
    return crossings


def force_coefficient_evidence(case: Path | str, task: dict) -> dict:
    rows, files = read_force_coefficients(case)
    start = task["time_control"]["start_time"]
    end = task["time_control"]["end_time"]
    window_start = start + 0.5 * (end - start)
    window = [row for row in rows if row["Time"] >= window_start]
    if len(window) < 2:
        raise ValueError("Force coefficient analysis window has fewer than two samples")
    times = [row["Time"] for row in window]
    cd = [row["Cd"] for row in window]
    cl = [row["Cl"] for row in window]
    mean_cd = sum(cd) / len(cd)
    mean_cl = sum(cl) / len(cl)
    centered_cl = [value - mean_cl for value in cl]
    cl_rms = math.sqrt(sum(value * value for value in centered_cl) / len(centered_cl))
    crossings = _rising_crossings(times, centered_cl)
    periods = [b - a for a, b in zip(crossings, crossings[1:]) if b > a]
    mean_period = sum(periods) / len(periods) if periods else None
    period_cv = None
    strouhal = None
    if mean_period is not None:
        period_cv = (math.sqrt(sum((value - mean_period) ** 2 for value in periods) / len(periods))
                     / mean_period)
        frequency = 1 / mean_period
        strouhal = (frequency * task["geometry"]["cylinder_diameter"]
                    / task["physics"]["inlet_velocity"])
    enough_cycles = len(periods) >= 5
    periodic = enough_cycles and period_cv is not None and period_cv <= 0.1
    evidence = {
        "files": files,
        "sample_count": len(rows),
        "analysis_sample_count": len(window),
        "analysis_window": {"start": window_start, "end": times[-1]},
        "mean_cd": mean_cd,
        "cd_amplitude": (max(cd) - min(cd)) / 2,
        "mean_cl": mean_cl,
        "cl_amplitude": (max(cl) - min(cl)) / 2,
        "cl_rms": cl_rms,
        "rising_crossings": len(crossings),
        "complete_periods": len(periods),
        "mean_period": mean_period,
        "period_cv": period_cv,
        "strouhal": strouhal,
        "signal_valid": periodic,
        "physical_validated": False,
        "limitations": ([] if periodic else [
            "后半段数据尚未包含至少五个稳定升力周期，不能据此完成物理验收。"
        ]),
    }
    if math.isclose(task["physics"]["reynolds_number"], 100, rel_tol=1e-8, abs_tol=1e-10):
        checks = {}
        for metric in ("mean_cd", "cl_amplitude", "strouhal"):
            value = evidence[metric]
            bounds = RE100_REFERENCE[metric]
            checks[metric] = {
                "value": value,
                **bounds,
                "passed": value is not None and bounds["minimum"] <= value <= bounds["maximum"],
            }
        evidence["reference_comparison"] = {
            "reynolds_number": 100,
            "checks": checks,
            "passed": periodic and all(item["passed"] for item in checks.values()),
            "source": RE100_REFERENCE["source"],
        }
    else:
        evidence["reference_comparison"] = None
        evidence["limitations"].append("当前仅固定了 Re=100 的公开物理参考区间。")
    return evidence


__all__ = ["RE100_REFERENCE", "force_coefficient_evidence", "read_force_coefficients"]
