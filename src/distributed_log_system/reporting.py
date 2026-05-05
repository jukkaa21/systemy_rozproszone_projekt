from __future__ import annotations

import csv
import json
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio


def export_results(
    output_dir: Path,
    summary: dict,
    benchmark: list[dict],
    export_dashboard: bool,
) -> None:
    _write_json(output_dir / "summary.json", summary)
    _write_csv(output_dir / "top_ips.csv", summary["aggregations"]["top_ips"], "ip")
    _write_csv(
        output_dir / "top_endpoints.csv",
        summary["aggregations"]["top_endpoints"],
        "endpoint",
    )
    _write_csv(
        output_dir / "top_error_endpoints.csv",
        summary["aggregations"]["top_error_endpoints"],
        "endpoint",
    )
    _write_csv(
        output_dir / "status_codes.csv",
        summary["aggregations"]["status_codes"],
        "status_code",
    )
    _write_csv(output_dir / "methods.csv", summary["aggregations"]["methods"], "method")
    _write_csv(output_dir / "hourly_trends.csv", summary["aggregations"]["hourly_trends"])
    _write_csv(output_dir / "anomalies.csv", summary["anomalies"])

    if benchmark:
        _write_csv(output_dir / "benchmark.csv", benchmark)

    if export_dashboard:
        dashboard_html = build_dashboard_html(summary=summary, benchmark=benchmark)
        (output_dir / "dashboard.html").write_text(dashboard_html, encoding="utf-8")


