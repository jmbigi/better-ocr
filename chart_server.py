#!/usr/bin/env python3
"""Daemon persistente de extracción de datos de gráficos con PP-Chart2Table.

Carga el modelo UNA sola vez (evitando el pico de 4.8 GB y los ~95 s de
inicialización en cada ejecución) y se cierra solo tras 1 hora sin peticiones
de inferencia. No queda ningún proceso en memoria entre usos.

Endpoints:
  POST /chart  {"image": "<ruta o URL a la imagen>"}
               -> {"ok": true, "filas": n, "markdown": "...", "csv": "..."}
               (400 JSON invalido/vacio, 413 cuerpo > 1 MB, 500 error interno)
  POST /vision {"image": "...", "modo": "...", "fallback": bool}
               -> resultado del modo (texto/graficos/doc/objetos/humano/auto)
  POST /revision {"archivo": "planilla.xlsx", "reglas": "ruta opcional",
                  "comparar": "otra.xlsx opcional", "vision": "docbee|ollama",
                  "hoja", "max_hallazgos"} -> hallazgos de formato/presentacion
               (analisis determinista + Vision IA 360 opt-in; sin modelos)
  GET  /health -> {"status": "ok", "modelo": "...", "uptime_s": ...}

Diseño:
  - HTTPServer de UN solo hilo a propósito: PaddleX NO es thread-safe, las
    peticiones de inferencia se procesan en serie por construcción.
  - Un hilo vigía comprueba la inactividad (por defecto 3600 s = 1 hora,
    configurable con --timeout). Nunca cierra mientras haya una inferencia
    en curso.
  - /health NO reinicia el temporizador: un monitor que haga ping cada minuto
    mantendría el proceso vivo para siempre. Solo /chart lo reinicia.

Uso:
  python chart_server.py --port 8080
  curl -X POST http://127.0.0.1:8080/chart -H 'Content-Type: application/json' \
       -d '{"image": "ejemplos/grafico_demo.png"}'

Para limitar tambien la vida total del proceso (no solo la inactividad):
  timeout 3600 python chart_server.py --port 8080

Requiere: paddlepaddle==3.3.1 (CPU) y "paddleocr[doc-parser]".
Antes de la primera ejecucion: export TMPDIR=/var/tmp (evitar OSError 122).
"""

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from extractor_final import markdown_a_df, obtener_markdown

LOG = logging.getLogger("chart_server")

# El cuerpo solo contiene JSON con una ruta o URL: 1 MB sobra con margen.
# Limita la memoria de peticiones maliciosas o rotas (respuesta 413).
MAX_CUERPO = 1_048_576


def cargar_modelo():
    from paddleocr import ChartParsing

    LOG.info("Cargando modelo PP-Chart2Table (puede tardar ~95 s y 4.8 GB de RAM)...")
    modelo = ChartParsing(device="cpu")  # device explícito: el default prioriza GPU
    LOG.info("Modelo cargado. Listo para recibir peticiones.")
    return modelo


def df_a_markdown(df: "pd.DataFrame") -> str:
    """Convierte un DataFrame a tabla markdown SIN dependencias externas.

    (No se usa df.to_markdown(): requiere el paquete 'tabulate', que no es
    dependencia del proyecto y rompería la respuesta si no está instalado.)
    Los saltos de línea dentro de una celda se sustituyen por espacios para
    no romper la estructura de la tabla markdown.
    """
    def celda(v) -> str:
        return str(v).replace("\n", " ").replace("\r", " ")

    cabecera = "| " + " | ".join(celda(c) for c in df.columns) + " |"
    separador = "| " + " | ".join("---" for _ in df.columns) + " |"
    filas = [
        "| " + " | ".join(celda(v) for v in fila) + " |"
        for fila in df.itertuples(index=False)
    ]
    return "\n".join([cabecera, separador] + filas)


