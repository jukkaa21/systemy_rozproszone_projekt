from __future__ import annotations

import csv
import json
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio


PLOT_CONFIG = {"responsive": True, "displaylogo": False}


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


def _build_horizontal_bar(items: list[dict], title: str, axis_title: str, color: str) -> go.Figure:
    ordered_items = list(reversed(items))
    figure = go.Figure(
        data=[
            go.Bar(
                x=[item["count"] for item in ordered_items],
                y=[item["key"] for item in ordered_items],
                orientation="h",
                marker_color=color,
                hovertemplate="%{y}: %{x}<extra></extra>",
            )
        ]
    )
    figure.update_layout(
        title=title,
        xaxis_title=axis_title,
        yaxis_title="",
        height=max(360, 120 + len(ordered_items) * 34),
        margin={"l": 178, "r": 24, "t": 56, "b": 56},
        bargap=0.24,
    )
    figure.update_xaxes(automargin=True, rangemode="tozero")
    figure.update_yaxes(automargin=True, ticklabelstandoff=12)
    return figure


def _figure_html(figure: go.Figure, include_plotlyjs: bool = False) -> str:
    return pio.to_html(
        figure,
        include_plotlyjs=include_plotlyjs,
        full_html=False,
        config=PLOT_CONFIG,
        default_width="100%",
        default_height="100%",
    )


def build_dashboard_html(summary: dict, benchmark: list[dict]) -> str:
    aggregations = summary["aggregations"]
    metrics = summary["metrics"]

    top_ip_figure = _build_horizontal_bar(
        items=aggregations["top_ips"],
        title="Top IP",
        axis_title="Liczba zapytan",
        color="#1f77b4",
    )

    endpoint_figure = _build_horizontal_bar(
        items=aggregations["top_endpoints"],
        title="Top endpointy",
        axis_title="Liczba zapytan",
        color="#ff7f0e",
    )

    error_endpoint_figure = _build_horizontal_bar(
        items=aggregations["top_error_endpoints"],
        title="Top endpointy bledow",
        axis_title="Liczba blednych odpowiedzi",
        color="#d62728",
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
    status_figure.update_layout(
        title="Rozklad kodow statusu",
        height=420,
        margin={"l": 24, "r": 24, "t": 56, "b": 64},
        legend={"orientation": "h", "x": 0.5, "xanchor": "center", "y": -0.08},
    )

    trend_figure = go.Figure()
    trend_figure.add_trace(
        go.Scatter(
            x=[point["hour"] for point in aggregations["hourly_trends"]],
            y=[point["requests"] for point in aggregations["hourly_trends"]],
            mode="lines+markers",
            name="Wszystkie",
            line={"color": "#2ca02c", "shape": "hv"},
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
                line={"color": color, "shape": "hv"},
                visible=True,
            )
        )
    trend_figure.update_layout(
        title="Trendy minutowe z filtrowaniem klas statusu",
        xaxis_title="Czas (UTC)",
        yaxis_title="Liczba zapytan",
        height=480,
        margin={"l": 72, "r": 32, "t": 86, "b": 72},
        legend={"orientation": "h", "x": 0.0, "xanchor": "left", "y": 1.03, "yanchor": "bottom"},
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
                "y": 1.14,
                "yanchor": "top",
            }
        ],
    )
    trend_figure.update_yaxes(tickformat=",d")

    benchmark_figure = None
    if benchmark:
        benchmark_y_max = max((item["duration_seconds"] for item in benchmark), default=0)
        benchmark_figure = go.Figure(
            data=[
                go.Bar(
                    x=[item["workers"] for item in benchmark],
                    y=[item["duration_seconds"] for item in benchmark],
                    width=0.35,
                    marker_color="#9467bd",
                    text=[f'{item["speedup_vs_first_run"]:.2f}x' for item in benchmark],
                    textposition="outside",
                    cliponaxis=False,
                )
            ]
        )
        benchmark_figure.update_layout(
            title="Benchmark 1 vs N workerow",
            xaxis_title="Liczba workerow",
            yaxis_title="Czas [s]",
            height=500,
            margin={"l": 72, "r": 32, "t": 92, "b": 64},
            bargap=0.55,
        )
        benchmark_figure.update_xaxes(
            tickmode="array",
            tickvals=[item["workers"] for item in benchmark],
            ticktext=[str(item["workers"]) for item in benchmark],
        )
        benchmark_figure.update_yaxes(
            range=[0, benchmark_y_max * 1.25 if benchmark_y_max else 1],
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
            <div class="chart">{_figure_html(benchmark_figure)}</div>
            <div class="table-scroll">
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
            </div>
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
            background: linear-gradient(180deg, #f8fafc 0%, #eef3f8 100%);
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
            grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr));
            gap: 16px;
            margin: 24px 0;
        }}
        .card, .panel {{
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 8px;
            box-shadow: 0 14px 38px rgba(16, 32, 58, 0.07);
            min-width: 0;
        }}
        .card {{
            padding: 18px;
        }}
        .card strong {{
            display: block;
            font-size: 1.6rem;
            margin-top: 8px;
            overflow-wrap: anywhere;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 20px;
        }}
        .panel {{
            padding: 20px;
            margin-top: 20px;
        }}
        .chart {{
            min-height: 320px;
            min-width: 0;
            overflow: hidden;
            width: 100%;
        }}
        .chart .js-plotly-plot,
        .chart .plot-container,
        .chart .svg-container {{
            max-width: 100%;
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
        .table-scroll {{
            overflow-x: auto;
            width: 100%;
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
            max-width: 100%;
            overflow-wrap: anywhere;
        }}
        @media (max-width: 860px) {{
            main {{
                padding: 24px 14px 36px;
            }}
            .grid {{
                grid-template-columns: 1fr;
            }}
            .panel {{
                padding: 16px;
            }}
            h1 {{
                font-size: 1.75rem;
            }}
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
                <div class="chart">{_figure_html(top_ip_figure, include_plotlyjs=True)}</div>
            </div>
            <div class="panel">
                <h2>Top endpointy</h2>
                <div class="chart">{_figure_html(endpoint_figure)}</div>
            </div>
            <div class="panel">
                <h2>Top błędy</h2>
                <div class="chart">{_figure_html(error_endpoint_figure)}</div>
            </div>
            <div class="panel">
                <h2>Status codes</h2>
                <div class="chart">{_figure_html(status_figure)}</div>
            </div>
        </section>
        <section class="panel">
            <h2>Trendy czasowe</h2>
            <p>Menu przy wykresie pozwala przefiltrowac widok dla wybranej klasy odpowiedzi HTTP.</p>
            <div class="chart">{_figure_html(trend_figure)}</div>
        </section>
        <section class="panel">
            <h2>Wykryte anomalie 5xx</h2>
            <div class="table-scroll">
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
            </div>
        </section>
        {benchmark_table}
    </main>
    <script>
        window.addEventListener("load", () => {{
            if (!window.Plotly) {{
                return;
            }}
            const resizeCharts = () => {{
                document.querySelectorAll(".js-plotly-plot").forEach((chart) => {{
                    window.Plotly.Plots.resize(chart);
                }});
            }};
            resizeCharts();
            window.addEventListener("resize", resizeCharts);
        }});
    </script>
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
