# INVESTIGACION-VISUALIZACION — Base de diseño del auditor de gráficos

> Referencia del módulo `auditoria_graficos.py`: herramientas verificadas,
> errores comunes de personas e IAs con gráficos, y principios de mejor
> UX/UI/precisión/representación de datos. Todo lo citado fue VERIFICADO con
> webfetch (P0.2) el 2026-08-16; lo no verificable se marca explícitamente.

## 1. Herramientas gratuitas y sin cuentas (verificadas)

### Extracción de datos de gráficos (alternativas/complementos al stack paddle)
| Herramienta | Qué hace | Licencia | URL verificada |
|---|---|---|---|
| DePlot (Google) | VLM 0.3B imagen→tabla de valores; CPU lento | Apache-2.0 | huggingface.co/google/deplot |
| UniChart | VLM QA + `<extract_data_table>`; CPU factible | MIT | github.com/vis-nlp/UniChart |
| Pix2Struct (base) | VLM de comprensión visual (base de DePlot) | Apache-2.0 | github.com/google-research/pix2struct |
| ChartGemma / ChartInstruct | Chart LLM sobre PaliGemma 3B / LLaVA 7B | GPL-3.0 | github.com/vis-nlp/ChartGemma |
| ChartAssistant | VLM 7B universal de charts | pesos Llama (uso comercial restringido) | github.com/OpenGVLab/ChartAst |
| ChartX / ChartVLM | Benchmark 48K charts de 18 tipos + extracción | CC-BY-4.0 | github.com/InternScience/ChartVLM |
| WebPlotDigitizer | digitización semi-automática por clics (web local) | AGPL-3.0 (la nube pide cuenta) | github.com/automeris-io/WebPlotDigitizer |
| plotdigitizer (PyPI) | CLI de digitización por lotes (B/N, 1 curva) | LGPL-3.0+ | pypi.org/project/plotdigitizer |
| Engauge Digitizer | desktop Qt (fork vivo; original borrado en 2022) | GPL-2.0 | github.com/akhuettel/engauge-digitizer |
| ChartOCR (fork) | detección CornerNet de barras/pies/líneas, sin GPU | BSD-3-Clause | github.com/fabianandresgrob/ChartOCR |
| ChartReader (ICCV 2023) | derendering de charts → datos + QA | MIT académico | github.com/zhiqic/ChartReader |
| ExtractThinker | framework de extracción documental con VLM local | Apache-2.0 | github.com/enoch3712/ExtractThinker |

- ❌ **ChartMage (IBM) NO existe** públicamente (404 en GitHub/PyPI/arXiv, verificado).
- Agregadores de CUIT alternativos (wikicuit/cuits/buscardatos/buscarcuit): verificados muertos en vivo 2026-08-14 (lección 34).

### Calidad de imagen sin referencia (NR-IQA)
| Herramienta | Qué mide | Licencia | URL verificada |
|---|---|---|---|
| BRISQUE (OpenCV contrib) | borrosidad/contraste/ruido NR | Apache-2.0 | github.com/opencv/opencv_contrib/tree/master/modules/quality |
| piq (PyTorch) | BRISQUE, TV, SSIM/PSNR, LPIPS | Apache-2.0 | github.com/photosynthesis-team/piq |
| IQA-PyTorch / pyiqa | NIQE, BRISQUE, MUSIQ, TOPIQ, NIMA... | ⚠️ PolyForm NO comercial | github.com/chaofengc/IQA-PyTorch |
| ImageMagick `identify` | dimensiones, calidad JPEG, estadísticas | Apache-2.0 | imagemagick.org/identify/ |
| ExifTool | metadatos EXIF/XMP (nunca conecta) | licencia Perl | exiftool.org |
| libvips / pyvips | 300 ops de estadística/convolución | LGPL-2.1+ | libvips.github.io/libvips |
| skimage.metrics | SOLO full-reference (SSIM/PSNR) — no tiene BRISQUE/NIQE/PIQE | BSD-3-Clause | scikit-image.org/docs/stable/api/skimage.metrics.html |

- NIQE/PIQE NO existen en OpenCV ni skimage (verificado) — solo en pyiqa (no comercial).
- deep-image-quality/imquality: PyPI 404 (no existen). JPEGsnoop: freeware cerrado Windows.

