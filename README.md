# TracePulse ⚡

**Lightweight API Latency Analyzer** — Trace any API endpoint and get a granular breakdown of DNS, TCP, TLS, server processing, and content transfer timings.

---

## Features

- **Granular Timing Breakdown** — See exactly where time is spent: DNS, TCP connect, TLS handshake, server processing, content transfer
- **Beautiful CLI Output** — Color-coded waterfall charts and bottleneck detection right in your terminal
- **Web Dashboard** — Interactive charts for trend analysis and regression detection
- **History & Comparison** — Save traces, compare endpoints, and track latency over time
- **Regression Detection** — Automatically detect performance degradations
- **Zero Infrastructure** — Runs locally, stores data in SQLite, no external dependencies

---

## Installation

```bash
cd tracepulse
pip install -e .
```

## Quick Start

### Trace an API

```bash
tracepulse trace https://api.github.com
```

### Multiple requests (with averages)

```bash
tracepulse trace https://api.github.com -n 5
```

### Compare endpoints

```bash
tracepulse compare https://api.github.com https://jsonplaceholder.typicode.com/posts
```

### View history

```bash
tracepulse history
tracepulse history https://api.github.com
```

### View stats

```bash
tracepulse stats https://api.github.com
```

### Launch dashboard

```bash
tracepulse dashboard
# Opens at http://127.0.0.1:8585
```

### JSON output

```bash
tracepulse trace https://api.github.com --json-output
```

---

## CLI Commands

| Command     | Description                                  |
| ----------- | -------------------------------------------- |
| `trace`     | Trace an API endpoint with timing breakdown  |
| `history`   | View trace history                           |
| `stats`     | Show aggregate statistics for a URL          |
| `compare`   | Compare latency across multiple URLs         |
| `dashboard` | Launch the web dashboard                     |
| `clean`     | Delete trace history                         |

### Trace Options

| Flag              | Description                          | Default |
| ----------------- | ------------------------------------ | ------- |
| `-m, --method`    | HTTP method                          | GET     |
| `-H, --header`    | Request header (`Key: Value`)        | —       |
| `-d, --data`      | Request body                         | —       |
| `-n, --count`     | Number of requests                   | 1       |
| `--timeout`       | Timeout in seconds                   | 30      |
| `-l, --label`     | Label for grouping                   | —       |
| `--headers`       | Show response headers                | off     |
| `--json-output`   | Output as JSON                       | off     |
| `--no-save`       | Don't save to history                | save    |

---

## Web Dashboard

The dashboard provides:

- **One-click tracing** — Enter a URL and trace directly from the browser
- **Waterfall charts** — Visual breakdown of each request phase
- **Latency trends** — Line charts showing performance over time
- **Regression alerts** — Automatic detection of performance degradations
- **Endpoint overview** — Summary of all traced endpoints with stats

---

## Architecture

```
tracepulse/
├── __init__.py          # Package init
├── cli.py               # Click-based CLI with Rich output
├── tracer.py            # Core timing engine (raw sockets)
├── storage.py           # SQLite persistence layer
├── analyzer.py          # Regression detection & trend analysis
└── dashboard/
    ├── app.py           # Flask web server & API
    ├── templates/
    │   └── index.html   # Dashboard SPA
    └── static/
        ├── style.css    # Dark theme styles
        └── app.js       # Chart.js visualizations
```

## How It Works

TracePulse uses **raw sockets** (not `requests`) to measure each phase of the HTTP lifecycle independently:

1. **DNS Resolution** — `socket.getaddrinfo()` timed separately
2. **TCP Connect** — `socket.connect()` to the resolved IP
3. **TLS Handshake** — `ssl.wrap_socket()` for HTTPS endpoints
4. **Server Processing** — Time from request sent to first byte received (TTFB)
5. **Content Transfer** — Time to download the full response body

Data is stored in a local **SQLite** database (`~/.tracepulse/traces.db`) for history, trends, and regression analysis.

---

## License

MIT
