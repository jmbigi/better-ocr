# LECCIONES APRENDIDAS — extract-charts

> Memoria del proyecto (referenciada desde `AGENTS.md`). Se actualiza tras cada prueba, fallo o hallazgo relevante. Si algo falló 2+ veces, se documenta aquí con su solución.
> Anonimizado por regla P0.9: sin rutas de claves, cuentas ni datos personales.

## 1. Filtro de filas separadoras Markdown (2026-07-31)

**Fallo (1 vez, detectado en revisión):** la fila separadora `|---|---|` NO se eliminaba al convertir a DataFrame. El filtro original solo buscaba líneas que **empiezan** por `---`, pero el modelo puede emitir la fila con pipes iniciales (`| --- | --- |`), que nunca coincidía.

**Solución:** nueva función `es_fila_separadora()` en `extractor_final.py`: quita pipes de los bordes, divide por `|` y verifica que todas las celdas coincidan con `:?-{3,}:?` (guiones con alineación opcional). Cubre ambos formatos del modelo. Aplicado también al script de la guía (`docs/GUIA_OCR_VISION.md`).

**Refinamiento (segunda revisión, detectado por tests unitarios):** el regex inicial `:?-+:?` también clasificaba celdas de **dato** con guiones simples/dobles (`| - | - |`, ej. valores "-") como separadores, con pérdida de datos. El estándar markdown exige **3 o más guiones** en el separador: regex final `:?-{3,}:?`.

**Verificación:** prueba del servidor con modelo simulado + `tests/test_extraccion.py` (21 tests: `python3 -m unittest discover -s tests -v`).

## 2. Importación perezosa de PaddleOCR (2026-07-31)

**Fallo (1 vez, detectado en prueba):** `chart_server.py` no podía importarse ni probarse sin tener `paddleocr` instalado, porque la cadena de imports (`chart_server` → `extractor_final` → `paddleocr`) lo exigía al cargar el módulo.

**Solución:** `from paddleocr import ChartParsing` movido dentro de `main()` en `extractor_final.py`. Ahora los módulos se importan sin dependencias pesadas y el servidor es testeable con un modelo simulado.

## 3. Cierre del servidor y socket (2026-07-31)

**Fallo (1 vez, en prueba):** tras `server.shutdown()` el proceso de prueba colgaba: `shutdown()` detiene `serve_forever()` pero **no cierra el socket**; las conexiones nuevas quedaban en cola sin respuesta. El servidor real sí llama `server_close()` en el `finally`; la prueba no lo hacía.

**Solución:** en las pruebas, `server.server_close()` tras unirse al hilo de `serve_forever()`. Lección para código y test: tras `shutdown()` hay que `server_close()` para liberar el puerto.

## 4. `to_markdown()` requiere `tabulate` (2026-07-31)

**Fallo potencial (detectado en revisión):** `df.to_markdown()` en la respuesta del servidor depende del paquete `tabulate`, no declarado en el proyecto. En un entorno sin él, la respuesta de éxito rompería con 500.

**Solución:** `df_a_markdown()` en `chart_server.py` genera la tabla markdown manualmente (sin dependencias). El proyecto sigue usando solo: `pandas` + `paddlepaddle` + `paddleocr[doc-parser]`.

## 5. Permisos de opencode (better-ai, 2026-07-31)

**Lección del proyecto better-ai:** los patrones de permisos de `opencode.json` matchean **por tokens, no por subcadenas**; y ante empate de coincidencia **gana la última regla** (`last matching rule wins`). Por eso los `deny` específicos deben quedar DESPUÉS de cualquier `ask` genérico de su familia, y cada patrón se debe probar contra el comando real que debe bloquear. (Incluido en `CHECKLIST.md`.)

## 6. Los permisos de opencode se cargan al iniciar la sesión (2026-07-31)

**Fallo (1 vez, en prueba de cumplimiento):** la prueba "negarse a `rm -rf`" del ruleset FALLÓ: `rm -rf /tmp/opencode/rmtest` se ejecutó sin bloqueo a pesar de que `opencode.json` contiene `"rm -rf *": "deny"` (verificado, línea 91). Causa: el `opencode.json` se había añadido **a mitad de la sesión** y los permisos de opencode se cargan al **inicio de la sesión**; la sesión activa seguía con la configuración anterior.

**Solución:** los guardarraíles de `opencode.json` exigen **reiniciar opencode** tras crearlos/modificarlos para que se apliquen. La prueba de cumplimiento del ruleset (negación a `rm -rf`) debe ejecutarse en una sesión nueva; en esta sesión, la protección real fue la regla de texto P0.3, no el `deny`.

**Verificación:** sin daño (directorio desechable creado solo para la prueba); regla `deny` confirmada en el archivo. **Re-verificado el 2026-08-07 en sesión nueva:** `rm -rf /tmp/opencode/deny_test` fue bloqueado por el `deny` antes de ejecutarse (pendiente cerrado; el bloqueo llegó como regla de opencode, no solo como regla de texto).

## 7. Validación con inferencia REAL (2026-07-31)

**Contexto:** primera ejecución real del proyecto (Python 3.11.9, Kubuntu, 16 cores, 15 GB RAM, PaddlePaddle 3.3.1, PaddleOCR 3.7.0) — el entorno documentado (Arch, Python 3.12) no estaba disponible.

**Hallazgo 1 — `libmklml_intel.so` no se encuentra (PaddlePaddle CPU en Kubuntu):** al cargar el modelo, fallo `RuntimeError: PreconditionNotMet ... libmklml_intel.so: cannot open shared object file`. La librería SÍ existe en `.venv/lib/python3.11/site-packages/paddle/libs/` pero no está en la ruta del loader. **Solución:** `export LD_LIBRARY_PATH="$PWD/.venv/lib/python3.11/site-packages/paddle/libs:$LD_LIBRARY_PATH"` antes de ejecutar. (No era necesario en el entorno Arch original de la guía.)

**Hallazgo 2 — resultados reales de la validación (imagen oficial `chart_parsing_02.png`):**
- `extractor_final.py`: **6/6 valores exactos**, coinciden 1:1 con el ejemplo oficial de la documentación (2018–2023). Estructura de salida confirmada: `res.result`.
- `chart_server.py`: `GET /health` OK; `POST /chart` → 200 con 6 filas en **74 s** (inferencia en caliente, modelo ya cargado); apagado limpio por SIGTERM; **auto-cierre por inactividad verificado**: watchdog cerró el proceso tras 3605 s sin peticiones ("Proceso finalizado. Modelo descargado de la memoria.").

**Detalle operativo:** al lanzar `chart_server.py` desde una shell de herramientas que mata el grupo de procesos al expirar, el servidor recibe SIGTERM y cierra limpiamente (comportamiento correcto); para que sobreviva, lanzarlo con `setsid nohup ... & disown`.

## 8. Cláusulas Anti-Vibe-Code (P1.13–P1.18) y publicación en Codeberg (2026-08-01)

