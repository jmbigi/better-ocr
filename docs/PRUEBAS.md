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

Total: 63 tests nuevos (suite completa 131/131 OK).

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

## Limitaciones verificadas

- Scatter: no soportado por ChartParsing (alucina).
- Gráfico mixto barras+línea (`bar_line_mixto`): el fast path lo rechaza (esperado: la línea mezcla las columnas del emparejado geométrico) y el fallback VLM lo resuelve **exacto** — 6/6 etiquetas + 12/12 valores en ~140 s con `CUDA_VISIBLE_DEVICES=""` (lección 18: sin forzar CPU, el VLM aborta con SIGABRT por el cudnn 9.1 del pyenv en máquinas con GPU visible).
- Captions/pinturas: requieren VLM ≥ 0.9B (~9 GB) → OOM en equipos de 7 GB.
- qwen2.5vl:7b en CPU: deja la RAM del host crítica; descargar con `keep_alive=0`.
- Portabilidad: solo probado en Linux/x86 (CPU 7.7 GB; GPU RTX 3070 8 GB validada en lección 17).
- docbee en GPU 8 GB: requiere max_pixels ≤ 0.5M px (OOM a resolución nativa); flash attention exige cu_seqlens int32 (paddle 3.3.1 GPU promueve a int64); LD_LIBRARY_PATH del host no debe sombrear nvidia-cudnn del venv (lección 17).
