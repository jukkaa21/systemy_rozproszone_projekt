from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .pipeline import execute_pipeline


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = "output/ui-run"
DEFAULT_WORKERS = max(1, (os.cpu_count() or 2) - 1)
REPORT_FILES = (
    "dashboard.html",
    "summary.json",
    "top_ips.csv",
    "top_endpoints.csv",
    "top_error_endpoints.csv",
    "status_codes.csv",
    "methods.csv",
    "hourly_trends.csv",
    "anomalies.csv",
    "benchmark.csv",
)


@dataclass
class UiState:
    status: str = "idle"
    message: str = "Gotowe do uruchomienia analizy."
    role: str = "dashboard"
    started_at: str | None = None
    finished_at: str | None = None
    output_dir: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    benchmark: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


STATE = UiState()
STATE_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()


INDEX_HTML = """<!doctype html>
<html lang="pl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Distributed Log Analytics UI</title>
    <style>
        :root {
            --bg: #f4f7fb;
            --panel: #ffffff;
            --ink: #162033;
            --muted: #607086;
            --border: #d7e0ec;
            --accent: #0b6efd;
            --accent-dark: #084eb3;
            --success: #147a43;
            --warning: #9a5b00;
            --error: #b42318;
        }
        * {
            box-sizing: border-box;
        }
        body {
            margin: 0;
            background: var(--bg);
            color: var(--ink);
            font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
        }
        header {
            border-bottom: 1px solid var(--border);
            background: var(--panel);
        }
        .shell {
            width: min(1440px, calc(100% - 32px));
            margin: 0 auto;
        }
        .topbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
            padding: 18px 0 14px;
        }
        h1 {
            margin: 0;
            font-size: 1.35rem;
            line-height: 1.2;
        }
        h2 {
            margin: 0 0 16px;
            font-size: 1.05rem;
        }
        .status-line {
            display: flex;
            flex-wrap: wrap;
            justify-content: flex-end;
            gap: 8px;
        }
        .pill {
            display: inline-flex;
            align-items: center;
            min-height: 32px;
            padding: 6px 10px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: #f8fafd;
            color: var(--muted);
            font-size: 0.9rem;
            overflow-wrap: anywhere;
        }
        .pill.success {
            border-color: rgba(20, 122, 67, 0.28);
            color: var(--success);
            background: rgba(20, 122, 67, 0.08);
        }
        .pill.running {
            border-color: rgba(11, 110, 253, 0.28);
            color: var(--accent-dark);
            background: rgba(11, 110, 253, 0.08);
        }
        .pill.error {
            border-color: rgba(180, 35, 24, 0.28);
            color: var(--error);
            background: rgba(180, 35, 24, 0.08);
        }
        .tabs {
            display: flex;
            gap: 4px;
            padding-bottom: 12px;
            overflow-x: auto;
        }
        .tab-button {
            appearance: none;
            border: 1px solid transparent;
            border-radius: 8px;
            background: transparent;
            color: var(--muted);
            cursor: pointer;
            font: inherit;
            padding: 10px 14px;
            white-space: nowrap;
        }
        .tab-button[aria-selected="true"] {
            border-color: var(--border);
            background: #edf4ff;
            color: var(--accent-dark);
        }
        main {
            padding: 20px 0 44px;
        }
        .view {
            display: none;
        }
        .view.active {
            display: block;
        }
        .layout {
            display: grid;
            grid-template-columns: minmax(0, 1.1fr) minmax(280px, 0.9fr);
            gap: 20px;
            align-items: start;
        }
        .panel {
            min-width: 0;
            padding: 20px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--panel);
            box-shadow: 0 12px 32px rgba(22, 32, 51, 0.06);
        }
        .panel + .panel {
            margin-top: 20px;
        }
        .form-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 16px;
        }
        .field {
            min-width: 0;
        }
        .field.full {
            grid-column: 1 / -1;
        }
        label {
            display: block;
            margin: 0 0 6px;
            color: var(--muted);
            font-size: 0.9rem;
            font-weight: 600;
        }
        input,
        textarea {
            width: 100%;
            min-width: 0;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: #fff;
            color: var(--ink);
            font: inherit;
            padding: 10px 11px;
        }
        textarea {
            min-height: 86px;
            resize: vertical;
        }
        input:focus,
        textarea:focus,
        button:focus-visible,
        a.button:focus-visible {
            outline: 3px solid rgba(11, 110, 253, 0.22);
            outline-offset: 2px;
        }
        .check-row {
            display: flex;
            align-items: center;
            gap: 9px;
            min-height: 42px;
        }
        .check-row input {
            width: 18px;
            height: 18px;
        }
        .check-row label {
            margin: 0;
            color: var(--ink);
            font-weight: 500;
        }
        .actions {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 18px;
        }
        button,
        a.button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 40px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: #fff;
            color: var(--ink);
            cursor: pointer;
            font: inherit;
            font-weight: 600;
            padding: 9px 13px;
            text-decoration: none;
        }
        button.primary {
            border-color: var(--accent);
            background: var(--accent);
            color: #fff;
        }
        button.primary:hover,
        a.button.primary:hover {
            background: var(--accent-dark);
            border-color: var(--accent-dark);
        }
        button:disabled,
        a.button.disabled {
            cursor: not-allowed;
            opacity: 0.56;
            pointer-events: none;
        }
        .dashboard-frame {
            width: 100%;
            height: calc(100vh - 220px);
            min-height: 720px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: #fff;
        }
        .empty-dashboard {
            display: none;
            min-height: 360px;
            align-items: center;
            justify-content: center;
            border: 1px dashed var(--border);
            border-radius: 8px;
            color: var(--muted);
            text-align: center;
            padding: 24px;
            background: #fff;
        }
        .downloads {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
            gap: 10px;
        }
        .download-link {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding: 10px 12px;
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--ink);
            text-decoration: none;
            background: #fff;
        }
        .download-link span {
            color: var(--muted);
            font-size: 0.86rem;
        }
        dl {
            display: grid;
            grid-template-columns: minmax(130px, 0.8fr) minmax(0, 1.2fr);
            gap: 10px 14px;
            margin: 0;
        }
        dt {
            color: var(--muted);
            font-weight: 600;
        }
        dd {
            margin: 0;
            overflow-wrap: anywhere;
        }
        .log {
            min-height: 136px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: #0f172a;
            color: #dbeafe;
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            font-size: 0.9rem;
            line-height: 1.45;
            margin: 0;
            overflow: auto;
            padding: 12px;
            white-space: pre-wrap;
        }
        @media (max-width: 980px) {
            .topbar {
                align-items: flex-start;
                flex-direction: column;
            }
            .status-line {
                justify-content: flex-start;
            }
            .layout,
            .form-grid {
                grid-template-columns: 1fr;
            }
            .dashboard-frame {
                min-height: 620px;
            }
        }
    </style>
</head>
<body>
    <header>
        <div class="shell">
            <div class="topbar">
                <h1>Distributed Log Analytics</h1>
                <div class="status-line">
                    <span class="pill" id="status-pill">Status: idle</span>
                    <span class="pill" id="role-pill">Widok: dashboard</span>
                    <span class="pill" id="output-pill">Output: brak</span>
                </div>
            </div>
            <nav class="tabs" aria-label="Role i widoki">
                <button class="tab-button" data-tab="dashboard" aria-selected="true">Dashboard</button>
                <button class="tab-button" data-tab="analyst" aria-selected="false">Analityk</button>
                <button class="tab-button" data-tab="admin" aria-selected="false">Admin</button>
            </nav>
        </div>
    </header>
    <main class="shell">
        <section class="view active" id="dashboard-view">
            <div class="panel">
                <h2>Dashboard</h2>
                <div class="actions">
                    <a class="button primary disabled" id="open-dashboard" href="#" target="_blank" rel="noreferrer">Otwórz dashboard</a>
                    <a class="button disabled" id="download-dashboard" href="#">Pobierz dashboard</a>
                    <a class="button disabled" id="download-report" href="#">Pobierz raport ZIP</a>
                </div>
                <div class="empty-dashboard" id="empty-dashboard">Brak wygenerowanego dashboardu.</div>
                <iframe class="dashboard-frame" id="dashboard-frame" title="Dashboard raportu"></iframe>
            </div>
        </section>

        <section class="view" id="analyst-view">
            <div class="layout">
                <section class="panel">
                    <h2>Analityk / operator</h2>
                    <form id="analyst-form" data-role="analyst">
                        <div class="form-grid">
                            <div class="field full">
                                <label for="analyst-input-paths">Ścieżki logów</label>
                                <textarea id="analyst-input-paths" name="input_paths" spellcheck="false"></textarea>
                            </div>
                            <div class="field full">
                                <label for="analyst-output-dir">Katalog wyników</label>
                                <input id="analyst-output-dir" name="output_dir" type="text">
                            </div>
                            <div class="field">
                                <label for="analyst-chunk-size">Chunk size</label>
                                <input id="analyst-chunk-size" name="chunk_size" type="number" min="1" step="1">
                            </div>
                            <div class="field">
                                <label for="analyst-top-n">Top N</label>
                                <input id="analyst-top-n" name="top_n" type="number" min="1" step="1">
                            </div>
                        </div>
                        <div class="actions">
                            <button class="primary" type="submit">Uruchom analizę</button>
                            <a class="button disabled" data-download-dashboard href="#">Pobierz dashboard</a>
                            <a class="button disabled" data-download-report href="#">Pobierz raport ZIP</a>
                        </div>
                    </form>
                </section>
                <aside class="panel">
                    <h2>Status analizy</h2>
                    <dl class="run-details" data-run-details></dl>
                </aside>
            </div>
        </section>

        <section class="view" id="admin-view">
            <div class="layout">
                <section class="panel">
                    <h2>Administrator</h2>
                    <form id="admin-form" data-role="admin">
                        <div class="form-grid">
                            <div class="field full">
                                <label for="admin-input-paths">Ścieżki logów</label>
                                <textarea id="admin-input-paths" name="input_paths" spellcheck="false"></textarea>
                            </div>
                            <div class="field full">
                                <label for="admin-output-dir">Katalog wyników</label>
                                <input id="admin-output-dir" name="output_dir" type="text">
                            </div>
                            <div class="field">
                                <label for="admin-chunk-size">Chunk size</label>
                                <input id="admin-chunk-size" name="chunk_size" type="number" min="1" step="1">
                            </div>
                            <div class="field">
                                <label for="admin-top-n">Top N</label>
                                <input id="admin-top-n" name="top_n" type="number" min="1" step="1">
                            </div>
                            <div class="field">
                                <label for="admin-workers">Workery</label>
                                <input id="admin-workers" name="workers" type="number" min="1" step="1">
                            </div>
                            <div class="field">
                                <label for="admin-anomaly-window">Okno anomalii</label>
                                <input id="admin-anomaly-window" name="anomaly_window" type="number" min="3" step="1">
                            </div>
                            <div class="field">
                                <label for="admin-anomaly-sigma">Próg anomalii sigma</label>
                                <input id="admin-anomaly-sigma" name="anomaly_sigma" type="number" min="0.1" step="0.1">
                            </div>
                            <div class="field">
                                <div class="check-row">
                                    <input id="admin-benchmark" name="benchmark" type="checkbox">
                                    <label for="admin-benchmark">Benchmark</label>
                                </div>
                            </div>
                            <div class="field full">
                                <label for="admin-benchmark-workers">Workery benchmarku</label>
                                <input id="admin-benchmark-workers" name="benchmark_workers" type="text">
                            </div>
                        </div>
                        <div class="actions">
                            <button class="primary" type="submit">Uruchom analizę</button>
                            <a class="button disabled" data-download-dashboard href="#">Pobierz dashboard</a>
                            <a class="button disabled" data-download-report href="#">Pobierz raport ZIP</a>
                        </div>
                    </form>
                </section>
                <aside class="panel">
                    <h2>Monitor pipeline</h2>
                    <pre class="log" id="pipeline-log"></pre>
                </aside>
            </div>
        </section>
    </main>
    <script>
        const state = {
            polling: null,
            dashboardVersion: Date.now(),
            dashboardFingerprint: "",
        };

        const tabs = document.querySelectorAll("[data-tab]");
        const views = {
            dashboard: document.getElementById("dashboard-view"),
            analyst: document.getElementById("analyst-view"),
            admin: document.getElementById("admin-view"),
        };
        const statusPill = document.getElementById("status-pill");
        const rolePill = document.getElementById("role-pill");
        const outputPill = document.getElementById("output-pill");
        const frame = document.getElementById("dashboard-frame");
        const emptyDashboard = document.getElementById("empty-dashboard");
        const openDashboard = document.getElementById("open-dashboard");
        const downloadDashboard = document.getElementById("download-dashboard");
        const downloadReport = document.getElementById("download-report");
        const pipelineLog = document.getElementById("pipeline-log");

        tabs.forEach((button) => {
            button.addEventListener("click", () => {
                const tab = button.dataset.tab;
                tabs.forEach((item) => item.setAttribute("aria-selected", String(item === button)));
                Object.entries(views).forEach(([name, view]) => {
                    view.classList.toggle("active", name === tab);
                });
            });
        });

        document.querySelectorAll("form[data-role]").forEach((form) => {
            form.addEventListener("submit", async (event) => {
                event.preventDefault();
                await runAnalysis(form);
            });
        });

        async function runAnalysis(form) {
            const role = form.dataset.role;
            const payload = formPayload(form, role);
            setButtonsDisabled(true);
            await fetch("/api/run", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload),
            }).then(async (response) => {
                const body = await response.json();
                if (!response.ok) {
                    throw new Error(body.error || "Nie udało się uruchomić analizy.");
                }
                await refreshStatus();
                startPolling();
            }).catch((error) => {
                pipelineLog.textContent = error.message;
                setButtonsDisabled(false);
            });
        }

        function formPayload(form, role) {
            const data = new FormData(form);
            const rawPaths = String(data.get("input_paths") || "");
            const payload = {
                role,
                input_paths: rawPaths.split(/\\n|,/).map((value) => value.trim()).filter(Boolean),
                output_dir: String(data.get("output_dir") || "").trim(),
                chunk_size: numberValue(data.get("chunk_size"), 10000),
                top_n: numberValue(data.get("top_n"), 10),
            };
            if (role === "admin") {
                payload.workers = numberValue(data.get("workers"), 1);
                payload.anomaly_window = numberValue(data.get("anomaly_window"), 5);
                payload.anomaly_sigma = floatValue(data.get("anomaly_sigma"), 2.0);
                payload.benchmark = Boolean(data.get("benchmark"));
                payload.benchmark_workers = String(data.get("benchmark_workers") || "")
                    .split(/\\s|,/)
                    .map((value) => value.trim())
                    .filter(Boolean)
                    .map((value) => Number.parseInt(value, 10))
                    .filter((value) => Number.isFinite(value) && value > 0);
            }
            return payload;
        }

        function numberValue(value, fallback) {
            const parsed = Number.parseInt(value, 10);
            return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
        }

        function floatValue(value, fallback) {
            const parsed = Number.parseFloat(value);
            return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
        }

        async function refreshStatus() {
            const response = await fetch("/api/status");
            const data = await response.json();
            applyStatus(data);
            return data;
        }

        function applyStatus(data) {
            const status = data.status || "idle";
            statusPill.textContent = `Status: ${status}`;
            statusPill.className = `pill ${status}`;
            rolePill.textContent = `Widok: ${data.role || "dashboard"}`;
            outputPill.textContent = `Output: ${data.output_dir || "brak"}`;

            const hasDashboard = Boolean(data.has_dashboard);
            const fingerprint = `${data.output_dir || ""}|${data.finished_at || ""}`;
            if (hasDashboard && state.dashboardFingerprint !== fingerprint) {
                state.dashboardVersion = Date.now();
                state.dashboardFingerprint = fingerprint;
            }
            const dashboardUrl = `/artifact?file=dashboard.html&v=${state.dashboardVersion}`;
            frame.style.display = hasDashboard ? "block" : "none";
            emptyDashboard.style.display = hasDashboard ? "none" : "flex";
            if (hasDashboard && frame.getAttribute("src") !== dashboardUrl) {
                frame.setAttribute("src", dashboardUrl);
            }

            setLink(openDashboard, hasDashboard, "/artifact?file=dashboard.html");
            setLink(downloadDashboard, hasDashboard, "/artifact?file=dashboard.html&download=1");
            setLink(downloadReport, hasDashboard, "/download/report.zip");
            document.querySelectorAll("[data-download-dashboard]").forEach((link) => {
                setLink(link, hasDashboard, "/artifact?file=dashboard.html&download=1");
            });
            document.querySelectorAll("[data-download-report]").forEach((link) => {
                setLink(link, hasDashboard, "/download/report.zip");
            });

            renderDetails(data);
            renderLog(data);
            setButtonsDisabled(status === "running");
            if (status !== "running" && state.polling) {
                clearInterval(state.polling);
                state.polling = null;
            }
        }

        function setLink(link, enabled, href) {
            link.href = enabled ? href : "#";
            link.classList.toggle("disabled", !enabled);
            link.setAttribute("aria-disabled", String(!enabled));
        }

        function setButtonsDisabled(disabled) {
            document.querySelectorAll("form button[type='submit']").forEach((button) => {
                button.disabled = disabled;
            });
        }

        function renderDetails(data) {
            const details = document.querySelector("[data-run-details]");
            const metrics = data.metrics || {};
            const config = data.config || {};
            const rows = [
                ["Status", data.message || data.status || "idle"],
                ["Start", data.started_at || "-"],
                ["Koniec", data.finished_at || "-"],
                ["Linie", metrics.processed_lines ?? "-"],
                ["Poprawne", metrics.valid_lines ?? "-"],
                ["Błędne", metrics.invalid_lines ?? "-"],
                ["Czas", metrics.duration_seconds !== undefined ? `${metrics.duration_seconds} s` : "-"],
                ["Workery", config.workers ?? "-"],
                ["Sigma", config.anomaly_sigma ?? "-"],
            ];
            details.innerHTML = rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(String(value))}</dd>`).join("");
        }

        function renderLog(data) {
            const metrics = data.metrics || {};
            const config = data.config || {};
            const benchmark = data.benchmark || [];
            const lines = [
                `status=${data.status || "idle"}`,
                `message=${data.message || ""}`,
                `role=${data.role || ""}`,
                `output=${data.output_dir || ""}`,
                `started_at=${data.started_at || ""}`,
                `finished_at=${data.finished_at || ""}`,
                `workers=${config.workers ?? ""}`,
                `chunk_size=${config.chunk_size ?? ""}`,
                `anomaly_sigma=${config.anomaly_sigma ?? ""}`,
                `processed_lines=${metrics.processed_lines ?? ""}`,
                `valid_lines=${metrics.valid_lines ?? ""}`,
                `duration_seconds=${metrics.duration_seconds ?? ""}`,
                `benchmark_runs=${benchmark.length}`,
            ];
            if (data.error) {
                lines.push(`error=${data.error}`);
            }
            pipelineLog.textContent = lines.join("\\n");
        }

        function startPolling() {
            if (state.polling) {
                clearInterval(state.polling);
            }
            state.polling = setInterval(refreshStatus, 1600);
        }

        function escapeHtml(value) {
            return value
                .replaceAll("&", "&amp;")
                .replaceAll("<", "&lt;")
                .replaceAll(">", "&gt;")
                .replaceAll('"', "&quot;")
                .replaceAll("'", "&#039;");
        }

        function fillDefaults(data) {
            const defaults = data.defaults || {};
            for (const prefix of ["analyst", "admin"]) {
                document.getElementById(`${prefix}-input-paths`).value = defaults.input_paths || "";
                document.getElementById(`${prefix}-output-dir`).value = defaults.output_dir || "output/ui-run";
                document.getElementById(`${prefix}-chunk-size`).value = defaults.chunk_size || 10000;
                document.getElementById(`${prefix}-top-n`).value = defaults.top_n || 10;
            }
            document.getElementById("admin-workers").value = defaults.workers || 1;
            document.getElementById("admin-anomaly-window").value = defaults.anomaly_window || 5;
            document.getElementById("admin-anomaly-sigma").value = defaults.anomaly_sigma || 2.0;
            document.getElementById("admin-benchmark-workers").value = `1 ${defaults.workers || 1}`;
        }

        refreshStatus().then((data) => {
            fillDefaults(data);
            if (data.status === "running") {
                startPolling();
            }
        });
    </script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lokalny interfejs webowy dla analizy logow.")
    parser.add_argument("--host", default="127.0.0.1", help="Adres nasluchiwania UI.")
    parser.add_argument("--port", type=int, default=8080, help="Port UI.")
    args = parser.parse_args(argv)

    _load_latest_dashboard()

    server = ThreadingHTTPServer((args.host, args.port), UiRequestHandler)
    print(f"UI dostepne pod adresem http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


class UiRequestHandler(BaseHTTPRequestHandler):
    server_version = "DistributedLogUI/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(INDEX_HTML)
        elif parsed.path == "/api/status":
            self._send_json(_state_payload())
        elif parsed.path == "/artifact":
            params = parse_qs(parsed.query)
            self._send_artifact(
                filename=params.get("file", [""])[0],
                download=params.get("download", ["0"])[0] == "1",
            )
        elif parsed.path == "/download/report.zip":
            self._send_report_zip()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Nie znaleziono zasobu.")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            self.send_error(HTTPStatus.NOT_FOUND, "Nie znaleziono zasobu.")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            config = _build_run_config(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if not RUN_LOCK.acquire(blocking=False):
            self._send_json(
                {"error": "Pipeline jest juz uruchomiony."},
                status=HTTPStatus.CONFLICT,
            )
            return

        thread = threading.Thread(target=_run_pipeline, args=(config,), daemon=True)
        thread.start()
        self._send_json({"status": "started"})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_artifact(self, filename: str, download: bool) -> None:
        file_path = _resolve_report_file(filename)
        if file_path is None or not file_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "Plik raportu nie istnieje.")
            return

        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
        self.end_headers()
        self.wfile.write(body)

    def _send_report_zip(self) -> None:
        output_dir = _current_output_dir()
        if output_dir is None:
            self.send_error(HTTPStatus.NOT_FOUND, "Brak raportu do pobrania.")
            return

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for filename in REPORT_FILES:
                file_path = output_dir / filename
                if file_path.exists() and file_path.is_file():
                    archive.write(file_path, arcname=filename)

        body = buffer.getvalue()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", 'attachment; filename="distributed-log-report.zip"')
        self.end_headers()
        self.wfile.write(body)


def _build_run_config(payload: dict[str, Any]) -> dict[str, Any]:
    role = str(payload.get("role", "analyst")).strip().lower()
    if role not in {"analyst", "admin"}:
        raise ValueError("Nieznana rola uruchomienia.")

    input_paths = payload.get("input_paths") or []
    if isinstance(input_paths, str):
        input_paths = [input_paths]
    input_paths = [str(path).strip() for path in input_paths if str(path).strip()]
    if not input_paths:
        raise ValueError("Podaj przynajmniej jedna sciezke logow.")

    output_dir = str(payload.get("output_dir") or DEFAULT_OUTPUT_DIR).strip()
    chunk_size = _positive_int(payload.get("chunk_size"), default=10000)
    top_n = _positive_int(payload.get("top_n"), default=10)

    config: dict[str, Any] = {
        "role": role,
        "input_paths": input_paths,
        "output_dir": output_dir,
        "workers": DEFAULT_WORKERS,
        "chunk_size": chunk_size,
        "top_n": top_n,
        "anomaly_window": 5,
        "anomaly_sigma": 2.0,
        "benchmark_workers": None,
    }

    if role == "admin":
        workers = _positive_int(payload.get("workers"), default=DEFAULT_WORKERS)
        anomaly_window = _positive_int(payload.get("anomaly_window"), default=5)
        anomaly_sigma = _positive_float(payload.get("anomaly_sigma"), default=2.0)
        benchmark = bool(payload.get("benchmark"))
        benchmark_workers = [
            _positive_int(value, default=workers)
            for value in payload.get("benchmark_workers") or []
        ]

        config.update(
            {
                "workers": workers,
                "anomaly_window": anomaly_window,
                "anomaly_sigma": anomaly_sigma,
                "benchmark_workers": benchmark_workers or [1, workers] if benchmark else None,
            }
        )

    return config


def _run_pipeline(config: dict[str, Any]) -> None:
    started_at = _now()
    with STATE_LOCK:
        STATE.status = "running"
        STATE.message = "Pipeline przetwarza logi."
        STATE.role = "admin" if config["role"] == "admin" else "analityk"
        STATE.started_at = started_at
        STATE.finished_at = None
        STATE.output_dir = config["output_dir"]
        STATE.config = _public_config(config)
        STATE.metrics = {}
        STATE.benchmark = []
        STATE.error = None

    try:
        summary = execute_pipeline(
            input_paths=config["input_paths"],
            output_dir=config["output_dir"],
            workers=config["workers"],
            chunk_size=config["chunk_size"],
            top_n=config["top_n"],
            anomaly_window=config["anomaly_window"],
            anomaly_sigma=config["anomaly_sigma"],
            benchmark_workers=config["benchmark_workers"],
            export_dashboard=True,
        )
    except Exception as exc:  # noqa: BLE001 - UI should surface pipeline failures.
        with STATE_LOCK:
            STATE.status = "error"
            STATE.message = "Pipeline zakonczyl sie bledem."
            STATE.finished_at = _now()
            STATE.error = str(exc)
    else:
        with STATE_LOCK:
            STATE.status = "success"
            STATE.message = "Raport zostal wygenerowany."
            STATE.finished_at = _now()
            STATE.metrics = summary.get("metrics", {})
            STATE.benchmark = summary.get("benchmark", [])
            STATE.config = summary.get("config", STATE.config)
            STATE.output_dir = config["output_dir"]
            STATE.error = None
    finally:
        RUN_LOCK.release()


def _state_payload() -> dict[str, Any]:
    with STATE_LOCK:
        output_dir = _path_from_output_dir(STATE.output_dir) if STATE.output_dir else None
        has_dashboard = bool(output_dir and (output_dir / "dashboard.html").exists())
        payload = {
            "status": STATE.status,
            "message": STATE.message,
            "role": STATE.role,
            "started_at": STATE.started_at,
            "finished_at": STATE.finished_at,
            "output_dir": STATE.output_dir,
            "config": STATE.config,
            "metrics": STATE.metrics,
            "benchmark": STATE.benchmark,
            "error": STATE.error,
            "has_dashboard": has_dashboard,
            "files": _available_files(output_dir) if output_dir else [],
            "defaults": _defaults(),
        }
    return payload


def _load_latest_dashboard() -> None:
    dashboards = sorted(
        PROJECT_ROOT.glob("output/**/dashboard.html"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not dashboards:
        return

    output_dir = dashboards[0].parent
    with STATE_LOCK:
        STATE.message = "Wczytano ostatni wygenerowany dashboard."
        STATE.output_dir = _display_path(output_dir)


def _current_output_dir() -> Path | None:
    with STATE_LOCK:
        raw_output_dir = STATE.output_dir
    return _path_from_output_dir(raw_output_dir) if raw_output_dir else None


def _resolve_report_file(filename: str) -> Path | None:
    if filename not in REPORT_FILES:
        return None

    output_dir = _current_output_dir()
    if output_dir is None:
        return None

    file_path = (output_dir / filename).resolve()
    if output_dir.resolve() not in file_path.parents and file_path != output_dir.resolve():
        return None
    return file_path


def _available_files(output_dir: Path | None) -> list[dict[str, str]]:
    if output_dir is None:
        return []

    files = []
    for filename in REPORT_FILES:
        file_path = output_dir / filename
        if file_path.exists() and file_path.is_file():
            files.append(
                {
                    "name": filename,
                    "url": f"/artifact?file={filename}",
                    "download_url": f"/artifact?file={filename}&download=1",
                }
            )
    return files


def _defaults() -> dict[str, Any]:
    default_input = "logfiles.log" if (PROJECT_ROOT / "logfiles.log").exists() else "sample.log"
    return {
        "input_paths": default_input,
        "output_dir": DEFAULT_OUTPUT_DIR,
        "workers": DEFAULT_WORKERS,
        "chunk_size": 10000,
        "top_n": 10,
        "anomaly_window": 5,
        "anomaly_sigma": 2.0,
    }


def _path_from_output_dir(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _public_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "workers": config["workers"],
        "chunk_size": config["chunk_size"],
        "top_n": config["top_n"],
        "anomaly_window": config["anomaly_window"],
        "anomaly_sigma": config["anomaly_sigma"],
        "benchmark": bool(config["benchmark_workers"]),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