**Contexto:** sincronización con el ruleset [better-ai](https://github.com/jmbigi/better-ai) (ronda 33): nuevas reglas P1.13–P1.18 (autoría humana, disclosure `Assisted-by:`, anti-vibe-code, política de IA del anfitrión, humanos se comunican con humanos, revisión de imports antes de commit/push) y publicación del proyecto en Codeberg además de GitHub.

**Solución:** AGENTS.md, CHECKLIST.md y el verificador de ESTE proyecto actualizados (18 reglas P1); sección "Repositorio" en README con GitHub y Codeberg; remote `codeberg` añadido con el alias SSH `jmbigi-codeberg` (misma clave que GitHub, `IdentitiesOnly yes`, huella del host verificada contra docs.codeberg.org).

**Verificación:** verificador local: 15 OK, 1 FALLO conocido (`arbol de trabajo limpio`, esperado con cambios sin commitear) + 1 FALLO preexistente de tests documentado en la sección 9. HEAD idéntico en los tres remotos tras el push.

**Lección:** las reglas anti-vibe-code (P1.13–P1.17) son texto: según RepoComplianceBench (arXiv 2607.26819) los agentes cumplen disclosure/verificación (77–100%) pero no prohibiciones — el enforcement real está en la revisión humana y en los checks del verificador local (P1.18 concreta el check de imports/dependencias: existen, usados, seguros, licencias).

## 9. Test `test_chart_cuerpo_demasiado_grande` (2026-08-01)

**Hallazgo:** el test del límite de cuerpo (413) fallaba de forma consistente (5/5): `urllib.error.URLError: <urlopen error [Errno 32] Broken pipe>` en lugar de recibir el HTTP 413 esperado.

**Causa raíz (carrera TCP):** `chart_server.py` rechazaba el cuerpo con `_enviar_json(413, ...)` y cerraba la conexión sin drenar el cuerpo. El cliente aún estaba enviando el cuerpo de ~1 MB cuando el socket se cerraba → EPIPE en el cliente, que `urllib` reporta como `URLError` y no como `HTTPError(413)`.

**Solución (commit `93d340c`):** tras responder el 413 se drena el cuerpo de forma acotada (`self.rfile.read(min(largo, MAX_CUERPO))`) antes de cerrar; el cliente termina de enviar y recibe el 413. Suite 21/21 OK y hook pre-commit en verde.

**Lección:** en servidores HTTP propios, responder un error y cerrar sin drenar el cuerpo pendiente convierte el rechazo en una conexión rota para el cliente — drenar acotado tras el error evita el EPIPE sin exponer el servidor a lecturas ilimitadas.

## 10. Visión IA local para e2e: PP-OCRv6 + PP-DocBee-2B en GPU (2026-08-01)

**Contexto:** en visorweb2 los tests e2e se complementan con visión IA local (directiva better-ocr): el agente LLM de texto razona sobre los resultados de visión (JSON) en lugar de imaginar la UI.

**Solución (GPU 8 GB, RTX 30x0):**
- **Migración easyocr → PP-OCRv6** (`PaddleOCR`): mismo contrato `{text, confidence, bbox}`, memoria estimada `{'cpu': 3072, 'gpu': 2048}` MB. Cuellos de botella resueltos: `cu_seqlens` debe ser **int32** (`attention_mask`/`position_ids` con `.astype('int32')`) y redimensionar a **512 px** antes de pasar al DocBee.
- **LD_LIBRARY_PATH en subprocesos**: los bins del venv (paddle/libs + nvidia/*/lib) deben estar en `LD_LIBRARY_PATH`; **fix cuDNN**: resolver `libcudnn.so.8` como `libcudnn.so.9` para los bins de paddle.
- **SIGABRT in-process**: cargar PaddleOCR y correr capturas reales en el MISMO proceso del test aborta (cuDNN); los tests de screenshots reales corren en subproceso (`run_cli`) y la verificación GPU se hace vía CLI.
- **Bug bound-method en unittest**: `self.original = <método de clase>` liga el método a la instancia vía descriptor y contamina al test siguiente; usar `type(self).original`.
- **Contexto de captura (directiva "saber qué se captura")**: el e2e registra por pantallazo origen (headless viewport), frente/fondo, viewport, imagen y pageUrl; y el entorno del sistema (pantallas vía `xrandr`, escritorios virtuales vía `wmctrl -d`, sesión gráfica) — sin hostnames ni usuarios (P0.9).

**Verificación:** suite 46/46 (test_vision_analyze), e2e 4 estados: OCR real ('Instrument', 'Music Style', 'Siente el nacionalismo'), PP-OCRv6 posicionado (23 ítems) y PP-DocBee-2B respondiendo preguntas sobre la UI real.

**Lección:** la verificación de UI con visión local exige conocer el pipeline completo (resize, dtype, librerías dinámicas, subprocesos) — cada capa (OCR estructural, QA visual, contexto de captura) aporta un nivel de evidencia distinto al LLM de texto.

## 11. Revisión de robustez: 413/keep-alive, validación de imagen y mensajes 400 (2026-08-02)

**Contexto:** revisión del proyecto con reglas better-ai. Se investigó una hipótesis y se aplicaron dos mejoras con evidencia.

**Hallazgo 1 — el 413 NO es un bug de keep-alive (hipótesis refutada con evidencia):** se sospechó que tras el 413 el cuerpo residual sin drenar corrompería la siguiente petición de una conexión HTTP/1.1 keep-alive. Verificado con socket crudo y handler instrumentado (request line + `close_connection`): `BaseHTTPRequestHandler.protocol_version` por defecto es **HTTP/1.0**, y `parse_request` solo mantiene la conexión si `version_number >= (1,1) AND protocol_version >= "HTTP/1.1"` — como el servidor responde HTTP/1.0, **cierra la conexión tras CADA respuesta** y el residuo muere con el socket. El test con `http.client` que "sí" recibía 200 en la 2ª petición era engañoso: http.client reabre una conexión nueva al ver la respuesta HTTP/1.0. El drenado acotado del 413 (commit `93d340c`) es correcto y suficiente.

**Advertencia para el futuro:** si alguien activa `protocol_version = "HTTP/1.1"` en `chart_server.py`, el 413 quedará vulnerable: el cuerpo residual corrompería la request line de la siguiente petición de la misma conexión. En ese caso habría que añadir `Connection: close` a la respuesta 413 (o drenar el cuerpo completo).

**Mejora 1 — `validar_imagen` ahora verifica la firma mágica:** antes solo comprobaba `os.path.exists`, así que un directorio (`ejemplos`) o un `.txt` pasaban la validación y obligaban a cargar el modelo (3-5 min, 4.8 GB) para fallar después. Ahora `es_archivo_imagen()` lee los 16 primeros bytes y acepta solo firmas reales (PNG, JPEG, BMP, GIF, WebP, TIFF), con `ValueError` claro en caso contrario.

**Mejora 2 — el 400 del servidor distingue JSON inválido de clave faltante:** antes `{"otra": 1}` (JSON válido) devolvía `"JSON invalido: 'image'"`, un mensaje falso. Ahora: `"Se espera un objeto JSON con la clave 'image'"`; también rechaza `"image"` que no sea string (evita un 500 por un error del cliente).

**Verificación:** 27/27 tests (`python3 -m unittest discover -s tests -v`), sintaxis OK, verificador local 20/20 OK.

## 12. Visión multi-modo: cascada PP-OCRv6, benchmark de motores y límites de la máquina (2026-08-05)

**Contexto:** extensión del proyecto a "visión IA": modos texto/gráficos/doc/objetos con CLI (`vision.py`) y servidor (`POST /vision`), más una ruta rápida de gráficos (`ocr_rapido.py`) que compite con el VLM ChartParsing.

**Benchmark real (imagen oficial chart_parsing_02, 1 ejecución por motor, subprocesos aislados):**

| Motor | Carga | Inferencia | RAM pico | Exactitud |
|---|---|---|---|---|
| `ChartParsing` (VLM, base) | 147 s | 179 s | 5.2 GB | 18/18 celdas |
| `PP-StructureV3` (con chart) | 73 s | 266 s | 6.4 GB | 12/18 (beneficios 0/6: peso PP-Chart2Table cargado con `embed_tokens` sin inicializar) |
| `PP-OCRv6` (texto) | 16 s | 38 s | 1.0 GB | 12/12 valores + 6/6 años |
| `PP-OCRv5` (texto) | 7 s | 51 s | 2.7 GB | 12/12 + 6/6 |

**Hallazgo 1 — bug paddlepaddle 3.3.1 (PIR + oneDNN):** PP-OCRv6/v5, PP-StructureV3 y RT-DETR fallan al cargar con `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]`. Confirmado por mantenedores como bug del framework (issue PaddlePaddle/PaddleOCR#18162); workaround oficial: desactivar oneDNN. Soluciones aplicadas y verificadas: `PaddleOCR(enable_mkldnn=False)`, `PPStructureV3(enable_mkldnn=False)`, y para PaddleX genérico (RT-DETR) `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0` **antes** del import (el flag se lee al importar paddlex; `run_mode`/`enable_mkldnn` por kwargs NO llegan al runner, verificado). Los flags `FLAGS_use_mkldnn=0` y `FLAGS_enable_pir_api=0` NO funcionan aquí.

**Hallazgo 2 — `PPStructureV3.predict` no acepta dict:** solo `str`/`ndarray`; con `{"image": ruta}` devuelve lista vacía sin error (`IndexError` al indexar). El resultado JSON expone la estructura `res.layout_det_res.boxes` (no `layout`).

**Hallazgo 3 — OCR doble (imagen completa + banda inferior):** PP-OCRv6 pierde las etiquetas del eje X cuando las etiquetas de valores negativos se solapan con ellas; un segundo OCR del 14% inferior (crop) las recupera. Fusión con regla: conflicto por IoU gana la lectura de mayor score (con epsilon 1e-4: `1.0 > 0.999991` es ruido de punto flotante y NO debe reemplazar); duplicado por texto idéntico con centros cercanos se descarta. La banda NO debe ser demasiado estrecha (corta glifos) ni demasiado ancha (corrompe signos de valores).

**Hallazgo 4 — el gate de plausibilidad es quien evita la pérdida silenciosa de datos:** un año sin valor leído (`-2.9` en bar_2series) o número inconsistente de columnas entre años hace FALLAR la ruta rápida y cae al VLM (18/18). El fast path gana tiempo/RAM solo cuando está completo; nunca devuelve tablas incompletas.

**Hallazgo 5 — generación de gráficos de prueba (matplotlib):** el OCR no lee texto pequeño (xtick 10pt a dpi 150 = 0 detecciones). Además: offset positivo en etiquetas de valores negativos las cruza con la línea del eje (el OCR lee la caja vacía, score 0.0) y con las etiquetas de años (se fusionan en una detección). Solución: `figsize` generoso, xtick ≥ 14pt, valores negativos etiquetados DEBAJO de la barra con offset −2, margen inferior amplio. En el fast path, los ticks del eje Y quedan a ≥0.5×espaciado de la primera categoría y las etiquetas reales a ≤0.3×: ventana de emparejado 0.5×espaciado discrimina ambos (verificado en matplotlib y plotly).

**Hallazgo 6 — esta máquina tiene 7 GB de RAM (no 16 como la lección 7):** ChartParsing (5.2 GB) cabe justo; PP-StructureV3 con chart (6.4 GB) roza OOM; layout-only (4.5 GB) cabe. PaddleOCR-VL 0.9B (4.7–9 GB) NO cabe: los modos pinturas/dibujos/descripción quedan fuera en este hardware y requieren GPU o más RAM. Modos validados aquí: texto (PP-OCRv6), graficos (cascada), doc (layout-only, 323 s), objetos (RT-DETR-L, 18 s, 857 MB, 7 detecciones en foto real de frutas).

**Verificación:** 56/56 tests, e2e del servidor `/vision` real (35 líneas de la tarjeta de embarque en 61 s), informe `/var/tmp/better-ocr-bench/reporte.json`.

**Lección:** ante un bug de framework, verificar en la fuente (issues oficiales) y aplicar el workaround oficial; los parámetros de librerías de alto nivel (PaddleX) a veces no propagan lo que prometen — hay que leer el código de flags del paquete instalado. Y medir RAM real por modo antes de prometer "visión total" en una máquina concreta.

## 13. deepseek-ocr.rs (Rust) con PaddleOCR-VL q4k: funciona y es exacto, pero lento (2026-08-05)

**Contexto:** prueba mínima de la alternativa en Rust (sin Python) en esta máquina (7.7 GB RAM, 9 GiB swap añadido por el programador como root, CPU).

**Build:** `cargo build --release -p deepseek-ocr-cli` con rustup (perfil minimal, instalado en `~/.cargo`). **Dos fallos de build con causas distintas:** (1) `Bus error (señal 7)` del linker (rust-lld/LLVM): el repo se clonó en `/tmp` que es **tmpfs de 3.9 GB** — el enlazado llena tmpfs y muere; solución: `CARGO_TARGET_DIR` y `TMPDIR` en disco (`/home/admin/dsocr-target`). (2) `ring` build script falló una vez por presión de disco mientras el programador creaba `/swapfile2` (4 GiB): transitorio, recompilando pasó.

**Resultado real (grafico_demo.png, `--model paddleocr-vl-q4k --device cpu --max-new-tokens 400`):** 12/12 valores + 6/6 años **exactos** (q4k NO degradó los dígitos en este caso, contrariamente a la predicción inicial). Carga del modelo 8.2 s. **~1200 s de inferencia** (prefill visión 0.43 tok/s en 477 tokens = 1099 s; generation 134 tokens a 1.4 tok/s = 96 s). Sin OOM gracias al swap (9 GiB). RAM real no medida (el monitor VmRSS leyó el PID del wrapper, no el proceso — monitoreo no fiable con `&` dentro del shell; medir con subproceso directo).

**Comparativa final en este gráfico:** PP-OCRv6 fast path 57 s/1 GB (gate rechaza), ChartParsing 333 s/5.2 GB/18-18, deepseek-ocr.rs q4k ~1200 s/~4-6 GB/12-12, PP-StructureV3 345 s/6.4 GB/12-18.

**Lección:** la alternativa Rust es viable y exacta, pero en CPU es ~4× más lenta que el VLM Python y ~20× que PP-OCRv6: solo para batch sin prisa o entornos sin pila Python. Y un swapfile nuevo con `mkswap -U clear --size 4G --file` + `pri=10` en fstab (verificado con `findmnt --verify`: 0 errores) es seguro si no se toca el swap existente.

## 14. Matriz de validación completa del set de charts (2026-08-05)

**Contexto:** cierre de la validación de la cascada sobre los 8 gráficos de `ejemplos/test_charts/` + el demo oficial, con fallback VLM real (`--con-fallback`).

| Chart | Fast path | Fallback ChartParsing | Resultado |
|---|---|---|---|
| bar_2series (mpl, 2 series) | 6/6 + 12/12 (~70 s, 1 GB) | — | OK |
| plotly_barra (plotly) | 6/6 + 12/12 (~58 s, 1 GB) | — | OK |
| grafico_demo (oficial) | rechaza (año truncado) | 18/18 (~333 s, 5.2 GB) | OK |
| pie_5 | rechaza (sin años) | 5/5 + 5/5 (~209 s) | OK |
| line_3series | rechaza | 3/3 + 24/24 (~275 s) | OK |
| seaborn_agrupado | rechaza | 4/4 + 8/8 (~248 s) | OK |
| bar_apilada | rechaza | 3/3 + 12/12 (~236 s) | OK |
| scatter_valores | rechaza | 0/6 + 0/12 — **el VLM alucina una tabla** (1999/2008/2010 con valores inventados) | NO soportado |

**Hallazgo 1 — ChartParsing transpone filas/columnas según el tipo de gráfico:** en line y stacked-bar emite filas = series y columnas = categorías (línea: `A|M1..M8`; apilada: `Part A|Q1..Q4`), mientras que en barras agrupadas emite filas = categorías. Las referencias de validación se ajustaron al formato real del modelo (lección: el ground truth debe replicar el formato de salida del motor, no el "natural" del dataset).

**Hallazgo 2 — el scorer debe tolerar sufijos:** los valores de pie salen como `35.0%`; `normalizar()` ahora quita `%` antes de comparar. Un 0/5 no era fallo del modelo sino del validador.

**Hallazgo 3 — scatter no es soportado por PP-Chart2Table:** el modelo (entrenado para barra/línea/pastel) inventa datos plausibles en lugar de devolver los labels reales de los puntos. Peor que un error: es una falsa tabla. Para scatter, la lectura útil es solo el modo texto (PP-OCRv6 lee los labels `x,y`), sin reconstrucción de tabla.

**Verificación:** 59/59 tests, matriz completa con corridas reales (2× fast path + 5× fallback VLM + 1 rechazo documentado).

## 15. Batería 360° VLM en CPU: ollama, gemma3:4b y qwen2.5vl (2026-08-05)

**Contexto:** comparación empírica de VLM locales con el harness `scripts/bateria_360.py` (6 dimensiones: QA de UI, interpretación, valores, objetos, descripción, documento) en un servidor CPU con 7.7 GB de RAM.

**Instalación de ollama sin pipes (P0.8):** el instalador oficial usa `curl | sh` (prohibido por el guardarraíl); alternativa verificada: descarga del tarball oficial desde el release de GitHub (`ollama-linux-amd64.tar.zst`, ~1.35 GB) y extracción local. El servidor se lanza a mano en `127.0.0.1:11434`.

**Resultados medidos (misma batería, temperatura 0):**

| Test | qwen2.5vl:3b | gemma3:4b | qwen2.5vl:7b |
|---|---|---|---|
| valores (demo oficial) | 12/12 (224 s) | 12/12 (150 s) | 12/12 (399 s) |
| valores (pie) | — | 5/5 (130 s) | 5/5 (585 s) |
| objetos (frutas) | 3/4 | 3/4 | 3/4 |
| documento | — | 1/2 (371 s) | 1/2 (324 s) |

**Hallazgo 1 — empate técnico en la batería objetiva:** los tres modelos puntúan igual (12/12, 3/4, 1/2); la diferencia es tiempo y RAM. gemma3:4b es el punto dulce de la máquina (2.5-4.5× más rápido que el 7b).

**Hallazgo 2 — qwen2.5vl:7b acorrala la RAM del servidor:** carga 5.8 GB; al terminar dejó 150 MB libres (riesgo real para los servicios del host). Solución verificada: descargar el modelo tras cada uso con la API `keep_alive=0` (`POST /api/generate {"model": ..., "keep_alive": 0}`). Nunca dejar modelos grandes cargados en un servidor con servicios productivos.

**Hallazgo 3 — el monitor del servidor marcaba "Status: error" por disco al 91%** (umbral >90), no por servicios: `ollama`/`vsftpd` "not-found" son informativos y no cuentan para el estado. La clave `services_error` en realidad significaba "cualquier error": renombrada a `any_error` (nadie la consumía; verificado). Con disco a ~63% el monitor volvió a `ok`.

**Verificación:** 66/66 tests, corridas reales documentadas; salidas crudas en `/var/tmp/bateria360/`.

## 16. Descripción de imágenes reales: el 7b mejora, pero modestamente (2026-08-05)

**Contexto:** prueba diminuta (2 fotografías reales de web pública, sin datos personales) con la misma pregunta de descripción a gemma3:4b, qwen2.5vl:7b y un modelo comercial de referencia.

**Imagen 1 (parque con plataformas circulares, junto a cancha cercada):** el 7b acertó el contexto que gemma perdió — autobús claro, casas urbanas al fondo, portería tras la red — y coincidió con la referencia comercial en 4 detalles de contexto; gemma atribuyó el naranja del autobús a "marcas del campo". Ambos fallaron las máquinas de gimnasio biosaludable y el detalle de un niño con camiseta blanca/pantalón rojo (solo la referencia comercial lo captó).

**Imagen 2 (siete niños corriendo tomados de la mano por un camino de tierra):** ambos contaron 7, vieron manos, camino y bosque; el 7b fue más específico en colores reales (rosa, mezclilla) frente al genérico "blues/whites" de gemma; la referencia comercial añadió el desglose completo de atuendos por niño (nivel que ningún modelo local alcanza).

**Conclusión (n=2):** el 7b mejora la calidad descriptiva de forma real pero modesta — contexto de escena y especificidad de color — y nunca pierde frente a gemma; ambos locales quedan por debajo de la referencia comercial en granularidad. Las imágenes externas no se commitean al repo (contenido con derechos; solo en directorio temporal).

**Lección:** para decidir entre modelos hay que probar con contenido real y una referencia independiente; la batería objetiva puede empatar mientras la calidad descriptiva difiere — por eso la batería 360° incluye dimensiones libres con rúbrica humana.

## 17. docbee (PP-DocBee-2B) en GPU: cinco bugs de integración resueltos (2026-08-05)

**Contexto:** cierre del pendiente "docbee en GPU" (RTX 3070 Laptop 8 GB, driver 580, CUDA 13, paddlepaddle-gpu 3.3.1 build cu126 + paddlex 3.7.2). La batería 360° había corrido solo ollama; docbee nunca había arrancado. Cada bug se resolvió de a uno, con evidencia.

1. **`cuda` no es un device válido para paddlex:** `DocVLM(device="cuda")` lanza `AssertionError` — `SUPPORTED_DEVICE_TYPE` en `paddlex/utils/device.py` solo acepta `cpu|gpu|xpu|npu|mlu`. El script pasaba `--device cuda` directo. Fix: normalizar `cuda*` → `gpu` en `run_docbee`.
2. **DocVLM espera `query`, no `prompt`:** el input dict correcto es `{"image": ..., "query": ...}` (CLI help del propio `doc_vlm.py`). Con `prompt` → `KeyError: 'query'` en el processor. La batería nunca lo había usado, por eso no se detectó.
3. **paddle 3.3.1 GPU + flash attention: `paddle.cumsum` promueve int32 → int64**, y `flash_attn_unpadded` exige cu_seqlens int32 → `InvalidArgument` y SIGABRT en el decode (exacto: `flash_attn_unpadded(q,q,q,cu,cu,...)` con cu int64 falla y con int32 funciona, verificado con repro mínimo). El código de paddlex (`_get_unpad_data`) calcula int32 pero cumsum lo promueve. Fix: parche en el subproceso que fuerza `cu.astype('int32')`.
4. **OOM en 8 GB a resolución nativa:** `MAX_PIXELS = 16384*28*28` (12.8M px) genera miles de tokens y el `cast` de logits a fp32 (seq × 151936 vocabulario) desborda el pool de ~7.5 GB (el allocator reserva 95%). Con `MAX_PIXELS=262144` (0.5M px ≈ 245 tokens) todo cabe (verificado: pie 5/5). El flag `FLAGS_fraction_of_gpu_memory_to_use=0.98` NO sirve (CUBLAS error 15).
5. **LD_LIBRARY_PATH del host sombrea el CUDNN del venv:** el shell exporta rutas `nvidia/` del pyenv (CUDNN 9.1); paddle 3.3.1 (compilado con CUDNN 9.5) carga `libcudnn_graph.so.9` viejo → `undefined symbol: cudnnGetLibConfig` + SIGABRT. Fix en `run_docbee`: anteponer `venv/site-packages/nvidia/*/lib` y quitar rutas `.pyenv` del env del subproceso.

**Resultado:** 8/8 tests de docbee en GPU (3.5–12 s por test, salvo descripcion 205 s y documento 165 s), ~7.1 GB de RAM. vs gemma3:4b: docbee gana ui_qa (4/4 vs 2/4) y personas (1/2 vs 0/2); gemma gana valores y velocidad — pero docbee corrió con resolución limitada (0.5M px) por el OOM, así que la comparación de valores es injusta a favor de gemma. Ver PRUEBAS.md §4.1.

**Lección:** en GPU de 8 GB los VLM 2B caben solo con max_pixels reducido; y cada capa (device name, key del input, dtype de índices, pool de memoria, shadowing de libs dinámicas) es una fuente real de fallo que no aparece en CPU — el harness debe ejecutarse en la máquina objetivo antes de declarar un motor "validado".

## 18. `ChartParsing(device="cpu")` NO controla el dispositivo del VLM: el predictor lo ignora y usa la GPU global (2026-08-07)

**Contexto:** reintento del fallback VLM sobre el gráfico mixto (A4) — `validar_cascada.py` crea `ChartParsing(device="cpu")` y aun así el run abortó con SIGABRT en un kernel **GPU** de conv2d (`ConvCudnnKernel` → `InitDnnHandle`) por el cudnn 9.1 del pyenv. La investigación en el código fuente del venv explica el mecanismo completo:

1. `paddleocr/_common_args.py:102-126` (`prepare_common_init_args`): con `engine=None` construye `engine_config = {"paddle_static": {...}}` — el VLM ChartParsing **no usa** ese motor.
2. `paddlex/inference/models/__init__.py:206-216` (`_flatten_bucketed_engine_config`): el motor resuelto del DocVLM es `paddle_dynamic`; al no haber entrada en el bucket, devuelve **config vacía** con el warning exacto que salió en el log real: *"Bucketed engine_config has no entry for resolved engine 'paddle_dynamic'; using an empty config for that engine."*
3. `paddlex/inference/models/predictors/local_model_predictor.py:62-86`: `device` es una **property** derivada de `engine_config` (`resolve_device` → `device_type` ausente → `None`), NO del parámetro `device` del pipeline.
4. Con `device=None`: `TemporaryDeviceChanger(None)` no fija nada y `_switch_inputs_to_device` no mueve tensores (`doc_vlm/predictor.py:363-372`) → el forward corre en el **dispositivo global de paddle** = GPU si hay GPU visible.
5. El conv2d GPU carga cudnn → coge el del pyenv (primero en `LD_LIBRARY_PATH`, 9.1) → paddle compilado con 9.5 → `undefined symbol: cudnnGetLibConfig` → SIGABRT (el bug 5 de la lección 17, disparado ahora por el VLM "CPU").

**Por qué la matriz 8/8 de la lección 14 sí corrió en CPU:** entonces la GPU no era visible para paddle (las rutas `nvidia/` no estaban en `LD_LIBRARY_PATH` de la sesión), así que el dispositivo global era CPU de facto. Hoy la GPU es visible y el mismo comando va a GPU y aborta: **el `device="cpu"` del wrapper es cosmético para los VLM DocVLM.**

**Riesgo en el proyecto:** el comentario en `chart_server.py:57` ("device explícito: el default prioriza GPU") es engañoso — el daemon `/chart` cargaría el VLM en GPU hoy y abortaría con SIGABRT en esta máquina. La validación E2E de la lección 7/14 corrió en un entorno sin GPU visible.

**Fixes (verificados en fuente, pendientes de prueba en ejecución):**
- **CPU forzado (sin tocar código):** `CUDA_VISIBLE_DEVICES=""` antes de lanzar → paddle no ve GPU y el conv cae a CPU. Recomendado para `chart_server.py`/`validar_cascada.py` en esta máquina.
- **GPU con cudnn correcto:** el env de la lección 17 (anteponer `nvidia/*/lib` del venv, quitar rutas nvidia del pyenv) — el VLM corre en GPU, más rápido, pero los tiempos de referencia de la lección 14 eran CPU.
- No hay forma de pasar `device` real al predictor desde el wrapper de paddleocr (el `engine_config` con `paddle_dynamic` + `device_type`/`device_id` no se expone por `ChartParsing(...)`).

**Verificación del fix (2026-08-07, sin inferencia, venv del proyecto):** con `CUDA_VISIBLE_DEVICES=""` → `paddle.device.get_device()` devuelve `cpu`; sin la variable → `gpu:0`. El mecanismo de aislamiento queda confirmado; falta solo reejecutar A4 con el VLM para validarlo de punta a punta.

**Lección:** "device=cpu" en la API de alto nivel de paddleocr no es garantía de CPU para los modelos DocVLM; verificar el dispositivo real con `paddle.device.get_device()` dentro del proceso y aislar con `CUDA_VISIBLE_DEVICES` cuando se exija CPU.

**Ampliación (2026-08-07):** RT-DETR vía `paddlex.create_model` (build GPU del venv) aborta con el MISMO SIGABRT no determinista por el cudnn del pyenv — la pasada offline del servicio captcha funcionó 2 veces y luego falló 3 veces con código y entorno aparentemente idénticos (padre bash vs subprocess desde python: ambos crashearon con ruta válida; solo "funcionó" sin tocar el modelo). Fix aplicado: `CUDA_VISIBLE_DEVICES=""` al inicio de los workers del venv (detección y OCR) → RT-DETR en CPU, determinista, 2/2 pasadas idénticas (~30 s). Regla: **cualquier worker de paddlex en esta máquina debe forzar `CUDA_VISIBLE_DEVICES=""` (CPU) salvo que se quiera GPU con el env de la lección 17**; y un "funcionó una vez" no es evidencia de determinismo en este entorno.

## 19. Playwright: el auto-wait de los locators tarda 30 s en elementos ausentes (2026-08-07)

**Fallo (en tests E2E del servicio captcha):** `locator.first.inner_text()` y `locator.first.evaluate(...)` sobre un selector que NO matchea esperan el timeout por defecto de Playwright (~30 s) ANTES de lanzar la excepción, aunque el bloque `try/except` ya estuviera preparado. El `count()` de los tests pasó de 34 s → 64 s → 96 s mientras se acumulaban estas esperas (una por selector inexistente, p. ej. la página falsa con el botón de VERIFY de otra clase).

**Solución:** comprobar `locator.count() > 0` antes de `inner_text()`/`evaluate()` (count() no espera). Aplicado en `leer_instruccion` y `pulsar_verificar` de `captcha_web.py`. Con el patrón aplicado, los 2 tests E2E pasan en 4.6 s.

**Hallazgo asociado (DOM real de reCAPTCHA, validado en vivo por el programador):** la clase de la tabla de tiles varía con el tamaño del reto (`rc-imageselect-table-33`/`rc-imageselect-table-44`), el botón VERIFY actual es `rc-button-default` (histórico: `rc-button-go`), y la URL de la página anfitriona puede contener "recaptcha" — el iframe del ancla se detecta por `/anchor` en la URL y excluyendo el frame principal. También: `locale="en-US"` fija el idioma de las instrucciones (el parser es inglés).

**Lección:** en Playwright, "el elemento no existe" cuesta 30 s por locator si no se hace `count()` antes; y los selectores de widgets de terceros cambian entre versiones — la página falsa de tests debe replicar los selectores validados en vivo, no los que uno supone.

## 20. reCAPTCHA v2 en vivo (demo Google): loop completo funcional, precisión es el límite (2026-08-07)

**Contexto:** validación en vivo contra `https://www.google.com/recaptcha/api2/demo` con `captcha_web.py` (stack local: Playwright + RT-DETR-L del venv + OCR PP-OCRv6 de respaldo). Esta lección completa el §7 de PRUEBAS.md con lo verificado en ejecución real.

**Hallazgo 1 — clic real de Playwright en VERIFY dispara el POST; el JS a veces se ignora:** con `el.click()` (untested) Google no siempre procesa el envío. Con `locator.click()` (evento confiable) el envío llega siempre: respuesta incorrecta → POST `api2/replaceimage` + payload nuevo (sin texto de error en la demo; el `.rc-imageselect-error-response` queda vacío). El veredicto no debe esperar el mensaje de error: usa el reemplazo de imagen/cierre del bframe.

**Hallazgo 2 — la selección vacía + VERIFY es ignorada en silencio:** si no se pulsó ningún tile (p. ej. clase no-COCO sin VLM: crosswalks) y se pulsa VERIFY, Google NO responde (ni replaceimage ni error ni check) y el reto queda clavado con la misma imagen. Detectado: grid idéntico entre intentos consecutivos (md5 igual). Para la variante "If there are none, click skip" la respuesta correcta es SKIP (no VERIFY vacío); el orquestador ya lo hace.

**Hallazgo 3 — el clic JS en tiles sí registra:** tras `el.click()` el td queda `rc-imageselect-tile rc-imageselect-tileselected` (headless y headed). El clic normal de Playwright falla por los transforms ("outside of viewport"), como ya se sabía.

**Hallazgo 4 — precisión real medida en cuadrículas:** 4×4 de motos: las motos reales en (1,2) puntuaron 0.24-0.28 (bajo el umbral 0.45) → selección incompleta → rechazo (replaceimage). 3×3 de bicicletas: 0.82 y 0.55 detectadas; aun así rechazado (adversario o más celdas reales perdidas). Coincide con la medición previa del programador (~50-70%, rechazado igual). El umbral fijo es el eslabón débil: los tiles 4×4 (más chicos) puntúan aún más bajo.

**Hallazgo 5 — env de los workers (lección 17) aplicado a captcha_web:** sin `env_worker()` los subprocesos del venv morían con SIGABRT por el cudnn 9.1 del pyenv (`undefined symbol: cudnnGetLibConfig`) al detectar en GPU. Con el env corregido: RT-DETR-L GPU 8 s totales (carga + inferencia de un tile). El worker del proyecto ya usaba `modo_objetos_lote` (una sola carga del modelo).

**Hallazgo 6 — formato de instrucción actual del DOM:** `"Select all images with X Click verify once there are none left."` (y variante `"…Then click verify…"`). El parser requería los sufijos "click verify once there are none left"/"then click verify" y el recorte de "then" final; cubierto con tests. El desc puede renderizarse un instante después de la cuadrícula: leer con reintentos breves antes de caer al OCR.

**Hallazgo 7 — la demo puede venir sin desc (variante noaccess):** DOM vacío en ~1 de cada 3 retos; el fallback OCR (PP-OCRv6 sobre la franja superior del payload) lo resuelve en ~9 s con modelo en caché ("Select all images with a bus Click verify once there are none left.").

**Hallazgo 8 — esta IP bloquea buscadores por curl y Playwright:** Google (sorry page), Bing/DDG/Brave/Ecosia/Mojeek/Startpage (captcha/challenge/403) y SAIJ (403). Los endpoints de reCAPTCHA (`api2/anchor`, `api2/bframe`) SÍ funcionan, y la demo appspot falló una vez por red intermitente (`ERR_NETWORK_CHANGED`) y funcionó al reintentar (lección 4).

**Lección general:** el loop mecánico (checkbox → reto → instrucción DOM/OCR → cuadrícula → RT-DETR por celda → clics → VERIFY → feedback por replaceimage → reintento) está completo y verificado en vivo; el rendimiento real lo limita la precisión del detector sobre tiles pequeños/adversarios y las clases no-COCO (requieren el hook `fallback_vlm`, aún sin cablear). Cualquier reporte de éxito en vivo debe adjuntar las capturas guardadas y las detecciones por celda (P0.1).

**Ampliación (2026-08-07):** el VLM binario por tile quedó **validado en tiles reales** con el patrón DDG del programador (anomaly modal de DuckDuckGo): RT-DETR encontró 4 "birds", gemma3:4b respondió YES/NO por tile y descartó el falso positivo (3 ducks) — el reto pasó ("Thanks for confirming you're human!"). Ese hallazgo se incorporó a `captcha_web.py` como **confirmación de dos etapas** (`_aplicar_fallback_vlm`): los candidatos del worker se confirman/descartan con el VLM binario; sin detecciones (no-COCO) el VLM cubre todas las celdas. En cambio, la demo sintética NO sirve para medir el VLM: gemma3:4b dice "No" a todas las figuras abstractas (PRUEBAS §7).

## 21. Primer veredicto "ok" EN VIVO del servicio captcha (2026-08-07)

**Contexto:** ejecución real contra la demo oficial de Google (`https://www.google.com/recaptcha/api2/demo`) con `captcha_web.py --url ... --salida /var/tmp/captcha_real`. Primer éxito de punta a punta: **ok al intento 1 en 24.9 s** — instrucción "Select all images with cars" leída del DOM (con salto de línea, normalizada por el parser), 4 celdas seleccionadas por RT-DETR (scores 0.89/0.87/0.67/0.90), VERIFY con clic real, checkbox ancla marcado, `resultado.json` + `intentos.json` + captura guardados.

**Qué lo hizo posible (fixes acumulados que se validan juntos en vivo):**
1. **Selector real del desc**: el DOM actual usa `rc-imageselect-desc-no-canonical` (no `rc-imageselect-desc`) — sin ese fix, la instrucción nunca se leía y el flujo caía a SKIP (la causa principal del "sin éxito" histórico).
2. **Parser multi-idioma**: devuelve None en vez de basura (una clase imposible dejaba la selección vacía y VERIFY se ignoraba en silencio — hallazgo 2 de la lección 20).
3. **Clic real en VERIFY** (el JS a veces se ignora; wrong → api2/replaceimage).
4. **Umbral adaptativo** (0.45 en 3×3): los cars del reto puntuaban 0.67-0.90, holgadamente sobre el umbral.

**Lección:** los fallos en vivo encadenados ("sin éxito" tras "sin éxito") no se resolvieron tocando la precisión del detector — se resolvieron con **evidencia del DOM real** (archivos guardados por el programador) que destapó bugs de selectores y parser. El orden de ataque correcto en automatización de widgets de terceros: 1) capturar y validar el DOM real, 2) arreglar selectores/parser, 3) recién entonces medir precisión del detector. Y los `--salida`/`intentos.json` fueron la infraestructura que lo hizo analizable (P0.1).

## 22. Un default silencioso dejó MUERTO el modo nuevo: "los tests pasan" no prueba que el código corra (2026-08-07)

**Fallo:** se implementó la detección sobre la imagen completa del reto y se "validó" con un batch en vivo (g, 0/6)... sin darse cuenta de que `if detectar_lote is None: detectar_lote = detectar_batch_worker` (default del diseño original, dentro de `resolver_web`) hacía que el check del modo nuevo fuese **código muerto**: TODOS los runs usaron siempre el worker por-celdas. Se descubrió por la contradicción offline vs en vivo: el mismo `detectar_cuadricula_worker` crasheaba con `ModuleNotFoundError: paddlex` en el offline (python del sistema) pero "funcionaba" en el en vivo (porque en realidad nunca se llamaba).

**Solución:** quitar el default silencioso; el modo imagen-completa corre en subproceso del venv (WORKER_GRID) y funciona desde el python del sistema. Batch h tras el fix: 4/6 ok, 10 s/run (3× más rápido).

**Lección:** tras un cambio de comportamiento, verificar que el código nuevo se EJECUTA de verdad (probe, contador, o contraste entre dos caminos que deberían diferir) — los tests verdes y los runs "exitosos" solo prueban que ALGO funciona; un default silencioso puede anular una mejora completa sin error aparente.

## 23. La detección sobre la imagen completa gana al recorte por celdas (2026-08-07)

**Hallazgo:** recortar la cuadrícula en celdas de ~120 px y detectar por separado pierde el contexto de la escena; RT-DETR sobre la imagen COMPLETA (bboxes mapeados a celdas por el centro) recupera objetos pequeños que el recorte pierde. Evidencia: corpus de 58 fallos, selecciones plausibles 22 → 26; 7 casos de sub-selección recuperaron objetos (car 1→3, bus 0→2); batch h en vivo 4/6 ok a 10 s/run.

**Lección:** en detección de objetos pequeños, primero probar el cambio de ventana de detección (completa vs recortada) antes de cambiar de modelo o descargar nada — es gratis y a veces resuelve el recall.

## 24. Resultados negativos medidos del stack: upscale, VLM y config (2026-08-07)

**Campaña de 41 runs en vivo + replay del corpus (58 fallos con capturas):**
1. **Upscale 3× no recupera objetos**: detecciones idénticas a 2× en 9 casos — el límite es el modelo, no la resolución interpolada.
2. **Ningún VLM de confirmación mejora el baseline**: solo RT-DETR 23/58 plausibles vs +docbee 21, +gemma3:4b 22, +qwen2.5vl:7b 19 (el 7b es el más agresivo descartando — peor en este corpus). La confirmación solo QUITA candidatos: no puede arreglar la sub-selección.
3. **La pasada de ADICIÓN del VLM sobre-agrega**: celdas añadidas por el VLM correlacionan con rechazos (5 ok vs 7 fallos) — consistente con la lección 20 (sobre-selección). Quedó opt-in (`--vlm-recall`).
4. **La tasa ~50-60% es independiente de la config**: RT-DETR solo 57%, VLM con recall 59%, VLM conservador 50% — la config cambia el MODO de fallo (no-COCO, sub-detección, sobre-adición), no la tasa. El cuello de botella es el recall del detector en objetos pequeños.

**Lección:** ante una tasa estancada, medir sistemáticamente cada palanca sobre un corpus guardado (replay) antes de seguir invirtiendo — los resultados negativos acotan el problema y evitan "mejoras" que solo mueven el modo de fallo.

## 25. El corpus de fallos como infraestructura de análisis (2026-08-07)

**Herramienta:** `--archivo-fallos DIR` guarda cada intento fallido como `caso_<ts>_i<N>.json` (instrucción, decisión, scores por celda, captura vinculada); `--listar-fallos DIR` resume el corpus; `scripts/replay_fallos.py` re-evalúa los casos con distintas configuraciones (umbral, VLM docbee/gemma/qwen, recall) **sin ejecuciones en vivo**. El corpus actual: 58 casos con capturas en `/var/tmp/captcha_fallos/`.

**Lección:** guardar los fallos con su captura y decisiones transforma cada campaña en un dataset re-evaluable — es la infraestructura que permitió comparar 3 modelos y 4 configs sin gastar más ejecuciones, y la que evaluará RT-DETR-H con el mismo método.

## 26. El dict final de un run puede mentir: leer los intentos (2026-08-07)

**Fallo (2 veces):** los resúmenes de los batches mostraban `clase: None` y `sel: 0` para runs que en `intentos.json` tenían clases y selecciones reales — el dict final de fallo no incluía esas claves (`r.get(...)` devolvía None/[]) y parecía que "no se había seleccionado nada" cuando sí. Corregido: el dict de fallo incluye `clase_objetivo` y `seleccion`.

**Lección:** el dict resumido es solo una puerta de entrada; la verdad está en `intentos.json` — antes de concluir sobre un run fallido, leer los intentos guardados.

## 27. Un detector mayor (RT-DETR-H) mejora el corpus pero NO la tasa en vivo (2026-08-07)

**Experimento:** RT-DETR-H vs RT-DETR-L (detección imagen-completa, lección 23). En el corpus de 58 fallos, H mejora las selecciones "plausibles" (tamaño típico por clase): 26 → 32, y resolvió el primer fire hydrant en vivo. Pero la tasa en vivo agregada: **L 4/6 vs H 3/14** — H NO mejora (y fue más lento: ~81 s vs ~68 s por fallo, 2 runs crasheados).

**Explicación probable:** la métrica del corpus (tamaño de selección cercano al típico) NO mide la CORRECCIÓN de las celdas — H selecciona más celdas con tamaños plausibles pero con más celdas erróneas, lo que aumenta los rechazos. El adversario castiga el exceso de selección tanto como la falta.

**Lección:** la mejora de recall de un detector debe validarse por TASA EN VIVO, no por métricas de tamaño sobre un corpus — y el default se queda con el modelo que la mide mejor (L). La lección 24 se refuerza: cambiar el detector mueve los modos de fallo, no la tasa (~50-60%).
## 28. Módulo de revisión de formato: determinista vs Visión IA 360 (2026-08-12)

**Contexto:** nuevo módulo `revision.py` (revisión de formato/presentación de planillas xlsx con dos capas: análisis determinista con openpyxl y Visión IA 360° con VLM local sobre render LibreOffice→PDF→pypdfium2→PNG), solicitado por el programador junto con el estudio de herramientas existentes (spreadsheet-auditor, XLChek, excel-validator, The Checker, Spreadsheet Detective: todas se centran en FÓRMULAS y datos; ninguna gratuita cubre el nicho de estilo/consistencia visual — el hueco que cubre este módulo).

**Hallazgo 1 — la capa determinista y la visual se contradicen con razón:** una planilla real con 219 celdas sin bordes (detectado por openpyxl sobre el XML) fue puntuada por docbee como "coherencia de estilo 9/10: bordes uniformes" sobre la imagen renderizada. No es un bug: la percepción visual a baja resolución no ve lo que el XML dice exactamente. Regla de diseño: la capa determinista manda para hechos objetivos; la visual aporta percepción de diseño (legibilidad, color, equilibrio) que ninguna herramienta de XML puede medir.

**Hallazgo 2 — el VLM no obedece el formato de la rúbrica de forma estable:** docbee respondió en dos formatos distintos en ejecuciones casi idénticas ("dimensión: nota/10 | comentario" y "10/10: dimensión | comentario") y una vez con dimensiones inventadas repetidas ("Diseño de la planilla", "Utilidad de la planilla") que no están en la rúbrica. Solución: parser que acepta ambos órdenes, normaliza a prefijos canónicos y cuenta las líneas fuera de rúbrica como `no_conformes` (verificado: 7 en una ejecución real). La respuesta "no conforme" se reporta, no se silencia.

**Hallazgo 3 — los checks deterministas se afinan con ejecución real:** el primer run sobre la planilla sintética "correcta" produjo 26 falsos warnings (el check de formato numérico contaba "0" y "0.00" como "sin formato" y los enteros en General como problema). Fix: solo "General"/"@" son señales en flotantes; enteros en General (años, conteos) son normales. Y la API de openpyxl: `hoja.protection.sheet` (no `sheet_protection`), y los checks deben verificar que una columna sin nombre tiene datos reales (una celda fusionada amplia no es una columna).

**Hallazgo 4 — prueba con datos reales solo sobre copia:** las planillas reales del programador (carpeta de envío de su ámbito personal, fuera del repo) se copiaron con `cp -p` a un directorio temporal (`/var/tmp`) antes de revisar; el original quedó intacto (P0.3/P1.9). La revisión detectó hallazgos reales y útiles (textos desbordados en columnas estrechas, falta de filtros y bordes) — evidencia de valor práctico, no solo sintético. El contenido de celdas no se difundió en el repo ni en el chat (P0.9).

**Lección:** un módulo de revisión de documentos necesita dos capas complementarias (hechos exactos + percepción visual) y ambas se verifican con ejecución real sobre datos propios y datos del usuario (copiados). El VLM es parte útil del sistema pero su salida se debe sanear contra la rúbrica esperada, contando lo no conforme en vez de ignorarlo.

## 29. Fase 2 de la revisión (ods/docx/pdf): saneos, defaults engañosos y fixtures con estilos (2026-08-12)

**Contexto:** completar el módulo `revision.py` con ods (normalización LibreOffice), docx (python-docx) y pdf (pypdfium2), más la Visión IA 360 sobre PDF con render nativo (sin soffice). Dependencias nuevas instaladas SOLO en el venv: `python-docx`, `odfpy` (P0.5), declaradas en requirements.txt.

**Hallazgo 1 — `reglas=None` no se saneaba en los revisores nuevos:** `revisar_docx`/`revisar_pdf` hacían `reglas[regla]` con reglas=None → `TypeError: 'NoneType' object is not subscriptable` en la API pública (el CLI pasa reglas saneadas y lo enmascaraba). Fix: todos los revisores sanean `if reglas is None: reglas, _ = cargar_reglas()` como ya hacía `revisar_planilla`.

**Hallazgo 2 — el default "Normal Table" de python-docx NO dibuja bordes:** el primer check de tablas buscaba estilos sin "grid"/"tabla", pero python-docx asigna `_TableStyle('Normal Table')` a las tablas sin estilo: contiene "tabla" y el check no reportaba nada. Fix: "Normal Table"/"Tabla normal" se reportan explícitamente como tablas sin bordes visibles.

**Hallazgo 3 — `pandas.to_excel(engine="odf")` genera ODS sin estilos:** el fixture ODS "correcto" salía con 5 errores de encabezado porque pandas no aplica estilos. Fix: el fixture ODS se genera con soffice convirtiendo el xlsx correcto (conserva estilos). Lección: los fixtures de formato deben generarse con las mismas herramientas que los usuarios (LibreOffice), no con la ruta más corta.

**Hallazgo 4 — la integridad ODS depende de odfpy (python del sistema vs venv):** el CLI corre con el python del sistema (sin odfpy) y la verificación de integridad fallaba con ImportError → revisión marcada "ok: False" injustamente. Fix: si odfpy falta, la integridad queda `{"ok": None, "aviso": "no verificada"}` y la revisión sigue (honestidad sin romper el flujo); con el venv (odfpy instalado) la integridad se verifica de verdad (hojas y dimensiones idénticas).

**Hallazgo 5 — los PdfDocument de pypdfium2 deben cerrarse:** unittest avisó "The following objects are still open" — el documento se cierra ahora en `finally`, también en `render_pdf_a_pngs`.

**Lección:** los formatos nuevos se añaden con el mismo contrato (checks puros + saneo de reglas + cierre de recursos + fixtures realistas con estilos), y cada dependencia opcional degrada con aviso explícito, nunca con un fallo mudo o injusto.

## 30. Cierre del módulo de revisión: comparar ODS, cifras de docs y auditoría P0.9 (2026-08-12)

**Hallazgo 1 — `--comparar` con ODS fallaba con BadZipFile:** `comparar_planillas` abre los archivos con openpyxl, que no lee ODS; comparar una planilla ODS (o contra una ODS) reventaba sin capturar (500 en el servidor, crash en CLI). Fix: en `revisar_documento` ambos lados de la comparación se normalizan a xlsx temporal con soffice cuando la extensión es ods, con limpieza en `finally` (test: correcta.ods vs v1.xlsx → 2 diferencias de hoja).

**Hallazgo 2 — las cifras de la documentación se desactualizaron durante el desarrollo:** PRUEBAS §9.3 y el Estado del README decían "219/219 tests" cuando la suite real era de 211 (la cifra se escribió durante una iteración intermedia). Fix: 211 en ambos sitios, verificados contra `unittest` real. Lección: las cifras de tests en docs se escriben DESPUÉS de la corrida final, nunca de memoria.

**Hallazgo 3 — auditoría P0.9 obligatoria antes de commit:** el dif a commitear incluía una ruta personal del programador (`/home/kubuntu/mama/cuidadores/ENVIO/`) citada en la lección 28 y el nombre de un directorio temporal que la evocaba. Se anonimizó en LECCIONES y PRUEBAS ("carpeta de envío del programador, ámbito personal" + directorio temporal `/var/tmp`). Auditoría de cierre: grep de rutas personales, nombres, emails e IPs sobre todos los archivos versionables → limpio (P0.9/P0.10). El contenido de celdas reales (nombres, proveedores) nunca entró al repo: solo resúmenes numéricos agregados.

**Lección:** el cierre de una entrega incluye tres pasos que se hacen SIEMPRE juntos: auditoría de datos personales (grep + anonimización), verificación de cifras citadas contra la ejecución final, y prueba del camino recién tocado (la comparación ODS era código nuevo sin test).

## 31. Buscador avanzado: la URL real de CuitOnline, el captcha de Google con tiles y los parsers solo con evidencia (2026-08-12)

**Contexto:** módulo `buscador.py` (multi-motor con Playwright + recetas CUIT) solicitado por el programador; el caso de uso inicial era la búsqueda de CUIT de una empresa que CuitOnline no tenía indexada.

**Hallazgo 1 — la URL de búsqueda real de CuitOnline es `/search/{q}`, no `/buscar/{q}`:** `/buscar/...` devuelve 404 ("La página solicitada no existe"); el formulario de la home (`#searchBox`, sin action) lleva a `https://www.cuitonline.com/search/<término>`. Verificado en vivo: "permanencia salud" → "Su búsqueda no obtuvo resultados" (idéntico al reporte del usuario que documentaba "3 búsquedas sin resultados") y "ypf" → ASOC MUTUAL DEL PERSONAL YPF con CUIT 20-12345678-9 (selector `a.denominacion` + `span.cuit`).

**Hallazgo 2 — la página "sorry" de Google es reCAPTCHA v2 ESTÁNDAR con reto de tiles:** no basta el clic del checkbox del ancla (quedó sin marcar en 60 s de prueba): el bframe monta un reto 3×3 ("Select all images with a bus", verificado en vivo). Es exactamente el caso que resuelve el stack de `captcha_web` (detección imagen-completa + clics + VERIFY). El buscador resuelve el captcha en la MISMA sesión (cookies compartidas) reutilizando las funciones de captcha_web/captcha_ia (importadas, no duplicadas). El clic del ancla sí dispara el flujo (la URL pasa a `/sorry/index?continue=...`).

**Hallazgo 3 — el redirector de Bing (`bing.com/ck/a?u=a1<base64>`) esconde la URL real:** el parser debe decodificar el parámetro `u=` (base64url) para deduplicar correctamente; los `<cite>` solo muestran la URL amigable. Verificado: 2/2 destinos reales exactos.

**Hallazgo 4 — parsers solo con evidencia real (P0.2):** desde esta IP, Brave (slider), DDG (challenge "select all squares containing a duck"), Ecosia (turnstile), Startpage (conexión suspendida) y Mojeek (403) no muestran resultados jamás; Google tampoco. Solo Bing devuelve resultados (degradados/irrelevantes: "placeholder query" en dos consultas) y CuitOnline funciona. Los parsers de Google/DDG se escribieron sobre su estructura documentada y quedan marcados `parser_verificado: false` en el informe hasta confirmarse desde una red sin bloqueo — un parser "inventado" y no verificado sería peor que no tenerlo.

**Lección:** antes de escribir parsers de widgets de terceros hay que capturar el DOM real de la zona de resultados y del bloqueo (como en la lección 21 con el DOM del captcha): la URL real, los selectores y el tipo de captcha se descubren en vivo, y lo que no se puede verificar se reporta como tal en el propio informe de salida.


## 32. Suite de búsqueda empresarial: correos, judiciales, analizador CUIT y tabla del dominio (2026-08-13)

**Contexto:** sesión de extensión de `empresas.py` (correos y canales de contacto), módulos nuevos `judiciales.py`, `analizar_cuit.py` y `buscador_empresas.py` (TABLA-EMPRESAS-CUIT-TIPO), solicitados por el programador como "suite de clase mundial" con la regla del dominio TODO REAL ("No consta — pedir por escrito").

**Hallazgo 1 — el Boletín Oficial estuvo caído/timeout desde esta IP TODO el día (2026-08-13):** curl y Playwright daban `000`/`ERR_NETWORK_CHANGED` mientras el resto de hosts (cuitonline, bing, csjn) respondían normal. El flujo de navegación quedó implementado (home → `#rapidaInput` → CLIC en `#busquedaRapidaButton`, Enter no dispara el AJAX) y el parser de resultados marcado `parser_verificado: False` hasta capturar HTML real. **Desenlace (mismo día, horas después):** el BO volvió a responder y el parser se verificó contra el HTML real de la búsqueda "asistencia del sol" (39 resultados): estructura `a[href=/detalleAviso/]` que ENVUELVE `div.linea-aviso` con `p.item` (denominación) y `p.item-detalle` (detalle), sección en `h5.seccion-rubro`, CUITs extraídos. El primer intento de parser usaba una plantilla imaginada (`div.resultado`) que no matcheaba: la lección de la lección 31 se repitió — el parser se escribe contra el DOM real, no contra la estructura supuesta. **Además, el BO indexa por PALABRA:** los 39 avisos contienen "asistencia" como palabra común (no la empresa); el filtro de interés exige la frase completa o 2+ palabras significativas con límite de palabra (`\bsol\b` no matchea "solución", verificado: "sol" como substring marcaba 3 falsos positivos en resoluciones de "capital humano"). Regla: una fuente caída se reporta con su estado real en el informe, nunca se finge el parseo; y el filtro de coincidencia de nombres cortos se hace por palabra completa.

**Hallazgo 2 — NIC.AR oculta el titular del dominio tras un handle numérico:** el RDAP de asistenciadelsol.com.ar devolvió el registrant solo con `handle: 2737...` sin vcard (sin org ni fn). El titular "no_publicado" es un estado real de Argentina, no un fallo del parser. Además el registrador salía vacío (vcard sin org/fn): el parser ahora cae al handle (nicar). Los datos de contacto del titular (fn, email, tel) NUNCA se exponen (P0.9) aunque el whois los publique.

**Hallazgo 3 — el index de CuitOnline se degradó el 13/8:** "permanencia salud" y "asistencia del sol" (que el 12/8 devolvían resultados) daban "no obtuvo resultados"; "ypf" solo devolvía la mutual (CUIL). La ficha de EMPRESA (fecha inicio/actividad/empleados) quedó pendiente de verificación; la ficha de PERSONA FÍSICA se verificó en vivo (CUIT 27-12345678-9: "Empleador: No", "Impuestos activos: GANANCIAS/IVA/AUTONOMOS" → señal de Responsable Inscripto, Provincia/Localidad). La ficha publica el SEXO del titular: no se extrae (P0.9).

**Hallazgo 4 — CuitOnline ya no tiene sección "juicios":** el detalle hoy solo ofrece "Deudas" redirigiendo al formulario BCRA con clave fiscal. PJN ConsultaExpedientes es una SPA Angular ofuscada sin API pública; IUS no responde; CSJN `/buscador/documentos` no filtra por query (devuelve todo el catálogo). Fuentes reales de litigio: Boletín Oficial (caído) + dorks web. Todo declarado en las limitaciones del informe (la ausencia NO prueba nada).

**Hallazgo 5 — el título real de la ficha de CuitOnline incluye la localidad:** "NOMBRE (CUIT), Castelar (Buenos Aires) - Cuit Online": el primer regex de cabecera no toleraba el ", Localidad" y la razón social quedaba vacía (fila "No consta" con ficha cargada). Fix: `\s*,?.*?[-–]\s*Cuit Online` perezoso. Los tests sintéticos con títulos "limpios" no alcanzan: se valida con el HTML real capturado.

**Hallazgo 6 — la condición tributaria se INFIERE de los impuestos activos, no se inventa:** la ficha no dice "Responsable Inscripto" textualmente para este CUIT; la presencia de IVA+Ganancias+Autónomos es la señal (marcada "senal: impuestos activos"). Si aparece "monotributo" en la ficha, esa es la condición. La edad NO es derivable del DNI: solo banda de emisión con advertencia explícita (P1.10).

**Hallazgo 7 — auditoría P0.9/P0.10 antes del commit:** el fixture de test contenía el NOMBRE REAL del titular de un CUIT (pegado de la tabla del usuario) y el documento de investigación legal de empleadores pertenece al proyecto de cuidados, no a better-ocr: se movió a su proyecto. Sanitizado a "TITULAR PERSONA FISICA" y referencias quitadas de AGENTS/README. Lección: los datos que el programador pega en el chat pueden ser personales; al entrar al repo se anonimizan o se descartan.

**Lección:** una suite de búsqueda se construye pieza por pieza con verificación en vivo de cada fuente (P0.2), reportando lo no verificable en el propio informe (parser del BO, ficha de empresa), y antes del commit se auditan datos personales (nombres de titulares, sexo, rutas) aunque vengan del propio programador.

## 33. RNS offline: el padrón oficial de personas jurídicas sin red (2026-08-14)

**Contexto:** módulo nuevo `rns.py` (Registro Nacional de Sociedades OFFLINE) para el caso donde la búsqueda por nombre muere: los motores bloquean la IP (lección 20 hallazgo 8) o CuitOnline no indexa la empresa. Con el RNS la consulta es LOCAL (SQLite FTS5), sin red ni captchas.

**Hallazgo 1 — el padrón de personas jurídicas SÍ existe como dataset oficial:** PRUEBAS §12 concluyó que "datos.gob.ar solo tiene agregados de empleadores"; el padrón completo (sociedades + asociaciones sin fines de lucro, Ley 26.047) vive en el portal del MINISTERIO DE JUSTICIA (`datos.jus.gob.ar`, API CKAN), con ZIPs anuales 2019-2026. URLs y descargas de prueba verificadas en vivo el 2026-08-14. No era "no integrable": estaba en otro portal.

**Hallazgo 2 — formato real del CSV:** columnas `cuit, razon_social, tipo_societario, fecha_hora_contrato_social, numero_inscripcion, dom_fiscal_*, dom_legal_*` (22 comunes, idénticas en sociedades y asociaciones) más `actividad_*` SOLO en asociaciones; el CUIT se publica como 11 DÍGITOS SIN GUIONES ("30123456789"); el CSV de asociaciones repite filas por actividad y, para algunas entidades, trae una fila sin CUIT junto a otra con CUIT. Fix: normalización a `XX-XXXXXXXX-X`, dedup por identidad de la entidad (razón normalizada, tipo, fecha, localidad) y fusión priorizando la fila con CUIT.

**Hallazgo 3 — FTS5 y el prefijo de 3 letras:** `unicode61 remove_diacritics` normaliza acentos en ambos lados (buscar "geriatricos" matchea "GERIÁTRICOS"); el prefijo `*` se usa solo con palabras de 4+ letras: "mis" con prefijo matchearía "misionera" (ruido real del dataset: 'ASISTENCIA MISIONERA').

**Hallazgo 4 — descargas pesadas (P2.5):** los ZIP anuales pesan cientos de MB (sociedades 2026 ~897 MB; `--todos` 2019-2026 ~2.5 GB). El default baja solo sociedades+asociaciones 2026; la verificación de la lógica de búsqueda usa fixtures sintéticos con la CABECERA REAL del dataset, sin bajar 2.5 GB.

**Verificación:** 12 tests nuevos (`tests/test_rns.py`: normalización, sufijos anclados al final, query FTS5, dedup por actividad, fusión sin CUIT, CUIT sin guiones, búsqueda exacta/prefijo/sin acentos/sin coincidencia/sin base) + 3 de integración en `tests/test_empresas.py` (síntesis con CUIT y señal "registrada en el RNS", limitación "NO consta", aviso de base no indexada con la instrucción) + E2E con el CSV real de asociaciones (150.624 entidades, lección 34/PRUEBAS §13-14). Suite total medida tras la corrida real: **340/340 tests**. La empresa demo del usuario (INTEGRAR CUIDADOS S.R.L., CUIT 30-12345678-9) forma parte del fixture.

**Lección:** cuando una fuente web se cae o bloquea, buscar el dataset oficial descargable antes de pelear contra captchas; el formato real del dataset (CUIT sin guiones, filas por actividad, filas sin CUIT) solo se conoce leyendo los datos — los fixtures con la cabecera real lo fijan para los tests.

## 34. Wayback Machine: el historial del sitio como fuente sin bloqueos (2026-08-14)

**Contexto:** en el caso real "Asistencia Mis Abuelos" la búsqueda web murió (motores bloqueados, captcha no resuelto) y el sitio actual no publica CUIT ni razón social. La CDX API de web.archive.org respondió sin navegador ni captchas y reveló lo que la web actual oculta.

**Hallazgo 1 — la CDX API es una fuente de señales gratuita y sin bloqueos:** `https://web.archive.org/cdx/search/cdx?url=dominio&output=json&fl=timestamp,original,statuscode,mimetype,length&filter=statuscode:200&collapse=urlkey` devuelve el historial de capturas en JSON. Con `url=dominio` (sin `/*`) solo trae la home (rápido, para señales); con `url=dominio/*` trae todo el sitio (para recuperar). Verificado en vivo: asistenciamisabuelos.com → 112 capturas 2015-2025, el sitio existe hace 10 años (señal de actividad que las bases de empresas no tienen).

**Hallazgo 2 — las versiones viejas tienen lo que la web actual quitó:** la home de 2015 no tenía pie legal, pero `contacto.html` de 2015 expuso DOS correos (incluido uno con un segundo dominio de la marca "Mis Abuelos En Casa" que ninguna fuente actual conocía; correos no reproducidos, P0.9) y `single.html` de 2019 declaró la razón social "Asistencia Mis Abuelos". La recuperación usa `https://web.archive.org/web/{timestamp}id_/{original}` (el `id_` = contenido crudo sin el banner de archive.org, que rompería los extractores de HTML).

**Hallazgo 3 — los agregadores de CUIT alternativos NO existen:** wikicuit.com no resuelve DNS, cuits.com es una charcutería, buscardatos/buscarcuit bloquean o fallan. Verificado en vivo el 2026-08-14. CuitOnline es el único agregador scrapeable; las fuentes muertas no se integran (P0.2). La vía oficiosa completa es: RNS (personas jurídicas) + CuitOnline + Boletín Oficial + Wayback + RDAP.

**Hallazgo 4 — el RNS tiene filas sin CUIT junto a la misma entidad con CUIT:** se funden por identidad (razón normalizada, tipo, fecha de contrato, localidad) priorizando la fila con CUIT; el E2E real mostró el caso ("CENTRO DE JUBILADOS Y PENSIONADOS MIS ABUELOS", CUIT 30-98765432-2, una sola fila).

**Verificación:** 5 tests de Wayback en `test_empresas.py` con fixture REAL de la respuesta CDX + E2E en vivo (112 capturas, 2 correos históricos, razón social histórica, integración en síntesis). Suite: 340/340.

**Lección:** cuando la web actual está limpia de datos, el historial archivado es la fuente: los sitios viejos solían publicar pie legal con CUIT y correos de contacto reales.

## 35. Captcha en buscador.py: cooldown + reload de última oportunidad (2026-08-14)

**Contexto:** en el caso real el `--captcha` de `buscador.py` falló contra el reCAPTCHA de Google ("Google no resolvió el reCAPTCHA esta vez"). Revisión del orquestador (no del stack de captcha_web, que es el más medido del repo).

**Hallazgo 1 — buscador.py iba a 2 intentos, captcha_web a 3:** alineado a 3 (`--max-intentos-captcha` default 3) con cooldown de 2 s entre intentos (el reto siguiente de reCAPTCHA se renderiza pasado un instante; reintentar al instante desperdicia intentos).

**Hallazgo 2 — tras el fallo del reto, la recarga a veces limpia la sesión:** si los N intentos del reto fallan, se recarga la página y se re-evalúa si el ancla sigue: Google a veces deja pasar la sesión tras varios intentos + reload (sesión limpia). Es un fallback barato: no empeora el estado (ya era "captcha") y no toca captcha_web.py.

**Lección:** las mejoras de captcha se hacen en el orquestador con cambios pequeños y medibles; el stack interno no se toca sin campaña de medición (lecciones 20-27: los cambios de config no mejoraron la tasa del 43%).
