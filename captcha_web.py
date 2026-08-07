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

import json
import os
import subprocess
import sys
import tempfile
import time

SELEC_INSTRUCCION = (
    "#rc-imageselect .rc-imageselect-desc, "
    "#rc-imageselect .rc-imageselect-desc-noaccess"
)
SELEC_TILES = "table.rc-imageselect-table td.rc-imageselect-tile"
SELEC_PAYLOAD = ".rc-imageselect-payload"
SELEC_VERIFY = "button.rc-button-go, .rc-button-go"
SELEC_ERROR = ".rc-imageselect-error-response"
SELEC_CHECKBOX = "#recaptcha-anchor"

UMBRAL_OBJETIVO = 0.45
UMBRAL_RESTO = 0.6
TIEMPO_ESPERA_RETO = 20.0
TIEMPO_ESPERA_VEREDICTO = 15.0

VENV_PYTHON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".venv", "bin", "python")

# Script del worker de deteccion (se ejecuta con el python del venv, que es
# quien tiene paddle/paddlex; una sola carga de RT-DETR para todo el lote).
WORKER_DETECCION = r"""
import json, os, sys
sys.path.insert(0, %(raiz)r)
os.environ.setdefault("TMPDIR", "/var/tmp")
from vision import modo_objetos
paths = json.load(sys.stdin)
out = {}
for p in paths:
    res = modo_objetos(p, False)
    out[p] = res.get("detecciones", []) if res.get("ok") else []
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
            capture_output=True, text=True, timeout=180,
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
    """Texto de la instruccion del reto (desc del DOM)."""
    locator = bframe.locator(SELEC_INSTRUCCION).first
    try:
        return locator.inner_text().strip()
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
    """Captura la cuadricula del reto y la devuelve como PIL Image."""
    from PIL import Image

    payload = bframe.locator(SELEC_PAYLOAD)
    ruta = os.path.join(tempfile.mkdtemp(prefix="captcha_reto_"),
                        "cuadricula.png")
    payload.screenshot(path=ruta)
    return Image.open(ruta)


def pulsar_tiles(bframe, seleccion: list, n: int) -> None:
    """Clic JS en cada tile seleccionado (el.click() evita el fallo
    'outside of viewport' de los transforms anti-automatizacion)."""
    for fila, col in seleccion:
        indice = fila * n + col
        bframe.locator(SELEC_TILES).nth(indice).evaluate("el => el.click()")


def pulsar_verificar(bframe) -> None:
    try:
        bframe.locator(SELEC_VERIFY).first.evaluate("el => el.click()")
    except Exception:
        pass


def veredicto(pagina, bframe) -> str:
    """Tras VERIFY: 'ok' | 'error' | 'pendiente' (espera acotada)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < TIEMPO_ESPERA_VEREDICTO:
        if bframe.locator(SELEC_ERROR).count() > 0:
            return "error"
        # si el bframe se cerro o el checkbox ancla quedo marcado: exito
        bframe_vivo = any("bframe" in (f.url or "") for f in pagina.frames)
        if not bframe_vivo:
            return "ok"
        try:
            ancla = pagina.frames[0].locator(SELEC_CHECKBOX)
            clase = ancla.get_attribute("class") or ""
            if "recaptcha-checkbox-checked" in clase:
                return "ok"
        except Exception:
            pass
        time.sleep(0.5)
    return "pendiente"


def resolver_web(url: str, headed: bool = False, salida: str = "",
                 timeout_s: float = 150.0) -> dict:
    """Ciclo completo real: checkbox -> reto -> instruccion -> tiles ->
    VERIFY -> veredicto. Import perezoso de Playwright (solo python del
    sistema); la deteccion va al venv por subproceso."""
    from playwright.sync_api import sync_playwright

    from captcha_ia import resolver

    t_inicio = time.monotonic()
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=not headed)
        contexto = navegador.new_context(viewport={"width": 1280, "height": 900})
        pagina = contexto.new_page()
        pagina.goto(url, timeout=30000)
        pagina.wait_for_timeout(1500)

        # 1) checkbox ancla dentro del iframe de reCAPTCHA
        marco_recaptcha = None
        for f in pagina.frames:
            if "recaptcha" in (f.url or ""):
                marco_recaptcha = f
                break
        if marco_recaptcha is None:
            navegador.close()
            return {"ok": False, "error": "no se encontro el iframe de reCAPTCHA"}
        marco_recaptcha.locator(SELEC_CHECKBOX).click()

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

        # 3) instruccion + cuadricula + captura
        instruccion = leer_instruccion(bframe)
        n = tamano_cuadricula(bframe)
        if n is None:
            navegador.close()
            return {"ok": False, "error": "cuadricula no reconocida (tiles raros)"}
        imagen = capturar_cuadricula(bframe)

        # 4) resolucion: celdas + RT-DETR batch + decision
        from captcha_ia import celdas_grid, aumentar_escala

        celdas_pil = [(f, c, aumentar_escala(celda))
                      for f, c, celda in celdas_grid(imagen, n=n)]
        detecciones = detectar_batch_worker(celdas_pil)

        def detectar_celda(_celda, fila, col):
            return detecciones.get((fila, col), [])

        res = resolver(imagen, instruccion, detectar_celda, n=n,
                       umbral_objetivo=UMBRAL_OBJETIVO,
                       umbral_resto=UMBRAL_RESTO)
        if not res["ok"]:
            navegador.close()
            return {**res, "instruccion": instruccion}

        # 5) clics + verificar + veredicto
        pulsar_tiles(bframe, res["seleccion"], n)
        pulsar_verificar(bframe)
        resultado = veredicto(pagina, bframe)

        if salida:
            os.makedirs(salida, exist_ok=True)
            imagen.save(os.path.join(salida, f"reto_{n}x{n}.png"))
            with open(os.path.join(salida, "resultado.json"), "w",
                      encoding="utf-8") as f:
                json.dump({**res, "veredicto": resultado,
                           "instruccion": instruccion}, f,
                          ensure_ascii=False, indent=2)

        navegador.close()
        return {**res, "veredicto": resultado, "instruccion": instruccion,
                "tiempo_s": round(time.monotonic() - t_inicio, 1)}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="reCAPTCHA v2 real (Playwright)")
    parser.add_argument("--url", required=True, help="pagina con reCAPTCHA v2")
    parser.add_argument("--headed", action="store_true",
                        help="navegador visible (default: headless)")
    parser.add_argument("--salida", default="",
                        help="directorio para guardar captura y resultado")
    parser.add_argument("--timeout", type=float, default=150.0,
                        help="tiempo maximo total en segundos")
    args = parser.parse_args()

    resultado = resolver_web(args.url, headed=args.headed,
                             salida=args.salida, timeout_s=args.timeout)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
