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
    "#rc-imageselect .rc-imageselect-desc-noaccess, "
    "#rc-imageselect .rc-imageselect-desc-no-canonical"
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

# Clases donde la pasada de recall del VLM queda EXCLUIDA (datos en vivo
# 2026-08-07, 23 runs): en car, 2/2 fallos coincidieron con una celda VLM
# anadida en celda vacia (v4/v8, seleccion de 5 en vez de 4) mientras las
# 3 victorias de car fueron sin celdas VLM; en el resto (motorcycle,
# bicycle, traffic light) las celdas VLM fueron correctas (t1/t4/t6, 2-3
# celdas cada uno, ok). La confirmacion de candidatos SI se mantiene.
SIN_RECALL_CLASES = {"car"}


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


# Parche GPU de docbee (leccion 17): paddle.cumsum promueve int32 -> int64 y
# flash_attn_unpadded exige int32; max_pixels 0.5M para caber en 8 GB VRAM.
PATCH_DOCBEE = """
from paddlex.inference.models.doc_vlm.modeling import qwen2_vl as _qm
_orig_unpad = _qm._get_unpad_data
def _fix_unpad(mask):
    indices, cu, mx = _orig_unpad(mask)
    return indices, cu.astype('int32'), mx
_qm._get_unpad_data = _fix_unpad
from paddlex.inference.models.doc_vlm.processors import qwen2_vl as _pq
_pq.MAX_PIXELS = 262144
"""

# Worker docbee: una sola carga del modelo, pregunta binaria por celda.
WORKER_DOCBEE = r"""
import json, sys
sys.path.insert(0, %(raiz)r)
%(patch)s
from paddleocr import DocVLM
modelo = DocVLM(model_name="PP-DocBee-2B", device="gpu")
preguntas = json.load(sys.stdin)
out = {}
for ruta, clase in preguntas:
    try:
        res = modelo.predict({"image": ruta,
                              "query": f"Does this image contain {clase}? "
                                       "Answer only yes or no."})
        texto = ""
        for x in res:
            texto += (x.json.get("res", {}).get("result") or "") + " "
        out[ruta] = texto.strip()
    except Exception:
        out[ruta] = ""
json.dump(out, sys.stdout)
"""


def fallback_vlm_docbee(celdas_pil: list, clase_objetivo: str = None,
                        timeout_s: float = 600.0) -> dict:
    """Re-evalua las celdas con docbee (PP-DocBee-2B) en GPU: subproceso del
    venv con el env de la leccion 17 (env_worker), una sola carga del modelo.

    MEDIDO EN VIVO (2026-08-07, tiles reales): docbee es mas conservador que
    gemma3:4b y coincide con RT-DETR — traffic light 4x4: docbee dijo SI a
    las 2 celdas de RT-DETR (gemma dijo SI a 5 sin solapamiento); crosswalk:
    gemma sobre-selecciono 2 celdas segun docbee. Mejor modelo de
    confirmacion que gemma para la pregunta binaria."""
    if not clase_objetivo or not celdas_pil:
        return {}
    rutas = {}
    directorio = tempfile.mkdtemp(prefix="captcha_docbee_")
    try:
        for fila, col, imagen in celdas_pil:
            ruta = os.path.join(directorio, f"f{fila}c{col}.png")
            imagen.save(ruta)
            rutas[(fila, col)] = ruta
        script = WORKER_DOCBEE % {
            "raiz": os.path.dirname(os.path.abspath(__file__)),
            "patch": PATCH_DOCBEE,
        }
        proc = subprocess.run(
            [VENV_PYTHON, "-c", script],
            input=json.dumps([(r, clase_objetivo) for r in rutas.values()]),
            capture_output=True, text=True, timeout=timeout_s,
            env=env_worker())
        if proc.returncode != 0:
            return {}
        salida = json.loads(proc.stdout)
        resultado = {}
        for (fila, col), ruta in rutas.items():
            if parsear_respuesta_vlm(salida.get(ruta, "")) is True:
                resultado[(fila, col)] = [{"clase": clase_objetivo,
                                           "score": 1.0}]
        return resultado
    except (json.JSONDecodeError, subprocess.TimeoutExpired):
        return {}
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


