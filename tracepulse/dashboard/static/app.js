/* ============================================================
   TracePulse Dashboard — JavaScript (Full Feature Set)
   ============================================================ */

let waterfallChart = null;
let trendChart = null;
let compareChart = null;
let currentUrl = null;

// ---- Utility ----

function latencyClass(ms) {
    if (ms < 100) return 'fast';
    if (ms < 300) return 'medium';
    if (ms < 600) return 'slow';
    return 'critical';
}

function colorForMs(ms) {
    if (ms < 50) return '#3fb950';
    if (ms < 200) return '#d29922';
    if (ms < 500) return '#db6d28';
    return '#f85149';
}

function formatTime(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleString();
}

// ---- Theme Toggle ----

function toggleTheme() {
    const html = document.documentElement;
    const current = html.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('tracepulse-theme', next);
    document.getElementById('theme-toggle').textContent = next === 'dark' ? '🌙' : '☀️';
}

function initTheme() {
    const saved = localStorage.getItem('tracepulse-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    document.getElementById('theme-toggle').textContent = saved === 'dark' ? '🌙' : '☀️';
}

// ---- Shareable Links ----

function checkShareableLink() {
    const params = new URLSearchParams(window.location.search);
    const traceId = params.get('trace');
    if (traceId) {
        loadSharedTrace(traceId);
    }
}

async function loadSharedTrace(traceId) {
    try {
        const res = await fetch(`/api/trace/${traceId}`);
        if (!res.ok) return;
        const data = await res.json();
        displayResult(data);
    } catch (err) {
        console.error('Failed to load shared trace:', err);
    }
}

function showShareToast(traceId) {
    const url = `${window.location.origin}?trace=${traceId}`;
    navigator.clipboard.writeText(url).then(() => {
        const toast = document.getElementById('share-toast');
        document.getElementById('share-toast-text').textContent = `Link copied: ${url}`;
        toast.classList.remove('hidden');
        setTimeout(() => toast.classList.add('hidden'), 3000);
    });
}

// ---- cURL Import ----

function toggleCurlModal() {
    document.getElementById('curl-modal').classList.toggle('hidden');
}

async function importCurl() {
    const curlCmd = document.getElementById('curl-input').value.trim();
    if (!curlCmd) return;

    try {
        const res = await fetch('/api/curl', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ curl: curlCmd }),
        });
        const data = await res.json();
        if (data.error && !data.url) {
            alert(data.error);
            return;
        }
        displayResult(data);
        toggleCurlModal();
        if (!data.error) loadUrls();
    } catch (err) {
        console.error('cURL import failed:', err);
    }
}

// ---- Presets ----

function togglePresetsPanel() {
    const panel = document.getElementById('presets-panel');
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) loadPresets();
}

async function loadPresets() {
    try {
        const res = await fetch('/api/presets');
        const presets = await res.json();
        const container = document.getElementById('presets-list');

        if (presets.length === 0) {
            container.innerHTML = '<p class="empty-state">No presets saved. Enter a URL above, then click Save Current.</p>';
            return;
        }

        container.innerHTML = presets.map(p => `
            <div class="preset-item">
                <div class="preset-info" onclick="usePreset('${p.url}', '${p.method}')">
                    <span class="preset-name">@${p.name}</span>
                    <span class="preset-method">${p.method}</span>
                    <span class="preset-url">${p.url}</span>
                </div>
                <button class="btn-danger-sm" onclick="deletePreset('${p.name}')">✕</button>
            </div>
        `).join('');
    } catch (err) {
        console.error('Failed to load presets:', err);
    }
}

function usePreset(url, method) {
    document.getElementById('url-input').value = url;
    document.getElementById('method-select').value = method;
    togglePresetsPanel();
}

async function saveCurrentAsPreset() {
    const name = document.getElementById('preset-name-input').value.trim();
    const url = document.getElementById('url-input').value.trim();
    const method = document.getElementById('method-select').value;
    if (!name || !url) { alert('Enter a preset name and URL first'); return; }

    let headers = {};
    try { headers = JSON.parse(document.getElementById('headers-input').value || '{}'); } catch(e) {}
    const body = document.getElementById('body-input').value || null;

    try {
        await fetch('/api/presets', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, url, method, headers, body }),
        });
        document.getElementById('preset-name-input').value = '';
        loadPresets();
    } catch (err) {
        console.error('Failed to save preset:', err);
    }
}

