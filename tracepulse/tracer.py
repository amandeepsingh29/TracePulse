"""
Core tracer module — performs HTTP requests with granular timing breakdown.

Captures: DNS resolution, TCP connection, TLS handshake, server processing,
and content transfer timings for any given URL.
"""

import concurrent.futures
import http.client
import json
import re
import shlex
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse


@dataclass
class TimingBreakdown:
    """Granular timing breakdown for an API request."""

    dns_ms: float = 0.0
    tcp_connect_ms: float = 0.0
    tls_handshake_ms: float = 0.0
    server_processing_ms: float = 0.0
    content_transfer_ms: float = 0.0
    total_ms: float = 0.0

    # Metadata
    url: str = ""
    method: str = "GET"
    status_code: int = 0
    response_size: int = 0
    headers_sent: dict = field(default_factory=dict)
    headers_received: dict = field(default_factory=dict)
    ip_address: str = ""
    tls_version: str = ""
    response_body: str = ""
    geo_info: str = ""
    error: Optional[str] = None

    @property
    def overhead_ms(self) -> float:
        """Time not accounted for in the main phases."""
        accounted = (
            self.dns_ms
            + self.tcp_connect_ms
            + self.tls_handshake_ms
            + self.server_processing_ms
            + self.content_transfer_ms
        )
        return max(0.0, self.total_ms - accounted)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "method": self.method,
            "status_code": self.status_code,
            "response_size": self.response_size,
            "ip_address": self.ip_address,
            "tls_version": self.tls_version,
            "dns_ms": round(self.dns_ms, 2),
            "tcp_connect_ms": round(self.tcp_connect_ms, 2),
            "tls_handshake_ms": round(self.tls_handshake_ms, 2),
            "server_processing_ms": round(self.server_processing_ms, 2),
            "content_transfer_ms": round(self.content_transfer_ms, 2),
            "total_ms": round(self.total_ms, 2),
            "overhead_ms": round(self.overhead_ms, 2),
            "response_body": self.response_body[:2000] if self.response_body else "",
            "geo_info": self.geo_info,
            "error": self.error,
        }


