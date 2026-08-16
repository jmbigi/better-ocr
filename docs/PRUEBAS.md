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

## 3. Cascada de gráficos (set de 9 charts, ground truth propio)

| Chart | Fast path (PP-OCRv6) | Fallback ChartParsing |
|---|---|---|
| bar_2series (mpl) | 6/6 + 12/12 (~70 s, 1 GB) | — |
| bar_line_mixto (mpl) | rechaza (52.5 s) | 6/6 + 12/12 (~140 s, CPU forzada, lección 18) |
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

### 4.1 Batería 360° VLM con docbee (PP-DocBee-2B) en GPU (RTX 3070 8 GB, 2026-08-05)

docbee ya NO es pendiente: corrió completo en GPU con `scripts/bateria_360.py
--motor docbee --device cuda` (max_pixels 262144, ver lección 17).

| Test (función) | Qué evalúa | docbee GPU | gemma3:4b (CPU) | Comentario |
|---|---|---|---|---|
| ui_qa (QA de UI) | Campos y valores visibles (flight/gate/seat/name) | **4/4** (9.7 s) | 2/4 (4.8 s) | docbee es modelo de documento: lee la estructura mejor que gemma |
| interpretacion (rúbrica) | Tendencias por año, 3 frases | rúbrica (12.4 s) | rúbrica (2.9 s) | Salidas crudas en `/var/tmp/bateria360/` para rúbrica humana |
| valores (demo) | 12 valores incl. negativos del gráfico oficial | 3/12 (4.6 s) | **7/12** (1.6 s) | docbee penalizado: max_pixels 0.5M px (8 GB) pierde dígitos; a resolución nativa daba 6/12 |
| valores (pie) | 5 valores de un gráfico circular | **5/5** (3.5 s) | **5/5** (1.5 s) | Empate: ambos exactos (35.0, 25.5, 18.2, 13.3, 8.0) |
| objetos (frutas) | banana/apple/orange + conteo | 3/4 (3.2 s) | 3/4 (1.9 s) | Empate: ambos fallan las frutas pequeñas |
| objetos (personas) | Personas en foto (esperado 10) | **1/2** (1.4 s) | 0/2 (1.1 s) | docbee acierta la clase; gemma no llega a 10 |
| descripcion (rúbrica) | Descripción libre de una foto | rúbrica (204.6 s) | rúbrica (7.0 s) | docbee 30× más lento: genera textos largos token a token |
| documento (etiquetas) | Tipo/título/secciones/figuras de un paper | **2/2** (165.1 s) | 2/2 (7.1 s) | Empate en puntaje; gemma 23× más rápido |

Salidas crudas y reporte: `/var/tmp/bateria360/`.

**Conclusión:** docbee gana donde importa la lectura de documentos/UI (ui_qa
4/4 y etiquetas de doc 2/2); gemma domina velocidad (10-30×) y valores — pero
con ventaja injusta por el límite de resolución del docbee en 8 GB (a
resolución nativa docbee da 6/12). En producción: gemma3:4b si importa
rapidez; docbee en GPU solo si la resolución no es crítica o hay >12 GB de
VRAM.

## 5. Servidor HTTP (E2E real)

- `POST /chart` demo → 200, 6 filas, markdown + CSV correctos (inferencia real ~180 s).
- `POST /vision` (modo texto) → 35 líneas de una tarjeta de embarque en 61 s.
- Cierre limpio por SIGTERM; auto-cierre por inactividad verificado (lección 7).

## 7. Servicio de captcha (reCAPTCHA v2 con el stack local, 2026-08-07)

Piezas puras verificadas por tests (sin navegador ni motores):

| Pieza | Cobertura | Evidencia |
|---|---|---|
| Parser de instrucción | prefijos, artículos, conectores, plurales irregulares, texto pegado sin espacio ("traffic lightsIf there are none..." → "traffic light") | 10 tests |
| Geometría de cuadrícula | celdas n×n con margen interno (excluye bordes), upscale 2× LANCZOS | 5 tests |
| Decisor por celda | umbral 0.45 clase objetivo / 0.6 resto; celdas sin detección → "inciertas" (no se descartan) | 8 tests |
| Demo sintética `--local` | determinista (misma semilla → mismo PNG), veredicto OK en 3×3 y 4×4 | 4 tests + CLI real |
| Orquestador (parte pura) | n_desde_tiles (9→3, 16→4, resto None), índice→(fila,col) | 3 tests |
| Orquestador E2E local | flujo completo de Playwright contra una página falsa que replica el DOM de reCAPTCHA (ancla en iframe recaptcha, reto en bframe, tiles que registran clics, VERIFY que marca el ancla): instrucción→selección→clics JS→veredicto "ok" | 2 tests de integración (4.6 s) |
| Reintento tras error/re-render (E2E) | VERIFY fallido en el 1er intento (error + replaceimage + instrucción cambia a "select all cars") → el orquestador reintenta y el 2º VERIFY limpia el error y triunfa; clics [1,6] en 2 intentos | 1 test (10.4 s los 3) |
| Camino SKIP (E2E) | instrucción sin clase ("Select all images" → None): se pulsa SKIP (clic real primero, JS como fallback) sin tiles, y el resultado distingue camino "skip"/"tiles" con ok=True | 1 test |
| Fallback OCR de instrucción | sin `div.rc-imageselect-desc`, la instrucción se obtiene por OCR (worker PP-OCRv6 inyectable) y el flujo sigue completo | cubierto por el 2º E2E |
| Pasada offline con RT-DETR real | `captcha_web.py --offline` sobre la demo sintética con el worker real (CPU forzada, lección 18): 2/2 pasadas idénticas — 5 celdas detectadas, 0 buses (correcto: las figuras sintéticas no son buses) | 2 ejecuciones reales |
| Umbral adaptativo por tamaño | `umbral_objetivo_para`: 0.45 en 3×3, 0.30 en 4×4 (lección 20 hallazgo 4: motos reales 0.24-0.28); configurable por `--umbral-objetivo`; `resolver_offline` reporta scores por celda (P0.1) | 2 tests |
| Fallback VLM cableado | celdas sin detección COCO re-evaluadas con pregunta binaria por celda a ollama (gemma3:4b, arrancado bajo demanda); parseo si/no tolerante; `--vlm-fallback [--vlm-modelo]` — cierra el pendiente de clases no-COCO (crosswalks/stairs) de la lección 20 | 6 tests (ollama simulado); validación en vivo pendiente |
| Variante "click skip" | parser devuelve None ("If there are no crosswalks, click skip") y el orquestador deja pasar la guarda de detección vacía SOLO para la variante none — la guarda hacía `continue` antes del camino SKIP dejando el reto clavado (bug real encontrado por el E2E) | 3 tests parser + 1 E2E (camino skip, 0 tiles) |
| Fallback VLM en modo offline | `--offline --vlm-fallback`: worker vacío (clase no-COCO) dispara el hook con la clase parseada, sin navegador — permite afinar el VLM sobre cuadrículas guardadas | 2 tests (worker y VLM simulados) |
| Fallback VLM en vivo (ollama) | primera validación real: arranque bajo demanda OK, pregunta binaria por celda, ~1 s/celda con modelo en caché, parseo si/no OK. **Hallazgo: gemma3:4b dice "No" a TODAS las figuras sintéticas de la demo** (rectángulos abstractos: recall 0/3 en buses) — la demo sintética no sirve para medir el VLM; los tiles reales requieren la pasada en vivo o cuadrículas guardadas | ejecución real 2026-08-07 (ollama detenido tras la prueba, estado restaurado) |
| Confirmación VLM de dos etapas | patrón DDG validado en vivo por el programador (RT-DETR 4 "birds" → VLM 3 ducks): los candidatos de la clase objetivo del worker se confirman/descartan con el VLM binario por tile; sin detecciones (no-COCO) el VLM cubre todas las celdas. `_aplicar_fallback_vlm` compartido por real y offline | 5 tests unitarios + 1 E2E (falso positivo descartado) |
| Contrato API ollama | payload de `/api/generate` blindado (modelo, prompt binario, imagen base64, temperatura 0, sin stream) | 1 test |
| Pasada offline sobre reto REAL | cuadrícula real 3×3 del programador (`reto_3x3_i1.png`): RT-DETR ve 8/9 celdas con scores altos (0.6-0.9: motorcycles, cars, bus, truck, traffic light); con instrucción plausible "select all motorcycles" el decisor selecciona (0,0),(0,1) y deja (2,1) incierta. **Hallazgo: i1 e i2 son el MISMO reto** (detecciones idénticas — confirma lección 20 hallazgo 2: grid clavado entre intentos). Instrucción real no verificada (sin resultado.json): la pasada demuestra la herramienta, no la precisión del reto | 2 ejecuciones reales |
| Selectores validados contra DOM real | el DOM del reto guardado por el programador (`/tmp/opencode/bframe_reto2.html`) contiene TODOS los selectores del orquestador: `.rc-imageselect-desc` (con la variante real `rc-imageselect-desc-no-canonical`), `td.rc-imageselect-tile`, `table.rc-imageselect-table`, `rc-button-default`, `.rc-imageselect-payload`; el otro archivo (`bframe_reto.html`) es el bootstrap de api2 (`recaptcha-token`, sin ancla renderizada aún) — confirma la detección del frame por "recaptcha" en la URL. El ancla (`#recaptcha-anchor`) queda validado por las ejecuciones en vivo del programador (no capturado en DOM) | verificación estática 2026-08-07 |
| Fix selector `desc-no-canonical` | el DOM real usa `rc-imageselect-desc-no-canonical` (no `rc-imageselect-desc`): el grep por substring daba falso positivo y `leer_instruccion` devolvía "" (flujo caía a SKIP — explica parte del "sin éxito" en vivo). Selector ampliado + E2E con la clase real | 1 E2E (129/129) |
| OCR lee el desc real + parser multi-idioma | el desc real del DOM guardado está en español ("Selecciona todas las imágenes con escaleras"): PP-OCRv6 lo lee exacto (tildes incluidas, renderizado 352px/16px+28px), pero el parser inglés devolvía basura (→ selección vacía → VERIFY ignorado en silencio). Guard de clases COCO multi-palabra: ahora devuelve None (→ SKIP); las clases multi-palabra reales (traffic light, fire hydrant, stop sign, parking meter) siguen parseando | 7 tests (131/131) |
| **Primer veredicto "ok" EN VIVO** | demo oficial de Google, 2026-08-07 13:43: instrucción del DOM "Select all images with cars" (con salto de línea, normalizada por el parser), 4 celdas seleccionadas por RT-DETR (scores 0.89/0.87/0.67/0.90), VERIFY → checkbox ancla marcado → **ok al intento 1 en 24.9 s** | `resultado.json` + `intentos.json` + captura guardados en `/var/tmp/captcha_real/`; sin procesos residuales |
| **Repetibilidad en vivo (n=8)** | 2026-08-07, 8 ejecuciones contra la demo oficial: **5/8 ok (62.5%)** con RT-DETR solo. Ok: car ×2, bus, motorcycle, car+VLM. Fallos con causa conocida: (1) "mountains or hills" — parser (bug ya corregido: clases "X or Y"); (2) "traffic lights" y "fire hydrant" — selección hecha (3 celdas) pero rechazada (precisión/adversario); (3) "crosswalks" — clase no-COCO sin detecciones COCO → sin clics (conservador correcto; `--vlm-fallback` la resolvería). Los intentos.json de cada run en `/var/tmp/captcha_real/r{1..6}/` y `vlm/` | 8 ejecuciones reales |