async function deletePreset(name) {
    try {
        await fetch(`/api/presets/${name}`, { method: 'DELETE' });
        loadPresets();
    } catch (err) {
        console.error('Failed to delete preset:', err);
    }
}

// ---- Trace ----

async function runTrace() {
    const url = document.getElementById('url-input').value.trim();
    if (!url) return;

    const method = document.getElementById('method-select').value;
    const btn = document.getElementById('trace-btn');
    const btnText = document.getElementById('trace-btn-text');
    const spinner = document.getElementById('trace-spinner');

    let headers = {};
    try { headers = JSON.parse(document.getElementById('headers-input').value || '{}'); } catch(e) {}
    const body = document.getElementById('body-input').value || null;

    btn.disabled = true;
    btnText.textContent = 'Tracing...';
    spinner.classList.remove('hidden');

    try {
        const res = await fetch('/api/trace', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, method, headers, body }),
        });
        const data = await res.json();
        displayResult(data);
        if (!data.error) loadUrls();
    } catch (err) {
        console.error('Trace failed:', err);
    } finally {
        btn.disabled = false;
        btnText.textContent = 'Trace';
        spinner.classList.add('hidden');
    }
}

// Enter key triggers trace
document.getElementById('url-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') runTrace();
});

function displayResult(data) {
    const section = document.getElementById('result-section');
    section.classList.remove('hidden');

    // Handle errors
    if (data.error) {
        document.getElementById('result-meta').innerHTML = `
            <div class="status error">ERROR</div>
            <div>${data.method} ${data.url}</div>
        `;

        if (waterfallChart) { waterfallChart.destroy(); waterfallChart = null; }
        const chartContainer = document.querySelector('.waterfall-chart');
        chartContainer.style.height = 'auto';
        const canvas = document.getElementById('waterfallChart');
        canvas.style.display = 'none';

        let errorIcon = '🔴', errorTitle = 'Request Failed', suggestion = '';
        const err = data.error.toLowerCase();
        if (err.includes('connection refused')) {
            errorIcon = '🚫'; errorTitle = 'Connection Refused';
            suggestion = 'The server is not listening on this port. Verify the host and port are correct and the service is running.';
        } else if (err.includes('dns') || err.includes('nodename') || err.includes('getaddrinfo')) {
            errorIcon = '🔍'; errorTitle = 'DNS Resolution Failed';
            suggestion = 'The hostname could not be resolved. Check for typos in the URL or verify your DNS settings.';
        } else if (err.includes('timed out') || err.includes('timeout')) {
            errorIcon = '⏱️'; errorTitle = 'Connection Timed Out';
            suggestion = 'The server did not respond in time. It may be overloaded, unreachable, or behind a firewall.';
        } else if (err.includes('tls') || err.includes('ssl') || err.includes('certificate')) {
            errorIcon = '🔒'; errorTitle = 'TLS/SSL Error';
            suggestion = 'The secure connection failed. The certificate may be invalid, expired, or the server may not support HTTPS.';
        }

        const grid = document.getElementById('timing-grid');
        grid.innerHTML = `
            <div class="error-display">
                <div class="error-icon">${errorIcon}</div>
                <div class="error-title">${errorTitle}</div>
                <div class="error-message">${data.error}</div>
                ${suggestion ? `<div class="error-suggestion"><span class="suggestion-label">💡 Suggestion:</span> ${suggestion}</div>` : ''}
                <div class="error-phases">
                    ${data.dns_ms > 0 ? `<div class="error-phase-tag phase-ok">✓ DNS ${data.dns_ms.toFixed(1)}ms</div>` : `<div class="error-phase-tag phase-fail">✗ DNS</div>`}
                    ${data.tcp_connect_ms > 0 ? `<div class="error-phase-tag phase-ok">✓ TCP ${data.tcp_connect_ms.toFixed(1)}ms</div>` : `<div class="error-phase-tag phase-fail">✗ TCP</div>`}
                    ${data.tls_handshake_ms > 0 ? `<div class="error-phase-tag phase-ok">✓ TLS ${data.tls_handshake_ms.toFixed(1)}ms</div>` : `<div class="error-phase-tag phase-skip">— TLS</div>`}
                    <div class="error-phase-tag phase-fail">✗ Response</div>
                </div>
            </div>
        `;

        document.getElementById('bottleneck-alert').classList.add('hidden');
        document.getElementById('geo-info').classList.add('hidden');
        document.getElementById('body-preview-section').classList.add('hidden');
        return;
    }

    // Restore chart area
    const canvas = document.getElementById('waterfallChart');
    canvas.style.display = '';
    document.querySelector('.waterfall-chart').style.height = '120px';

    // Meta info
    const statusClass = data.status_code >= 200 && data.status_code < 300 ? 'ok' : data.status_code < 400 ? 'redirect' : 'error';
    document.getElementById('result-meta').innerHTML = `
        <div class="status ${statusClass}">HTTP ${data.status_code}</div>
        <div>${data.method} ${data.url}</div>
        <div>${data.ip_address}${data.tls_version ? ' • ' + data.tls_version : ''}</div>
        <div>${(data.response_size || 0).toLocaleString()} bytes</div>
    `;

    // Geo info
    const geoEl = document.getElementById('geo-info');
    if (data.geo_info) {
        geoEl.innerHTML = `📍 <strong>Server Location:</strong> ${data.geo_info}`;
        geoEl.classList.remove('hidden');
    } else {
        geoEl.classList.add('hidden');
    }

    // Waterfall chart
    const phases = [
        { label: 'DNS Lookup', value: data.dns_ms, color: '#58a6ff' },
        { label: 'TCP Connect', value: data.tcp_connect_ms, color: '#bc8cff' },
        { label: 'TLS Handshake', value: data.tls_handshake_ms, color: '#39d2c0' },
        { label: 'Server Processing', value: data.server_processing_ms, color: '#d29922' },
        { label: 'Content Transfer', value: data.content_transfer_ms, color: '#3fb950' },
    ];

    if (waterfallChart) waterfallChart.destroy();

    const ctx = document.getElementById('waterfallChart').getContext('2d');
    waterfallChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: phases.map(p => p.label),
            datasets: [{
                data: phases.map(p => p.value),
                backgroundColor: phases.map(p => p.color),
                borderRadius: 4,
                barThickness: 28,
            }],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: { label: (ctx) => `${ctx.parsed.x.toFixed(1)}ms` },
                },
            },
            scales: {
                x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#8b949e', callback: (v) => v + 'ms' } },
                y: { grid: { display: false }, ticks: { color: '#e6edf3', font: { size: 12 } } },
            },
        },
    });

    // Timing cards
    const grid = document.getElementById('timing-grid');
    const total = data.total_ms || 1;
    grid.innerHTML = phases.map(p => `
        <div class="timing-card" data-phase="${p.label.split(' ')[0].toLowerCase()}">
            <div class="label">${p.label}</div>
            <div class="value" style="color: ${colorForMs(p.value)}">${p.value.toFixed(1)}ms</div>
            <div class="pct">${(p.value / total * 100).toFixed(1)}%</div>
        </div>
    `).join('') + `
        <div class="timing-card" data-phase="total">
            <div class="label">Total</div>
            <div class="value" style="color: ${colorForMs(total)}">${total.toFixed(1)}ms</div>
            <div class="pct">100%</div>
        </div>
    `;

    // Bottleneck alert
    const bottleneck = document.getElementById('bottleneck-alert');
    const maxPhase = phases.reduce((a, b) => a.value > b.value ? a : b);
    if (maxPhase.value > total * 0.5) {
        bottleneck.classList.remove('hidden');
        bottleneck.innerHTML = `⚠ <strong>Bottleneck detected:</strong> ${maxPhase.label} accounts for ${(maxPhase.value / total * 100).toFixed(0)}% of total latency (${maxPhase.value.toFixed(1)}ms)`;
    } else {
        bottleneck.classList.add('hidden');
    }

    // Response body preview
    const bodySection = document.getElementById('body-preview-section');
    const bodyContent = document.getElementById('body-preview-content');
    if (data.response_body) {
        let bodyText = data.response_body;
        try {
            bodyText = JSON.stringify(JSON.parse(bodyText), null, 2);
        } catch(e) {}
        bodyContent.textContent = bodyText.substring(0, 2000);
        bodySection.classList.remove('hidden');
    } else {
        bodySection.classList.add('hidden');
    }
}

