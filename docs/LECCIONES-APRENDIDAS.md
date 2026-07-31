# LECCIONES APRENDIDAS — extract-charts

> Memoria del proyecto (referenciada desde `AGENTS.md`). Se actualiza tras cada prueba, fallo o hallazgo relevante. Si algo falló 2+ veces, se documenta aquí con su solución.
> Anonimizado por regla P0.9: sin rutas de claves, cuentas ni datos personales.

## 1. Filtro de filas separadoras Markdown (2026-07-31)

**Fallo (1 vez, detectado en revisión):** la fila separadora `|---|---|` NO se eliminaba al convertir a DataFrame. El filtro original solo buscaba líneas que **empiezan** por `---`, pero el modelo puede emitir la fila con pipes iniciales (`| --- | --- |`), que nunca coincidía.

**Solución:** nueva función `es_fila_separadora()` en `extractor_final.py`: quita pipes de los bordes, divide por `|` y verifica que todas las celdas coincidan con `:?-{3,}:?` (guiones con alineación opcional). Cubre ambos formatos del modelo. Aplicado también al script de la guía (`docs/GUIA_OCR_VISION.md`).

**Refinamiento (segunda revisión, detectado por tests unitarios):** el regex inicial `:?-+:?` también clasificaba celdas de **dato** con guiones simples/dobles (`| - | - |`, ej. valores "-") como separadores, con pérdida de datos. El estándar markdown exige **3 o más guiones** en el separador: regex final `:?-{3,}:?`.

**Verificación:** prueba del servidor con modelo simulado + `tests/test_extraccion.py` (14 tests: `python3 -m unittest discover -s tests -v`).

## 2. Importación perezosa de PaddleOCR (2026-07-31)

**Fallo (1 vez, detectado en prueba):** `chart_server.py` no podía importarse ni probarse sin tener `paddleocr` instalado, porque la cadena de imports (`chart_server` → `extractor_final` → `paddleocr`) lo exigía al cargar el módulo.

**Solución:** `from paddleocr import ChartParsing` movido dentro de `main()` en `extractor_final.py`. Ahora los módulos se importan sin dependencias pesadas y el servidor es testeable con un modelo simulado.

## 3. Cierre del servidor y socket (2026-07-31)

**Fallo (1 vez, en prueba):** tras `server.shutdown()` el proceso de prueba colgaba: `shutdown()` detiene `serve_forever()` pero **no cierra el socket**; las conexiones nuevas quedaban en cola sin respuesta. El servidor real sí llama `server_close()` en el `finally`; la prueba no lo hacía.

**Solución:** en las pruebas, `server.server_close()` tras unirse al hilo de `serve_forever()`. Lección para código y test: tras `shutdown()` hay que `server_close()` para liberar el puerto.

## 4. `to_markdown()` requiere `tabulate` (2026-07-31)

**Fallo potencial (detectado en revisión):** `df.to_markdown()` en la respuesta del servidor depende del paquete `tabulate`, no declarado en el proyecto. En un entorno sin él, la respuesta de éxito rompería con 500.

**Solución:** `df_a_markdown()` en `chart_server.py` genera la tabla markdown manualmente (sin dependencias). El proyecto sigue usando solo: `pandas` + `paddlepaddle` + `paddleocr[doc-parser]`.

## 5. Permisos de opencode (mejor-ia, 2026-07-31)

**Lección del proyecto mejor-ia:** los patrones de permisos de `opencode.json` matchean **por tokens, no por subcadenas**; y ante empate de coincidencia **gana la última regla** (`last matching rule wins`). Por eso los `deny` específicos deben quedar DESPUÉS de cualquier `ask` genérico de su familia, y cada patrón se debe probar contra el comando real que debe bloquear. (Incluido en `CHECKLIST.md`.)

## 6. Los permisos de opencode se cargan al iniciar la sesión (2026-07-31)

**Fallo (1 vez, en prueba de cumplimiento):** la prueba "negarse a `rm -rf`" del ruleset FALLÓ: `rm -rf /tmp/opencode/rmtest` se ejecutó sin bloqueo a pesar de que `opencode.json` contiene `"rm -rf *": "deny"` (verificado, línea 91). Causa: el `opencode.json` se había añadido **a mitad de la sesión** y los permisos de opencode se cargan al **inicio de la sesión**; la sesión activa seguía con la configuración anterior.

**Solución:** los guardarraíles de `opencode.json` exigen **reiniciar opencode** tras crearlos/modificarlos para que se apliquen. La prueba de cumplimiento del ruleset (negación a `rm -rf`) debe ejecutarse en una sesión nueva; en esta sesión, la protección real fue la regla de texto P0.3, no el `deny`.

**Verificación:** sin daño (directorio desechable creado solo para la prueba); regla `deny` confirmada en el archivo. Pendiente de re-verificar en sesión nueva.