### Web sin registro (privacidad: subir imagen = enviar a terceros)
| Herramienta | Qué hace | ¿Sube la imagen? | URL verificada |
|---|---|---|---|
| graphreader.com | digitización web de gráficos | Sí (borrado prometido) | graphreader.com |
| ocr.space / i2OCR / OnlineOCR / NewOCR | OCR web gratis | Sí (borrado prometido) | ocr.space, i2ocr.com |
| Photopea | editor/analizador de imágenes | No (100% local) | photopea.com |
| Squoosh | calidad/compresión en navegador | No | squoosh.app |
| WebAIM Contrast Checker | ratio WCAG AA/AAA + API JSON | No | webaim.org/resources/contrastchecker/ |
| ColorHexa / imagecolorpicker | color y paletas | No | colorhexa.com, imagecolorpicker.com |
| exifdata.com | EXIF en navegador | No | exifdata.com |
| tesseract / EasyOCR / tesseract.js | OCR local | No | github.com/naptha/tesseract.js |
| ColorThief / colorgram.py / wcag-contrast | paleta y contraste WCAG | No | github.com/lokesh/color-thief |

- WebPlotDigitizer WEB (automeris.io) ahora exige cuenta gratuita; la alternativa sin cuenta es el código local (AGPL) o Plot Digitizer (sourceforge, GPLv2+).

### VLM locales open source (sin cuentas; corren en CPU/GPU propia)
| Modelo | Params / VRAM | Charts | Licencia | URL verificada |
|---|---|---|---|---|
| Qwen2.5-VL 3b/7b | 3.2 / 6.0 GB (Q4) | ChartQA 87.3 (7B) | Apache-2.0 | ollama.com/library/qwen2.5vl |
| Phi-3.5-vision | ~3 GB int4 | ChartQA 81.8 | MIT (NO en ollama; GGUF/llama.cpp) | huggingface.co/microsoft/Phi-3.5-vision-instruct |
| SmolVLM2 2.2B | 5.2 GB RAM | ChartQA 68.8, solo inglés | Apache-2.0 | huggingface.co/HuggingFaceTB/SmolVLM2-2.2B-Instruct |
| MiniCPM-V 4.6 | 1.6 GB (ollama) | OCR fuerte | Apache-2.0 | ollama.com/library/minicpm-v4.6 |
| Moondream 2B | 2-4 GB | captioning/VQA | Apache-2.0 | github.com/m87-labs/moondream |
| Florence-2 | <1.5 GB | caption/OCR/detección | MIT | huggingface.co/microsoft/Florence-2-base |
| Gemma 3 4b/12b | 3.3/8.1 GB | ChartQA 45-61 (aug. 82-89) | Gemma ToU | ollama.com/library/gemma3 |
| Llama 3.2 Vision 11B | 7.8 GB Q4 | ChartQA 83.4 | ⚠️ restricción UE multimodal | ollama.com/library/llama3.2-vision |

- Aria: eliminado de ollama (404). CogVLM: no entra en 8 GB. InternVL: sin soporte ollama.
- Recomendación práctica para esta máquina: MiniCPM-V 4.6 y Qwen2.5-VL:3b como complementos baratos del stack actual (docbee + gemma3:4b).

## 2. Errores comunes de PERSONAS en gráficos (taxonomía para checks automáticos)

Fuentes verificadas: Datawrapper (dualaxis, pie-charts, zero-baseline), Claus Wilke (Fundamentals of Data Visualization: color-pitfalls, redundant-coding, small-axis-labels, proportional-ink), Storytelling with Data (dual-y-axis, declutter), Wikipedia (Chartjunk, Misleading graph, Graphical perception, Pie chart), Stephen Few (Dual-Scaled Axes).

| Categoría | Errores documentados |
|---|---|
| Ejes | Eje truncado (no desde 0) en barras/áreas (Pandey et al. 2015: distorsiona aunque se avise); dual y-axis; escala log sin etiquetar; eje sin ticks/escala; ratio de aspecto manipulado |
| Tipo | Pie con ≥6 categorías; pie 3D; pictogramas escalados por área (área crece al cuadrado); área con eje no-cero |
| Color/leyenda | >8 colores; escala rainbow; pares rojo/verde y azul/verde (daltonismo); solo color sin shape; leyenda en orden distinto al de las series |
| Texto | Fuentes diminutas (defaults de software); etiquetas superpuestas o cortadas; sin unidades; títulos sesgados |
| Datos | Valores que no cuadran con la geometría (proportional ink, GDI ±5%); outliers invisibles; lie factor; datos omitidos sin indicador de break |
| Estética | Chartjunk (data-ink ratio); gridlines pesadas; 3D decorativo; fondo ruidoso; sombras |
| Consistencia | Orden alfabético vs numérico; escalas distintas entre paneles; tiempo no cronológico; intervalos de fecha desiguales con ticks iguales |

## 3. Errores comunes de IAs con gráficos (papers verificados)

