# extract-charts — Extracción de datos en gráficos mixtos (CPU)

## Objetivo del proyecto

Crear un procedimiento documentado, reproducible y validado en ejecución real para **extraer datos tabulares de gráficos mixtos (barras + líneas)** usando el módulo **PP‑Chart2Table** de PaddleOCR sobre **CPU, Python 3.12 y sin GPU**, con especial énfasis en:

- Honestidad técnica: qué se ha probado, qué se ha contrastado con documentación oficial y qué **no** está garantizado (p. ej. series de líneas).
- Robustez operativa: gestión del error `OSError(122)` (`TMPDIR`), acceso defensivo a la API de PaddleX, limpieza del Markdown y conversión a CSV.
- Despliegue real: lecciones empíricas verificadas (una sola instancia por máquina, PaddleX no es thread-safe, patrón daemon persistente).

**Estado:** Verificado con ejecución real en dos entornos: Arch (Python 3.12, 6/6 valores exactos en gráfico de barras propio) y Kubuntu (Python 3.11.9, imagen oficial de PaddleOCR: 6/6 valores exactos; servidor validado con 74 s de inferencia en caliente y auto-cierre por inactividad verificado). Extendido con **cascada rápida multi-modo** (texto/gráficos/doc/objetos, ver `vision.py`): benchmark real en esta máquina (7 GB RAM, CPU): PP-OCRv6 lee 12/12 valores + 6/6 años de un gráfico de barras en ~56 s y ~1 GB; ChartParsing 18/18 celdas en ~333 s y 5.2 GB; RT-DETR-L detecta objetos reales en ~18 s y ~0.9 GB; PP-StructureV3 layout en ~323 s y 4.5 GB. **Servicio captcha (reCAPTCHA v2): validado en vivo contra la demo oficial** — campaña de 69 ejecuciones reales (2026-08-07, 30 ok = 43% agregado); stack final medido: detección **imagen-completa** (default, mejor recall que por celdas) + RT-DETR-L (default; RT-DETR-H con `--modelo-detector`) + confirmación VLM opt-in (`--vlm-fallback docbee|ollama`) + umbral adaptativo (`--umbral-objetivo`); corpus de 58 fallos re-evaluable (`--archivo-fallos`, `scripts/replay_fallos.py`); ver `docs/PRUEBAS.md` §7 y lecciones 20-27. **Revisión de formato y presentación (`revision.py`): validado el 2026-08-12** — suite completa de 340 tests (incluye los 16 checks deterministas xlsx + 8 docx + 5 pdf + comparación de versiones + rúbrica VLM), planillas sintéticas de referencia (`scripts/generar_planillas.py`: correcta 0 hallazgos, con_fallos detecta 14 checks, docx correcto 0 y con_fallos 8 warnings, pdf limpio 0) y ejecución real sobre planillas del usuario (copia en `/var/tmp`, original intacto): presupuesto 221 warnings (219 bordes + filtro + ancho), proveedores 20 warnings + 6 info (textos desbordados incluidos); **Visión IA 360° en vivo con docbee GPU** (rúbrica 6 dimensiones en ~28-34 s/página, notas parseadas + no_conformes) sobre xlsx y PDF (render nativo). **RNS offline (`rns.py`): validado el 2026-08-14** — búsqueda de personas jurídicas SIN red: descarga única del padrón oficial (Ley 26.047, datos.jus.gob.ar), indexación SQLite FTS5 y consulta local; integrado en `empresas.py` (paso 0.5; 12 tests propios + 3 de integración + E2E real con 150.624 asociaciones). **Wayback Machine en `empresas.py --wayback` (validado el 2026-08-14):** historial del sitio vía CDX API (sin navegador ni bloqueos; 112 capturas 2015-2025 para asistenciamisabuelos.com) y recuperación de capturas antiguas que conservan correos/razón social/CUITs que la web actual quitó (contacto.html 2015: 2 correos + dominio desconocido; single.html 2019: razón social). Limitado estrictamente a las pruebas descritas: gráficos de líneas y otros tipos no garantizados; modos pinturas/objetos-avanzados requieren hardware mayor (ver abajo).

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
| `captcha_web.py` | **Orquestador real con Playwright:** checkbox → reto en bframe → instrucción del DOM (fallback OCR PP-OCRv6) → captura → detección RT-DETR por celda (subproceso por lotes del venv, una carga) → confirmación VLM de dos etapas opcional (`--vlm-fallback`, ollama binario por tile) → clics JS en tiles → VERIFY/SKIP → veredicto por el checkbox ancla. Reintento tras re-render; umbral adaptativo por tamaño (0.45 en 3×3, 0.30 en 4×4, `--umbral-objetivo`); `--salida` guarda capturas + `intentos.json` (decisión y scores por celda, fallos analizables). Pasada offline sin navegador: `--offline IMAGEN --n 3 --instruccion "..."`. Requiere playwright en el python del sistema. |
| `revision.py` | **Revisión de formato y presentación de planillas y documentos:** dos capas complementarias. (1) Análisis determinista — **xlsx/xlsm** con openpyxl (16 checks configurables por JSON: encabezados, bordes, alineación, anchos, formato numérico, filtros, celdas vacías/mezcladas, filas y columnas ocultas, errores de fórmula, duplicados de encabezado, texto desbordado, estilos inconsistentes, islas de datos, protección) + comparación entre versiones (`--comparar`: estructura, estilos y valores); **ods** normalizado vía LibreOffice con verificación de integridad de la conversión; **docx** con python-docx (8 checks: estilos de título vs negrita manual, fuentes, márgenes, numeración manual, párrafos vacíos, tablas sin bordes, encabezado/pie, imágenes); **pdf** con pypdfium2 (5 checks: páginas vacías/escasas, sin capa de texto, rotación, tamaños). (2) **Visión IA 360°** opt-in (`--vision docbee|ollama`) para cualquier formato: render a imagen (PDF nativo con pypdfium2; el resto LibreOffice → PDF → PNG) y rúbrica de 6 dimensiones de diseño/presentación con notas /10 parseadas y conteo de no conformes. |
| `buscador.py` | **Buscador avanzado multi-motor (Playwright):** busca una consulta en Google/Bing/Brave/DDG/Mojeek/Ecosia/Startpage, detecta bloqueos y captchas por motor (verificados con HTML real de esta IP: Google "sorry" = reCAPTCHA v2, Brave slider, DDG challenge, Ecosia turnstile, Startpage suspendida, Mojeek 403) y con `--captcha` resuelve el reCAPTCHA v2 **en la misma sesión** reutilizando el stack de `captcha_web` (RT-DETR + clics, reto 3×3 verificado en vivo; 3 intentos con cooldown + reload de última oportunidad cuando el reto falla). Recetas de dominio: `--recetas cuit` busca razón social/CUIT en CuitOnline (`search/{q}`, parser verificado) y Dateas (hoy 404, reportado). JSON unificado por motor + síntesis con ranking multi-motor (bonus a cuitonline/dateas/afip). Sin dependencias nuevas: Playwright del python del sistema. |
| `empresas.py` | **CLI de búsqueda de empresas:** verifica una empresa con pasos independientes — CuitOnline con variantes automáticas del nombre (limpia sufijos legales SRL/SA/SAS/SH) + Dateas; **RNS offline** (paso 0.5, `--sin-rns`/`--rns-db`); web oficial con `--sitio` (vigencia, CUITs, razón social del pie "©", **correos** del home + páginas de contacto con desofuscación `[at]`/`[dot]`, WhatsApp y redes, con reintentos de red); **RDAP NIC.AR del dominio con titular del DNS** (tipo de titular y org solo si jurídica — nunca contactos, P0.9) y dominios candidatos derivados del nombre cuando no hay `--sitio`; **Wayback Machine** (`--wayback`): historial del sitio vía CDX API sin bloqueos (señales siempre; recupera capturas antiguas que conservan correos/CUITs/razón social que la web actual quitó); dorks de correos (`"@dominio"`), de recomendadores (opiniones/mapas) y de juicios (con limitación honesta: los expedientes laborales argentinos no son buscables públicamente por razón social). Informe JSON (`sintesis` con CUITs, correos, canales, señales y limitaciones) + resumen en consola. Reutiliza el motor de `buscador.py` (importado, no duplicado). |
| `rns.py` | **Registro Nacional de Sociedades OFFLINE:** descarga la base oficial de personas jurídicas argentinas (sociedades + asociaciones sin fines de lucro; Ley 26.047, datos.jus.gob.ar — URLs verificadas en vivo 2026-08-14), la indexa en SQLite FTS5 y busca por razón social SIN red ni captchas. Comandos: `descargar` (default sociedades+asociaciones 2026; `--todos` 2019-2026 ~2.5 GB), `indexar`, `buscar "razón" [--json]`, `auto`. El dataset trae el CUIT como 11 dígitos sin guiones (se normaliza a XX-XXXXXXXX-X) y filas duplicadas por actividad en asociaciones (se deduplican). Solo stdlib. |
| `judiciales.py` | **Buscador de demandas judiciales (empresas y personas):** Boletín Oficial (edictos de quiebras/concursos/remates/sociedades; flujo real de navegación con clic en la búsqueda rápida; parser verificado contra HTML real 2026-08-13: `div.linea-aviso` + `h5.seccion-rubro`, CUITs y señales de litigio; filtro de interés con frase completa o 2+ palabras, sin falsos positivos de palabras comunes) + dorks web de litigio (juicio/fallo/demanda/sentencia/expediente/CNAT) con `buscador.py`. Límites verificados: CNAT/SECLO no buscables por nombre, PJN SPA sin API, IUS caído, CuitOnline sin sección juicios. La ausencia NO prueba nada (chequeo real: antecedentes judiciales). |
| `analizar_cuit.py` | **Analizador de CUIT con IA de algoritmo:** reglas deterministas con confianza y explicación — persona física (prefijos 20/23/24/25/26/27) vs jurídica (30/33/34); década estimada de emisión del DNI (nunca la edad, advertencia explícita); tipo de empresa por razón social; ficha de CuitOnline (Empleador Sí/No, impuestos activos → condición Monotributo/Responsable Inscripto, provincia/localidad; el sexo no se extrae, P0.9); titular DNS de dominios candidatos; judiciales y recomendadores (dorks). Resumen VLM opt-in (`--vision docbee|ollama`) que solo redacta lo ya verificado. |
| `buscador_empresas.py` | **Buscador inteligente por campos:** búsquedas completas por CUIT o nombre (`--lista` para varias) que generan la **TABLA-EMPRESAS-CUIT-TIPO.md** en el formato estándar del dominio (Empresa | CUIT | Razón social | Tipo | Condición | Empleadora | Fuente) con la regla **TODO REAL** ("No consta — pedir por escrito" para lo no verificado; "Empleadora" solo marca lo confirmado en CuitOnline; los titulares personas físicas no se publican, P0.9). |
| `chart_server.py` | Daemon HTTP persistente: POST `/chart` (→ `markdown` + `csv`) y POST `/vision` (multi-modo), GET `/health`. Carga el modelo una sola vez y **se cierra solo tras 1 hora sin peticiones de inferencia** (no queda procesos en memoria). |
| `requirements.txt` | Dependencias del proyecto (paddlepaddle 3.3.1, paddleocr[doc-parser] 3.7.0, pandas). |
| `tests/test_extraccion.py` | Pruebas unitarias (stdlib + pandas, sin paddleocr): filtrado de separadores, conversión a DataFrame, acceso a la API y servidor HTTP con modelo simulado. |
| `tests/test_ocr_rapido.py` | Pruebas del emparejamiento geométrico y la fusión de pasadas OCR (modelo simulado, sin paddle). |
| `tests/test_vision.py` | Pruebas del clasificador auto-modo y formatos de salida. |
| `tests/test_revision.py` | Pruebas de `revision.py` (41 tests): checks de formato puros sobre libros en memoria, reglas JSON, comparación de versiones, parser de la rúbrica VLM y gestión de errores (LibreOffice y motores simulados). |
| `scripts/verificar-proyecto.sh` | Verificación local completa (open source, sin cuentas): sintaxis, tests, reglas P0/P1, config, seguridad y repo. `bash scripts/verificar-proyecto.sh`. |
| `scripts/benchmark_ocr.py` | Benchmark de motores (ChartParsing / PP-StructureV3 / PP-OCRv6 / PP-OCRv5) sobre la misma imagen: tiempo de carga, inferencia, RAM y puntuación contra la referencia oficial. |
| `scripts/generar_charts.py` | Genera gráficos de prueba con datos CONOCIDOS (matplotlib, seaborn, plotly) + CSV de referencia como ground truth. |
| `scripts/generar_planillas.py` | Genera documentos de prueba sintéticos para el módulo de revisión: `correcta.xlsx` (0 hallazgos), `con_fallos.xlsx` (viola 14 checks), `v1/v2.xlsx` (para `--comparar`), `correcta.ods` (con estilos, vía soffice), `documento_correcto.docx` / `documento_con_fallos.docx` (estilos vs formato manual) y `documento.pdf`. |
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

