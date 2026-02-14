"""
Regression analyzer — detects performance regressions by comparing
recent traces against historical baselines.
"""

from dataclasses import dataclass
from typing import Optional

from tracepulse.storage import get_traces


@dataclass
class RegressionResult:
    """Result of a regression analysis."""

    url: str
    has_regression: bool
    phase: str  # which phase regressed (or "total")
    baseline_ms: float
    current_ms: float
    change_pct: float  # percentage change
    severity: str  # "low", "medium", "high", "critical"
    message: str

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "has_regression": self.has_regression,
            "phase": self.phase,
            "baseline_ms": round(self.baseline_ms, 2),
            "current_ms": round(self.current_ms, 2),
            "change_pct": round(self.change_pct, 1),
            "severity": self.severity,
            "message": self.message,
        }


def _severity_from_pct(pct: float) -> str:
    """Classify regression severity based on percentage increase."""
    if pct < 20:
        return "low"
    elif pct < 50:
        return "medium"
    elif pct < 100:
        return "high"
    else:
        return "critical"


def detect_regressions(
    url: str,
    recent_count: int = 5,
    baseline_count: int = 20,
    threshold_pct: float = 20.0,
) -> list[RegressionResult]:
    """
    Detect performance regressions by comparing recent traces against
    a historical baseline.

    Args:
        url: The URL to analyze.
        recent_count: Number of most recent traces to use as "current".
        baseline_count: Number of older traces to use as "baseline".
        threshold_pct: Minimum percentage increase to flag as regression.

    Returns:
        List of RegressionResult for each phase that shows regression.
    """
    all_traces = get_traces(url=url, limit=recent_count + baseline_count)

    if len(all_traces) < recent_count + 3:
        return []  # Not enough data

    recent = all_traces[:recent_count]
    baseline = all_traces[recent_count:]

    if not baseline:
        return []

    phases = [
        ("dns_ms", "DNS Lookup"),
        ("tcp_connect_ms", "TCP Connect"),
        ("tls_handshake_ms", "TLS Handshake"),
        ("server_processing_ms", "Server Processing"),
        ("content_transfer_ms", "Content Transfer"),
        ("total_ms", "Total Latency"),
    ]

    results = []
    for field, name in phases:
        baseline_vals = [t[field] for t in baseline if t[field] is not None]
        recent_vals = [t[field] for t in recent if t[field] is not None]

        if not baseline_vals or not recent_vals:
            continue

        baseline_avg = sum(baseline_vals) / len(baseline_vals)
        recent_avg = sum(recent_vals) / len(recent_vals)

        if baseline_avg == 0:
            continue

        change_pct = ((recent_avg - baseline_avg) / baseline_avg) * 100

        if change_pct > threshold_pct:
            severity = _severity_from_pct(change_pct)
            results.append(
                RegressionResult(
                    url=url,
                    has_regression=True,
                    phase=name,
                    baseline_ms=baseline_avg,
                    current_ms=recent_avg,
                    change_pct=change_pct,
                    severity=severity,
                    message=(
                        f"{name} increased by {change_pct:.1f}% "
                        f"({baseline_avg:.1f}ms → {recent_avg:.1f}ms)"
                    ),
                )
            )

    return results


def get_trend(url: str, limit: int = 50) -> list[dict]:
    """
    Get latency trend data for a URL over time.

    Returns list of dicts with timestamp and phase timings,
    ordered chronologically (oldest first).
    """
    traces = get_traces(url=url, limit=limit)
    traces.reverse()  # Oldest first

    trend = []
    for t in traces:
        trend.append(
            {
                "timestamp": t["created_at"],
                "dns_ms": t["dns_ms"],
                "tcp_connect_ms": t["tcp_connect_ms"],
                "tls_handshake_ms": t["tls_handshake_ms"],
                "server_processing_ms": t["server_processing_ms"],
                "content_transfer_ms": t["content_transfer_ms"],
                "total_ms": t["total_ms"],
                "status_code": t["status_code"],
            }
        )

    return trend