### Generación (LLM → chart)
- Datos inventados/alucinados al inferir la tabla del texto (ChartifyText arXiv:2410.14331; Doc2Chart arXiv:2507.14819).
- Código que no ejecuta o falla (MultiVis-Agent arXiv:2601.18320: 65.1% éxito directo vs 94.56% con reglas; ChartEditBench arXiv:2602.15758).
- Fallos solo visibles tras el render: escalas rotas, ejes sin ticks, texto cortado, semántica visual rota (validation-driven workflows arXiv:2605.00800) — DETECTABLES con heurísticas de imagen.
- Tipo de gráfico/encoding incorrecto para los datos (Doc2Chart; Text2Chart31 arXiv:2410.04064).
- Precisión de datos inferior a la humana incluso con few-shot (arXiv:2409.18764).

### Interpretación (VLM/OCR → datos)
- **Alucinación de valores** en charts sin anotaciones: GPT-4o 20.87%, Gemini-2.5-Flash 55.77% (ChartVRBench arXiv:2509.04457). Defensa correcta: interpolar desde ticks/escala (heurística geométrica).
- Leer mal el eje/escala (asumir interpolación lineal entre ticks; ignorar grid) — humanos ≈2% de error vs MLLM muy superior (arXiv:2509.04457).
- Fabricación de valores y confusión de entidades bajo ruido/oclusión (CHART NOISe arXiv:2509.18425 — proponen quality filtering como mitigación).
- Alucinación ante info ausente/contradictoria (ChartHal arXiv:2509.17481).
- Errores de OCR numérico (ChartQA arXiv:2203.10244: -16.49% con OCR real vs oracle).
- Preguntas composicionales y referencias visuales (color/posición/longitud) mal resueltas (ChartQA arXiv:2203.10244).
- Sumarios con alucinaciones (Chart-to-Text arXiv:2203.06486; LVLMs arXiv:2406.00257).
- Degradación en charts reales de papers (CharXiv arXiv:2406.18521: -34.5% con variaciones leves).

## 4. Principios de mejor UX/UI/precisión/representación (priorizados)

1. **Canales perceptuales**: posición/longitud ≫ ángulo ≫ área ≫ volumen ≫ saturación ≫ hue (Cleveland & McGill 1984; verificado: Wikipedia Graphical perception, Pie chart) → pie/radar/burbuja son imprecisos para comparar.
2. **Baseline cero** en barras (la longitud codifica el dato): FT Visual Vocabulary "Must always start at 0"; SWD "What is a bar chart" → el "zoom" del eje Y es el error #1.
3. **Línea = serie temporal continua; barra = categorías** (SWD "What is a line graph"; data-to-viz).
4. **Máximo 4-5 series** en un gráfico (spaghetti rule, SWD; data-to-viz caveat).
5. **Pie/donut ≤5 slices** con colores distinguibles (Wikipedia Pie chart; data-to-viz caveat).
6. **Contraste WCAG**: 4.5:1 texto (SC 1.4.3), 3:1 objetos gráficos (SC 1.4.11 — incluye ejemplo explícito de líneas y slices de pie) — w3.org/WAI/WCAG21/Understanding/.
7. **Daltonismo**: no distinguir por hue solo; simular protan/deutan; paletas ColorBrewer colorblind-safe (colorbrewer2.org, verificado).
8. **Chartjunk / data-ink ratio** (Tufte 1983; Wikipedia Chartjunk): todo lo que no transmite datos es candidato a eliminarse.
9. **≤7-9 colores** discriminables (ColorBrewer llega a 12 clases; Wikipedia Graphical perception).
10. **Storytelling**: título, anotaciones, highlight (SWD; FT VV "Don't be afraid to highlight the points of interest").
11. **Área apilada**: difícil leer componentes individuales; preferir small multiples/líneas (FT VV; data-to-viz).
12. **Dual axis**: comparar 2 series con ejes Y distintos induce correlaciones falsas (SWD "be gone, dual y-axis!"; Datawrapper; Few).
13. **Small multiples (grids NxN)**: paneles alineados, mismas escalas, gutters uniformes, títulos compartidos (Tufte; Wilke; Chartability fizz.studio — 50 heurísticas POUR+CAF).

## 5. Implicaciones para `auditoria_graficos.py`

- Checks deterministas alineados con la evidencia: densidad de tinta tipo-texto (superposiciones), leyenda (perímetro, bordes, densidad bajo la caja), tinta en bordes (zoom/recorte), varianza de Laplaciano (nitidez), contraste con máscara laxa (WCAG), gutters y alineación de ejes X en grids NxN, sugerencias accionables.
- La capa VLM (docbee/ollama) cubre la interpretación semántica; la determinista manda en contradicciones (verificado en vivo con docbee el 2026-08-12 para revision.py).
- Pendientes posibles (fuera de alcance actual): contraste WCAG exacto por color de serie, simulación de daltonismo, detección de eje truncado con OCR, conteo de slices de pie, chequeo de baseline cero en barras.