# 3g. Captcha: fallback VLM para clases no-COCO (crosswalks, stairs...)
python3 captcha_web.py --url https://pagina.con.recaptcha --vlm-fallback

# 3h. Revision de formato/presentacion (analisis determinista)
python3 revision.py planilla.xlsx                  # xlsx/xlsm/ods/docx/pdf
python3 revision.py planilla.xlsx --reglas reglas.json      # reglas propias
python3 revision.py v1.xlsx --comparar v2.xlsx              # diff de versiones
python3 revision.py documento.docx --salida md              # resumen legible

# 3i. Revision con Vision IA 360 (diseno/presentacion por VLM local:
#     render -> PNG -> rubrica de 6 dimensiones)
.venv/bin/python revision.py planilla.xlsx --vision docbee  # GPU (o --device cpu)
python3 revision.py documento.pdf --vision ollama           # gemma3:4b local

# 3j. Revision por servidor sin cargar el modelo VLM (solo /health y /revision)
python3 chart_server.py --port 8080 --sin-modelo
curl -X POST http://127.0.0.1:8080/revision -H 'Content-Type: application/json' \
     -d '{"archivo": "planilla.xlsx"}'

# 3k. Buscador avanzado multi-motor (Playwright del python del sistema)
python3 buscador.py "Permanencia Salud Srl"                  # todos los motores
python3 buscador.py "YPF" --motores bing --recetas cuit      # motor + receta CUIT
python3 buscador.py "empresa x" --captcha --salida /var/tmp/busq   # resuelve reCAPTCHA

