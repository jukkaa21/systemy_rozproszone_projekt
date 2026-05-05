from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .pipeline import execute_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rozproszony system ETL i analizy logow serwerowych."
    )
    parser.add_argument(
        "--input-path",
        action="append",
        required=True,
        help="Sciezka do pliku, katalogu albo wzorca glob z logami. Parametr mozna podac wiele razy.",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Katalog, do ktorego zostana zapisane raporty.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 1),
        help="Liczba workerow przetwarzajacych chunky logow.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=10000,
        help="Liczba linii w jednym fragmencie przetwarzanym przez workera.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Liczba rekordow prezentowanych w agregacjach typu top N.",
    )
    parser.add_argument(
        "--anomaly-window",
        type=int,
        default=5,
        help="Liczba poprzednich bucketow czasowych wykorzystywanych jako baseline dla detekcji anomalii.",
    )
    parser.add_argument(
        "--anomaly-sigma",
        type=float,
        default=2.0,
        help="Mnoznik odchylenia standardowego dla detekcji anomalii 5xx.",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Uruchom dodatkowy benchmark porownujacy 1 workera i zadana liczbe workerow.",
    )
    parser.add_argument(
        "--benchmark-workers",
        type=int,
        nargs="*",
        help="Lista liczby workerow do benchmarku. Domyslnie: 1 oraz wartosc --workers.",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Nie generuj pliku HTML z dashboardem.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    benchmark_workers = None
    if args.benchmark:
        benchmark_workers = args.benchmark_workers or [1, args.workers]

    summary = execute_pipeline(
        input_paths=args.input_path,
        output_dir=args.output_dir,
        workers=args.workers,
        chunk_size=args.chunk_size,
        top_n=args.top_n,
        anomaly_window=args.anomaly_window,
        anomaly_sigma=args.anomaly_sigma,
        benchmark_workers=benchmark_workers,
        export_dashboard=not args.no_dashboard,
    )

    print(json.dumps(summary["metrics"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