Total: 65 tests nuevos (suite completa 133/133 OK).
| **VLM fallback EN VIVO (reCAPTCHA)** | demo oficial, 2026-08-07: `--vlm-fallback` → **ok al intento 1 en 31.7 s** (clase car, 4 celdas): la confirmación de dos etapas corrió sobre los candidatos del worker (4 celdas, ~1.7 s/celda con gemma3:4b en caché; +7 s vs sin VLM) y los confirmó todos; el reto pasó. El caso "mountains or hills" (no-COCO con detección vacía) queda pendiente de medir: el VLM lo cubriría con "Does this image contain mountains or hills?" por tile | `intentos.json` en `/var/tmp/captcha_real/vlm/`; ollama detenido tras la prueba (estado restaurado) |
| **Pasada de recall VLM EN VIVO** | el 4×4 de traffic lights que falló con RT-DETR solo (r5, 3 celdas) se **resolvió con `--vlm-fallback`** (v2, 47.7 s): la pasada de recall encontró traffic lights en 2 celdas que RT-DETR perdió (score 1.0 del VLM, 13/16 celdas vacías en el 4×4) — 2/3 celdas seleccionadas vinieron del VLM. Fix adicional: sin candidatos del objetivo, el VLM cubre TODAS las celdas y fusiona (caso "mountains or hills" con bicycles/cars detectados, v3) | `intentos.json` v2/v3; agregado n=11 en vivo: 7/11 ok (63.6%) |
| **Límite medido: recall pass sobre-agrega en clases comunes** | n=16 en vivo, 8 ok (50%): 2/2 runs de cars con una 5ª celda añadida SOLO por el VLM (score 1.0 en celda vacía, v4 y v8) fueron rechazados; los 3 runs de cars con 4 celdas RT-DETR pasaron. El recall pass ayuda en clases difíciles (traffic lights 4×4) pero añade falsos positivos en comunes → **configurable con `--sin-vlm-recall`**. Dict de fallo ahora incluye `seleccion` (el análisis por `intentos.json` mostraba selecciones reales que el resumen final no) | intentos.json v4/v8/v9/v6 |
| **Tendencia del recall pass por clase (n=15 analizados)** | empate 2-2 en los runs VLM: ayudó en traffic light 4×4 (v2, 2 celdas VLM, ok) y bicycle (v7, 2 celdas VLM, ok); perjudicó en car 3×3 (v4/v8, 1 celda VLM errónea, rechazado). gemma3:4b acierta clases específicas (traffic light, bicycle) y sobre-dice "sí" en clases comunes de escenas de calle (car). Guía de tuning: recall ON para clases específicas/tiles 4×4; `--sin-vlm-recall` para clases comunes en 3×3 | análisis de los intentos.json existentes, 0 ejecuciones nuevas |
| **Corpus real del parser** | las 12 instrucciones únicas vistas en los 17 runs en vivo quedan como test de regresión (cars, bus, motorcycles, bicycles, traffic lights, crosswalks, fire hydrant, mountains or hills, variantes "If there are none, click skip" y "Click verify once there are none left") + variante con salto de línea del DOM | 1 test (13 aserciones) |
| **Batch t1-t6 (6/6 ok con VLM)** | 2026-08-07, 6 ejecuciones seguidas con `--vlm-fallback` TODAS resueltas: motorcycle 4×4 (3 celdas VLM correctas), bicycle ×2, bus, traffic light ×2 (2 celdas VLM correctas). Agregado con VLM: 11/16 (68.75%); total 23 runs, 15 ok (65.2%) | intentos.json t1-t6 |
| **Política de recall por clase (datos en vivo)** | 23 runs analizados: en `car` 2/2 fallos coincidieron con una celda VLM añadida en celda vacía (v4/v8) y las 3 victorias fueron sin celdas VLM → `SIN_RECALL_CLASES={"car"}` (recall excluido, confirmación de candidatos intacta); en motorcycle/bicycle/traffic light las celdas VLM fueron correctas (t1/t4/t6). Sigue disponible `--sin-vlm-recall` global | 1 test (137/137) |
| **Default conservador: adición VLM opt-in** | batch p1-p6 (2/6): la sobre-selección del VLM se confirma en VARIAS clases — p2-i3 traffic light (5 celdas VLM → rechazo), p4-i1 crosswalk no-COCO (6 celdas VLM → rechazo), p4-i2 fire hydrant (5 → rechazo), p6-i1 motorcycle (2 → rechazo). Agregado con celdas VLM: **5 ok vs 7 fallos** → la pasada de ADICIÓN (recall + cobertura no-COCO) pasa a **opt-in (`--vlm-recall`)**; la confirmación de candidatos (solo descarta) se mantiene siempre con `--vlm-fallback`. `SIN_RECALL_CLASES={"car"}` sigue activa cuando `--vlm-recall` está encendido | 29 runs acumulados; 2 tests nuevos (138/138) |
| **Validación del default conservador (batch c1-c6, 3/6)** | cars 2/2 ok con la política (c4: 3 celdas, c6: 4 celdas tras re-render desde motorcycle 4×4 sub-detectado a 1 celda ×2 rechazos); fallos esperados: no-COCO sin `--vlm-recall` (c2 mountains or hills, c3 crosswalk — opt-in por diseño) y sub-detección (c1 bus, 2 celdas) | intentos.json c1-c6 |
| **Cierre de campaña: la tasa es ~50-60% independiente de la config (35 runs, 18 ok = 51.4%)** | RT-DETR solo: 4/7 (57%); VLM con recall: 13/22 (59%); VLM conservador: 3/6 (50%). La configuración cambia el MODO de fallo (qué retos fallan: no-COCO, sub-detección, sobre-adición), no la tasa — el límite fundamental es la precisión del detector/VLM sobre tiles pequeños y el rechazo adversario (consistente con la medición histórica del programador ~50-70%) | 35 ejecuciones reales 2026-08-07 |
| **Corpus de fallos para análisis avanzado** | `--archivo-fallos DIR` guarda cada intento fallido como `caso_<ts>_i<N>.json` (instrucción, decisión, scores por celda, captura vinculada) y `--listar-fallos DIR` lo resume en tabla. **Poblado con el histórico: 58 casos con capturas** en `/var/tmp/captcha_fallos/` (runs r1..c6, d1-d6) — permite re-evaluar configuraciones (umbral, VLM docbee/ollama, recall) sin nuevas ejecuciones en vivo | 2 tests + 1 E2E (143/143) |
| **docbee como VLM de confirmación** | experimento sobre tiles reales: docbee coincide EXACTAMENTE con RT-DETR (traffic light 4×4: docbee 2 celdas == RT-DETR vs gemma 5 sin solapamiento; crosswalk: gemma sobre-seleccionó 2). `--vlm-fallback docbee` implementado (GPU, env lección 17); en el batch d1-d6 su conservadurismo dejó selecciones menores (car sel 0-1) — sin mejora neta aún | 3 tests + 6 runs d |
| **Replay del corpus: comparación de modelos (58 fallos reales)** | `scripts/replay_fallos.py` re-evalúa los casos guardados (detecciones RT-DETR + capturas) con distintas configuraciones sin ejecuciones en vivo. Resultado (selecciones "plausibles" vs referencia por clase): **solo RT-DETR 23/58, +docbee 21, +gemma3:4b 22, +qwen2.5vl:7b 19** — ningún modelo de confirmación mejora el baseline (la confirmación solo quita candidatos); los fallos del corpus son ~60% **sub-selección** (recall de RT-DETR en tiles pequeños), el modo que la config no puede arreglar. qwen2.5vl:7b es el más agresivo descartando (peor en este corpus) | replay ejecutado 2026-08-07; qwen2.5vl:7b descargado (ollama pull) |
| **Upscale 3× NO recupera objetos (resultado negativo)** | 9 casos de sub-selección (uno por clase×tamaño) re-detectados a 3× vs 2×: **detecciones idénticas** en todos — el límite es la capacidad de RT-DETR-L para objetos pequeños, no la resolución interpolada. El siguiente palo sería un detector mayor (RT-DETR-H, no en caché: requiere descarga ~500 MB + más cómputo) | experimento offline 2026-08-07 (2 cargas del modelo, 9 capturas) |
| **Detección imagen-completa EN VIVO (fix de código muerto)** | el default `detectar_lote = detectar_batch_worker` en resolver_web dejaba MUERTO el modo imagen-completa (los runs anteriores usaron siempre per-celda). Corregido: la detección sobre la cuadrícula completa corre en subproceso del venv (WORKER_GRID) y funciona desde el python del sistema. **Batch h: 4/6 ok** (traffic light ×3, motorcycle — 10 s/run, 3× más rápido que per-celda) | intentos h1-h6 |
| **Imagen-completa en el corpus (58 fallos)** | plausibles **22 → 26** con la imagen completa; 7 casos de sub-selección recuperaron objetos (car 1→3, bus 0→2, d3_i2 ahora selecciona 3 en offline) | replay 2026-08-07 |
| **RT-DETR-H: mejor recall de objetos pequeños (corpus 26 → 32)** | `--modelo-detector RT-DETR-H` cableado (modo_objetos_lote + WORKER_GRID con modelo por argv): corpus de 58 fallos, plausibles **26 (L) → 32 (H)** (+23%). En vivo (batch j, 1/6): **primer fire hydrant resuelto de la historia** (j2, sel 4, ok) — clase que falló 4+ veces; el resto dentro del ruido histórico (~50%, selecciones reales de tamaño típico rechazadas: car sel 3, bus sel 3-4). Tasa en vivo requiere muestra mayor para confirmar la ganancia del corpus | 146/146 tests; intentos j1-j6 |
| **RT-DETR-H EN VIVO: la ganancia del corpus NO se traslada a la tasa (batch k, 2/10 + 2 crash)** | agregado H en vivo: **3/14 (21%)** vs L imagen-completa **4/6 (67%)** en las muestras actuales. El tamaño "plausible" del corpus no predice el éxito en vivo: H selecciona más celdas (tamaños correctos pero con más celdas erróneas → más rechazos). **L se mantiene como default**; H queda disponible con `--modelo-detector` (útil si se confirma con más datos) | intentos j1-j6, k1-k10 (k2/k3 crashearon sin guardar) |