// ---- URL List ----

async function loadUrls() {
    try {
        const res = await fetch('/api/urls');
        const urls = await res.json();

        const container = document.getElementById('url-list');
        if (urls.length === 0) {
            container.innerHTML = '<p class="empty-state">No traces yet. Trace an API above to get started.</p>';
            return;
        }

        container.innerHTML = urls.map(u => `
            <div class="url-item" onclick="loadDetails('${u.url}')">
                <div class="url-name">${u.url}</div>
                <div class="url-stats">
                    <span>${u.trace_count} traces</span>
                    <span class="latency ${latencyClass(u.avg_total_ms)}">avg ${u.avg_total_ms.toFixed(0)}ms</span>
                    <span>min ${u.min_total_ms.toFixed(0)}ms</span>
                    <span>max ${u.max_total_ms.toFixed(0)}ms</span>
                </div>
            </div>
        `).join('');
    } catch (err) {
        console.error('Failed to load URLs:', err);
    }
}

// ---- Stats & Percentiles ----

async function loadStats(url) {
    try {
        const res = await fetch(`/api/stats?url=${encodeURIComponent(url)}`);
        const s = await res.json();

        currentUrl = url;
        const section = document.getElementById('stats-section');
        section.classList.remove('hidden');
        document.getElementById('stats-title').textContent = `Statistics — ${url}`;

        const statsGrid = document.getElementById('stats-grid');
        statsGrid.innerHTML = `
            <div class="stat-card"><div class="stat-label">Traces</div><div class="stat-value">${s.trace_count || 0}</div></div>
            <div class="stat-card"><div class="stat-label">Avg Latency</div><div class="stat-value" style="color:${colorForMs(s.avg_total_ms)}">${(s.avg_total_ms || 0).toFixed(0)}ms</div></div>
            <div class="stat-card"><div class="stat-label">Min</div><div class="stat-value" style="color:#3fb950">${(s.min_total_ms || 0).toFixed(0)}ms</div></div>
            <div class="stat-card"><div class="stat-label">Max</div><div class="stat-value" style="color:#f85149">${(s.max_total_ms || 0).toFixed(0)}ms</div></div>
        `;

        const pctGrid = document.getElementById('percentile-grid');
        if (s.p50_ms !== undefined) {
            pctGrid.innerHTML = `
                <div class="pct-card"><div class="pct-label">P50 (median)</div><div class="pct-value" style="color:${colorForMs(s.p50_ms)}">${s.p50_ms.toFixed(0)}ms</div></div>
                <div class="pct-card"><div class="pct-label">P95</div><div class="pct-value" style="color:${colorForMs(s.p95_ms)}">${s.p95_ms.toFixed(0)}ms</div></div>
                <div class="pct-card"><div class="pct-label">P99</div><div class="pct-value" style="color:${colorForMs(s.p99_ms)}">${s.p99_ms.toFixed(0)}ms</div></div>
            `;
        } else {
            pctGrid.innerHTML = '';
        }
    } catch (err) {
        console.error('Failed to load stats:', err);
    }
}