def trace_request(
    url: str,
    method: str = "GET",
    headers: Optional[dict] = None,
    body: Optional[str] = None,
    timeout: float = 30.0,
    follow_redirects: bool = True,
    max_redirects: int = 10,
) -> TimingBreakdown:
    """
    Perform an HTTP(S) request with detailed timing breakdown.

    Measures each phase of the request lifecycle independently:
    - DNS resolution
    - TCP connection
    - TLS handshake (HTTPS only)
    - Server processing (time to first byte)
    - Content transfer (time to download body)
    """
    parsed = urlparse(url)
    is_https = parsed.scheme == "https"
    host = parsed.hostname
    port = parsed.port or (443 if is_https else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"

    timing = TimingBreakdown(url=url, method=method.upper())
    if headers:
        timing.headers_sent = dict(headers)

    request_headers = {"Host": host, "User-Agent": "TracePulse/1.0", "Accept": "*/*"}
    if headers:
        request_headers.update(headers)

    sock = None
    try:
        # --- Phase 1: DNS Resolution ---
        t_dns_start = time.perf_counter()
        addr_info = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        t_dns_end = time.perf_counter()
        timing.dns_ms = (t_dns_end - t_dns_start) * 1000

        if not addr_info:
            timing.error = f"DNS resolution failed for {host}"
            return timing

        family, socktype, proto, _, sockaddr = addr_info[0]
        timing.ip_address = sockaddr[0]

        # --- Phase 2: TCP Connection ---
        t_tcp_start = time.perf_counter()
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        sock.connect(sockaddr)
        t_tcp_end = time.perf_counter()
        timing.tcp_connect_ms = (t_tcp_end - t_tcp_start) * 1000

        # --- Phase 3: TLS Handshake ---
        if is_https:
            t_tls_start = time.perf_counter()
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
            t_tls_end = time.perf_counter()
            timing.tls_handshake_ms = (t_tls_end - t_tls_start) * 1000
            timing.tls_version = sock.version() or ""

        # --- Phase 4: Send Request & Server Processing (TTFB) ---
        request_line = f"{method.upper()} {path} HTTP/1.1\r\n"
        header_lines = "".join(f"{k}: {v}\r\n" for k, v in request_headers.items())

        if body:
            header_lines += f"Content-Length: {len(body.encode())}\r\n"

        raw_request = f"{request_line}{header_lines}\r\n"
        if body:
            raw_request += body

        t_send_start = time.perf_counter()
        sock.sendall(raw_request.encode())

        # Read status line (first byte = TTFB)
        response_data = b""
        chunk = sock.recv(4096)
        t_first_byte = time.perf_counter()
        timing.server_processing_ms = (t_first_byte - t_send_start) * 1000
        response_data += chunk

        # --- Phase 5: Content Transfer ---
        t_transfer_start = time.perf_counter()

        # Use a short timeout for reading remaining data to avoid
        # blocking forever on keep-alive connections
        sock.settimeout(2.0)

        # Read rest of the response
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                # Check if we've received content-length worth of data
                # by peeking at headers so far
                header_end_pos = response_data.find(b"\r\n\r\n")
                if header_end_pos != -1:
                    header_text = response_data[:header_end_pos].decode("utf-8", errors="replace").lower()
                    body_so_far = len(response_data) - header_end_pos - 4
                    # Check for content-length
                    for line in header_text.split("\r\n"):
                        if line.startswith("content-length:"):
                            expected = int(line.split(":", 1)[1].strip())
                            if body_so_far >= expected:
                                break
                    else:
                        # Check for chunked transfer ending
                        if b"transfer-encoding: chunked" in response_data[:header_end_pos].lower():
                            if response_data.endswith(b"0\r\n\r\n"):
                                break
                        continue
                    break
            except socket.timeout:
                break

        t_transfer_end = time.perf_counter()
        timing.content_transfer_ms = (t_transfer_end - t_transfer_start) * 1000

        # Parse response
        try:
            header_end = response_data.find(b"\r\n\r\n")
            if header_end != -1:
                header_section = response_data[:header_end].decode("utf-8", errors="replace")
                body_section = response_data[header_end + 4 :]
                timing.response_size = len(body_section)

                lines = header_section.split("\r\n")
                if lines:
                    status_parts = lines[0].split(" ", 2)
                    if len(status_parts) >= 2:
                        timing.status_code = int(status_parts[1])

                resp_headers = {}
                for line in lines[1:]:
                    if ": " in line:
                        k, v = line.split(": ", 1)
                        resp_headers[k.lower()] = v
                timing.headers_received = resp_headers

                # Handle chunked transfer encoding — accumulate body size
                if resp_headers.get("transfer-encoding", "").lower() == "chunked":
                    timing.response_size = _parse_chunked_size(body_section)

                # Capture response body (truncated)
                try:
                    timing.response_body = body_section.decode("utf-8", errors="replace")[:4000]
                except Exception:
                    timing.response_body = f"<binary {len(body_section)} bytes>"

        except Exception:
            pass

        # Total
        timing.total_ms = (
            timing.dns_ms
            + timing.tcp_connect_ms
            + timing.tls_handshake_ms
            + timing.server_processing_ms
            + timing.content_transfer_ms
        )

        # Handle redirects
        if follow_redirects and timing.status_code in (301, 302, 303, 307, 308) and max_redirects > 0:
            location = timing.headers_received.get("location", "")
            if location:
                if location.startswith("/"):
                    location = f"{parsed.scheme}://{host}{location}"
                redirect_timing = trace_request(
                    location, method, headers, body, timeout, follow_redirects, max_redirects - 1
                )
                # Combine timings — keep original DNS/TCP/TLS, add redirect total
                redirect_timing.dns_ms += timing.dns_ms
                redirect_timing.tcp_connect_ms += timing.tcp_connect_ms
                redirect_timing.tls_handshake_ms += timing.tls_handshake_ms
                redirect_timing.total_ms += timing.total_ms
                redirect_timing.url = url  # Keep original URL
                return redirect_timing

    except socket.gaierror as e:
        timing.error = f"DNS resolution failed: {e}"
    except ConnectionRefusedError:
        timing.error = f"Connection refused by {host}:{port}"
    except socket.timeout:
        timing.error = f"Connection timed out after {timeout}s"
    except ssl.SSLError as e:
        timing.error = f"TLS error: {e}"
    except Exception as e:
        timing.error = f"Request failed: {e}"
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    return timing


def _parse_chunked_size(data: bytes) -> int:
    """Estimate actual body size from chunked transfer encoding."""
    total = 0
    try:
        parts = data.split(b"\r\n")
        i = 0
        while i < len(parts):
            line = parts[i].strip()
            if line:
                try:
                    chunk_size = int(line, 16)
                    if chunk_size == 0:
                        break
                    total += chunk_size
                    i += 2  # skip chunk data line
                    continue
                except ValueError:
                    total += len(parts[i])
            i += 1
    except Exception:
        total = len(data)
    return total


def trace_multiple(
    url: str,
    count: int = 3,
    method: str = "GET",
    headers: Optional[dict] = None,
    body: Optional[str] = None,
    timeout: float = 30.0,
) -> list[TimingBreakdown]:
    """Run multiple traces and return all results."""
    results = []
    for _ in range(count):
        result = trace_request(url, method, headers, body, timeout)
        results.append(result)
    return results


def trace_concurrent(
    urls: list[str],
    count_per_url: int = 3,
    method: str = "GET",
    headers: Optional[dict] = None,
    body: Optional[str] = None,
    timeout: float = 30.0,
    max_workers: int = 5,
) -> dict[str, list[TimingBreakdown]]:
    """Trace multiple URLs concurrently. Returns dict of url -> results."""
    results = {url: [] for url in urls}

    def _trace_one(url: str) -> tuple[str, TimingBreakdown]:
        return url, trace_request(url, method, headers, body, timeout)

    tasks = [(url, i) for url in urls for i in range(count_per_url)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_trace_one, url): url for url, _ in tasks}
        for future in concurrent.futures.as_completed(futures):
            url, result = future.result()
            results[url].append(result)

    return results