def build_dashboard_html(summary: dict, benchmark: list[dict]) -> str:
    aggregations = summary["aggregations"]
    metrics = summary["metrics"]

    top_ip_figure = go.Figure(
        data=[
            go.Bar(
                x=[item["key"] for item in aggregations["top_ips"]],
                y=[item["count"] for item in aggregations["top_ips"]],
                marker_color="#1f77b4",
            )
        ]
    )
    top_ip_figure.update_layout(
        title="Top IP",
        xaxis_title="IP",
        yaxis_title="Liczba zapytan",
        height=420,
    )

    endpoint_figure = go.Figure(
        data=[
            go.Bar(
                x=[item["key"] for item in aggregations["top_endpoints"]],
                y=[item["count"] for item in aggregations["top_endpoints"]],
                marker_color="#ff7f0e",
            )
        ]
    )
    endpoint_figure.update_layout(
        title="Top endpointy",
        xaxis_title="Endpoint",
        yaxis_title="Liczba zapytan",
        height=420,
    )

    error_endpoint_figure = go.Figure(
        data=[
            go.Bar(
                x=[item["key"] for item in aggregations["top_error_endpoints"]],
                y=[item["count"] for item in aggregations["top_error_endpoints"]],
                marker_color="#d62728",
            )
        ]
    )
    error_endpoint_figure.update_layout(
        title="Top endpointy bledow",
        xaxis_title="Endpoint",
        yaxis_title="Liczba blednych odpowiedzi",
        height=420,
    )

    status_figure = go.Figure(
        data=[
            go.Pie(
                labels=[item["key"] for item in aggregations["status_codes"]],
                values=[item["count"] for item in aggregations["status_codes"]],
                hole=0.45,
            )
        ]
    )
    status_figure.update_layout(title="Rozklad kodow statusu", height=420)

    trend_figure = go.Figure()
    trend_figure.add_trace(
        go.Scatter(
            x=[point["hour"] for point in aggregations["hourly_trends"]],
            y=[point["requests"] for point in aggregations["hourly_trends"]],
            mode="lines+markers",
            name="Wszystkie",
            line={"color": "#2ca02c"},
            visible=True,
        )
    )
    for family, color in (("2xx", "#1f77b4"), ("3xx", "#17becf"), ("4xx", "#ff7f0e"), ("5xx", "#d62728")):
        family_points = aggregations["status_family_trends"][family]
        trend_figure.add_trace(
            go.Scatter(
                x=[point["hour"] for point in family_points],
                y=[point["requests"] for point in family_points],
                mode="lines",
                name=family,
                line={"color": color},
                visible=True,
            )
        )
    trend_figure.update_layout(
        title="Trendy godzinowe z filtrowaniem klas statusu",
        xaxis_title="Godzina (UTC)",
        yaxis_title="Liczba zapytan",
        height=480,
        updatemenus=[
            {
                "buttons": [
                    {
                        "label": "Pokaz wszystko",
                        "method": "update",
                        "args": [{"visible": [True, True, True, True, True]}],
                    },
                    {
                        "label": "Tylko 5xx",
                        "method": "update",
                        "args": [{"visible": [False, False, False, False, True]}],
                    },
                    {
                        "label": "Tylko 4xx",
                        "method": "update",
                        "args": [{"visible": [False, False, False, True, False]}],
                    },
                    {
                        "label": "Tylko 2xx",
                        "method": "update",
                        "args": [{"visible": [False, True, False, False, False]}],
                    },
                ],
                "direction": "down",
                "x": 1.0,
                "xanchor": "right",
                "y": 1.16,
                "yanchor": "top",
            }
        ],
    )

    benchmark_figure = None
    if benchmark:
        benchmark_figure = go.Figure(
            data=[
                go.Bar(
                    x=[item["workers"] for item in benchmark],
                    y=[item["duration_seconds"] for item in benchmark],
                    marker_color="#9467bd",
                    text=[f'{item["speedup_vs_first_run"]:.2f}x' for item in benchmark],
                    textposition="outside",
                )
            ]
        )
        benchmark_figure.update_layout(
            title="Benchmark 1 vs N workerow",
            xaxis_title="Liczba workerow",
            yaxis_title="Czas [s]",
            height=420,
        )

    anomaly_rows = "".join(
        """
        <tr>
            <td>{timestamp}</td>
            <td>{error_count}</td>
            <td>{baseline_mean}</td>
            <td>{baseline_stddev}</td>
            <td>{threshold}</td>
        </tr>
        """.format(**anomaly)
        for anomaly in summary["anomalies"]
    )
    if not anomaly_rows:
        anomaly_rows = """
        <tr>
            <td colspan="5">Brak wykrytych anomalii dla aktualnych parametrow.</td>
        </tr>
        """

    benchmark_table = ""
    if benchmark:
        benchmark_rows = "".join(
            """
            <tr>
                <td>{workers}</td>
                <td>{duration_seconds}</td>
                <td>{processed_lines}</td>
                <td>{speedup_vs_first_run}</td>
            </tr>
            """.format(**item)
            for item in benchmark
        )
        benchmark_table = f"""
        <section class="panel">
            <h2>Benchmark</h2>
            <div class="chart">{pio.to_html(benchmark_figure, include_plotlyjs=False, full_html=False)}</div>
            <table>
                <thead>
                    <tr>
                        <th>Workery</th>
                        <th>Czas [s]</th>
                        <th>Przetworzone linie</th>
                        <th>Speedup</th>
                    </tr>
                </thead>
                <tbody>{benchmark_rows}</tbody>
            </table>
        </section>
        """

    return f"""
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Distributed Log Analytics Dashboard</title>
    <style>
        :root {{
            --bg: #f3f5f9;
            --panel: #ffffff;
            --ink: #10203a;
            --muted: #58677c;
            --accent: #0b6efd;
            --border: #d7dfeb;
        }}
        * {{
            box-sizing: border-box;
        }}
        body {{
            margin: 0;
            font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
            background:
                radial-gradient(circle at top left, rgba(11, 110, 253, 0.12), transparent 30%),
                linear-gradient(180deg, #f7f9fc 0%, #edf2f9 100%);
            color: var(--ink);
        }}
        main {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 32px 20px 48px;
        }}
        h1, h2 {{
            margin: 0 0 12px;
        }}
        p {{
            color: var(--muted);
        }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin: 24px 0;
        }}
        .card, .panel {{
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 18px;
            box-shadow: 0 18px 60px rgba(16, 32, 58, 0.08);
        }}
        .card {{
            padding: 18px;
        }}
        .card strong {{
            display: block;
            font-size: 1.75rem;
            margin-top: 8px;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 20px;
        }}
        .panel {{
            padding: 20px;
            margin-top: 20px;
        }}
        .chart {{
            min-height: 320px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 18px;
            font-size: 0.95rem;
        }}
        th, td {{
            padding: 12px 10px;
            border-bottom: 1px solid var(--border);
            text-align: left;
        }}
        th {{
            color: var(--muted);
        }}
        .meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            font-size: 0.95rem;
        }}
        .pill {{
            padding: 8px 12px;
            border-radius: 999px;
            background: rgba(11, 110, 253, 0.08);
            color: var(--accent);
        }}
    </style>
</head>
<body>
    <main>
        <h1>Distributed Log Analytics Dashboard</h1>
        <p>Statyczny dashboard generowany po wykonaniu pipeline'u ETL. Wyniki mozna eksportowac do CSV/JSON i wykorzystac w kolejnym etapie budowy GUI.</p>
        <div class="meta">
            <span class="pill">Workery: {summary["config"]["workers"]}</span>
            <span class="pill">Chunk size: {summary["config"]["chunk_size"]}</span>
            <span class="pill">Plikow: {len(summary["input_files"])}</span>
            <span class="pill">Wygenerowano: {summary["generated_at_utc"]}</span>
        </div>
        <section class="cards">
            <article class="card">
                <span>Przetworzone linie</span>
                <strong>{metrics["processed_lines"]}</strong>
            </article>
            <article class="card">
                <span>Poprawne rekordy</span>
                <strong>{metrics["valid_lines"]}</strong>
            </article>
            <article class="card">
                <span>Odrzucone rekordy</span>
                <strong>{metrics["invalid_lines"]}</strong>
            </article>
            <article class="card">
                <span>Sredni czas odpowiedzi</span>
                <strong>{metrics["average_response_time_ms"]} ms</strong>
            </article>
            <article class="card">
                <span>Sredni rozmiar odpowiedzi</span>
                <strong>{metrics["average_response_bytes"]} B</strong>
            </article>
            <article class="card">
                <span>Czas wykonania</span>
                <strong>{metrics["duration_seconds"]} s</strong>
            </article>
        </section>
        <section class="grid">
            <div class="panel">
                <h2>Top IP</h2>
                <div class="chart">{pio.to_html(top_ip_figure, include_plotlyjs=True, full_html=False)}</div>
            </div>
            <div class="panel">
                <h2>Top endpointy</h2>
                <div class="chart">{pio.to_html(endpoint_figure, include_plotlyjs=False, full_html=False)}</div>
            </div>
            <div class="panel">
                <h2>Top błędy</h2>
                <div class="chart">{pio.to_html(error_endpoint_figure, include_plotlyjs=False, full_html=False)}</div>
            </div>
            <div class="panel">
                <h2>Status codes</h2>
                <div class="chart">{pio.to_html(status_figure, include_plotlyjs=False, full_html=False)}</div>
            </div>
        </section>
        <section class="panel">
            <h2>Trendy czasowe</h2>
            <p>Menu przy wykresie pozwala przefiltrowac widok dla wybranej klasy odpowiedzi HTTP.</p>
            <div class="chart">{pio.to_html(trend_figure, include_plotlyjs=False, full_html=False)}</div>
        </section>
        <section class="panel">
            <h2>Wykryte anomalie 5xx</h2>
            <table>
                <thead>
                    <tr>
                        <th>Znacznik czasu</th>
                        <th>Liczba bledow</th>
                        <th>Srednia z okna</th>
                        <th>Odchylenie standardowe</th>
                        <th>Prog alarmowy</th>
                    </tr>
                </thead>
                <tbody>{anomaly_rows}</tbody>
            </table>
        </section>
        {benchmark_table}
    </main>
</body>
</html>
"""


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict], rename_key_to: str | None = None) -> None:
    normalized_rows = []
    for row in rows:
        if rename_key_to and "key" in row:
            normalized = {rename_key_to: row["key"], "count": row["count"]}
        else:
            normalized = row
        normalized_rows.append(normalized)

    if not normalized_rows:
        headers = [rename_key_to, "count"] if rename_key_to else ["empty"]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
        return

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(normalized_rows[0].keys()))
        writer.writeheader()
        writer.writerows(normalized_rows)