def crear_handler(modelo, estado):
    """Construye la clase del handler con acceso al modelo y al estado compartido."""

    class ChartHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            LOG.info("peticion %s: %s", self.address_string(), fmt % args)

        def _enviar_json(self, codigo, datos):
            cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            if codigo == 413:
                # Blindaje (leccion 12): con protocol_version HTTP/1.0 la
                # conexion se cierra tras cada respuesta y esto es inocuo;
                # si alguien activa HTTP/1.1, el cuerpo residual del 413
                # corromperia la request line de la siguiente peticion.
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):
            if self.path != "/health":
                self._enviar_json(404, {"ok": False, "error": "Solo GET /health o POST /chart o POST /vision o POST /revision"})
                return
            self._enviar_json(200, {
                "status": "ok",
                "modelo": "PP-Chart2Table" if modelo else "no cargado (--sin-modelo)",
                "modos": ["auto", "texto", "graficos", "doc", "objetos", "humano"],
                "revision": True,
                "auditoria": True,
                "uptime_s": round(time.time() - estado["inicio"]),
            })

        def _recibir_json(self, clave="image"):
            """Lee y valida el cuerpo JSON. Devuelve (None, datos) o (codigo, error).

            `clave` es la clave obligatoria segun el endpoint ('image' para
            /chart y /vision, 'archivo' para /revision).
            """
            try:
                largo = int(self.headers.get("Content-Length", 0))
            except ValueError:
                return 400, "Content-Length invalida"
            if largo <= 0:
                return 400, "Cuerpo JSON vacio"
            if largo > MAX_CUERPO:
                try:
                    # Drenar el cuerpo (limitado) antes de cerrar: el cliente
                    # termina de enviar y recibe el 413 en lugar de un
                    # BrokenPipe/RST (race clasico de HTTP con cuerpos grandes).
                    self.rfile.read(min(largo, MAX_CUERPO))
                except Exception:
                    pass  # cliente ya cerro la conexion
                return 413, "Cuerpo demasiado grande"
            try:
                datos = json.loads(self.rfile.read(largo).decode("utf-8"))
            except Exception as exc:  # JSON invalido
                return 400, f"JSON invalido: {exc}"
            if not isinstance(datos, dict) or not isinstance(datos.get(clave), str):
                # JSON valido pero sin la clave esperada: mensaje distinto del
                # JSON malformado, para que la API no mienta sobre la causa.
                return 400, f"Se espera un objeto JSON con la clave '{clave}'"
            return None, datos

        def _procesar_vision(self, datos):
            """POST /vision: enruta al modo indicado (import perezoso de
            vision.py: evita el ciclo chart_server -> vision -> chart_server)."""
            import vision

            modo = datos.get("modo", "auto")
            if modo not in vision.MODOS:
                self._enviar_json(400, {
                    "ok": False,
                    "error": f"modo invalido: {modo} (validos: {vision.MODOS})",
                })
                return
            con_fallback = bool(datos.get("fallback", False))
            LOG.info("Vision modo=%s para: %s", modo, datos["image"])
            resultado = vision.ejecutar(datos["image"], modo, con_fallback)
            self._enviar_json(200, resultado)

        def _procesar_revision(self, datos):
            """POST /revision: revision de formato/presentacion (xlsx).

            Sin modelos: analisis determinista (openpyxl) + comparacion y
            Vision IA 360 opcionales. No marca actividad de inferencia.
            """
            import revision

            if not os.path.exists(datos["archivo"]):
                self._enviar_json(400, {"ok": False,
                                        "error": f"el archivo no existe: {datos['archivo']}"})
                return
            reglas = datos.get("reglas") or None
            if reglas and not os.path.exists(reglas):
                self._enviar_json(400, {"ok": False,
                                        "error": f"el archivo de reglas no existe: {reglas}"})
                return
            LOG.info("Revision de: %s (reglas: %s)", datos["archivo"], reglas or "defaults")
            resultado = revision.revisar_documento(
                datos["archivo"], reglas=revision.cargar_reglas(reglas)[0],
                comparar=datos.get("comparar"),
                hoja_solo=datos.get("hoja"),
                max_hallazgos=int(datos.get("max_hallazgos", 500)),
                vision=datos.get("vision"),
                vision_host=datos.get("vision_host", "127.0.0.1"),
                vision_device=datos.get("vision_device", "cuda"),
                vision_modelo=datos.get("vision_modelo", "gemma3:4b"))
            self._enviar_json(200, resultado)

        def _procesar_auditoria(self, datos):
            """POST /auditoria: auditoría visual de gráficos/charts.

            Sin modelos: análisis determinista (superposiciones, leyenda,
            zoom, nitidez, contraste, layout NxN, sugerencias) + descripción
            VLM opt-in (--vision docbee|ollama). No marca actividad de
            inferencia del modelo pesado.
            """
            import auditoria_graficos

            if not os.path.exists(datos["image"]):
                self._enviar_json(400, {"ok": False,
                                        "error": f"el archivo no existe: {datos['image']}"})
                return
            LOG.info("Auditoria de: %s (vision: %s)",
                     datos["image"], datos.get("vision") or "off")
            resultado = auditoria_graficos.auditar(
                datos["image"], vision=datos.get("vision"),
                device=datos.get("vision_device", "gpu"))
            self._enviar_json(200, resultado)

        def do_POST(self):
            if self.path not in ("/chart", "/vision", "/revision", "/auditoria"):
                self._enviar_json(404, {"ok": False, "error": "Solo GET /health o POST /chart o POST /vision o POST /revision o POST /auditoria"})
                return

            clave = "archivo" if self.path == "/revision" else "image"
            error, datos = self._recibir_json(clave)
            if error:
                self._enviar_json(error, {"ok": False, "error": datos})
                return

            if self.path == "/revision":
                self._procesar_revision(datos)
                return
            if self.path == "/auditoria":
                self._procesar_auditoria(datos)
                return

            # Marcar actividad y bloquear el cierre por inactividad
            estado["ocupado"] = True
            estado["ultima_actividad"] = time.time()
            try:
                if self.path == "/vision":
                    self._procesar_vision(datos)
                    return
                if modelo is None:
                    self._enviar_json(503, {"ok": False,
                                            "error": "modelo no cargado (servidor con --sin-modelo)"})
                    return
                LOG.info("Inferencia iniciada para: %s", datos["image"])
                t0 = time.time()
                resultados = modelo.predict({"image": datos["image"]})
                if not resultados:
                    raise RuntimeError("No se obtuvo ningun resultado del modelo.")
                df = markdown_a_df(obtener_markdown(resultados[0]))
                LOG.info("Inferencia completada en %.1f s (%d filas)", time.time() - t0, len(df))
                self._enviar_json(200, {
                    "ok": True,
                    "filas": len(df),
                    "markdown": df_a_markdown(df),
                    "csv": df.to_csv(index=False),
                })
            except Exception as exc:
                LOG.exception("Error en la inferencia")
                self._enviar_json(500, {"ok": False, "error": str(exc)})
            finally:
                estado["ocupado"] = False

    return ChartHandler


