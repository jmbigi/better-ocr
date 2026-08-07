#!/usr/bin/env python3
"""Orquestador web de captcha_ia: reCAPTCHA v2 con Playwright (modo REAL).

Navegador: chromium de Playwright (este equipo tiene playwright 1.62 en el
python del sistema con los navegadores ya descargados). La deteccion RT-DETR
corre en el venv del proyecto como subproceso por lotes: una sola carga del
modelo (~18 s, ~1 GB) para todas las celdas, sin tocar el entorno del venv y
sin chocar con la regla de un VLM por maquina (RT-DETR no es VLM).

Flujo: checkbox -> reto en iframe bframe -> instruccion del DOM (fallback
OCR opcional) -> tamano de cuadricula (3x3/4x4) -> captura -> resolver()
(celdas + deteccion batch + decision) -> clics JS en tiles (el.click(),
el clic normal de Playwright falla por los transforms anti-automatizacion) ->
VERIFY -> veredicto por el estado del checkbox ancla.

Las funciones puras (n_desde_tiles, indice_a_fila_col) son testeables sin
Playwright; el resto se valida en vivo (los selectores DOM de reCAPTCHA).

Uso:
    python3 captcha_web.py --url https://ejemplo.com/pagina-con-recaptcha
    python3 captcha_web.py --url ... --headed --salida /var/tmp/reto/
"""

import base64
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

SELEC_INSTRUCCION = (
    "#rc-imageselect .rc-imageselect-desc, "
    "#rc-imageselect .rc-imageselect-desc-noaccess"
)
# El prefijo de la tabla varia con el tamano del reto (rc-imageselect-table-33,
# rc-imageselect-table-44): los tiles se seleccionan por su propia clase.
SELEC_TILES = "td.rc-imageselect-tile"
# La tabla completa (sin la banda de instruccion, que va dentro del payload
# por encima de la cuadricula). La clase varia con el tamano del reto
# (rc-imageselect-table, -33, -44): selector por prefijo.
SELEC_TABLA = 'table[class*="rc-imageselect-table"]'
SELEC_PAYLOAD = ".rc-imageselect-payload"
# El boton de VERIFY en las versiones actuales es rc-button-default
# (historico: rc-button-go).
SELEC_VERIFY = "button.rc-button-default, .rc-button-default"
SELEC_SKIP = "button:has-text('Skip')"
SELEC_ERROR = ".rc-imageselect-error-response"
SELEC_CHECKBOX = "#recaptcha-anchor"

UMBRAL_OBJETIVO = 0.45
UMBRAL_RESTO = 0.6
TIEMPO_ESPERA_RETO = 20.0
TIEMPO_ESPERA_VEREDICTO = 15.0


def umbral_objetivo_para(n: int) -> float:
    """Umbral de la clase objetivo segun el tamano de la cuadricula.

    Los tiles 4x4 son mas chicos y los objetos reales puntuan mas bajo
    (medido en vivo, leccion 20 hallazgo 4: motos reales 0.24-0.28 en 4x4
    vs 0.45 en 3x3). Con el umbral fijo 0.45 la seleccion quedaba incompleta
    y el reto se rechazaba siempre. 0.30 sigue pudiendo perder las mas bajas:
    los scores por celda se reportan (resolver_offline) para ajustar con
    datos reales via --umbral-objetivo."""
    return 0.45 if n == 3 else 0.30


RE_VLM_SI = re.compile(r"\b(yes|y)\b", re.IGNORECASE)
RE_VLM_NO = re.compile(r"\bno\b", re.IGNORECASE)


def parsear_respuesta_vlm(texto: str):
    """Interpreta la respuesta binaria del VLM: True (si) | False (no) |
    None (indeterminada). Tolerante a variaciones de formato."""
    if not texto:
        return None
    t = texto.strip().lower()
    if RE_VLM_SI.search(t) and not RE_VLM_NO.search(t):
        return True
    if RE_VLM_NO.search(t) and not RE_VLM_SI.search(t):
        return False
    return None