Total: 76 tests nuevos (suite completa 146/146 OK).

Pendientes (requieren ejecución en vivo o VLM libre):
- Validación real contra la demo oficial de Google
  (`--url https://www.google.com/recaptcha/api2/demo`): loop completo
  verificado en vivo (checkbox → reto → instrucción DOM/OCR → tiles →
  VERIFY → feedback `replaceimage` → reintento; ver lección 19). Aún sin
  veredicto "ok": la precisión real de RT-DETR sobre tiles pequeños queda
  por debajo del adversario (4×4 motos: celdas reales a 0.24-0.28 bajo el
  umbral; 3×3 bicicletas detectadas y aun así rechazado).
- Pasada por celda offline sobre cuadrículas reales guardadas: 4×4 motos —
  (1,1) 0.85 y (2,2) 0.59 seleccionadas, moto real en (1,2) a 0.24-0.28
  perdida (selección incompleta → rechazo); 3×3 crosswalks — sin detecciones
  (clase no-COCO), VERIFY vacío ignorado por Google (imagen intacta).
- Fallback VLM para clases no-COCO (crosswalks, stairs...): hook reservado
  (`fallback_vlm=`), requiere un VLM libre (regla: un VLM por máquina).

## 8. Alternativa Rust (deepseek-ocr.rs, q4k)

- 12/12 + 6/6 exacto, carga 8 s, inferencia ~1200 s (0.4-1.4 tok/s en CPU).
- Build: fallo por tmpfs (Bus error) resuelto con CARGO_TARGET_DIR en disco.
- RAM no medida (monitor del proceso no fiable con `&`); sin OOM con swap.

## 9. Revisión de formato y presentación (`revision.py`, 2026-08-12)

Dos capas: análisis determinista (openpyxl) + Visión IA 360° (VLM local sobre
render del libro). Suite completa: **193/193 tests** (16 checks + comparación +
parser de rúbrica + endpoint del servidor).

### 9.1 Planillas sintéticas (`scripts/generar_planillas.py`)