# 3l. Busqueda de EMPRESAS: CUIT, razon social, CORREOS, canales, juicios
python3 empresas.py "Permanencia Salud Srl" --sitio permanencia.com.ar --salida /var/tmp/emp
python3 empresas.py "Asistencia del Sol"                 # solo por nombre (+ RDAP de dominios candidatos)
python3 empresas.py "X SRL" --sin-juicios                # mas rapido, sin dorks

# 3l2. RNS offline: Registro Nacional de Sociedades (personas juridicas;
#      descarga UNICA, busqueda local sin red ni captchas)
python3 rns.py descargar                        # ZIP oficiales (sociedades+asociaciones 2026)
python3 rns.py indexar                          # base SQLite FTS5 (rns.db)
python3 rns.py buscar "Asistencia del Sol" --json
python3 rns.py auto "Permanencia Salud"         # descargar + indexar + buscar

# 3m. Demandas judiciales (Boletin Oficial + dorks; por nombre o CUIT)
python3 judiciales.py "Asistencia del Sol" --salida /var/tmp/jud

# 3n. Analizador de CUIT con IA de algoritmo (fisica/juridica, decada DNI,
#     tipo de empresa, condicion tributaria, empleador, dominios, juicios)
python3 analizar_cuit.py 20-12345678-9 --salida /var/tmp/perfil

