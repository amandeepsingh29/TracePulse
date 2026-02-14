"""
Export module — generate CSV, HTML, and JSON reports from trace data.
"""

import csv
import io
import json
from datetime import datetime
from typing import Optional

from tracepulse.storage import get_traces, get_stats


def export_csv(url: Optional[str] = None, label: Optional[str] = None, limit: int = 100) -> str:
    """Export traces as CSV string."""
    traces = get_traces(url=url, label=label, limit=limit)
    if not traces:
        return ""

    output = io.StringIO()
    fields = [
        "id", "url", "method", "status_code", "response_size",
        "ip_address", "tls_version", "dns_ms", "tcp_connect_ms",
        "tls_handshake_ms", "server_processing_ms", "content_transfer_ms",
        "total_ms", "error", "label", "created_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for t in traces:
        row = {k: t.get(k, "") for k in fields}
        row["created_at"] = datetime.fromtimestamp(t["created_at"]).isoformat()
        writer.writerow(row)

    return output.getvalue()


def export_json(url: Optional[str] = None, label: Optional[str] = None, limit: int = 100) -> str:
    """Export traces as formatted JSON string."""
    traces = get_traces(url=url, label=label, limit=limit)
    for t in traces:
        t["created_at_iso"] = datetime.fromtimestamp(t["created_at"]).isoformat()
        # Parse stored JSON strings back to dicts
        for key in ("headers_sent", "headers_received"):
            if isinstance(t.get(key), str):
                try:
                    t[key] = json.loads(t[key])
                except (json.JSONDecodeError, TypeError):
                    pass
    return json.dumps(traces, indent=2)


def export_html(url: Optional[str] = None, label: Optional[str] = None, limit: int = 100) -> str:
    """Export traces as a standalone HTML report."""
    traces = get_traces(url=url, label=label, limit=limit)
    if not traces:
        return "<html><body><p>No traces found.</p></body></html>"

    stats = get_stats(url) if url else {}

    phase_colors = {
        "dns": "#58a6ff",
        "tcp": "#bc8cff",
        "tls": "#39d2c0",
        "server": "#d29922",
        "transfer": "#3fb950",
    }

    def color_for_ms(ms):
        if ms < 50: return "#3fb950"
        if ms < 200: return "#d29922"
        if ms < 500: return "#db6d28"
        return "#f85149"

    # Build rows
    rows_html = ""
    for t in traces:
        ts = datetime.fromtimestamp(t["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        sc = t.get("status_code", 0)
        sc_color = "#3fb950" if 200 <= sc < 300 else "#f85149"
        total = t.get("total_ms", 0)
        rows_html += f"""
        <tr>
            <td>{t['id']}</td>
            <td>{ts}</td>
            <td>{t.get('method', 'GET')}</td>
            <td style="color: {sc_color}">{sc}</td>
            <td>{t.get('dns_ms', 0):.1f}</td>
            <td>{t.get('tcp_connect_ms', 0):.1f}</td>
            <td>{t.get('tls_handshake_ms', 0):.1f}</td>
            <td>{t.get('server_processing_ms', 0):.1f}</td>
            <td>{t.get('content_transfer_ms', 0):.1f}</td>
            <td style="color: {color_for_ms(total)}; font-weight: bold">{total:.0f}</td>
            <td>{t.get('ip_address', '')}</td>
        </tr>"""

    # Stats summary
    stats_html = ""
    if stats and stats.get("trace_count"):
        stats_html = f"""
        <div class="stats-grid">
            <div class="stat"><span class="stat-label">Traces</span><span class="stat-value">{stats['trace_count']}</span></div>
            <div class="stat"><span class="stat-label">Avg</span><span class="stat-value" style="color:{color_for_ms(stats['avg_total_ms'])}">{stats['avg_total_ms']:.0f}ms</span></div>
            <div class="stat"><span class="stat-label">Min</span><span class="stat-value" style="color:#3fb950">{stats['min_total_ms']:.0f}ms</span></div>
            <div class="stat"><span class="stat-label">Max</span><span class="stat-value" style="color:#f85149">{stats['max_total_ms']:.0f}ms</span></div>
        </div>"""

    # Chart data for inline JS
    chart_data = []
    for t in reversed(traces[:50]):
        chart_data.append({
            "ts": datetime.fromtimestamp(t["created_at"]).strftime("%H:%M"),
            "dns": t.get("dns_ms", 0),
            "tcp": t.get("tcp_connect_ms", 0),
            "tls": t.get("tls_handshake_ms", 0),
            "server": t.get("server_processing_ms", 0),
            "transfer": t.get("content_transfer_ms", 0),
            "total": t.get("total_ms", 0),
        })

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>TracePulse Report — {url or 'All Traces'}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d1117; color: #e6edf3; margin: 0; padding: 2rem; }}
h1 {{ color: #58a6ff; font-size: 1.5rem; }} h2 {{ color: #e6edf3; font-size: 1.1rem; margin-top: 2rem; }}
.meta {{ color: #8b949e; font-size: 0.85rem; margin-bottom: 1rem; }}
.stats-grid {{ display: flex; gap: 1rem; margin: 1rem 0; }}
.stat {{ background: #1c2128; border: 1px solid #30363d; border-radius: 8px; padding: 0.8rem 1.2rem; text-align: center; }}
.stat-label {{ display: block; font-size: 0.75rem; color: #8b949e; text-transform: uppercase; }}
.stat-value {{ display: block; font-size: 1.3rem; font-weight: 700; margin-top: 0.2rem; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; font-size: 0.85rem; }}
th {{ background: #161b22; color: #8b949e; text-align: left; padding: 0.6rem 0.8rem; border-bottom: 1px solid #30363d; }}
td {{ padding: 0.5rem 0.8rem; border-bottom: 1px solid #21262d; }}
tr:hover {{ background: rgba(88,166,255,0.05); }}
.chart-container {{ height: 250px; margin: 1rem 0; }}
canvas {{ width: 100% !important; height: 100% !important; }}
</style>
</head>
<body>
<h1>⚡ TracePulse Report</h1>
<div class="meta">
    <strong>{url or 'All Endpoints'}</strong> &bull; Generated: {now} &bull; {len(traces)} traces
</div>
{stats_html}
<div class="chart-container"><canvas id="chart"></canvas></div>
<h2>Trace Details</h2>
<table>
<thead><tr><th>ID</th><th>Time</th><th>Method</th><th>Status</th><th>DNS (ms)</th><th>TCP (ms)</th><th>TLS (ms)</th><th>Server (ms)</th><th>Transfer (ms)</th><th>Total (ms)</th><th>IP</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>
<script>
const d = {json.dumps(chart_data)};
new Chart(document.getElementById('chart'), {{
    type: 'line',
    data: {{
        labels: d.map(x => x.ts),
        datasets: [
            {{ label: 'Total', data: d.map(x => x.total), borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.1)', fill: true, tension: 0.3, borderWidth: 2 }},
            {{ label: 'Server', data: d.map(x => x.server), borderColor: '#d29922', borderDash: [5,5], tension: 0.3, borderWidth: 1, pointRadius: 0 }},
            {{ label: 'DNS', data: d.map(x => x.dns), borderColor: '#58a6ff', borderDash: [5,5], tension: 0.3, borderWidth: 1, pointRadius: 0 }},
        ]
    }},
    options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ labels: {{ color: '#8b949e' }} }} }},
        scales: {{
            x: {{ grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#8b949e' }} }},
            y: {{ grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#8b949e', callback: v => v+'ms' }} }}
        }}
    }}
}});
</script>
</body>
</html>"""