| Planilla | Resultado | Evidencia |
|---|---|---|
| `correcta.xlsx` | **0 hallazgos** (error 0, warning 0, info 0) | CLI real |
| `con_fallos.xlsx` | **12 errores + 51 warnings + 8 info**; detecta 14 checks: encabezados sin negrita (8), bordes (44), anchos (1), formato numérico (3: "120" como texto + General en flotantes), filtros (1), celdas vacías (1), celdas mezcladas (1), fila oculta (1), columna oculta (1), #DIV/0! (1), encabezado duplicado (1), texto desbordado (6), estilos inconsistentes (1), islas de datos (1) | CLI real |
| `v1.xlsx` vs `v2.xlsx` | 6 diferencias: dimensión (A1:B4 → A1:B5), encabezado B1, 2 valores, fila nueva (2 celdas) | `--comparar` |

### 9.2 Datos reales del programador (copia previa, original intacto)

Planillas de la carpeta de envío del programador (ámbito personal, copiadas
con `cp -p` a un directorio temporal `/var/tmp`; los reportes JSON quedan
fuera del repo; contenido de celdas no difundido, P0.9):

| Planilla | Determinista | Visión IA 360 (docbee GPU) |
|---|---|---|
| PRESUPUESTO...xlsx | 221 warnings: 219 celdas sin bordes, sin auto-filtro, 1 ancho fuera de rango | 1 página, rúbrica 8/9/7/6/8/7 (legibilidad/coherencia/color/estructura/formato/presentación), 33.8 s |
| PROVEEDORES...xlsx | 20 warnings + 6 info: 18 sin bordes, sin filtro, 1 ancho, **6 textos probablemente desbordados** (C3, E6, E8, C12...) | 1 página, rúbrica 10/9/8/7, **7 líneas no conformes** (docbee inventó dimensiones repetidas) |

**Hallazgo de diseño (lección):** la capa determinista ve el XML exacto
(219 celdas sin bordes) mientras el VLM evalúa la imagen renderizada y puede
decir "bordes uniformes" (9/10). No es una contradicción: la determinista
manda para hechos objetivos, la visual para percepción de diseño.

**Hallazgo del parser de rúbrica:** docbee respondió en dos formatos distintos
en ejecuciones casi idénticas — "dimensión: nota/10 | comentario" y
"10/10: dimensión | comentario" — y una vez con dimensiones inventadas
repetidas ("Diseño de la planilla", "Utilidad..."). El parser acepta ambos
órdenes, normaliza a prefijos canónicos de la rúbrica y cuenta el resto como
`no_conformes` (7 en la segunda ejecución real).

### 9.3 Fase 2: ods, docx y pdf (2026-08-12, mismos sintéticos)

Suite completa tras la fase 2: **211/211 tests** (16 checks xlsx + 8 docx + 5
pdf + comparación + rúbrica + endpoint).

| Formato | Fixture correcto | Fixture con fallos |
|---|---|---|
| `ods` (soffice normaliza→xlsx) | `correcta.ods`: **0/0/0**; integridad de conversión verificada con odfpy (venv) y "no verificada con aviso" sin él (python del sistema) | — |
| `docx` (python-docx) | `documento_correcto.docx`: **0 errores, 0 warnings, 1 info** (sin encabezado/pie, informativo) | `documento_con_fallos.docx`: **8 warnings + 2 info** — título manual con negrita 18pt, 3 fuentes mezcladas, márgenes 0.4 cm, 3 numeraciones manuales, racha de 3 párrafos vacíos, tabla sin estilo ("Normal Table" no dibuja bordes) |
| `pdf` (pypdfium2) | `documento.pdf` (convertido de docx): **0/0/0**, 1 página, 331 caracteres | checks probados por unidad (páginas vacías/escasas, sin capa de texto, rotación, tamaños) |

**Visión IA 360° sobre PDF (render nativo sin soffice):** `documento.pdf` con
docbee GPU → 6/6 dimensiones parseadas (8/9/7/6/8/7), 0 no conformes, 28.3 s.

**Hallazgos de la fase 2:** (1) `reglas=None` no se saneaba en los revisores
docx/pdf (TypeError en la API pública; los revisores ahora sanean como
`revisar_planilla`). (2) python-docx asigna "Normal Table" por defecto a las
tablas: un check que solo buscara estilos `None`/sin "grid" dejaba pasar el
default sin bordes — ahora "Normal Table" se reporta explícitamente. (3) El
fixture ODS inicial se generó con `pandas.to_excel(engine="odf")` que NO aplica
estilos (falsos errores de encabezado); se genera vía soffice desde el xlsx
correcto para conservar estilos. (4) la integridad ODS necesita odfpy: sin él
queda "no verificada" con aviso (la revisión sigue, honesta).

### 9.4 Servidor

- `POST /revision` con `--sin-modelo`: 200 con resumen de hallazgos (~0.2 s);
  `/health` informa `"modelo": "no cargado (--sin-modelo)"`; `/chart` → **503**
  (sin el VLM cargado, no se gasta RAM). Verificado en vivo 2026-08-12.
- 6 tests nuevos del endpoint (200/400 por archivo inexistente y por clave
  `archivo`/`image`/reglas inexistentes, 503, health sin modelo).

## 10. Buscador avanzado (`buscador.py`, 2026-08-12)

Multi-motor con Playwright + detección de bloqueos/captchas + recetas CUIT.
Suite: **25 tests nuevos** (parsers, normalización, dedupe/ranking, bloqueos).

### 10.1 Reconocimiento de motores (HTML real, una visita por motor, esta IP)

| Motor | Estado observado | Evidencia |
|---|---|---|
| Google | página "sorry" = **reCAPTCHA v2 estándar** (iframe `recaptcha/enterprise/anchor` + bframe; clic del ancla dispara el flujo; reto de tiles 3×3 "Select all images with a bus" verificado en vivo) | probe 2026-08-12 |
| Bing | responde con resultados reales (10 `li.b_algo`) pero **degradados/irrelevantes** desde esta IP ("placeholder query" en dos consultas distintas) | 2 capturas |
| Brave | **slider** anti-bot ("Arrastra el control deslizante") — no resoluble por el stack | captura |
| DDG (html) | **challenge propio** ("Select all squares containing a duck") — no es reCAPTCHA | captura |
| Ecosia | **turnstile** (Cloudflare) + "Un momento…" | captura |
| Startpage | **conexión suspendida** (bloqueo de red, no captcha) | captura |
| Mojeek | **403 Forbidden** | captura |
| CuitOnline | búsqueda real funciona: `search/{q}` (la URL `/buscar/` da 404); "permanencia salud" → "Su búsqueda no obtuvo resultados" (coincide con el reporte del usuario); "ypf" → ASOC MUTUAL DEL PERSONAL YPF, CUIT 20-12345678-9 extraído | 3 capturas |
| Dateas | **404 "Página no encontrada"** en `consulta_cuit?q=` (y variantes) — caída verificada, se reporta | 2 capturas |

### 10.2 Parsers

- **Bing** (verificado con HTML real): `li.b_algo` → `h2 a` + `p.b_lineclamp`; la URL del redirector `bing.com/ck/a?u=a1<base64>` se decodifica al destino real (2/2 URLs exactas en fixture real).
- **CuitOnline** (verificado con HTML real): `div.hit` → `a.denominacion` (razón social) + `span.cuit` (XX-XXXXXXXX-X); caso vacío detectado sin resultados.
- **Google y DDG**: parsers sobre su estructura documentada, marcados `parser_verificado: false` (esta IP no muestra resultados reales); se confirmarán en una IP sin bloqueo.
- **Brave/Mojeek/Ecosia/Startpage**: solo detección de bloqueo (sin parser de resultados: no se pudo verificar su DOM desde aquí, P0.2).

### 10.3 Detección de bloqueos (25 tests + verificación contra HTML real)

`detectar_bloqueo`/`detectar_recaptcha` clasifican cada motor: captcha (Google, resoluble), captcha_slider (Brave), challenge_ddg, turnstile (Ecosia), suspendida (Startpage), http_403 (Mojeek), sin_resultados (CuitOnline), pagina_no_encontrada (Dateas). Verificado 6/6 contra los HTML capturados + 8 tests con fixtures.