def _garantizar_ollama(host: str = "127.0.0.1", puerto: int = 11434,
                       binario: str = ""):
    """Arranca el servidor ollama bajo demanda si no responde (mismo patron
    que scripts/bateria_360.py; no es un servicio permanente)."""
    try:
        with socket.create_connection((host, puerto), timeout=2):
            return True
    except OSError:
        pass
    binario = binario or os.path.expanduser("~/ollama/bin/ollama")
    if not os.path.exists(binario):
        import shutil
        binario = shutil.which("ollama") or binario
    if not os.path.exists(binario):
        return False
    try:
        subprocess.Popen([binario, "serve"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        return False
    for _ in range(30):
        try:
            with socket.create_connection((host, puerto), timeout=2):
                return True
        except OSError:
            time.sleep(2)
    return False


def _preguntar_ollama(imagen_pil, clase: str, host: str, modelo: str,
                      timeout_s: float) -> str:
    """Una pregunta binaria por celda: 'Does this image contain <clase>?
    Answer only yes or no.' Devuelve el texto crudo del modelo."""
    from PIL import Image

    import io

    buf = io.BytesIO()
    imagen_pil.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    prompt = (f"Does this image contain {clase}? "
              "Answer only yes or no.")
    cuerpo = json.dumps({
        "model": modelo, "prompt": prompt, "images": [b64], "stream": False,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(
        f"http://{host}:11434/api/generate", data=cuerpo,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read().decode()).get("response", "")


def fallback_vlm_ollama(celdas_pil: list, clase_objetivo: str = None,
                        host: str = "127.0.0.1", modelo: str = "gemma3:4b",
                        timeout_s: float = 90.0) -> dict:
    """Re-evalua las celdas con un VLM local (ollama). Pregunta binaria por
    celda — mas acotada que 'select all tiles with X', que sobre-selecciona
    (leccion 20). Devuelve {(fila, col): [{"clase", "score": 1.0}]} para las
    celdas con respuesta 'si'; las demas quedan vacias (el decisor las
    trata como inciertas). Sin clase_objetivo no se puede preguntar: {}."""
    if not clase_objetivo or not celdas_pil:
        return {}
    if not _garantizar_ollama(host):
        return {}
    resultado = {}
    for fila, col, imagen in celdas_pil:
        try:
            texto = _preguntar_ollama(imagen, clase_objetivo, host, modelo,
                                      timeout_s)
            if parsear_respuesta_vlm(texto) is True:
                resultado[(fila, col)] = [{"clase": clase_objetivo,
                                           "score": 1.0}]
        except Exception:
            continue  # celda fallida: queda incierta
    return resultado

VENV_PYTHON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".venv", "bin", "python")


def env_worker() -> dict:
    """Entorno para los subprocesos del venv (paddle en GPU).

    El host tiene librerias nvidia (CUDNN 9.1) en LD_LIBRARY_PATH que rompen
    paddle (compilado con 9.5): "undefined symbol: cudnnGetLibConfig" + SIGABRT
    (leccion 17). Fix verificado en scripts/bateria_360.py: anteponer las
    nvidia-* del site-packages del VENV y quitar rutas .pyenv.
    """
    import glob
    import site as _site

    raiz = os.path.dirname(os.path.abspath(__file__))
    venv_site = sorted(glob.glob(os.path.join(raiz, ".venv", "lib",
                                              "python*", "site-packages")))
    nvidia_dirs = []
    for sp in venv_site:
        nvidia_dirs += sorted(glob.glob(os.path.join(sp, "nvidia", "*", "lib")))
    ld = ":".join(p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":")
                  if p and ".pyenv" not in p)
    return {
        **os.environ,
        "TMPDIR": "/var/tmp",
        "PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT": "0",
        "LD_LIBRARY_PATH": ":".join(nvidia_dirs + [ld]),
    }

# Script del worker de deteccion (se ejecuta con el python del venv, que es
# quien tiene paddle/paddlex). modo_objetos_lote carga RT-DETR UNA vez para
# todo el lote: create_model no cachea y cargar por imagen multiplica el
# tiempo por N (hallazgo 2026-08-07, timeout del worker).
# CUDA_VISIBLE_DEVICES="" fuerza CPU: con GPU visible, paddle (build GPU)
# toca el cudnn 9.1 del pyenv y aborta con SIGABRT de forma NO determinista
# (leccion 18 ampliada: no solo ChartParsing, tambien RT-DETR; hallazgo
# 2026-08-07). RT-DETR en CPU: ~18 s medidos, determinista.
WORKER_DETECCION = r"""
import json, os, sys
sys.path.insert(0, %(raiz)r)
os.environ.setdefault("TMPDIR", "/var/tmp")
os.environ["CUDA_VISIBLE_DEVICES"] = ""
from vision import modo_objetos_lote
paths = json.load(sys.stdin)
json.dump(modo_objetos_lote(paths), sys.stdout)
"""

# Worker OCR (fallback de instruccion): PP-OCRv6 en modo texto (vision).
# CUDA_VISIBLE_DEVICES="" igual que el worker de deteccion (leccion 18).
WORKER_OCR = r"""
import json, os, sys
sys.path.insert(0, %(raiz)r)
os.environ.setdefault("TMPDIR", "/var/tmp")
os.environ["CUDA_VISIBLE_DEVICES"] = ""
from vision import modo_texto
paths = json.load(sys.stdin)
out = {}
for p in paths:
    res = modo_texto(p)
    out[p] = " ".join(linea["texto"] for linea in res.get("lineas", [])) if res.get("ok") else ""
json.dump(out, sys.stdout)
"""


def n_desde_tiles(n_tiles: int):
    """Cantidad de tiles -> tamano de la cuadricula de reCAPTCHA v2 (3 o 4)."""
    if n_tiles not in (9, 16):
        return None
    return int(round(n_tiles ** 0.5))


def indice_a_fila_col(indice: int, n: int):
    """Indice plano del tile (orden de reCAPTCHA: fila-mayor) -> (fila, col)."""
    return divmod(indice, n)


def detectar_batch_worker(celdas_pil: list) -> dict:
    """Deteccion RT-DETR por lotes via subproceso del venv.

    `celdas_pil`: lista de (fila, col, imagen PIL). Devuelve
    {(fila, col): [detecciones...]}. Una carga de RT-DETR para todo el lote;
    ~18 s la primera y ~1 GB de RAM. Celdas sin resultado -> [] (incierta).
    """
    if not celdas_pil:
        return {}
    rutas = {}
    directorio = tempfile.mkdtemp(prefix="captcha_celdas_")
    try:
        for fila, col, imagen in celdas_pil:
            ruta = os.path.join(directorio, f"f{fila}c{col}.png")
            imagen.save(ruta)
            rutas[(fila, col)] = ruta
        script = WORKER_DETECCION % {"raiz": os.path.dirname(os.path.abspath(__file__))}
        proc = subprocess.run(
            [VENV_PYTHON, "-c", script],
            input=json.dumps(list(rutas.values())),
            capture_output=True, text=True, timeout=180, env=env_worker(),
        )
        if proc.returncode != 0:
            return {clave: [] for clave in rutas}
        salida = json.loads(proc.stdout)
        return {clave: salida.get(ruta, []) for clave, ruta in rutas.items()}
    except (json.JSONDecodeError, subprocess.TimeoutExpired):
        return {clave: [] for clave in rutas}
    finally:
        for ruta in rutas.values():
            try:
                os.unlink(ruta)
            except OSError:
                pass
        try:
            os.rmdir(directorio)
        except OSError:
            pass


def leer_instruccion(bframe) -> str:
    """Texto de la instruccion del reto (desc del DOM). count() evita el
    auto-wait de 30 s de Playwright cuando el elemento no existe; el desc
    puede renderizarse un instante despues de la cuadricula (se reintenta)."""
    locator = bframe.locator(SELEC_INSTRUCCION)
    for _ in range(5):
        if locator.count() > 0:
            try:
                texto = locator.first.inner_text().strip()
                if texto:
                    return texto
            except Exception:
                pass
        time.sleep(0.4)
    return ""


def worker_ocr_texto(rutas: list) -> dict:
    """OCR PP-OCRv6 por subproceso del venv: ruta -> texto ('' si falla)."""
    if not rutas:
        return {}
    script = WORKER_OCR % {"raiz": os.path.dirname(os.path.abspath(__file__))}
    try:
        proc = subprocess.run(
            [VENV_PYTHON, "-c", script],
            input=json.dumps(rutas),
            capture_output=True, text=True, timeout=120, env=env_worker(),
        )
        if proc.returncode != 0:
            return {r: "" for r in rutas}
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, subprocess.TimeoutExpired):
        return {r: "" for r in rutas}


def leer_instruccion_ocr(bframe, worker=worker_ocr_texto) -> str:
    """Fallback OCR de la instruccion: captura la banda superior del payload
    (el texto va encima de la cuadricula) y la lee con PP-OCRv6."""
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="captcha_inst_") as directorio:
        try:
            ruta_payload = os.path.join(directorio, "payload.png")
            bframe.locator(SELEC_PAYLOAD).screenshot(path=ruta_payload)
            im = Image.open(ruta_payload)
            ancho, alto = im.size
            ruta_franja = os.path.join(directorio, "franja.png")
            im.crop((0, 0, ancho, alto // 4)).save(ruta_franja)
            texto = worker([ruta_franja]).get(ruta_franja, "")
            return " ".join(texto.split())
        except Exception:
            return ""


def tamano_cuadricula(bframe):
    """Cuenta los tiles del reto -> n (3 o 4) o None."""
    try:
        n_tiles = bframe.locator(SELEC_TILES).count()
    except Exception:
        return None
    return n_desde_tiles(n_tiles)


def capturar_cuadricula(bframe):
    """Captura SOLO la cuadricula (la tabla de tiles; la instruccion va en
    una banda aparte y no debe entrar en las celdas)."""
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="captcha_reto_") as directorio:
        ruta = os.path.join(directorio, "cuadricula.png")
        bframe.locator(SELEC_TABLA).screenshot(path=ruta)
        return Image.open(ruta)
def pulsar_tiles(bframe, seleccion: list, n: int) -> None:
    """Clic JS en cada tile seleccionado (el.click() evita el fallo
    'outside of viewport' de los transforms anti-automatizacion). Un tile
    obsoleto (re-render) no aborta el resto."""
    for fila, col in seleccion:
        indice = fila * n + col
        try:
            bframe.locator(SELEC_TILES).nth(indice).evaluate("el => el.click()")
        except Exception:
            continue


def pulsar_verificar(bframe) -> None:
    """Pulsa VERIFY si existe. El clic REAL de Playwright dispara el POST de
    verificacion (verificado en vivo: wrong -> api2/replaceimage); el clic JS
    a veces se ignora en silencio. Clic JS como fallback (count() evita el
    auto-wait de 30 s)."""
    try:
        if bframe.locator(SELEC_VERIFY).count() > 0:
            try:
                bframe.locator(SELEC_VERIFY).first.click(timeout=4000)
                return
            except Exception:
                pass
            bframe.locator(SELEC_VERIFY).first.evaluate("el => el.click()")
    except Exception:
        pass


def pulsar_skip(bframe) -> bool:
    """Pulsa SKIP si existe (instrucciones del tipo 'si no hay ninguna').
    Clic REAL primero (el JS a veces se ignora en silencio, hallazgo en vivo
    de VERIFY) con JS como fallback."""
    try:
        if bframe.locator(SELEC_SKIP).count() > 0:
            try:
                bframe.locator(SELEC_SKIP).first.click(timeout=4000)
                return True
            except Exception:
                pass
            bframe.locator(SELEC_SKIP).first.evaluate("el => el.click()")
            return True
    except Exception:
        pass
    return False


def veredicto(pagina, bframe) -> str:
    """Tras VERIFY: 'ok' | 'error' | 'pendiente' (espera acotada)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < TIEMPO_ESPERA_VEREDICTO:
        try:
            if bframe.locator(SELEC_ERROR).count() > 0:
                return "error"
        except Exception:
            pass  # bframe en re-render (replaceimage): reintentar
        # si el bframe se cerro: exito
        try:
            if not any("bframe" in (f.url or "") for f in pagina.frames):
                return "ok"
        except Exception:
            pass
        # el ancla vive en el iframe de reCAPTCHA (no en frames[0])
        for f in pagina.frames:
            if "recaptcha" in (f.url or "") and f != pagina.main_frame:
                try:
                    clase = f.locator(SELEC_CHECKBOX).get_attribute("class") or ""
                    if "recaptcha-checkbox-checked" in clase:
                        return "ok"
                except Exception:
                    pass
                break
        time.sleep(0.5)
    return "pendiente"


def resolver_web(url: str, headed: bool = False, salida: str = "",
                 timeout_s: float = 150.0, max_intentos: int = 3,
                 fallback_vlm=None, detectar_lote=None,
                 ocr_fallback=None, umbral_objetivo: float = None) -> dict:
    """Ciclo completo real: checkbox -> reto -> instruccion -> tiles ->
    VERIFY/SKIP -> veredicto, con reintento tras re-render del reto.

    Import perezoso de Playwright (solo python del sistema); la deteccion va
    al venv por subproceso. `fallback_vlm(celdas_inciertas)` es un hook
    reservado (requiere un VLM libre: docbee/ollama); si no se pasa, las
    celdas inciertas simplemente no se pulsan. `detectar_lote(celdas_pil)`
    y `ocr_fallback(bframe)` permiten inyectar detector/OCR (tests)."""
    from playwright.sync_api import sync_playwright

    from captcha_ia import resolver

    if detectar_lote is None:
        detectar_lote = detectar_batch_worker
    if ocr_fallback is None:
        ocr_fallback = leer_instruccion_ocr

    t_inicio = time.monotonic()
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=not headed)
        # locale fijo en-US: el parser de instrucciones de captcha_ia es
        # ingles (las instrucciones de reCAPTCHA siguen el idioma del
        # navegador: hl=en con locale en-US, verificado en la demo).
        contexto = navegador.new_context(locale="en-US",
                                         viewport={"width": 1280, "height": 900})
        pagina = contexto.new_page()
        try:
            pagina.goto(url, timeout=30000)
        except Exception as exc:
            navegador.close()
            return {"ok": False, "error": f"navegacion fallida: {exc}"}
        pagina.wait_for_timeout(1500)

        # 1) checkbox ancla dentro del iframe de reCAPTCHA
        # (excluir el frame principal: la propia URL de la pagina puede
        # contener "recaptcha", p. ej. la demo de Google; con red inestable
        # el iframe puede tardar mas de un par de segundos en montarse)
        t0 = time.monotonic()
        marco_recaptcha = None
        while time.monotonic() - t0 < TIEMPO_ESPERA_RETO:
            for f in pagina.frames:
                if "recaptcha" in (f.url or "") and "/anchor" in f.url:
                    marco_recaptcha = f
                    break
            if marco_recaptcha is None:
                for f in pagina.frames:
                    if ("recaptcha" in (f.url or "")
                            and f != pagina.main_frame):
                        marco_recaptcha = f
                        break
            if marco_recaptcha is not None:
                break
            try:
                pagina.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            time.sleep(1.0)
        if marco_recaptcha is None:
            navegador.close()
            return {"ok": False, "error": "no se encontro el iframe de reCAPTCHA"}
        try:
            marco_recaptcha.locator(SELEC_CHECKBOX).click()
        except Exception as exc:
            navegador.close()
            return {"ok": False, "error": f"clic del ancla fallido: {exc}"}

        # 2) esperar el reto en el iframe bframe
        t0 = time.monotonic()
        bframe = None
        while time.monotonic() - t0 < TIEMPO_ESPERA_RETO:
            for f in pagina.frames:
                if "bframe" in (f.url or ""):
                    bframe = f
                    break
            if bframe:
                break
            time.sleep(0.5)
        if bframe is None:
            navegador.close()
            return {"ok": False, "error": "el reto no aparecio en bframe"}

        from captcha_ia import aumentar_escala, celdas_grid

        resultado = None
        for intento in range(1, max_intentos + 1):
            if time.monotonic() - t_inicio > timeout_s:
                navegador.close()
                return {"ok": False, "error": "tiempo maximo agotado",
                        "intento": intento}

            # 3) instruccion + cuadricula + captura (puede re-renderizarse)
            instruccion = leer_instruccion(bframe)
            if not instruccion:
                instruccion = ocr_fallback(bframe)
            n = tamano_cuadricula(bframe)
            if n is None:
                continue  # re-render en curso: reintentar
            try:
                imagen = capturar_cuadricula(bframe)
            except Exception:
                continue  # bframe en re-render: reintentar con la nueva captura

            # 4) resolucion: celdas + RT-DETR batch + decision
            celdas_pil = [(f, c, aumentar_escala(celda))
                          for f, c, celda in celdas_grid(imagen, n=n)]
            detecciones = detectar_lote(celdas_pil)
            if not any(detecciones.values()):
                # el worker fallo, el reto se re-renderizo a mitad, o la
                # clase no es COCO (crosswalks/stairs): no pulsar a ciegas
                if fallback_vlm is not None:
                    from captcha_ia import parsear_instruccion
                    clase = parsear_instruccion(instruccion)
                    if clase and bframe.locator(SELEC_TILES).count() >= 4:
                        detecciones = fallback_vlm(celdas_pil, clase)
                if not any(detecciones.values()):
                    continue  # reintentar con la nueva captura

            def detectar_celda(_celda, fila, col):
                return detecciones.get((fila, col), [])

            res = resolver(imagen, instruccion, detectar_celda, n=n,
                           umbral_objetivo=umbral_objetivo
                           or umbral_objetivo_para(n),
                           umbral_resto=UMBRAL_RESTO)
            es_variante_none = "skip" in instruccion.lower()
            if not res["ok"]:
                # instruccion no parseable: opcion conservadora = SKIP
                pulsar_skip(bframe)
            elif es_variante_none and not res["seleccion"]:
                # "If there are none, click skip" y no detectamos el objeto:
                # la respuesta correcta es SKIP, no VERIFY sin tiles
                pulsar_skip(bframe)
            else:
                pulsar_tiles(bframe, res["seleccion"], n)
                pulsar_verificar(bframe)

            # 5) veredicto
            resultado = veredicto(pagina, bframe)
            if salida:
                os.makedirs(salida, exist_ok=True)
                try:
                    imagen.save(os.path.join(salida, f"reto_{n}x{n}_i{intento}.png"))
                except Exception:
                    pass
            if resultado == "ok":
                camino = "skip" if not res["ok"] else "tiles"
                if salida:
                    with open(os.path.join(salida, "resultado.json"), "w",
                              encoding="utf-8") as f:
                        json.dump({**res, "ok": True, "camino": camino,
                                   "veredicto": resultado,
                                   "instruccion": instruccion,
                                   "intento": intento}, f,
                                  ensure_ascii=False, indent=2)
                navegador.close()
                return {**res, "ok": True, "camino": camino,
                        "veredicto": resultado,
                        "instruccion": instruccion, "intento": intento,
                        "tiempo_s": round(time.monotonic() - t_inicio, 1)}
            # error o pendiente: el reto se re-renderiza; reintentar

        navegador.close()
        return {"ok": False, "error": "sin exito tras varios intentos",
                "veredicto": resultado or "pendiente",
                "instruccion": instruccion,
                "intento": max_intentos,
                "tiempo_s": round(time.monotonic() - t_inicio, 1)}


def resolver_offline(imagen_ruta: str, n: int, instruccion: str,
                     umbral_objetivo: float = None) -> dict:
    """Pipeline completo SIN navegador sobre una cuadricula guardada (pasada
    por celda offline): celdas + RT-DETR batch (una carga) + decision.

    Incluye las detecciones por celda en el resultado (P0.1: reportar los
    scores para ajustar el umbral con datos reales)."""
    from PIL import Image

    from captcha_ia import aumentar_escala, celdas_grid, resolver

    if umbral_objetivo is None:
        umbral_objetivo = umbral_objetivo_para(n)
    imagen = Image.open(imagen_ruta)
    celdas_pil = [(f, c, aumentar_escala(celda))
                  for f, c, celda in celdas_grid(imagen, n=n)]
    detecciones = detectar_batch_worker(celdas_pil)

    def detectar_celda(_celda, fila, col):
        return detecciones.get((fila, col), [])

    res = resolver(imagen, instruccion, detectar_celda, n=n,
                   umbral_objetivo=umbral_objetivo, umbral_resto=UMBRAL_RESTO)
    res["celdas_detectadas"] = sum(1 for v in detecciones.values() if v)
    res["umbral_objetivo"] = umbral_objetivo
    res["detecciones_por_celda"] = {
        f"{f},{c}": detecciones.get((f, c), [])
        for f, c, _ in celdas_pil
    }
    return res


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="reCAPTCHA v2 real (Playwright)")
    parser.add_argument("--url", help="pagina con reCAPTCHA v2")
    parser.add_argument("--offline", metavar="IMAGEN",
                        help="pasada por celda offline sobre una cuadricula guardada")
    parser.add_argument("--n", type=int, choices=(3, 4),
                        help="tamano de la cuadricula (con --offline)")
    parser.add_argument("--instruccion", default="",
                        help="instruccion del reto (con --offline)")
    parser.add_argument("--headed", action="store_true",
                        help="navegador visible (default: headless)")
    parser.add_argument("--salida", default="",
                        help="directorio para guardar captura y resultado")
    parser.add_argument("--timeout", type=float, default=150.0,
                        help="tiempo maximo total en segundos")
    parser.add_argument("--max-intentos", type=int, default=3,
                        help="reintentos tras re-render del reto")
    parser.add_argument("--umbral-objetivo", type=float, default=None,
                        help="umbral de la clase objetivo (default por tamano: "
                             "0.45 en 3x3, 0.30 en 4x4; leccion 20 hallazgo 4)")
    parser.add_argument("--vlm-fallback", action="store_true",
                        help="celdas sin deteccion COCO re-evaluadas por un "
                             "VLM local (ollama gemma3:4b, arrancado bajo "
                             "demanda; pregunta binaria por celda)")
    parser.add_argument("--vlm-modelo", default="gemma3:4b",
                        help="modelo ollama para --vlm-fallback")
    args = parser.parse_args()

    if args.offline:
        if not args.n or not args.instruccion:
            parser.error("--offline requiere --n y --instruccion")
        resultado = resolver_offline(args.offline, args.n, args.instruccion,
                                     umbral_objetivo=args.umbral_objetivo)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return
    if not args.url:
        parser.error("se requiere --url (modo real) o --offline IMAGEN")

    if args.vlm_fallback:
        import functools
        fallback_vlm = functools.partial(fallback_vlm_ollama,
                                         modelo=args.vlm_modelo)
    else:
        fallback_vlm = None
    resultado = resolver_web(args.url, headed=args.headed,
                             salida=args.salida, timeout_s=args.timeout,
                             max_intentos=args.max_intentos,
                             umbral_objetivo=args.umbral_objetivo,
                             fallback_vlm=fallback_vlm)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