def celda_de_bbox(bbox: list, n: int, ancho: float, alto: float):
    """Centro del bbox (x1,y1,x2,y2) -> (fila, col) del tile correspondiente.

    Usado para mapear las detecciones de la imagen COMPLETA del reto a sus
    celdas (la deteccion completa recupera objetos que el recorte por celda
    pierde — medido en el corpus de fallos 2026-08-07: 4/9 casos de
    sub-seleccion recuperaron objetos)."""
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    tam = min(ancho, alto) // n
    ox = (ancho - tam * n) // 2
    oy = (alto - tam * n) // 2
    fila = int((cy - oy) // tam)
    col = int((cx - ox) // tam)
    if 0 <= fila < n and 0 <= col < n:
        return (fila, col)
    return None


# Worker de deteccion sobre la imagen COMPLETA del reto (mejor recall que
# por celda, medido en el corpus 2026-08-07): RT-DETR sobre la cuadricula
# entera, bboxes mapeados a celdas por el centro (celda_de_bbox). Corre en
# el venv por subproceso con CPU forzada (leccion 18) para funcionar desde
# el python del sistema.
WORKER_GRID = r"""
import json, os, sys
sys.path.insert(0, %(raiz)r)
os.environ.setdefault("TMPDIR", "/var/tmp")
os.environ["CUDA_VISIBLE_DEVICES"] = ""
from vision import modo_objetos_lote
ruta = sys.argv[1]
json.dump(modo_objetos_lote([ruta]), sys.stdout)
"""


def detectar_cuadricula_worker(imagen, n: int) -> dict:
    """Deteccion RT-DETR sobre la imagen COMPLETA del reto, mapeada a celdas
    por el centro del bbox. Subproceso del venv (WORKER_GRID), una sola
    carga del modelo, CPU forzada.

    Mejor recall que la deteccion por celda (las celdas recortadas pierden
    el contexto de la escena): corpus de 58 fallos, plausibles 22 -> 26.
    """
    with tempfile.TemporaryDirectory(prefix="captcha_grid_") as directorio:
        ruta = os.path.join(directorio, "cuadricula.png")
        imagen.save(ruta)
        script = WORKER_GRID % {"raiz": os.path.dirname(os.path.abspath(__file__))}
        proc = subprocess.run(
            [VENV_PYTHON, "-c", script, ruta],
            capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            return {}
        salida = json.loads(proc.stdout)
    ancho, alto = imagen.size
    detecciones = {}
    for det in salida.get(ruta, []):
        celda = celda_de_bbox(det.get("bbox", []), n, ancho, alto)
        if celda is not None:
            detecciones.setdefault(celda, []).append(det)
    return detecciones


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
                 ocr_fallback=None, umbral_objetivo: float = None,
                 vlm_recall: bool = False, archivo_fallos: str = "") -> dict:
    """Ciclo completo real: checkbox -> reto -> instruccion -> tiles ->
    VERIFY/SKIP -> veredicto, con reintento tras re-render del reto.

    Import perezoso de Playwright (solo python del sistema); la deteccion va
    al venv por subproceso. `fallback_vlm(celdas_inciertas)` es un hook
    reservado (requiere un VLM libre: docbee/ollama); si no se pasa, las
    celdas inciertas simplemente no se pulsan. `detectar_lote(celdas_pil)`
    y `ocr_fallback(bframe)` permiten inyectar detector/OCR (tests)."""
    from playwright.sync_api import sync_playwright

    from captcha_ia import resolver

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
        registro = []
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

            # 4) resolucion: deteccion sobre la imagen COMPLETA (mejor
            # recall que por celda, medido en el corpus) + decision
            celdas_pil = [(f, c, aumentar_escala(celda))
                          for f, c, celda in celdas_grid(imagen, n=n)]
            if detectar_lote is None:
                detecciones = detectar_cuadricula_worker(imagen, n)
            else:
                detecciones = detectar_lote(celdas_pil)
            es_variante_none = "skip" in instruccion.lower()
            if not es_variante_none and fallback_vlm is not None:
                from captcha_ia import parsear_instruccion
                clase = parsear_instruccion(instruccion)
                if clase and bframe.locator(SELEC_TILES).count() >= 4:
                    detecciones = _aplicar_fallback_vlm(
                        detecciones, celdas_pil, clase,
                        umbral_objetivo or umbral_objetivo_para(n),
                        fallback_vlm, recall=vlm_recall)
            if not any(detecciones.values()) and not es_variante_none:
                # el worker fallo o el reto se re-renderizo a mitad, o la
                # clase no es COCO y el VLM tampoco encontro nada: no pulsar
                # a ciegas; reintentar con la nueva captura
                continue
            # la variante "click skip" NO es clic a ciegas: se deja
            # pasar para que el camino SKIP la resuelva

            def detectar_celda(_celda, fila, col):
                return detecciones.get((fila, col), [])

            res = resolver(imagen, instruccion, detectar_celda, n=n,
                           umbral_objetivo=umbral_objetivo
                           or umbral_objetivo_para(n),
                           umbral_resto=UMBRAL_RESTO)
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
            # registro por intento (P0.1: los fallos en vivo deben ser
            # analizables: instruccion, decision y scores por celda)
            registro.append({
                "intento": intento,
                "instruccion": instruccion,
                "n": n,
                "clase_objetivo": res.get("clase_objetivo"),
                "seleccion": sorted(res.get("seleccion", [])),
                "descartadas": sorted(res.get("descartadas", [])),
                "captura": (f"reto_{n}x{n}_i{intento}.png" if salida else ""),
                "veredicto": resultado,
                "inciertas": sorted(res.get("inciertas", [])),
                "detecciones_por_celda": {
                    f"{f},{c}": detecciones.get((f, c), [])
                    for f, c, _ in celdas_pil
                },
                "veredicto": resultado,
            })
            if salida:
                with open(os.path.join(salida, "intentos.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(registro, f, ensure_ascii=False, indent=2)
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
        # corpus de fallos (P0.1 + analisis avanzado): cada intento fallido
        # queda como caso.json con la decision, los scores y la captura
        # vinculada, para re-evaluar configuraciones sin nuevas ejecuciones
        if archivo_fallos:
            try:
                os.makedirs(archivo_fallos, exist_ok=True)
                marca = int(time.time())
                for rec in registro:
                    ruta = os.path.join(archivo_fallos,
                                        f"caso_{marca}_i{rec['intento']}.json")
                    with open(ruta, "w", encoding="utf-8") as f:
                        json.dump(rec, f, ensure_ascii=False, indent=2)
            except OSError:
                pass
        return {"ok": False, "error": "sin exito tras varios intentos",
                "veredicto": resultado or "pendiente",
                "clase_objetivo": res.get("clase_objetivo"),
                "seleccion": sorted(res.get("seleccion", [])),
                "instruccion": instruccion,
                "intento": max_intentos,
                "tiempo_s": round(time.monotonic() - t_inicio, 1)}


def listar_fallos(directorio: str) -> list:
    """Resumen del corpus de fallos (caso_*.json): filas para analisis
    avanzado — clase, n, seleccion, tamano de seleccion, veredicto e
    instruccion de cada intento guardado."""
    filas = []
    if not os.path.isdir(directorio):
        return filas
    for nombre in sorted(os.listdir(directorio)):
        if not nombre.startswith("caso_") or not nombre.endswith(".json"):
            continue
        try:
            with open(os.path.join(directorio, nombre), encoding="utf-8") as f:
                caso = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        filas.append({
            "archivo": nombre,
            "n": caso.get("n"),
            "clase": caso.get("clase_objetivo"),
            "seleccion": len(caso.get("seleccion", [])),
            "veredicto": caso.get("veredicto"),
            "instruccion": caso.get("instruccion", "").replace("\n", " "),
            "captura": caso.get("captura", ""),
        })
    return filas


def _aplicar_fallback_vlm(detecciones: dict, celdas_pil: list, clase: str,
                          umbral: float, fallback_vlm, recall: bool = True) -> dict:
    """Dos etapas + cobertura de huecos opcional (patron DDG validado en vivo):

    1. Los candidatos de la clase objetivo del worker se confirman/descartan
       con el VLM binario por tile (4 'birds' -> 3 ducks).
    2. Si recall=True, las celdas SIN NINGUNA deteccion se re-evaluan con el
       mismo VLM (puede encontrar objetos que el detector perdio). MEDIDO EN
       VIVO 2026-08-07: ayuda en clases dificiles (4x4 traffic lights: 2/3
       celdas del VLM, reto resuelto) pero sobre-agrega en clases comunes
       (cars: 2/2 runs con una 5a celda VLM erronea fueron rechazados; los
       runs con solo RT-DETR pasaron) — configurable con --sin-vlm-recall.
    3. Si el worker no detecto nada (clase no-COCO), el VLM cubre todas las
       celdas.

    Devuelve las detecciones filtradas/aumentadas. Sin fallback o sin clase
    no cambia nada."""
    if fallback_vlm is None or not clase:
        return detecciones
    candidatas = [(f, c, celda) for (f, c, celda) in celdas_pil
                  if any(d.get("clase") == clase
                         and d.get("score", 0) >= umbral
                         for d in detecciones.get((f, c), []))]
    inciertas = [(f, c, celda) for (f, c, celda) in celdas_pil
                 if not detecciones.get((f, c), [])]
    if not candidatas:
        # Sin candidatos del objetivo: con recall=True el VLM cubre TODAS
        # las celdas (clases no-COCO); con recall=False (default) no se
        # anade nada — devolver sin preguntar. La cobertura total demostro
        # sobre-seleccion en vivo (p4-i1: 6 celdas VLM rechazadas).
        if not recall:
            return detecciones
        encontradas = fallback_vlm(celdas_pil, clase) or {}
        for (f, c), dets in encontradas.items():
            detecciones.setdefault((f, c), []).extend(dets)
        return detecciones
    confirmadas = fallback_vlm(candidatas, clase) or {}
    for (f, c, _) in candidatas:
        if (f, c) not in confirmadas:
            detecciones[(f, c)] = []
    # pasada extra sobre las celdas sin ninguna deteccion (recall), salvo
    # para las clases de SIN_RECALL_CLASES (politica por clase, datos en
    # vivo: en 'car' las celdas VLM coincidieron con 2/2 fallos)
    if recall and clase not in SIN_RECALL_CLASES and inciertas:
        encontradas = fallback_vlm(inciertas, clase) or {}
        for (f, c, _) in inciertas:
            if (f, c) in encontradas:
                detecciones[(f, c)] = encontradas[(f, c)]
    return detecciones


def resolver_offline(imagen_ruta: str, n: int, instruccion: str,
                     umbral_objetivo: float = None,
                     fallback_vlm=None, vlm_recall: bool = False) -> dict:
    """Pipeline completo SIN navegador sobre una cuadricula guardada (pasada
    por celda offline): celdas + RT-DETR batch (una carga) + decision.

    Si el worker no detecta nada (clase no-COCO) y se pasa fallback_vlm
    (hook fallback_vlm(celdas_pil, clase)), las celdas se re-evaluan con el
    VLM. Incluye las detecciones por celda en el resultado (P0.1: reportar
    los scores para ajustar el umbral con datos reales)."""
    from PIL import Image

    from captcha_ia import aumentar_escala, celdas_grid, parsear_instruccion, resolver

    if umbral_objetivo is None:
        umbral_objetivo = umbral_objetivo_para(n)
    imagen = Image.open(imagen_ruta)
    celdas_pil = [(f, c, aumentar_escala(celda))
                  for f, c, celda in celdas_grid(imagen, n=n)]
    detecciones = detectar_cuadricula_worker(imagen, n)
    if fallback_vlm is not None:
        clase = parsear_instruccion(instruccion)
        detecciones = _aplicar_fallback_vlm(detecciones, celdas_pil, clase,
                                            umbral_objetivo, fallback_vlm,
                                            recall=vlm_recall)

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
    parser.add_argument("--vlm-fallback", nargs="?", const="ollama",
                        choices=("docbee", "ollama"), default=None,
                        help="VLM de confirmacion/cobertura: 'ollama' "
                             "(gemma3:4b, arrancado bajo demanda; default) o "
                             "'docbee' (PP-DocBee-2B en GPU, env leccion 17 — "
                             "mas conservador y concordante con RT-DETR, "
                             "medido en vivo 2026-08-07). Pregunta binaria "
                             "por celda")
    parser.add_argument("--vlm-modelo", default="gemma3:4b",
                        help="modelo ollama para --vlm-fallback ollama")
    parser.add_argument("--vlm-recall", action="store_true",
                        help="habilita la pasada de ADICION del VLM (recall "
                             "sobre celdas sin deteccion + cobertura de "
                             "clases no-COCO). Default OFF: datos en vivo "
                             "(23+ runs) muestran que las celdas VLM anadidas "
                             "correlacionan con rechazos (5 ok vs 7 fallos); "
                             "la confirmacion de candidatos se mantiene "
                             "siempre con --vlm-fallback")
    parser.add_argument("--archivo-fallos", default="",
                        help="directorio del corpus de fallos: cada intento "
                             "fallido de una ejecucion sin exito se guarda "
                             "como caso_<ts>_i<N>.json (instruccion, decision, "
                             "scores por celda y captura vinculada) para "
                             "analisis avanzado sin nuevas ejecuciones")
    parser.add_argument("--listar-fallos", metavar="DIR",
                        help="resume el corpus de fallos (caso_*.json) de DIR "
                             "en una tabla: clase, n, seleccion, veredicto")
    args = parser.parse_args()

    if args.listar_fallos:
        for fila in listar_fallos(args.listar_fallos):
            print(f"{fila['archivo']:<24} n={fila['n']} "
                  f"{str(fila['clase']):<16} sel={fila['seleccion']} "
                  f"{fila['veredicto']:<10} {fila['instruccion'][:40]}")
        return

    if args.offline:
        if not args.n or not args.instruccion:
            parser.error("--offline requiere --n y --instruccion")
        if args.vlm_fallback == "ollama":
            import functools
            fallback_vlm = functools.partial(fallback_vlm_ollama,
                                             modelo=args.vlm_modelo)
        elif args.vlm_fallback == "docbee":
            fallback_vlm = fallback_vlm_docbee
        else:
            fallback_vlm = None
        resultado = resolver_offline(
            args.offline, args.n, args.instruccion,
            umbral_objetivo=args.umbral_objetivo, fallback_vlm=fallback_vlm,
            vlm_recall=args.vlm_recall)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return
    if not args.url:
        parser.error("se requiere --url (modo real) o --offline IMAGEN")

    if args.vlm_fallback == "ollama":
        import functools
        fallback_vlm = functools.partial(fallback_vlm_ollama,
                                         modelo=args.vlm_modelo)
    elif args.vlm_fallback == "docbee":
        fallback_vlm = fallback_vlm_docbee
    else:
        fallback_vlm = None
    resultado = resolver_web(args.url, headed=args.headed,
                             salida=args.salida, timeout_s=args.timeout,
                             max_intentos=args.max_intentos,
                             umbral_objetivo=args.umbral_objetivo,
                             fallback_vlm=fallback_vlm,
                             vlm_recall=args.vlm_recall,
                             archivo_fallos=args.archivo_fallos)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