### 10.4 E2E real (CLI, 2026-08-12)

`python3 buscador.py "Permanencia Salud" --motores bing --recetas cuit --salida /tmp/opencode/busq_smoke2` → bing ok (10 resultados, 3.4 s), cuitonline ok sin_resultados (3.9 s), dateas bloqueado 404 (3.5 s), JSON con `resultado.json` + HTML crudos por motor/receta (P0.1), ranking multi-motor funcional. El flujo de resolución de captcha quedó verificado en vivo a nivel de mecanismo (ancla → bframe con reto de tiles); la tasa de éxito del stack ya está medida en el §7 (~50-60%).

## 11. Búsqueda de empresas (`empresas.py`, 2026-08-12)

CLI que verifica una empresa reutilizando el motor de `buscador.py`. Suite: **16 tests nuevos** (variantes de nombre, extracción de CUIT/razón social, parseo RDAP).

### 11.1 Ejecución real (4 empresas, salidas en `/tmp/opencode/emp_*`)

| Empresa | CuitOnline (variantes) | Dateas | Web oficial | RDAP | Señales | CUIT |
|---|---|---|---|---|---|---|
| Permanencia Salud Srl (`--sitio permanencia.com.ar`) | 2 variantes: sin resultados | 404 | activa ("Cuidado y Acompañamiento para Adultos Mayores"), sin CUIT en HTML, sin razón social en pie | creado 2007-11-07 (RDAP nic.ar, sin www) | web activa, dominio registrado | **NO ENCONTRADO** |
| Asistencia del Sol | sin resultados | 404 | (sin --sitio) | (sin --sitio) | solo Bing (degradado) | NO ENCONTRADO |
| Cuidarte Siempre | sin resultados | 404 | (sin --sitio) | (sin --sitio) | solo Bing (degradado) | NO ENCONTRADO |
| Asistencia Mis Abuelos | sin resultados | 404 | (sin --sitio) | (sin --sitio) | solo Bing (degradado) | NO ENCONTRADO |

Detalles verificados:
- **Sufijos legales anclados al final**: "Permanencia Salud Srl" → variantes ["Permanencia Salud Srl", "Permanencia Salud"]; "Sa Salud Srl" → "Sa Salud" (sin el ancla, "Sa" inicial se perdía — bug real encontrado por tests).
- **Sitio oficial**: extrae CUITs (regex AFIP) y razón social del pie "© 2025 <razón social>" (recortando "Todos los derechos reservados."); con reintentos de navegación (la red es intermitente: ERR_NETWORK_CHANGED y timeouts transitorios observados 2 veces en la misma sesión).
- **RDAP de NIC.AR**: solo registrador y fechas; el vcard del titular NUNCA se incluye (P0.9, cubierto por test con fixture que incluye un titular falso).
- **Juicios**: 4 dorks por empresa ("X" juicio/fallo/demanda/sentencia) sobre Bing; desde esta IP Bing devuelve resultados degradados, por lo que el informe deja explícita la limitación (ausencia ≠ inexistencia; chequeo real = antecedentes con CUIT).
- **Robustez**: si CuitOnline/Dateas fallan por red (ERR_NETWORK_CHANGED), se reintentan hasta 3 veces antes de reportar error; cada paso es independiente (el fallo de uno no corta el resto).

## Limitaciones verificadas

- Scatter: no soportado por ChartParsing (alucina).
- Gráfico mixto barras+línea (`bar_line_mixto`): el fast path lo rechaza (esperado: la línea mezcla las columnas del emparejado geométrico) y el fallback VLM lo resuelve **exacto** — 6/6 etiquetas + 12/12 valores en ~140 s con `CUDA_VISIBLE_DEVICES=""` (lección 18: sin forzar CPU, el VLM aborta con SIGABRT por el cudnn 9.1 del pyenv en máquinas con GPU visible).
- Captions/pinturas: requieren VLM ≥ 0.9B (~9 GB) → OOM en equipos de 7 GB.
- qwen2.5vl:7b en CPU: deja la RAM del host crítica; descargar con `keep_alive=0`.
- Portabilidad: solo probado en Linux/x86 (CPU 7.7 GB; GPU RTX 3070 8 GB validada en lección 17).
- docbee en GPU 8 GB: requiere max_pixels ≤ 0.5M px (OOM a resolución nativa); flash attention exige cu_seqlens int32 (paddle 3.3.1 GPU promueve a int64); LD_LIBRARY_PATH del host no debe sombrear nvidia-cudnn del venv (lección 17).
- Revisión (`revision.py`): la estimación de texto desbordado en xlsx es heurística (no hay motor de layout en openpyxl); la rúbrica VLM es perceptual y puede contradecir al análisis determinista (manda el determinista); docbee en GPU requiere el env de la lección 17 y `max_pixels` limitado; la integridad de la conversión ods→xlsx requiere odfpy (sin él, aviso "no verificada"); los estilos ODS se normalizan vía LibreOffice (no son XML de Excel).
- Buscador (`buscador.py`): los parsers de Google y DDG no están verificados en vivo (esta IP está bloqueada en esos motores; quedan marcados `parser_verificado: false` hasta confirmarlos desde otra red); Bing devuelve resultados degradados/irrelevantes desde esta IP (el parser funciona, la calidad del origen no); la resolución de captcha hereda la tasa medida del stack (~50-60%); Dateas está caído (404 verificado) y la receta solo lo reporta; AFIP/ARCA no permite búsqueda pública por nombre (solo por CUIT, con captcha propio) — no incluida.
- Empresas (`empresas.py`): el CUIT no encontrado en CuitOnline NO prueba que la razón social no exista en AFIP (puede no estar indexada o diferir el nombre legal) — el informe lo dice explícitamente; los dorks de juicios corren sobre Bing (degradado en esta IP) y la ausencia no prueba nada (limitación honesta del §11); el RDAP solo aporta registrador/fechas (sin titular, P0.9); la razón social del pie de web se extrae solo si el footer tiene el formato "© año <razón social>" (común pero no universal).

## 12. Suite de búsqueda empresarial (correos, judiciales, analizador CUIT, tabla — 2026-08-13)

**Corridas reales en vivo (evidencia, P0.1):**

- `empresas.py "Asistencia del Sol" --sitio www.asistenciadelsol.com.ar --salida /var/tmp/empresas_asol` (12/8):
  web oficial activa, **sin correos publicados** (landing SwipePages), canales detectados: WhatsApp 5491171212222 e Instagram/Facebook (el píxel `facebook.com/tr` se excluye — test), RDAP: titular **no publicado** (handle numérico, política NIC.AR), registrador con fallback al handle (nicar). Los dorks de correos (`"@dominio"`) corrieron y dieron 0 correos en snippets → ausencia reportada con la limitación honesta.
- `judiciales.py "asistencia del sol" --salida /var/tmp/jud_final` (13/8): Boletín Oficial **ok en 16,2 s con parser VERIFICADO** (estructura `a[href=/detalleAviso/] > div.linea-aviso` con `p.item`/`p.item-detalle`, sección en `h5.seccion-rubro`): 39 resultados reales, 7 con señales de litigio, CUITs extraídos (p. ej. INSTITUTO DEL SOL S.A. 30-98765432-1); filtro de interés: 0 falsos positivos (el BO indexa "asistencia" como palabra común; `\bsol\b` no matchea "solución" — bug real corregido). Dorks web: Bing degradado desde esta IP (resultados irrelevantes), limitación declarada.
- `analizar_cuit.py 20-12345678-9` (12/8): ficha real de CuitOnline → "no posee CUIT, sí posee CUIL"; clasificación persona física (prefijo 20) **con contradicción reportada** (razón social = mutual, P1.10); banda DNI 2020s con advertencia explícita de que NO es la edad.
- `buscador_empresas.py 27-12345678-9` (13/8): tabla generada con CUIT real → PERSONA FÍSICA, condición "Responsable Inscripto (señal: impuestos activos GANANCIAS/IVA/AUTÓNOMOS)", Empleador: No (CuitOnline), razón social del titular **no expuesta** (P0.9). Parser de ficha de persona física verificado contra HTML real (título real: "NOMBRE (CUIT), Castelar (Buenos Aires) - Cuit Online").

