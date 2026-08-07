# extract-charts — Extracción de datos en gráficos mixtos (CPU)

## Objetivo del proyecto

Crear un procedimiento documentado, reproducible y validado en ejecución real para **extraer datos tabulares de gráficos mixtos (barras + líneas)** usando el módulo **PP‑Chart2Table** de PaddleOCR sobre **CPU, Python 3.12 y sin GPU**, con especial énfasis en:

- Honestidad técnica: qué se ha probado, qué se ha contrastado con documentación oficial y qué **no** está garantizado (p. ej. series de líneas).
- Robustez operativa: gestión del error `OSError(122)` (`TMPDIR`), acceso defensivo a la API de PaddleX, limpieza del Markdown y conversión a CSV.
- Despliegue real: lecciones empíricas verificadas (una sola instancia por máquina, PaddleX no es thread-safe, patrón daemon persistente).

**Estado:** Verificado con ejecución real en dos entornos: Arch (Python 3.12, 6/6 valores exactos en gráfico de barras propio) y Kubuntu (Python 3.11.9, imagen oficial de PaddleOCR: 6/6 valores exactos; servidor validado con 74 s de inferencia en caliente y auto-cierre por inactividad verificado). Extendido con **cascada rápida multi-modo** (texto/gráficos/doc/objetos, ver `vision.py`): benchmark real en esta máquina (7 GB RAM, CPU): PP-OCRv6 lee 12/12 valores + 6/6 años de un gráfico de barras en ~56 s y ~1 GB; ChartParsing 18/18 celdas en ~333 s y 5.2 GB; RT-DETR-L detecta objetos reales en ~18 s y ~0.9 GB; PP-StructureV3 layout en ~323 s y 4.5 GB. Limitado estrictamente a las pruebas descritas: gráficos de líneas y otros tipos no garantizados; modos pinturas/objetos-avanzados requieren hardware mayor (ver abajo).

## Repositorio

- GitHub: <https://github.com/jmbigi/better-ocr>
- Codeberg: <https://codeberg.org/jmbigi/better-ocr>

## Archivos del proyecto

