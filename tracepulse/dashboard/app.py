"""
Flask-based web dashboard for TracePulse.
"""

import json
import os
import time
from datetime import datetime

from flask import Flask, jsonify, render_template, request, Response

from tracepulse.analyzer import detect_regressions, get_trend
from tracepulse.storage import (
    get_all_presets, get_all_urls, get_percentile_stats, get_preset,
    get_stats, get_trace_by_id, get_traces, save_preset, delete_preset,
)
from tracepulse.tracer import get_geo_info, parse_curl, trace_concurrent, trace_request


def create_app() -> Flask:
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    static_dir = os.path.join(os.path.dirname(__file__), "static")

    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/urls")
    def api_urls():
        urls = get_all_urls()
        url_data = []
        for u in urls:
            s = get_stats(u)
            url_data.append(
                {
                    "url": u,
                    "trace_count": s.get("trace_count", 0),
                    "avg_total_ms": round(s.get("avg_total_ms", 0), 1),
                    "min_total_ms": round(s.get("min_total_ms", 0), 1),
                    "max_total_ms": round(s.get("max_total_ms", 0), 1),
                    "last_traced": s.get("last_traced", 0),
                }
            )
        return jsonify(url_data)

    @app.route("/api/traces")
    def api_traces():
        url = request.args.get("url")
        label = request.args.get("label")
        limit = request.args.get("limit", 50, type=int)
        traces = get_traces(url=url, label=label, limit=limit)
        return jsonify(traces)

    @app.route("/api/trace", methods=["POST"])
    def api_trace():
        data = request.get_json()
        url = data.get("url", "")
        method = data.get("method", "GET")
        headers = data.get("headers", {})
        body = data.get("body", None)

        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"

        result = trace_request(url, method=method, headers=headers, body=body)

        resp = result.to_dict()

        # Add geo info if we have an IP
        if result.ip_address and not result.error:
            resp["geo_info"] = get_geo_info(result.ip_address)

        if not result.error:
            from tracepulse.storage import save_trace
            save_trace(result)

        return jsonify(resp)

    @app.route("/api/trace/<int:trace_id>")
    def api_trace_by_id(trace_id):
        """Get a single trace by ID — for shareable links."""
        t = get_trace_by_id(trace_id)
        if not t:
            return jsonify({"error": "Trace not found"}), 404
        return jsonify(t)

    @app.route("/api/trend")
    def api_trend():
        url = request.args.get("url")
        if not url:
            return jsonify({"error": "url parameter required"}), 400
        limit = request.args.get("limit", 50, type=int)
        trend = get_trend(url, limit=limit)
        return jsonify(trend)

    @app.route("/api/regressions")
    def api_regressions():
        url = request.args.get("url")
        if not url:
            return jsonify({"error": "url parameter required"}), 400
        results = detect_regressions(url)
        return jsonify([r.to_dict() for r in results])

    @app.route("/api/stats")
    def api_stats():
        url = request.args.get("url")
        if not url:
            return jsonify({"error": "url parameter required"}), 400
        s = get_stats(url)
        p = get_percentile_stats(url)
        if p:
            s.update(p)
        return jsonify(s)

    @app.route("/api/compare", methods=["POST"])
    def api_compare():
        """Compare multiple URLs concurrently."""
        data = request.get_json()
        urls = data.get("urls", [])
        count = data.get("count", 3)
        if not urls:
            return jsonify({"error": "urls list required"}), 400

        normalized = []
        for url in urls:
            if not url.startswith("http://") and not url.startswith("https://"):
                url = f"https://{url}"
            normalized.append(url)

        from tracepulse.tracer import average_timing
        raw = trace_concurrent(normalized, count)
        results = {}
        for url, traces in raw.items():
            avg = average_timing(traces)
            results[url] = avg.to_dict()
        return jsonify(results)

    @app.route("/api/curl", methods=["POST"])
    def api_curl():
        """Parse and trace a cURL command."""
        data = request.get_json()
        curl_cmd = data.get("curl", "")
        parsed = parse_curl(curl_cmd)
        if not parsed["url"]:
            return jsonify({"error": "Could not parse cURL command"}), 400

        url = parsed["url"]
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"

        result = trace_request(url, parsed["method"], parsed["headers"], parsed["body"])
        resp = result.to_dict()
        resp["parsed_method"] = parsed["method"]
        resp["parsed_headers"] = parsed["headers"]

        if not result.error:
            from tracepulse.storage import save_trace
            save_trace(result)

        return jsonify(resp)

    # --- Presets API ---

    @app.route("/api/presets")
    def api_presets():
        return jsonify(get_all_presets())

    @app.route("/api/presets", methods=["POST"])
    def api_preset_save():
        data = request.get_json()
        name = data.get("name", "").strip()
        url = data.get("url", "").strip()
        if not name or not url:
            return jsonify({"error": "name and url required"}), 400
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"
        save_preset(name, url, data.get("method", "GET"),
                    data.get("headers", {}), data.get("body"))
        return jsonify({"ok": True})

    @app.route("/api/presets/<name>", methods=["DELETE"])
    def api_preset_delete(name):
        if delete_preset(name):
            return jsonify({"ok": True})
        return jsonify({"error": "Not found"}), 404

    # --- Export API ---

    @app.route("/api/export")
    def api_export():
        fmt = request.args.get("format", "csv")
        url = request.args.get("url")
        limit = request.args.get("limit", 100, type=int)

        from tracepulse.exporter import export_csv, export_json, export_html
        if fmt == "json":
            content = export_json(url=url, limit=limit)
            return Response(content, mimetype="application/json",
                          headers={"Content-Disposition": "attachment; filename=tracepulse_export.json"})
        elif fmt == "html":
            content = export_html(url=url, limit=limit)
            return Response(content, mimetype="text/html",
                          headers={"Content-Disposition": "attachment; filename=tracepulse_report.html"})
        else:
            content = export_csv(url=url, limit=limit)
            return Response(content, mimetype="text/csv",
                          headers={"Content-Disposition": "attachment; filename=tracepulse_export.csv"})

    return app
