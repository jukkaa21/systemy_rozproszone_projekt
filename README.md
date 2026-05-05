# Rozproszony system przetwarzania logów

Początkowa wersja projektu do przedmiotu "systemy rozproszone". System realizuje pipeline ETL dla wielu plików logów, uruchamia przetwarzanie równoległe z kontrolowaną liczbą workerów, generuje agregacje, wykrywa proste anomalie oraz przygotowuje dashboard HTML i eksport wyników do `CSV` oraz `JSON`.

## Co robi system

Pipeline składa się z etapów:

1. `Wczytywanie` wielu plików lub katalogów z logami.
2. `Parsowanie` każdej linii do rekordu z polami.
3. `Walidacja` wpisów i odrzucanie błędnych rekordów.
4. `Normalizacja` metod HTTP, statusów i znaczników czasu do `UTC`.
5. `Map` - każdy worker przetwarza własny chunk logów.
6. `Reduce` - wyniki częściowe są scalane do wspólnego raportu.

Generowane analizy:

- top IP
- top endpointy
- top endpointy błędów
- rozkład kodów statusu
- trendy czasowe per godzina
- detekcja anomalii 5xx na oknach czasowych
- benchmark `1 worker vs N workerów`

## Struktura projektu

`TestFileGenerator.py`
Generator syntetycznych logów w formacie zbliżonym do Common Log Format.

`main.py`
Punkt wejścia aplikacji CLI.

`src/distributed_log_system/`
Moduły ETL, map/reduce, raportowania i dashboardu.

## Wymagane biblioteki

- `plotly` - generowanie dashboardu HTML
- `Faker` - generator danych testowych

## Uruchamianie lokalne

### 1. Instalacja zależności

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Wygenerowanie logów testowych

```bash
python TestFileGenerator.py
```

Generator utworzy plik `logfiles.log` w katalogu projektu. To jest bazowy plik wejściowy, na którym pracuje system.

### 3. Uruchomienie pipeline'u

```bash
python main.py \
  --input-path logfiles.log \
  --output-dir output/run-local \
  --workers 4 \
  --chunk-size 10000 \
  --benchmark
```

System obsługuje także wiele wejść:

```bash
python main.py \
  --input-path data/logs-a \
  --input-path data/logs-b \
  --input-path "data/*.log" \
  --output-dir output/run-many-files \
  --workers 8
```

## Wyniki

Po uruchomieniu system zapisuje do katalogu wyjściowego:

- `summary.json` - pełne podsumowanie wykonania
- `top_ips.csv`
- `top_endpoints.csv`
- `top_error_endpoints.csv`
- `status_codes.csv`
- `methods.csv`
- `hourly_trends.csv`
- `anomalies.csv`
- `benchmark.csv` - jeśli włączono benchmark
- `dashboard.html` - statyczny dashboard do prezentacji

Dashboard można otworzyć w przeglądarce bez dodatkowego serwera.

## Najważniejsze parametry

- `--workers` - liczba workerów przetwarzających logi
- `--chunk-size` - liczba linii w jednym chunku
- `--top-n` - ile rekordów pokazywać w agregacjach typu top
- `--benchmark` - włącza porównanie `1 worker vs N workerów`
- `--benchmark-workers 1 2 4 8` - ręczna lista workerów do benchmarku
- `--no-dashboard` - pomija generowanie dashboardu HTML

## Uruchamianie w Dockerze

### 1. Zbudowanie obrazu

```bash
docker build -t distributed-log-system .
```

### 2. Uruchomienie kontenera

Zakładamy, że logi są w katalogu `./data` na hoście:

```bash
docker run --rm \
  -v "$(pwd)/data:/data" \
  -v "$(pwd)/output:/output" \
  distributed-log-system \
  --input-path /data \
  --output-dir /output/docker-run \
  --workers 4 \
  --benchmark
```

### 3. Opcjonalnie: wygenerowanie danych bez lokalnego `pip install`

Jeżeli chcesz korzystać wyłącznie z Dockera, generator też możesz uruchomić w kontenerze:

```bash
docker run --rm \
  -v "$(pwd):/workspace" \
  -w /workspace \
  --entrypoint python \
  distributed-log-system \
  /app/TestFileGenerator.py
```

To polecenie utworzy `logfiles.log` w katalogu projektu na hoście.

Jeżeli chcesz przetwarzać logi wygenerowane przez `TestFileGenerator.py`, możesz zamontować cały katalog projektu:

```bash
docker run --rm \
  -v "$(pwd):/workspace" \
  distributed-log-system \
  --input-path /workspace/logfiles.log \
  --output-dir /workspace/output/docker-run \
  --workers 4
```

Jeżeli do prezentacji chcesz pokazać wiele plików wejściowych, możesz wskazać katalog albo glob z wieloma plikami `.log`. System działa wtedy tak samo, bo agreguje wyniki ze wszystkich wejść.

## Jak pokazać skalowanie

Do prezentacji można użyć na przykład:

```bash
python main.py \
  --input-path logfiles.log \
  --output-dir output/benchmark \
  --workers 8 \
  --benchmark \
  --benchmark-workers 1 2 4 8
```

Wynik pojawi się w `benchmark.csv` oraz na dashboardzie.