# 3o. Buscador inteligente por campos -> TABLA-EMPRESAS-CUIT-TIPO.md
python3 buscador_empresas.py 27-12345678-9 --salida /var/tmp/tabla
python3 buscador_empresas.py "Asistencia del Sol" --salida /var/tmp/tabla
python3 buscador_empresas.py --lista empresas.txt --con-dorks --salida /var/tmp/tabla
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
python3 -m py_compile extractor_final.py chart_server.py ocr_rapido.py vision.py captcha_ia.py captcha_web.py revision.py buscador.py empresas.py judiciales.py analizar_cuit.py buscador_empresas.py

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
- **Revisión de formato (`revision.py`):** el análisis determinista (openpyxl/python-docx/pypdfium2) es exacto sobre el archivo (p. ej. 219 celdas sin bordes en una planilla real que el VLM "ve" bien); la Visión IA 360° es perceptual y puede contradecirlo (la capa determinista manda para hechos objetivos, la visual para diseño/apariencia). La rúbrica del VLM a veces devuelve dimensiones inventadas o notas sin dimensión: se parsean las válidas y el resto se cuenta como `no_conformes` (verificado con docbee en vivo). Formatos: xlsx/xlsm (openpyxl), ods (normalización LibreOffice + integridad opcional con odfpy), docx (python-docx), pdf (pypdfium2).
- **docbee (PP-DocBee-2B) en la batería 360°: VALIDADO en GPU (RTX 3070 8 GB)** — 8/8 tests del harness (`scripts/bateria_360.py --motor docbee --device cuda`); gana ui_qa (4/4 vs 2/4) y personas (1/2 vs 0/2) frente a gemma3:4b, y pie 5/5. Corre con `max_pixels` reducido a 0.5M px para caber en 8 GB (OOM a resolución nativa), por lo que en lectura de valores queda por debajo de gemma (3/12 vs 7/12). Entorno GPU: `paddlepaddle-gpu==3.3.1` (índice oficial cu126) + `LD_LIBRARY_PATH` con los `nvidia/*/lib` del venv por delante. Detalle: `docs/PRUEBAS.md` §4.1 y lección 17.
- **Imágenes de prueba externas:** contenido con derechos de terceros → solo en directorios temporales, nunca en el repo.

## Documentación oficial de referencia

- PaddleOCR — Módulo de gráficos (`chart_parsing`): `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/module_usage`
- PaddleOCR — Pipeline OCR general: `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/pipeline_usage/OCR.en.md`
- PaddleOCR — Pipeline de comprensión de documentos: `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/pipeline_usage/doc_understanding.md`
- PaddleOCR — Guía de instalación: `https://github.com/PaddlePaddle/PaddleOCR/tree/main/docs/version3.x/installation.md`
