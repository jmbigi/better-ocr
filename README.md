# extract-charts — Extracción de datos en gráficos mixtos (CPU)

## Objetivo del proyecto

Crear un procedimiento documentado, reproducible y validado en ejecución real para **extraer datos tabulares de gráficos mixtos (barras + líneas)** usando el módulo **PP‑Chart2Table** de PaddleOCR sobre **CPU, Python 3.12 y sin GPU**, con especial énfasis en:

- Honestidad técnica: qué se ha probado, qué se ha contrastado con documentación oficial y qué **no** está garantizado (p. ej. series de líneas).
- Robustez operativa: gestión del error `OSError(122)` (`TMPDIR`), acceso defensivo a la API de PaddleX, limpieza del Markdown y conversión a CSV.
- Despliegue real: lecciones empíricas verificadas (una sola instancia por máquina, PaddleX no es thread-safe, patrón daemon persistente).

**Estado:** Verificado parcialmente (gráficos de barras: 6/6 valores exactos). Limitado estrictamente a las pruebas descritas.

## Archivos del proyecto

| Archivo | Descripción |
| :--- | :--- |
| `AGENTS.md` | **Reglas de IA del proyecto** (conjunto [better-ia](https://github.com/jmbigi/better-ia), CC BY-SA 4.0, con reglas específicas de este proyecto añadidas). opencode lo carga automáticamente en cada sesión. |
| `opencode.json` | **Guardarraíles deterministas** para opencode: `deny` de comandos destructivos (`rm -rf`, `git reset --hard`, etc.) y edición/lectura de `.env`. Se aplican en runtime sin depender del modelo. |
| `CHECKLIST.md` | Checklist de verificación pre-entrega (imprimible). |
| `README.md` | Este archivo: objetivos del proyecto y referencias a sus archivos. |
| `extractor_final.py` | Script principal validado: extrae la tabla del gráfico con `ChartParsing` y genera `datos_extraidos.csv` + `salida_bruta.json` para depuración. Expone `obtener_markdown()` y `markdown_a_df()` reutilizables. |
| `chart_server.py` | Daemon HTTP persistente (POST `/chart` → `markdown` + `csv`, GET `/health`). Carga el modelo una sola vez y **se cierra solo tras 1 hora sin peticiones de inferencia** (no queda procesos en memoria). Probado con modelo simulado. |
| `docs/GUIA_OCR_VISION.md` | **Documento general reutilizable** (se puede pegar en otros proyectos). Incluye Chart OCR, Text OCR y AI Vision con PaddleOCR. |
| `docs/LECCIONES-APRENDIDAS.md` | Memoria del proyecto: fallos, hallazgos y soluciones (referenciada desde `AGENTS.md`). |
| `ejemplos/grafico_demo.png` | Imagen de prueba oficial de PaddleOCR (gráfico de ejemplo, descargada del repositorio oficial). |
| `.gitignore` | Excluye `__pycache__/`, entornos virtuales y las salidas generadas por los scripts. |

## Uso rápido

```bash
# 1. Entorno (ver GUIA_OCR_VISION.md para detalles y advertencias)
pip install paddlepaddle==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install -U "paddleocr[doc-parser]"

# 2. Crítico: evitar OSError(122) si /tmp es pequeño
export TMPDIR=/var/tmp

# 3a. Extracción directa con la imagen de prueba
python extractor_final.py ejemplos/grafico_demo.png

# 3b. O servidor persistente (se cierra solo tras 1 h sin peticiones)
python chart_server.py --port 8080
curl -X POST http://127.0.0.1:8080/chart -H 'Content-Type: application/json' \
     -d '{"image": "ejemplos/grafico_demo.png"}'
```

## Salidas generadas por `extractor_final.py`

| Archivo | Contenido |
| :--- | :--- |
| `salida_bruta.json` | JSON crudo del modelo (estructura `res.result` o `result`) para depuración e inspección obligatoria. |
| `datos_extraidos.csv` | Tabla extraída limpia (UTF‑8 con BOM), lista para importar en Excel/hojas de cálculo. |

## Advertencias críticas (resumen)

- **Gráficos de líneas no garantizados:** en la prueba realizada el modelo no detectó la línea roja superpuesta. Validar antes de usar en producción.
- **RAM:** pico de carga de 4.8 GB; nunca ejecutar dos instancias de `ChartParsing` por máquina (OOM confirmado).
- **Concurrencia:** PaddleX no es thread-safe; serializar la inferencia (daemon persistente recomendado).

## Documentación oficial de referencia

- PaddleOCR — Módulo de gráficos (`chart_parsing`): `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/module_usage`
- PaddleOCR — Pipeline OCR general: `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/pipeline_usage/OCR.en.md`
- PaddleOCR — Pipeline de comprensión de documentos: `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/pipeline_usage/doc_understanding.md`
- PaddleOCR — Guía de instalación: `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/installation.md`
