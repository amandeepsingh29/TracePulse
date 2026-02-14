"""
CLI interface for TracePulse — beautiful terminal output with Rich.
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from tracepulse.tracer import (
    TimingBreakdown, average_timing, get_geo_info, parse_curl,
    trace_concurrent, trace_multiple, trace_request,
)
from tracepulse.storage import (
    delete_preset, delete_traces, get_all_presets, get_all_urls,
    get_percentile_stats, get_preset, get_stats, get_traces,
    save_preset, save_trace,
)

console = Console()


def _phase_bar(value: float, total: float, width: int = 30) -> str:
    if total == 0:
        return ""
    ratio = min(value / total, 1.0)
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)


def _color_for_ms(ms: float) -> str:
    if ms < 50:
        return "green"
    elif ms < 200:
        return "yellow"
    elif ms < 500:
        return "dark_orange"
    else:
        return "red"


def _render_timing(timing: TimingBreakdown, show_headers: bool = False, show_body: bool = False, show_geo: bool = False) -> None:
    if timing.error:
        console.print(f"\n[red bold]✗ Error:[/red bold] {timing.error}\n")
        return

    geo_str = ""
    if show_geo and timing.ip_address:
        geo = timing.geo_info or get_geo_info(timing.ip_address)
        if geo:
            geo_str = f"  •  📍 {geo}"

    status_color = "green" if 200 <= timing.status_code < 300 else "yellow" if timing.status_code < 400 else "red"
    console.print()
    console.print(
        Panel(
            f"[bold]{timing.method}[/bold] {timing.url}\n"
            f"[{status_color}]HTTP {timing.status_code}[/{status_color}]  •  "
            f"[bold]{timing.total_ms:.0f}ms[/bold]  •  "
            f"{timing.response_size:,} bytes  •  "
            f"{timing.ip_address}"
            + (f"  •  {timing.tls_version}" if timing.tls_version else "")
            + geo_str,
            title="[bold cyan]TracePulse[/bold cyan]",
            border_style="cyan",
        )
    )

    total = timing.total_ms
    phases = [
        ("DNS Lookup", timing.dns_ms, "blue"),
        ("TCP Connect", timing.tcp_connect_ms, "magenta"),
        ("TLS Handshake", timing.tls_handshake_ms, "cyan"),
        ("Server Processing", timing.server_processing_ms, "yellow"),
        ("Content Transfer", timing.content_transfer_ms, "green"),
    ]

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("Phase", style="bold", width=20)
    table.add_column("Time", justify="right", width=10)
    table.add_column("Chart", width=35)
    table.add_column("%", justify="right", width=6)

    for name, ms, color in phases:
        pct = (ms / total * 100) if total > 0 else 0
        bar = _phase_bar(ms, total, 30)
        ms_color = _color_for_ms(ms)
        table.add_row(
            f"[{color}]{name}[/{color}]",
            f"[{ms_color}]{ms:.1f}ms[/{ms_color}]",
            f"[{color}]{bar}[/{color}]",
            f"{pct:.1f}%",
        )

    console.print(table)
    console.print()

    max_phase = max(phases, key=lambda p: p[1])
    if max_phase[1] > total * 0.5:
        console.print(
            f"  [yellow]⚠ Bottleneck:[/yellow] [bold]{max_phase[0]}[/bold] "
            f"accounts for [bold]{max_phase[1] / total * 100:.0f}%[/bold] of total latency\n"
        )

    if show_headers and timing.headers_received:
        header_table = Table(title="Response Headers", show_header=True, header_style="bold dim", box=None)
        header_table.add_column("Header", style="dim")
        header_table.add_column("Value")
        for k, v in timing.headers_received.items():
            header_table.add_row(k, v)
        console.print(header_table)
        console.print()

    if show_body and timing.response_body:
        body_preview = timing.response_body[:1500]
        try:
            parsed = json.loads(body_preview)
            body_preview = json.dumps(parsed, indent=2)[:1500]
        except (json.JSONDecodeError, TypeError):
            pass
        console.print(Panel(body_preview, title="[dim]Response Body (preview)[/dim]", border_style="dim", expand=False))
        console.print()


@click.group()
@click.version_option(package_name="tracepulse")
def cli():
    """TracePulse — Lightweight API latency analyzer.

    Trace any API endpoint to get a detailed breakdown of DNS, TCP,
    TLS, server processing, and content transfer timings.
    """
    pass


@cli.command()
@click.argument("url")
@click.option("-m", "--method", default="GET", help="HTTP method")
@click.option("-H", "--header", multiple=True, help="Header in 'Key: Value' format")
@click.option("-d", "--data", default=None, help="Request body data")
@click.option("-n", "--count", default=1, type=int, help="Number of requests")
@click.option("--timeout", default=30.0, type=float, help="Timeout in seconds")
@click.option("--save/--no-save", default=True, help="Save to history")
@click.option("-l", "--label", default=None, help="Label for grouping")
@click.option("--headers/--no-headers", "show_headers", default=False, help="Show response headers")
@click.option("--body/--no-body", "show_body", default=False, help="Show response body preview")
@click.option("--geo/--no-geo", "show_geo", default=False, help="Show server geolocation")
@click.option("--json-output", "json_out", is_flag=True, help="Output as JSON")
def trace(url, method, header, data, count, timeout, save, label, show_headers, show_body, show_geo, json_out):
    """Trace an API endpoint and show timing breakdown."""
    if url.startswith("@"):
        preset = get_preset(url[1:])
        if not preset:
            console.print(f"[red]Preset '{url[1:]}' not found.[/red]")
            return
        url = preset["url"]
        method = preset.get("method", method)
        if preset.get("headers"):
            for k, v in preset["headers"].items():
                header = list(header) + [f"{k}: {v}"]
        if preset.get("body"):
            data = data or preset["body"]
        console.print(f"[dim]Using preset: {url}[/dim]")

    parsed_headers = {}
    for h in header:
        if ": " in h:
            k, v = h.split(": ", 1)
            parsed_headers[k] = v

    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"

    if count > 1:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task(f"Tracing {url} ({count} requests)...", total=count)
            results = []
            for i in range(count):
                result = trace_request(url, method, parsed_headers, data, timeout)
                results.append(result)
                progress.advance(task)

        if json_out:
            console.print_json(json.dumps([r.to_dict() for r in results], indent=2))
        else:
            for i, result in enumerate(results):
                console.print(f"\n[dim]─── Request {i + 1}/{count} ───[/dim]")
                _render_timing(result, show_headers, show_body, show_geo)
            avg = average_timing(results)
            console.print("[dim]─── Average ───[/dim]")
            _render_timing(avg, show_headers=False)

        if save:
            for result in results:
                save_trace(result, label)
            console.print(f"[dim]  ✓ {len(results)} traces saved to history[/dim]\n")
    else:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            progress.add_task(f"Tracing {url}...", total=None)
            result = trace_request(url, method, parsed_headers, data, timeout)

        if json_out:
            console.print_json(json.dumps(result.to_dict(), indent=2))
        else:
            _render_timing(result, show_headers, show_body, show_geo)

        if save:
            row_id = save_trace(result, label)
            console.print(f"[dim]  ✓ Trace #{row_id} saved to history[/dim]\n")


@cli.command()
@click.argument("url", required=False)
@click.option("-l", "--label", default=None, help="Filter by label")
@click.option("-n", "--limit", default=20, type=int, help="Number of traces")
def history(url, label, limit):
    """View trace history for a URL."""
    if not url and not label:
        urls = get_all_urls()
        if not urls:
            console.print("[dim]No traces found. Run 'tracepulse trace <url>' first.[/dim]")
            return

        table = Table(title="Traced URLs", show_header=True, header_style="bold")
        table.add_column("#", style="dim", width=4)
        table.add_column("URL", style="cyan")
        table.add_column("Traces", justify="right")
        table.add_column("Avg Latency", justify="right")

        for i, u in enumerate(urls, 1):
            s = get_stats(u)
            avg_ms = s.get("avg_total_ms", 0)
            cnt = s.get("trace_count", 0)
            color = _color_for_ms(avg_ms)
            table.add_row(str(i), u, str(cnt), f"[{color}]{avg_ms:.0f}ms[/{color}]")

        console.print(table)
        console.print("\n[dim]  Use 'tracepulse history <url>' for details[/dim]\n")
        return

    traces = get_traces(url=url, label=label, limit=limit)
    if not traces:
        console.print("[dim]No traces found.[/dim]")
        return

    table = Table(title=f"Trace History — {url or label}", show_header=True, header_style="bold")
    table.add_column("ID", style="dim", width=5)
    table.add_column("Time", width=18)
    table.add_column("Status", justify="center", width=6)
    table.add_column("DNS", justify="right", width=8)
    table.add_column("TCP", justify="right", width=8)
    table.add_column("TLS", justify="right", width=8)
    table.add_column("Server", justify="right", width=8)
    table.add_column("Transfer", justify="right", width=8)
    table.add_column("Total", justify="right", width=10, style="bold")

    for t in traces:
        ts = datetime.fromtimestamp(t["created_at"]).strftime("%Y-%m-%d %H:%M")
        status = str(t["status_code"])
        status_color = "green" if t["status_code"] and 200 <= t["status_code"] < 300 else "red"
        total_color = _color_for_ms(t["total_ms"] or 0)
        table.add_row(str(t["id"]), ts, f"[{status_color}]{status}[/{status_color}]",
            f"{t['dns_ms']:.1f}ms", f"{t['tcp_connect_ms']:.1f}ms",
            f"{t['tls_handshake_ms']:.1f}ms", f"{t['server_processing_ms']:.1f}ms",
            f"{t['content_transfer_ms']:.1f}ms", f"[{total_color}]{t['total_ms']:.0f}ms[/{total_color}]")

    console.print(table)
    console.print()


@cli.command()
@click.argument("url")
def stats(url):
    """Show aggregate statistics with percentiles."""
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"

    s = get_stats(url)
    if not s or not s.get("trace_count"):
        console.print(f"[dim]No traces found for {url}[/dim]")
        return

    p = get_percentile_stats(url)
    pct_info = ""
    if p:
        pct_info = (
            f"\n  [bold]Percentiles[/bold]\n"
            f"  P50 (median):    [{_color_for_ms(p['p50_ms'])}]{p['p50_ms']:.0f}ms[/{_color_for_ms(p['p50_ms'])}]\n"
            f"  P95:             [{_color_for_ms(p['p95_ms'])}]{p['p95_ms']:.0f}ms[/{_color_for_ms(p['p95_ms'])}]\n"
            f"  P99:             [{_color_for_ms(p['p99_ms'])}]{p['p99_ms']:.0f}ms[/{_color_for_ms(p['p99_ms'])}]\n"
        )

    console.print(
        Panel(
            f"[bold cyan]{url}[/bold cyan]\n\n"
            f"  Traces:          [bold]{s['trace_count']}[/bold]\n"
            f"  Avg Latency:     [bold]{s['avg_total_ms']:.0f}ms[/bold]\n"
            f"  Min Latency:     [green]{s['min_total_ms']:.0f}ms[/green]\n"
            f"  Max Latency:     [red]{s['max_total_ms']:.0f}ms[/red]\n"
            + pct_info +
            f"\n  [bold]Phase Averages[/bold]\n"
            f"  DNS:             {s['avg_dns_ms']:.1f}ms\n"
            f"  TCP:             {s['avg_tcp_ms']:.1f}ms\n"
            f"  TLS:             {s['avg_tls_ms']:.1f}ms\n"
            f"  Server:          {s['avg_server_ms']:.1f}ms\n"
            f"  Transfer:        {s['avg_transfer_ms']:.1f}ms\n\n"
            f"  First Traced:    {datetime.fromtimestamp(s['first_traced']).strftime('%Y-%m-%d %H:%M')}\n"
            f"  Last Traced:     {datetime.fromtimestamp(s['last_traced']).strftime('%Y-%m-%d %H:%M')}",
            title="[bold]Statistics[/bold]",
            border_style="cyan",
        )
    )


@cli.command()
@click.argument("urls", nargs=-1, required=True)
@click.option("-n", "--count", default=3, type=int, help="Requests per URL")
@click.option("--timeout", default=30.0, type=float, help="Timeout in seconds")
@click.option("--concurrent/--sequential", default=True, help="Run concurrently")
def compare(urls, count, timeout, concurrent):
    """Compare latency across multiple URLs (concurrent by default)."""
    normalized = []
    for url in urls:
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"
        normalized.append(url)

    if concurrent:
        console.print(f"[dim]Tracing {len(normalized)} URLs concurrently ({count} each)...[/dim]")
        raw = trace_concurrent(normalized, count, timeout=timeout)
        all_results = {u: average_timing(r) for u, r in raw.items()}
    else:
        all_results = {}
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            for url in normalized:
                task = progress.add_task(f"Tracing {url}...", total=count)
                results = []
                for _ in range(count):
                    results.append(trace_request(url, timeout=timeout))
                    progress.advance(task)
                all_results[url] = average_timing(results)

    table = Table(title="Comparison", show_header=True, header_style="bold")
    table.add_column("URL", style="cyan", width=40)
    table.add_column("DNS", justify="right")
    table.add_column("TCP", justify="right")
    table.add_column("TLS", justify="right")
    table.add_column("Server", justify="right")
    table.add_column("Transfer", justify="right")
    table.add_column("Total", justify="right", style="bold")

    for url, avg in all_results.items():
        tc = _color_for_ms(avg.total_ms)
        table.add_row(url, f"{avg.dns_ms:.1f}ms", f"{avg.tcp_connect_ms:.1f}ms",
            f"{avg.tls_handshake_ms:.1f}ms", f"{avg.server_processing_ms:.1f}ms",
            f"{avg.content_transfer_ms:.1f}ms", f"[{tc}]{avg.total_ms:.0f}ms[/{tc}]")

    console.print()
    console.print(table)
    console.print()


@cli.command()
@click.argument("url")
@click.option("--interval", "-i", default=10.0, type=float, help="Seconds between traces")
@click.option("--alert-above", "-a", default=None, type=float, help="Alert threshold (ms)")
@click.option("--timeout", default=10.0, type=float, help="Timeout in seconds")
@click.option("-m", "--method", default="GET", help="HTTP method")
def watch(url, interval, alert_above, timeout, method):
    """Continuously monitor an API endpoint. Press Ctrl+C to stop."""
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"

    console.print(
        f"\n[bold cyan]Watching[/bold cyan] {url} every [bold]{interval}s[/bold]"
        + (f" | alert above [yellow]{alert_above}ms[/yellow]" if alert_above else "")
        + "\n[dim]Press Ctrl+C to stop[/dim]\n"
    )

    trace_num = 0
    try:
        while True:
            trace_num += 1
            result = trace_request(url, method=method, timeout=timeout)
            save_trace(result)
            ts = datetime.now().strftime("%H:%M:%S")
            if result.error:
                console.print(f"  [{ts}] [red]✗ {result.error}[/red]")
            else:
                tc = _color_for_ms(result.total_ms)
                sc = "green" if 200 <= result.status_code < 300 else "red"
                line = (
                    f"  [{ts}] [{sc}]{result.status_code}[/{sc}] "
                    f"[{tc}]{result.total_ms:>7.0f}ms[/{tc}]  "
                    f"DNS:{result.dns_ms:>5.0f}  TCP:{result.tcp_connect_ms:>5.0f}  "
                    f"TLS:{result.tls_handshake_ms:>5.0f}  Srv:{result.server_processing_ms:>5.0f}  "
                    f"Xfr:{result.content_transfer_ms:>5.0f}"
                )
                if alert_above and result.total_ms > alert_above:
                    line += f"  [red bold]⚠ ALERT! >{alert_above}ms[/red bold]"
                console.print(line)
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print(f"\n[dim]Stopped after {trace_num} traces.[/dim]")
        s = get_stats(url)
        if s and s.get("trace_count"):
            console.print(f"[dim]  Avg: {s['avg_total_ms']:.0f}ms | Min: {s['min_total_ms']:.0f}ms | Max: {s['max_total_ms']:.0f}ms[/dim]\n")


@cli.command(name="curl")
@click.argument("curl_command", nargs=-1, required=True)
@click.option("--headers/--no-headers", "show_headers", default=False)
@click.option("--body/--no-body", "show_body", default=False)
def curl_import(curl_command, show_headers, show_body):
    """Import and trace a cURL command.

    Example: tracepulse curl 'curl -X GET https://api.github.com -H "Accept: application/json"'
    """
    curl_str = " ".join(curl_command)
    parsed = parse_curl(curl_str)
    if not parsed["url"]:
        console.print("[red]Could not parse URL from cURL command.[/red]")
        return

    console.print(f"[dim]Parsed: {parsed['method']} {parsed['url']}[/dim]")
    url = parsed["url"]
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        progress.add_task(f"Tracing {url}...", total=None)
        result = trace_request(url, parsed["method"], parsed["headers"], parsed["body"])

    _render_timing(result, show_headers, show_body)
    row_id = save_trace(result)
    console.print(f"[dim]  ✓ Trace #{row_id} saved[/dim]\n")


@cli.group()
def preset():
    """Manage saved API presets."""
    pass


@preset.command(name="save")
@click.argument("name")
@click.argument("url")
@click.option("-m", "--method", default="GET")
@click.option("-H", "--header", multiple=True)
@click.option("-d", "--data", default=None)
def preset_save(name, url, method, header, data):
    """Save a URL as a named preset. Use with: tracepulse trace @name"""
    headers = {}
    for h in header:
        if ": " in h:
            k, v = h.split(": ", 1)
            headers[k] = v
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"
    save_preset(name, url, method, headers, data)
    console.print(f"[green]✓[/green] Preset [bold]{name}[/bold] saved → {method} {url}")
    console.print(f"[dim]  Use: tracepulse trace @{name}[/dim]")


@preset.command(name="list")
def preset_list():
    """List all saved presets."""
    presets = get_all_presets()
    if not presets:
        console.print("[dim]No presets. Use 'tracepulse preset save <name> <url>'[/dim]")
        return
    table = Table(title="Saved Presets", show_header=True, header_style="bold")
    table.add_column("Name", style="cyan bold")
    table.add_column("Method", width=8)
    table.add_column("URL")
    table.add_column("Headers", style="dim")
    for p in presets:
        h = len(p.get("headers", {}))
        table.add_row(f"@{p['name']}", p["method"], p["url"], f"{h} header(s)" if h else "—")
    console.print(table)


@preset.command(name="delete")
@click.argument("name")
def preset_delete(name):
    """Delete a saved preset."""
    if delete_preset(name):
        console.print(f"[green]✓[/green] Preset [bold]{name}[/bold] deleted")
    else:
        console.print(f"[red]Preset '{name}' not found.[/red]")


@cli.command()
@click.argument("url", required=False)
@click.option("-l", "--label", default=None, help="Filter by label")
@click.option("-f", "--format", "fmt", type=click.Choice(["csv", "json", "html"]), default="csv")
@click.option("-o", "--output", "outfile", default=None, help="Output file")
@click.option("-n", "--limit", default=100, type=int)
def export(url, label, fmt, outfile, limit):
    """Export trace data as CSV, JSON, or HTML report."""
    from tracepulse.exporter import export_csv, export_html, export_json

    if fmt == "csv":
        content = export_csv(url=url, label=label, limit=limit)
    elif fmt == "json":
        content = export_json(url=url, label=label, limit=limit)
    else:
        content = export_html(url=url, label=label, limit=limit)

    if not content:
        console.print("[dim]No traces to export.[/dim]")
        return

    if outfile:
        with open(outfile, "w") as f:
            f.write(content)
        console.print(f"[green]✓[/green] Exported to [bold]{outfile}[/bold] ({fmt.upper()})")
    else:
        console.print(content)


@cli.command()
@click.option("--url", default=None)
@click.option("--older-than", default=None, type=int, help="Delete older than N days")
@click.option("--all", "delete_all", is_flag=True)
@click.confirmation_option(prompt="Are you sure?")
def clean(url, older_than, delete_all):
    """Delete trace history."""
    count = delete_traces() if delete_all else delete_traces(url=url, older_than_days=older_than)
    console.print(f"[green]✓[/green] Deleted {count} trace(s)")


@cli.command()
@click.option("-p", "--port", default=8585, type=int)
@click.option("--host", default="127.0.0.1")
def dashboard(port, host):
    """Launch the TracePulse web dashboard."""
    from tracepulse.dashboard.app import create_app
    console.print(f"\n[bold cyan]TracePulse Dashboard[/bold cyan] at [link=http://{host}:{port}]http://{host}:{port}[/link]\n[dim]Press Ctrl+C to stop[/dim]\n")
    app = create_app()
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    cli()