// ---- Export ----

function exportData(format) {
    const url = currentUrl ? `&url=${encodeURIComponent(currentUrl)}` : '';
    window.open(`/api/export?format=${format}${url}`, '_blank');
}

// ---- Comparison ----

async function runComparison() {
    const text = document.getElementById('compare-urls').value.trim();
    if (!text) return;

    const urls = text.split('\n').map(u => u.trim()).filter(u => u);
    if (urls.length < 2) { alert('Enter at least 2 URLs'); return; }

    try {
        const res = await fetch('/api/compare', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ urls, count: 3 }),
        });
        const data = await res.json();
        displayComparison(data);
    } catch (err) {
        console.error('Comparison failed:', err);
    }
}

function displayComparison(data) {
    const urls = Object.keys(data);
    const phases = ['dns_ms', 'tcp_connect_ms', 'tls_handshake_ms', 'server_processing_ms', 'content_transfer_ms'];
    const phaseLabels = ['DNS', 'TCP', 'TLS', 'Server', 'Transfer'];
    const phaseColors = ['#58a6ff', '#bc8cff', '#39d2c0', '#d29922', '#3fb950'];

    if (compareChart) compareChart.destroy();

    const ctx = document.getElementById('compareChart').getContext('2d');
    compareChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: urls.map(u => { try { return new URL(u).hostname; } catch { return u; } }),
            datasets: phases.map((p, i) => ({
                label: phaseLabels[i],
                data: urls.map(u => data[u][p] || 0),
                backgroundColor: phaseColors[i],
                borderRadius: 2,
            })),
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: '#8b949e' } },
                tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)}ms` } },
            },
            scales: {
                x: { stacked: true, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#8b949e' } },
                y: { stacked: true, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#8b949e', callback: v => v + 'ms' } },
            },
        },
    });

    // Results table
    const container = document.getElementById('compare-results');
    let html = '<table class="compare-table"><thead><tr><th>URL</th><th>DNS</th><th>TCP</th><th>TLS</th><th>Server</th><th>Transfer</th><th>Total</th></tr></thead><tbody>';
    for (const url of urls) {
        const d = data[url];
        const tc = colorForMs(d.total_ms);
        html += `<tr>
            <td class="compare-url">${url}</td>
            <td>${(d.dns_ms||0).toFixed(1)}ms</td>
            <td>${(d.tcp_connect_ms||0).toFixed(1)}ms</td>
            <td>${(d.tls_handshake_ms||0).toFixed(1)}ms</td>
            <td>${(d.server_processing_ms||0).toFixed(1)}ms</td>
            <td>${(d.content_transfer_ms||0).toFixed(1)}ms</td>
            <td style="color:${tc};font-weight:700">${(d.total_ms||0).toFixed(0)}ms</td>
        </tr>`;
    }
    html += '</tbody></table>';
    container.innerHTML = html;
}

// ---- Details (Trend + Regressions + Stats) ----

async function loadDetails(url) {
    await Promise.all([loadTrend(url), loadRegressions(url), loadStats(url)]);
}

async function loadTrend(url) {
    const section = document.getElementById('trend-section');
    section.classList.remove('hidden');
    document.getElementById('trend-title').textContent = `Latency Trend — ${url}`;

    try {
        const res = await fetch(`/api/trend?url=${encodeURIComponent(url)}`);
        const data = await res.json();

        if (data.length === 0) {
            section.classList.add('hidden');
            return;
        }

        const labels = data.map(d => {
            const dt = new Date(d.timestamp * 1000);
            return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        });

        if (trendChart) trendChart.destroy();

        const ctx = document.getElementById('trendChart').getContext('2d');
        trendChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [
                    { label: 'Total', data: data.map(d => d.total_ms), borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.1)', fill: true, tension: 0.3, borderWidth: 2 },
                    { label: 'DNS', data: data.map(d => d.dns_ms), borderColor: '#58a6ff', borderDash: [5, 5], tension: 0.3, borderWidth: 1, pointRadius: 0 },
                    { label: 'Server', data: data.map(d => d.server_processing_ms), borderColor: '#d29922', borderDash: [5, 5], tension: 0.3, borderWidth: 1, pointRadius: 0 },
                    { label: 'TLS', data: data.map(d => d.tls_handshake_ms), borderColor: '#39d2c0', borderDash: [5, 5], tension: 0.3, borderWidth: 1, pointRadius: 0 },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { labels: { color: '#8b949e' } },
                    tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)}ms` } },
                },
                scales: {
                    x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#8b949e' } },
                    y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#8b949e', callback: (v) => v + 'ms' } },
                },
            },
        });

        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
        console.error('Failed to load trend:', err);
    }
}

async function loadRegressions(url) {
    const section = document.getElementById('regression-section');

    try {
        const res = await fetch(`/api/regressions?url=${encodeURIComponent(url)}`);
        const data = await res.json();

        section.classList.remove('hidden');
        const container = document.getElementById('regression-list');

        if (data.length === 0) {
            container.innerHTML = '<p class="no-regressions">✓ No regressions detected</p>';
            return;
        }

        container.innerHTML = data.map(r => `
            <div class="regression-item severity-${r.severity}">
                <div>
                    <div class="regression-phase">${r.phase}</div>
                    <div class="regression-detail">${r.message}</div>
                </div>
                <span class="regression-badge severity-${r.severity}">
                    +${r.change_pct.toFixed(0)}% ${r.severity}
                </span>
            </div>
        `).join('');
    } catch (err) {
        console.error('Failed to load regressions:', err);
    }
}

// ---- Init ----
initTheme();
loadUrls();
checkShareableLink();