def average_timing(results: list[TimingBreakdown]) -> TimingBreakdown:
    """Calculate average timing from multiple trace results."""
    if not results:
        return TimingBreakdown()

    n = len(results)
    avg = TimingBreakdown(
        url=results[0].url,
        method=results[0].method,
        status_code=results[0].status_code,
        dns_ms=sum(r.dns_ms for r in results) / n,
        tcp_connect_ms=sum(r.tcp_connect_ms for r in results) / n,
        tls_handshake_ms=sum(r.tls_handshake_ms for r in results) / n,
        server_processing_ms=sum(r.server_processing_ms for r in results) / n,
        content_transfer_ms=sum(r.content_transfer_ms for r in results) / n,
        total_ms=sum(r.total_ms for r in results) / n,
        response_size=results[-1].response_size,
        ip_address=results[0].ip_address,
        tls_version=results[0].tls_version,
    )
    return avg


def parse_curl(curl_command: str) -> dict:
    """
    Parse a cURL command string into trace parameters.
    Returns dict with: url, method, headers, body.
    """
    # Clean up the command
    cmd = curl_command.strip()
    if cmd.startswith("curl "):
        cmd = cmd[5:]
    elif cmd.startswith("curl\n"):
        cmd = cmd[5:]

    # Handle line continuations
    cmd = cmd.replace("\\\n", " ").replace("\\\r\n", " ")

    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()

    url = ""
    method = "GET"
    headers = {}
    body = None

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-X", "--request") and i + 1 < len(tokens):
            method = tokens[i + 1].upper()
            i += 2
        elif tok in ("-H", "--header") and i + 1 < len(tokens):
            h = tokens[i + 1]
            if ": " in h:
                k, v = h.split(": ", 1)
                headers[k] = v
            elif ":" in h:
                k, v = h.split(":", 1)
                headers[k.strip()] = v.strip()
            i += 2
        elif tok in ("-d", "--data", "--data-raw", "--data-binary") and i + 1 < len(tokens):
            body = tokens[i + 1]
            if method == "GET":
                method = "POST"
            i += 2
        elif tok in ("-u", "--user") and i + 1 < len(tokens):
            import base64
            creds = base64.b64encode(tokens[i + 1].encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
            i += 2
        elif tok.startswith("http://") or tok.startswith("https://"):
            url = tok
            i += 1
        elif tok in ("-k", "--insecure", "-s", "--silent", "-v", "--verbose",
                      "-L", "--location", "-i", "--include", "--compressed"):
            i += 1
        elif tok in ("-o", "--output", "-w", "--write-out", "--connect-timeout",
                      "--max-time", "-A", "--user-agent") and i + 1 < len(tokens):
            if tok in ("-A", "--user-agent"):
                headers["User-Agent"] = tokens[i + 1]
            i += 2
        else:
            # Could be URL without flag
            if not url and not tok.startswith("-"):
                url = tok
            i += 1

    return {"url": url, "method": method, "headers": headers, "body": body}


def get_geo_info(ip_address: str) -> str:
    """Get geolocation info for an IP address using free API."""
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"http://ip-api.com/json/{ip_address}?fields=city,country,isp", timeout=3)
        data = json.loads(resp.read().decode())
        parts = []
        if data.get("city"):
            parts.append(data["city"])
        if data.get("country"):
            parts.append(data["country"])
        if data.get("isp"):
            parts.append(data["isp"])
        return ", ".join(parts) if parts else ""
    except Exception:
        return ""