def vigia(server, estado, tiempo_max_inactividad):
    """Cierra el servidor si lleva mas de `tiempo_max_inactividad` s sin inferencias."""
    while True:
        time.sleep(5)
        if estado["ocupado"]:
            continue
        inactivo = time.time() - estado["ultima_actividad"]
        if inactivo >= tiempo_max_inactividad:
            LOG.warning(
                "Sin peticiones de inferencia durante %.0f s. Cerrando por inactividad.",
                inactivo,
            )
            server.shutdown()  # Bloquea hasta que la peticion en curso termine (none en curso)
            return


def main():
    parser = argparse.ArgumentParser(description="Daemon de extraccion de graficos con PP-Chart2Table")
    parser.add_argument("--host", default="127.0.0.1", help="Interfaz de escucha (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Puerto (default: 8080)")
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Segundos de inactividad antes de cerrarse (default: 3600 = 1 hora)",
    )
    parser.add_argument(
        "--sin-modelo",
        action="store_true",
        help="No cargar el modelo VLM (4.8 GB): sirve /health y /revision "
             "sin inferencia (/chart responde 503)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.sin_modelo:
        modelo = None
        LOG.info("Modo sin modelo: solo /health y /revision (sin inferencia)")
    else:
        modelo = cargar_modelo()
    estado = {"inicio": time.time(), "ultima_actividad": time.time(), "ocupado": False}
    server = HTTPServer((args.host, args.port), crear_handler(modelo, estado))

    def apagar(_sig, _frame):
        LOG.info("Senal recibida, cerrando...")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, apagar)
    signal.signal(signal.SIGINT, apagar)

    LOG.info("chart_server escuchando en http://%s:%d (cierre automatico tras %d s de inactividad)",
             args.host, args.port, args.timeout)

    threading.Thread(
        target=vigia,
        args=(server, estado, args.timeout),
        daemon=True,
    ).start()

    try:
        server.serve_forever()
    except Exception:
        LOG.exception("Error fatal")
        sys.exit(1)
    finally:
        server.server_close()
    LOG.info("Proceso finalizado. Modelo descargado de la memoria.")


if __name__ == "__main__":
    main()
