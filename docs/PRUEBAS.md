# PRUEBAS — Evidencia de verificación (better-ocr)

> Referenciado desde `AGENTS.md`. Resumen de las pruebas REALES ejecutadas y su
> resultado. Sin datos personales (P0.9). Detalle narrativo en
> `docs/LECCIONES-APRENDIDAS.md`.

## 1. Suite unitaria

- `python3 -m unittest discover -s tests` → **66/66 OK** (solo stdlib + pandas;
  paddleocr y PaddleX simulados en los tests de servidor).
- Sintaxis: `python3 -m py_compile extractor_final.py chart_server.py ocr_rapido.py vision.py` → OK.
- Verificador local: `bash scripts/verificar-proyecto.sh` → **19 OK, 0 FALLOS**.

## 2. Benchmark de motores (imagen oficial chart_parsing_02, CPU, 1 ejecución por motor)

| Motor | Carga | Inferencia | RAM pico | Exactitud |
|---|---|---|---|---|
| ChartParsing (VLM) | 147 s | 179 s | 5.2 GB | 18/18 celdas |
| PP-StructureV3 (con chart) | 73 s | 266 s | 6.4 GB | 12/18 |
| PP-OCRv6 (texto) | 16 s | 38 s | 1.0 GB | 12/12 valores + 6/6 años |
| PP-OCRv5 (texto) | 7 s | 51 s | 2.7 GB | 12/12 + 6/6 |

Informe completo: `/var/tmp/better-ocr-bench/reporte.json`.

## 3. Cascada de gráficos (set de 8 charts, ground truth propio)

| Chart | Fast path (PP-OCRv6) | Fallback ChartParsing |
|---|---|---|
| bar_2series (mpl) | 6/6 + 12/12 (~70 s, 1 GB) | — |
| plotly_barra | 6/6 + 12/12 (~58 s) | — |
| grafico_demo (oficial) | rechaza (seguro) | 18/18 (~333 s, 5.2 GB) |
| pie_5 | rechaza | 5/5 + 5/5 (~209 s) |
| line_3series | rechaza | 3/3 + 24/24 (~275 s) |
| seaborn_agrupado | rechaza | 4/4 + 8/8 (~248 s) |
| bar_apilada | rechaza | 3/3 + 12/12 (~236 s) |
| scatter_valores | rechaza | 0/12 — **VLM alucina tabla** (no soportado) |

## 4. Batería 360° VLM en CPU (misma batería, temperatura 0)

| Test | qwen2.5vl:3b | gemma3:4b | qwen2.5vl:7b |
|---|---|---|---|
| valores (demo) | 12/12 (224 s) | 12/12 (150 s) | 12/12 (399 s) |
| valores (pie) | — | 5/5 (130 s) | 5/5 (585 s) |
| objetos (frutas) | 3/4 | 3/4 | 3/4 |
| documento | — | 1/2 (371 s) | 1/2 (324 s) |

Descripción de imágenes reales (n=2): qwen2.5vl:7b > gemma3:4b (contexto de
escena y especificidad de color), ambos < modelo comercial de referencia.
**Pendiente:** docbee (PP-DocBee-2B) en equipo con GPU.

## 5. Servidor HTTP (E2E real)

- `POST /chart` demo → 200, 6 filas, markdown + CSV correctos (inferencia real ~180 s).
- `POST /vision` (modo texto) → 35 líneas de una tarjeta de embarque en 61 s.
- Cierre limpio por SIGTERM; auto-cierre por inactividad verificado (lección 7).

## 6. Alternativa Rust (deepseek-ocr.rs, q4k)

- 12/12 + 6/6 exacto, carga 8 s, inferencia ~1200 s (0.4-1.4 tok/s en CPU).
- Build: fallo por tmpfs (Bus error) resuelto con CARGO_TARGET_DIR en disco.
- RAM no medida (monitor del proceso no fiable con `&`); sin OOM con swap.

## Limitaciones verificadas

- Scatter: no soportado por ChartParsing (alucina).
- Captions/pinturas: requieren VLM ≥ 0.9B (~9 GB) → OOM en equipos de 7 GB.
- qwen2.5vl:7b en CPU: deja la RAM del host crítica; descargar con `keep_alive=0`.
- Portabilidad: solo probado en Linux/x86 (CPU 7.7 GB y 16 GB con GPU en lección 10).