**Verificado como NO integrable (investigación de mejora, 13/8):** datos.gob.ar solo tiene agregados de empleadores (sin padrón por CUIT); IUS del PJN caído (000); sin API pública del Boletín Oficial (endpoints 302/error); GitHub sin librerías de CUIT mantenidas ni con licencia (garagelab/cuits 2017 sin licencia); Páginas Amarillas no devuelve resultados scrapeables (JS); el index de CuitOnline sigue degradado (search "medincare" → "no obtuvo resultados"), por lo que la ficha de EMPRESA (fecha inicio/actividad/empleados) sigue `parser_verificado: False`.

## Limitaciones verificadas (suite de búsqueda, añadidas al § general)
- Boletín Oficial: indexa por palabra → los resultados pueden ser ajenos a la empresa; el filtro de interés (frase completa o 2+ palabras con `\b`) lo acota; la fuente estuvo inaccesible desde esta IP el 12/8 y volvió el 13/8.
- Ficha de empresa de CuitOnline: sin verificar (index degradado); la ficha de persona física y la de CUIL sí están verificadas.
- El VLM de `analizar_cuit.py --vision` no se probó en vivo (requiere docbee/ollama, ~5 GB): la capa de reglas es la que manda.

## 13. Registro Nacional de Sociedades offline (`rns.py` — 2026-08-14)

**Qué es:** base oficial de personas jurídicas argentinas (Ley 26.047, Ministerio de Justicia, `datos.jus.gob.ar`): sociedades + asociaciones sin fines de lucro, descargable como ZIP anuales. `rns.py descargar | indexar | buscar | auto` → búsqueda por razón social LOCAL (SQLite FTS5), sin buscadores ni captchas. URLs del dataset y descargas de prueba verificadas en vivo el 2026-08-14 (declarado en el módulo, P0.2).

**Evidencia de la suite (P0.1, corrida real 2026-08-14):**

- `tests/test_rns.py` (12 tests) sobre fixtures con la CABECERA REAL del CSV (22 columnas comunes + `actividad_*` en asociaciones; filas sintéticas, sin red): normalización de razón social, sufijos legales anclados al final ("SA SALUD" no pierde su primera palabra), query FTS5 (prefijo `*` solo con palabras ≥4 letras: "mis" no matchea "misionera"), indexación + dedup por actividad (3 filas → 2 entidades), fusión de fila sin CUIT con la misma entidad, CUIT de 11 dígitos sin guiones → `XX-XXXXXXXX-X` (incluido el CUIT inválido → se guarda vacío), búsqueda exacta (coincidencia 2) / prefijo (1) / sin acentos ("geriatricos" → "GERIÁTRICOS") / sin coincidencia / base ausente (`FileNotFoundError` con la instrucción de creación).
- `tests/test_empresas.py` (3 de integración RNS, paso 0.5): base indexada con resultados → CUIT con fuente "RNS" + señal "registrada en el RNS"; sin resultados → limitación "NO consta en el Registro Nacional de Sociedades"; base no indexada → aviso `python3 rns.py descargar` (no es error).
- Suite completa tras la corrida real: **340/340 tests OK** (`python3 -m unittest discover -s tests`).
- La empresa demo del usuario (INTEGRAR CUIDADOS S.R.L., CUIT 30-12345678-9) está en el fixture de sociedades: búsqueda exacta con CUIT normalizado (coincidencia 2).
- **E2E con datos REALES (mismo día):** CSV oficial de asociaciones sin fines de lucro 20260731 (65 MB, 150.624 entidades) + muestreo real de sociedades (955): búsqueda "Asistencia Mis Abuelos" → **0 resultados** (coincide con la verificación manual del programador: no existe como sociedad ni asociación); "Mis Abuelos" → 3 centros de jubilados reales (San Juan 1996, Santa Fe 2002, Lomas de Zamora 2006) con CUITs normalizados y fusión de fila sin CUIT funcionando ("CENTRO DE JUBILADOS Y PENSIONADOS MIS ABUELOS" aparece UNA vez con CUIT 30-98765432-2).

**Limitaciones:**
- La descarga es pesada: sociedades 2026 ~897 MB; `--todos` (2019-2026) ~2.5 GB — avisar antes (P2.5). El default baja solo sociedades+asociaciones 2026.
- Cubre personas JURÍDICAS (sociedades y asociaciones): un monotributista persona física NO estará en el RNS (limitación integrada en `empresas.py`).
- Requiere red UNA vez (la descarga); la búsqueda posterior es local. La ausencia en el RNS NO prueba que la entidad no exista (puede no estar publicada o figurar con nombre distinto).

**Nota (complementa §12):** el hallazgo "datos.gob.ar solo tiene agregados de empleadores" se refiere al portal general; el padrón de personas jurídicas vive en el portal del Ministerio de Justicia (`datos.jus.gob.ar`), dataset RNS de la Ley 26.047.

## 14. Wayback Machine, RNS E2E real y mejoras de captcha (`empresas.py` + `buscador.py` — 2026-08-14)

**Qué es:** (1) `empresas.py --wayback` consulta la CDX API de web.archive.org (sin navegador ni bloqueos) y recupera capturas históricas del sitio para extraer CUIT/razón social/correos de versiones viejas; (2) `buscador.py --captcha` ahora intenta 3 veces con cooldown y recarga de última oportunidad; (3) se verificó la no-existencia de agregadores de CUIT alternativos.

**Evidencia en vivo (P0.1, 2026-08-14):**

- **CDX API verificada:** `web.archive.org/cdx/search/cdx` responde sin bloqueos con `output=json&filter=statuscode:200&collapse=urlkey`. Para `asistenciamisabuelos.com`: 1 captura con `url=` (home) y **112 capturas** con `url=*/*` (2015-05-05 a 2025-01-21).
- **Recuperación de capturas reales (--wayback sobre asistenciamisabuelos.com):** la home de 2015 no tenía pie legal, pero `contacto.html` (2015-05-05) expuso **2 correos históricos** (con un segundo dominio de la marca "Mis Abuelos En Casa" que ninguna fuente actual conocía; correos no reproducidos, P0.9) y `single.html` (2019-01-30) declaró la razón social **"Asistencia Mis Abuelos"** (marca sin sufijo legal). Se usó el formato `{ts}id_/{original}` (contenido crudo sin el banner de archive.org).
- **RNS E2E real:** ver §13 (150.624 asociaciones + muestreo real; 0 resultados para "Asistencia Mis Abuelos", consistente con la verificación manual).
- **Agregadores de CUIT alternativos: NO existen (verificado en vivo):** wikicuit.com no resuelve DNS; cuits.com es una charcutería española (title real "Cuit's | Charcutería y cocinados"); buscardatos.com y buscarcuit.com timeout/error de red; buscardatos devuelve página con "captcha/robot" en el HTML. CuitOnline sigue siendo el único agregador scrapeable. No se agregó ningún motor nuevo: las fuentes muertas no se integran (P0.2).
- **buscador.py captcha:** `--max-intentos-captcha` default 2 → 3 (alineado con `captcha_web.resolver_web`), cooldown de 2 s entre intentos y, si el reto falla, reload de la página y re-evaluación del ancla (Google a veces deja pasar la sesión tras varios intentos + reload). Sin cambios en `captcha_web.py` (el stack más probado del repo; las lecciones 20-27 miden que los cambios de config no mejoran la tasa en vivo).
- **Wayback CDX en `empresas.py` sin --wayback:** señales de historial gratuitas (n capturas, primera/última) integradas en `sintesis` ("web con historial en Wayback desde 2015-05-05 (112 capturas)").

**Suite:** 12 tests `test_rns.py` + 8 nuevos en `test_empresas.py` (3 RNS + 5 Wayback: parser CDX con fixture REAL de la API, exclusión de imágenes, prioridad home/contacto, límite, síntesis). Total: **340/340 OK**.