| Archivo | Descripción |
| :--- | :--- |
| `AGENTS.md` | **Reglas de IA del proyecto** (conjunto [better-ai](https://github.com/jmbigi/better-ai), CC BY-SA 4.0, con reglas específicas de este proyecto añadidas). opencode lo carga automáticamente en cada sesión. |
| `opencode.json` | **Guardarraíles deterministas** para opencode: `deny` de comandos destructivos (`rm -rf`, `git reset --hard`, etc.) y edición/lectura de `.env`. Se aplican en runtime sin depender del modelo. |
| `CHECKLIST.md` | Checklist de verificación pre-entrega (imprimible). |
| `README.md` | Este archivo: objetivos del proyecto y referencias a sus archivos. |
| `extractor_final.py` | Script principal validado: extrae la tabla del gráfico con `ChartParsing` y genera `datos_extraidos.csv` + `salida_bruta.json` para depuración. Expone `obtener_markdown()`, `markdown_a_df()` y `validar_imagen()` (valida existencia + firma mágica antes de cargar el modelo) reutilizables. Por defecto usa la imagen demo (`ejemplos/grafico_demo.png`). |
| `ocr_rapido.py` | **Ruta rápida en cascada:** PP-OCRv6 + emparejamiento geométrico por bboxes (año↔valor) con gate de plausibilidad. Si el gate falla, el llamador cae al VLM `ChartParsing` (lento pero exacto). Validado 12/12 valores en 2 charts en ~1 GB de RAM. |
| `vision.py` | **CLI multi-modo de visión:** `auto` (clasifica y rutea), `texto` (PP-OCRv6), `graficos` (cascada), `doc` (PP-StructureV3 layout-only), `objetos` (RT-DETR-L) y `humano` (filtro clase person). Salidas json/csv/md. |
| `captcha_ia.py` | **Resolución de retos reCAPTCHA v2 con el stack local (RT-DETR + VLM):** piezas puras testeables (parser de instrucción, geometría de cuadrícula, decisor por celda) + demo sintética determinista `--local` (3×3/4×4, sin navegador). El pipeline `resolver()` recibe un detector inyectable. |
| `captcha_web.py` | **Orquestador real con Playwright:** checkbox → reto en bframe → instrucción del DOM → captura → detección RT-DETR por celda (subproceso por lotes del venv) → clics JS en tiles → VERIFY/SKIP → veredicto por el checkbox ancla. Reintento tras re-render. `python3 captcha_web.py --url <pagina> [--headed] [--salida dir]`. Requiere playwright en el python del sistema (los navegadores ya están descargados en esta máquina). |
| `chart_server.py` | Daemon HTTP persistente: POST `/chart` (→ `markdown` + `csv`) y POST `/vision` (multi-modo), GET `/health`. Carga el modelo una sola vez y **se cierra solo tras 1 hora sin peticiones de inferencia** (no queda procesos en memoria). |
| `requirements.txt` | Dependencias del proyecto (paddlepaddle 3.3.1, paddleocr[doc-parser] 3.7.0, pandas). |
| `tests/test_extraccion.py` | Pruebas unitarias (stdlib + pandas, sin paddleocr): filtrado de separadores, conversión a DataFrame, acceso a la API y servidor HTTP con modelo simulado. |
| `tests/test_ocr_rapido.py` | Pruebas del emparejamiento geométrico y la fusión de pasadas OCR (modelo simulado, sin paddle). |
| `tests/test_vision.py` | Pruebas del clasificador auto-modo y formatos de salida. |
| `scripts/verificar-proyecto.sh` | Verificación local completa (open source, sin cuentas): sintaxis, tests, reglas P0/P1, config, seguridad y repo. `bash scripts/verificar-proyecto.sh`. |
| `scripts/benchmark_ocr.py` | Benchmark de motores (ChartParsing / PP-StructureV3 / PP-OCRv6 / PP-OCRv5) sobre la misma imagen: tiempo de carga, inferencia, RAM y puntuación contra la referencia oficial. |
| `scripts/generar_charts.py` | Genera gráficos de prueba con datos CONOCIDOS (matplotlib, seaborn, plotly) + CSV de referencia como ground truth. |
| `scripts/validar_cascada.py` | Valida la ruta rápida (ocr_rapido) contra los CSV de referencia de `ejemplos/test_charts/`. |
| `scripts/bateria_360.py` | **Batería 360°:** compara VLM locales (docbee / ollama) en 6 dimensiones (QA UI, interpretación, valores, objetos, descripción, documento) con las mismas imágenes y prompts; scoring automático + rúbrica humana. |
| `scripts/hooks/pre-commit` | Hook git local que ejecuta la verificación antes de cada commit (instalación: `cp scripts/hooks/pre-commit .git/hooks/pre-commit`). |
| `docs/GUIA_OCR_VISION.md` | **Documento general reutilizable** (se puede pegar en otros proyectos). Incluye Chart OCR, Text OCR y AI Vision con PaddleOCR. |
| `docs/LECCIONES-APRENDIDAS.md` | Memoria del proyecto: fallos, hallazgos y soluciones (referenciada desde `AGENTS.md`). |
| `ejemplos/grafico_demo.png` | Imagen de prueba oficial de PaddleOCR (gráfico de ejemplo, descargada del repositorio oficial). |
| `.gitignore` | Excluye `__pycache__/`, entornos virtuales y las salidas generadas por los scripts. |

## Uso rápido

```bash
# 1. Entorno (ver GUIA_OCR_VISION.md para detalles y advertencias)
pip install paddlepaddle==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install -r requirements.txt   # el resto (paddleocr[doc-parser], pandas) desde PyPI

# 2. Crítico: evitar OSError(122) si /tmp es pequeño
export TMPDIR=/var/tmp

# 2b. Solo en sistemas con el error "libmklml_intel.so: cannot open shared object file" (verificado en Kubuntu)
export LD_LIBRARY_PATH="$PWD/.venv/lib/python3.11/site-packages/paddle/libs:$LD_LIBRARY_PATH"

# 3a. Extracción directa con la imagen de prueba
python extractor_final.py ejemplos/grafico_demo.png

# 3b. O servidor persistente (se cierra solo tras 1 h sin peticiones)
python chart_server.py --port 8080
curl -X POST http://127.0.0.1:8080/chart -H 'Content-Type: application/json' \
     -d '{"image": "ejemplos/grafico_demo.png"}'

# 3c. Visión multi-modo (CLI)
/venv/bin/python vision.py ejemplos/test_charts/bar_2series.png --modo graficos --salida csv
/venv/bin/python vision.py ejemplos/test_charts/texto_boarding.png --modo texto
/venv/bin/python vision.py foto.png --modo objetos          # RT-DETR: frutas/personas...
/venv/bin/python vision.py imagen.png                       # auto: clasifica y rutea

# 3d. Visión por servidor
curl -X POST http://127.0.0.1:8080/vision -H 'Content-Type: application/json' \
     -d '{"image": "foto.png", "modo": "objetos"}'

# 3e. Captcha: demo sintética determinista (sin navegador, veredicto OK)
python3 captcha_ia.py --local            # 3x3; usa --n 4 para 4x4

# 3f. Captcha: modo REAL con Playwright (python del sistema)
python3 captcha_web.py --url https://pagina.con.recaptcha --salida /var/tmp/reto
```

## Perfiles por máquina (visión)

Cada equipo puede limitar los modos según su RAM, sin borrar código:

- `BETTER_OCR_PERFIL=completo` (default): sin límite, todos los modos.
- `BETTER_OCR_PERFIL=ligero`: máx. ~3500 MB por modo — permite `texto`, `graficos` (ruta rápida) y `objetos`; bloquea `doc` y el fallback VLM con un mensaje claro antes de cargar el modelo.
- Ajuste fino opcional: archivo `better_ocr.json` en la raíz del proyecto, p. ej. `{"perfil": "completo", "ram_max_mb": 6000}`.

RAM medida por modo (MB): texto 1000, graficos rápido 1000, graficos VLM 5200, doc 4500, objetos 900.

## Pruebas

```bash
# Sintaxis (sin dependencias)
python3 -m py_compile extractor_final.py chart_server.py ocr_rapido.py vision.py captcha_ia.py captcha_web.py

# Pruebas unitarias (solo stdlib + pandas; paddleocr se simula)
python3 -m unittest discover -s tests -v
```

*Verificación local (open source, sin cuentas ni servicios externos): `bash scripts/verificar-proyecto.sh` ejecuta sintaxis + tests + checks de reglas, config y seguridad, y el hook `pre-commit` la ejecuta automáticamente antes de cada commit (instalación: `cp scripts/hooks/pre-commit .git/hooks/pre-commit`).*

## Salidas generadas por `extractor_final.py`

| Archivo | Contenido |
| :--- | :--- |
| `salida_bruta.json` | JSON crudo del modelo (estructura `res.result` o `result`) para depuración e inspección obligatoria. |
| `datos_extraidos.csv` | Tabla extraída limpia (UTF‑8 con BOM), lista para importar en Excel/hojas de cálculo. |

## Advertencias críticas (resumen)

- **Gráficos de líneas no garantizados:** en la prueba realizada el modelo no detectó la línea roja superpuesta. Validar antes de usar en producción.
- **RAM:** pico de carga de 4.8 GB (ChartParsing) y 6.4 GB (PP-StructureV3 con chart); esta máquina tiene 7 GB totales: no ejecutar nunca dos modelos VLM a la vez (OOM confirmado históricamente). PP-OCRv6 y RT-DETR son ligeros (~1 GB).
- **Concurrencia:** PaddleX no es thread-safe; serializar la inferencia (daemon persistente recomendado).
- **Bug paddlepaddle 3.3.1 (PIR + oneDNN):** PP-OCRv6, PP-StructureV3 y RT-DETR fallan con `ConvertPirAttribute2RuntimeAttribute` si usan mkldnn. Workarounds aplicados en el código: `enable_mkldnn=False` (PaddleOCR/PPStructureV3) y `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0` (RT-DETR). Detalle y fuentes: issue PaddlePaddle/PaddleOCR#18162.
- **Cascada de gráficos:** la ruta rápida (PP-OCRv6 + geometría) solo es fiable con categorías tipo año consecutivas y etiquetas de valor legibles; el gate la rechaza y cae al VLM ChartParsing en cualquier otra situación (líneas, pastel, scatter, etiquetas solapadas).
- **Límites de "visión 360°":** pinturas/dibujos/descripción de escenas requieren un VLM de captioning (PaddleOCR-VL 0.9B ≈ 4.7-9 GB) — fuera del alcance de esta máquina de 7 GB. Objetos reales y personas: RT-DETR-L (validado, ~0.9 GB).
- **VLM locales en CPU (batería 360°):** gemma3:4b es el punto dulce (12/12 valores en ~150 s, RAM segura); qwen2.5vl:7b mejora la descripción de escenas modestamente pero es 2.7× más lento y puede dejar la RAM del host crítica (descargar con `keep_alive=0` tras cada uso). Los modelos comerciales de referencia superan a ambos locales en granularidad descriptiva. **Ollama no es servicio permanente**: el harness lo arranca bajo demanda en la primera ejecución y se detiene con `pkill -f "ollama serve"`.
- **docbee (PP-DocBee-2B) en la batería 360°: VALIDADO en GPU (RTX 3070 8 GB)** — 8/8 tests del harness (`scripts/bateria_360.py --motor docbee --device cuda`); gana ui_qa (4/4 vs 2/4) y personas (1/2 vs 0/2) frente a gemma3:4b, y pie 5/5. Corre con `max_pixels` reducido a 0.5M px para caber en 8 GB (OOM a resolución nativa), por lo que en lectura de valores queda por debajo de gemma (3/12 vs 7/12). Entorno GPU: `paddlepaddle-gpu==3.3.1` (índice oficial cu126) + `LD_LIBRARY_PATH` con los `nvidia/*/lib` del venv por delante. Detalle: `docs/PRUEBAS.md` §4.1 y lección 17.
- **Imágenes de prueba externas:** contenido con derechos de terceros → solo en directorios temporales, nunca en el repo.

## Documentación oficial de referencia

- PaddleOCR — Módulo de gráficos (`chart_parsing`): `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/module_usage`
- PaddleOCR — Pipeline OCR general: `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/pipeline_usage/OCR.en.md`
- PaddleOCR — Pipeline de comprensión de documentos: `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/pipeline_usage/doc_understanding.md`
- PaddleOCR — Guía de instalación: `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/installation.md`