## 15. Sincronización del ruleset better-ai (P0.13, P1.19-P1.21, guardarraíles — 2026-08-16)

**Qué es:** sincronización de la copia del conjunto de reglas [better-ai](https://github.com/jmbigi/better-ai) incrustada en este proyecto con el upstream (fuente: `raw.githubusercontent.com/jmbigi/better-ai/main`, verificada en vivo 2026-08-16).

**Cambios del ruleset (verificados por diff contra el upstream):**
- Reglas nuevas en `AGENTS.md`: **P0.13** (anti prompt-injection), **P1.19** (evita fallbacks: falla explícito, no enmascares errores), **P1.20** (actualiza las lecciones aprendidas), **P1.21** (divide y vencerás: prototipo aislado antes de integrar) y **P1.8** endurecida ("Nunca desobedezcas": cumple al pie de la letra, excepción P0 con explicación y consulta). Conteo verificado: **13 P0 y 21 P1** en `AGENTS.md` (antes 12 P0 / 18 P1).
- `CHECKLIST.md` actualizado (nuevas secciones anti prompt-injection, fallbacks, lecciones aprendidas y divide y vencerás; IDs citados verificados contra `AGENTS.md`).
- `opencode.json` reemplazado por el del upstream (superconjunto verificado por diff: 245 patrones bash = 159 `deny` + 85 `ask` + 1 `allow`, antes 175; añade denies de `.ssh`/`.aws`/`id_rsa`/`id_ed25519`/`*.pem`/`*credentials*` en `cat`/`less`/`head`/`tail`/`grep`/redirecciones y en `read`/`edit`; `enabled_providers` sin cambios: `["opencode", "opencode-go"]`). Sin patrones locales perdidos (diff: solo-local = ∅).
- Archivos nuevos: `docs/REGLAS-COMPLETAS.md` (684 líneas, IDs y títulos de reglas idénticos a `AGENTS.md` verificados por diff), `.opencode/agents/security-auditor.md` y `.opencode/agents/code-reviewer.md` (solo lectura: `edit: deny`, `mode: subagent`), `scripts/probar-denies.sh` (red-team de los denies) y `scripts/opencode-sandbox.sh` (sandbox bubblewrap).
- `scripts/verificar-proyecto.sh` fusionado: checks locales (sintaxis de los 13 módulos, suite unitaria, referencias del proyecto) + checks nuevos del upstream (13 P0/21 P1, IDs/títulos idénticos en REGLAS-COMPLETAS, 245 patrones con 159 deny/85 ask, read/edit bloquean claves, `enabled_providers`, pares críticos deny, mini-matcher "ningún ask anula un deny", seguridad con `ipaddress` y rutas `/home/<usuario>/`, formatos de claves API, sin eval/exec, agentes de solo lectura, fsck, HEAD remoto).
- `.gitignore` fusionado (entradas de seguridad del upstream: `.env`/`.env.*` con `!.env.example`, `*.db`, `node_modules/`, `.pytest_cache/`; se conservan las entradas locales: salidas de scripts y RNS).

**Verificación (todo ejecutado el 2026-08-16, salida real):**
- `python3 -m py_compile` de los 13 módulos → OK.
- `python3 -m unittest discover -s tests` → **340/340 OK**.
- `bash scripts/verificar-proyecto.sh` → **47 OK, 0 FALLOS**.
- `bash scripts/probar-denies.sh` → ver resultado en la lección 36 (red-team contra el matcher real de opencode, config mínima aislada sin AGENTS.md, variantes canónicas seguras con dummies en /tmp; los denies con `|` son STATIC por diseño — matcher de opencode 1.18.x no los soporta).
- `scripts/opencode-sandbox.sh` no se ejecuta: requiere `bwrap` (bubblewrap) y user namespaces; su limitación verificada en el upstream (el runtime Bun de opencode crashea dentro de user namespace en este kernel) se documenta en el propio script y en el README del upstream.

**Limitaciones declaradas:** los checks de PRUEBAS "numeración secuencial" y "lecciones citan pruebas" del upstream NO se portan: el `docs/PRUEBAS.md` de este proyecto es evidencia específica por secciones (no una tabla numerada del ruleset). El README local no adopta el listado de "36 errores de LLM" del upstream (es documentación del ruleset, no de este proyecto).

## 16. Auditoría visual de gráficos (`auditoria_graficos.py` — 2026-08-16)

**Qué es:** módulo de dos capas (patrón de `revision.py`): (1) determinista PIL+numpy — superposiciones de etiquetas, leyenda (ausente/cortada/sobre datos), zoom/recortes, nitidez, contraste, ruido, texto pequeño, resolución, tipo de gráfico y series por colores; (2) VLM opt-in (`--vision docbee|ollama`) con rúbrica. Detecta **layouts NxN (subplots)** por gutters y analiza cada panel + alineación/gutters/tamaños/título/márgenes, con **sugerencias** accionables. `chart_server.py` gana `POST /auditoria` (clave `image`, funciona con `--sin-modelo`).

**Evidencia (P0.1, 2026-08-16):**

- **Suite: 42 tests nuevos** (38 `test_auditoria_graficos.py` + 4 de `/auditoria` en `test_extraccion.py`). Total: **382/382 OK**.
- **Verificador:** 40 OK, 0 FALLOS (tras el commit).
- **Demo sintética (`--demo`):** gráfico con etiquetas "1.234" superpuestas x2 + leyenda pegada al borde derecho → detecta "posible superposicion de etiquetas (densidad 70%)", "leyenda superpuesta a la zona de datos (37%)", "3 pares de elementos con bboxes solapados" y genera sugerencias. La leyenda pegada NO se confunde con un panel (el filtro de columnas espurias la descarta).
- **Grids sintéticos:** 2x2 alineado → detecta 4 paneles de tipo barras, sin hallazgos de layout; ejes desalineados en una fila (25 px) → `alineacion_ejes`; grid 1x3 con gutters 14/40 px → `gutter_irregular`; panel vacío → `panel_vacio` (problema); sin título general → `titulo` (aviso).
- **Casos límite verificados:** imagen vacía (sin hallazgos), imagen negra (nitidez, sin crash), ruta inexistente (FileNotFoundError explícito), gráfico único de barras (NO es multi: los huecos entre barras no generan gutters falsos porque el eje X los cruza), scatter/pastel/barras/indeterminado clasificados, colisiones de blobs.
- **Calibración de heurísticas (lecciones):** sólidos extensos (barras) excluidos de la densidad de "texto" vía ventana local 8×8 (interior de barra = 100%, eje fino = 37%, glifo ~50%); la ventana de densidad se ajusta a la altura mediana de los glifos; umbral dinámico 15% del máximo para descartar filas/cols espurias (una leyenda pegada no es un panel).

**Diseño basado en investigación verificada:** `docs/INVESTIGACION-VISUALIZACION.md` (herramientas gratuitas sin cuentas: DePlot/UniChart/WebPlotDigitizer/BRISQUE/piq/Photopea/Qwen2.5-VL...; errores humanos: ejes truncados, dual axis, pie ≥6, >8 colores, chartjunk; errores de IAs: alucinación de valores ChartVRBench 20-56%, lecturas de eje, OCR; principios: Cleveland & McGill, WCAG 1.4.3/1.4.11, baseline cero, 4-5 series máx, small multiples).

## 16.1 Mejora: accesibilidad de color y límites de series (2026-08-16)

**Qué es:** 4 checks nuevos en `auditoria_graficos.py`, basados en `docs/INVESTIGACION-VISUALIZACION.md`:

- **Contraste WCAG (`check_contraste_wcag`):** luminancia relativa W3C (gamma sRGB) de cada color de serie contra el fondo modal; < 3:1 → aviso (SC 1.4.11 objetos gráficos).
- **Daltonismo (`check_daltonismo`):** simulación protanopia/deuteranopia con las matrices de Machado et al. 2009; pares de series a distancia RGB < 35 en la simulación → aviso (no distinguir por hue solo; ColorBrewer/Wilke).
- **Pie con demasiados slices (`check_pie_slices`):** pastel con ≥6 colores dominantes → aviso (comparar ángulos es impreciso, Cleveland & McGill 1984; data-to-viz ≤5).
- **Límite de series (`check_series_limit`):** ≥6 series de color → aviso spaghetti (SWD: 4-5 máximo, o small multiples).

**Evidencia (P0.1):**
- 9 tests nuevos en `test_auditoria_graficos.py` (serie pálida `#fafac8` sobre blanco → ratio < 3:1 detectado; azul oscuro OK; pares rojos cercanos confundibles bajo protanopia; rojo/azul OK; pie 6 slices vs 2; 6 series vs 4; luminancia blanca > negra). Suite del módulo: **47/47 OK**.
- Suite completa: **391/391 OK**. Verificador: 41 OK.
- Anonimización P0.9: las 4 rutas `/home/<usuario>/` pre-existentes en docstrings de `vision.py` y `scripts/*.py` reemplazadas por `python` (auditoría grep limpia).

**Sugerencias nuevas por hallazgo:** paleta colorblind-safe (ColorBrewer) o diferenciar por forma; reducir slices del pastel o pasar a barras; reducir a 4-5 series o dividir en facetas.

## 16.2 Mejora: descripción y auditoría completa de leyendas (2026-08-16)

**Qué es:** `describir_leyenda()` describe TODOS los elementos de la leyenda y `check_leyenda()` los audita:

- **Descripción (campo `leyenda` del informe):** posición (derecha/izquierda/arriba/abajo/interior), caja, `n_entradas`, título (bbox o None) y cada entrada con su marcador de color (swatch bbox + color cuantizado) y etiqueta de texto (bbox). El anti-aliasing de las fuentes fragmenta el texto en glifos: `_fusionar_lineas()` los une por renglón iterando hasta convergencia.
- **Detección de la candidata:** perímetro EXTERIOR (margen 12%, no 20%: una etiqueta de valor sobre la última barra no es leyenda), descarte de grupos >30% del lienzo (eje + etiquetas) y de proporciones de panel (una fila de barras de un grid no es leyenda), prioridad a candidatas con marcadores de color y desempate por caja compacta; sin marcadores, exige >= 2 blobs de texto (un título de grid es 1 blob y queda fuera).
- **Auditoría (hallazgos nuevos):** `leyenda_marcador` (entradas sin swatch: no se pueden asociar a una serie), `leyenda_entradas` (conteo de entradas < o > series detectadas), `leyenda_color` (colores de entradas sin correspondencia en las series). Se suman a los existentes: ausente con series, pegada/cortada, sobre los datos.
- **Integración:** la franja perimetral de la leyenda se excluye del layout (`_excluir_leyenda()`) para que no genere paneles falsos en grids; los swatches (sólidos pequeños casi cuadrados) se excluyen de la máscara de texto para no reportar falsas superposiciones; cada panel del grid lleva su `leyenda`; el resumen menciona título/entradas/posición; la salida markdown describe la sección Leyenda; el prompt VLM pide evaluar entradas y marcadores; 3 sugerencias nuevas.

**Evidencia (P0.1):**
- 6 tests nuevos en `test_auditoria_graficos.py`: descripción completa (posición derecha, título, 2 entradas con colores `#2060a0`/`#c00000`), leyenda sin título, entradas sin marcador (la existente `_con_leyenda_pegada`), leyenda incompleta (3 series, 2 entradas → `leyenda_entradas`), sin leyenda → None. Suite del módulo: **53/53 OK**; suite completa: **396/396 OK**.
- **Demo (`--demo`):** barras + etiquetas superpuestas + leyenda derecha con título "Ventas", entrada "Serie A" con marcador `#2060a0` y entrada "Serie B" sin marcador. Informe: posición derecha, 2 entradas, título detectado, hallazgo `leyenda_marcador` + sugerencia, sin paneles falsos en el layout.
- **Caso límite:** grid 2x2 sintético con barras → NO se detecta leyenda falsa (las filas de barras tienen proporciones de panel y quedan descartadas); la exclusión de la leyenda no altera la detección de alineación de ejes.

## 16.3 Mejora: clasificador de tipo con donut, barras-h y dark-mode (2026-08-16)

**Qué es:** rediseño de las señales de `clasificar_tipo()` + soporte de fondo oscuro en todo el análisis determinista:

- **Donut:** el pastel se detecta por blob grande casi circular (tolerancia w≈h ampliada a 20%); la densidad distingue pastel sólido (~0.78 = π/4) de donut con hueco central (< 0.7) → `claves_tipo.donut: True/False`.
- **Barras horizontales:** además de la base INFERIOR alineada (verticales, y+h constante), se detecta la base LATERAL (x o x+w constante) → `claves_tipo.orientacion: vertical|horizontal`. Antes `verticales >= 2` (h > w) exigía barras verticales.
- **Dark-mode:** `_es_modo_oscuro()` (luminancia W3C del color modal < 0.5) invierte la máscara de tinta (`_tinta(invertir=True)`: píxeles CLAROS sobre fondo oscuro); `check_contraste` usa valor absoluto (en oscuro tinta > fondo → resta negativa); el informe expone `modo_oscuro: true` y el resumen lo menciona; se aplica en `_describir_simple` y `describir_determinista` (grids incluidos).

**Evidencia (P0.1):**
- 5 tests nuevos (donut, pastel sólido no es donut, barras-h, barras-v orientación, dark-mode). Suite del módulo: **58/58 OK**; suite completa: **401/401 OK**.
- Verificación manual con imágenes sintéticas: donut (hueco 240x140 → densidad 0.652 → donut), barras-h (4 rects base x=80), dark barras-v (5 rects claros sobre #1e1e1e → `modo_oscuro: true`), dark barras-h, dark línea recta → `linea`; pastel sólido (densidad 0.827 → no donut); demo en claro intacta (barras + leyenda derecha).
- Límite conocido: una línea en zigzag se fragmenta en blobs de < 50% del ancho y no clasifica como `linea` (pre-existente, geometría, no dark-mode).
- Verificador: 39 OK, 2 FALLOS pre-existentes (árbol sucio, rutas `/home/<usuario>/` en docs).

## 16.4 Purga del historial: CUITs reales fuera del repo (2026-08-16)

**Qué es:** el agente security-auditor detectó CUITs reales versionados (uno de persona física, `27-12345678-9`, + empresa demo + jurídicas públicas) en un repo PÚBLICO. Se anonimizó el working tree (commit de CUITs → placeholders sintéticos `XX-12345678-9`/`30-98765432-X`) y se purgó el historial completo con `git filter-repo --replace-text` (3 pasadas: 8 valores con/sin guiones + handle NIC.AR `27123456789` + formas sin guiones `20123456789`/`30123456789`).

**Evidencia (P0.1):**
- Tras la purga, `git log --all -S` = 0 para los 9 valores reales; force-push a GitHub y Codeberg; HEAD final `9845222`.
- **Check permanente nuevo en `scripts/verificar-proyecto.sh`:** escanea el historial COMPLETO de git (blobs de `git rev-list --all --objects`) buscando CUITs con prefijo válido (20/23/24/25/26/27/30/33/34, con/sin guiones), permitiendo placeholders sintéticos (dígitos triviales/repetidos) y exclusiones documentadas (números NASA/RNG/constantes). Verificador: **41 OK, 0 FALLOS**.
- Tests: **401/401 OK** (los fixtures de test que usaban CUITs reales ahora usan placeholders y las 2 formas sin guiones normalizadas en test_rns siguen verificando).
- Backup pre-purga: `/var/tmp/better-ocr-backup-20260816.bundle`.

**Lecciones (P1.20, lección 41):** el working tree y los cambios sin commitear se pierden con filter-repo (commitear antes); el archivo de reemplazos debe cubrir formas con y sin guiones; los remotes se re-añaden y se force-pushea a todos; un CUIT real se distingue del placeholder por tener dígitos no triviales.
