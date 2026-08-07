#!/usr/bin/env python3
"""Test de integracion del orquestador real (Playwright) contra una pagina
falsa LOCAL que replica el DOM de reCAPTCHA v2 (mismo origen, sin terceros):
checkbox ancla en iframe 'recaptcha', reto en iframe 'bframe' con la
cuadricula, tiles que registran clics, y VERIFY que marca el ancla.

Valida el glue del orquestador: frames, selectores, clics JS, veredicto.
Los selectores REALES de reCAPTCHA se validan aparte, en vivo.

Requiere playwright (python del sistema) y los navegadores descargados.
Ejecutar: python3 -m unittest discover -s tests -v
"""

import json
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PAGINA_PRINCIPAL = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<h1>Pagina falsa con reCAPTCHA</h1>
<form id="mi-formulario"><input name="x" value="1"></form>
<iframe id="marco-recaptcha" src="/recaptcha.html"></iframe>
<iframe id="marco-bframe" src="/bframe.html"></iframe>
</body></html>"""

PAGINA_RECAPTCHA = """<!doctype html><html><body>
<!-- El ancla NO se marca con el clic inicial (en reCAPTCHA real solo se
     marca al resolver el reto); el VERIFY de bframe lo marca. -->
<div id="recaptcha-anchor" class="recaptcha-checkbox"
     style="width:28px;height:28px;border:1px solid #999;background:#fff"></div>
</body></html>"""

PAGINA_BFRAME = """<!doctype html><html><body>
<div id="rc-imageselect">
  {desc}
</div>
<div class="rc-imageselect-payload">
  <table class="rc-imageselect-table"><tbody>
    {filas}
  </tbody></table>
</div>
<button class="rc-button-default" data-fallos="{fallos}"
 onclick="if (window.__verifies === undefined) window.__verifies = 0;
  window.__verifies += 1;
  var fallos = parseInt(this.getAttribute('data-fallos') || '0');
  if (window.__verifies > fallos) {{
    var e = document.querySelector('.rc-imageselect-error-response');
    if (e) e.remove();
    parent.document.getElementById('marco-recaptcha').contentDocument
      .getElementById('recaptcha-anchor')
      .classList.add('recaptcha-checkbox-checked');
  }} else {{
    document.querySelector('.rc-imageselect-desc').innerText = 'select all cars';
    var e = document.createElement('div');
    e.className = 'rc-imageselect-error-response';
    e.innerText = 'wrong';
    document.getElementById('rc-imageselect').appendChild(e);
  }}">Verify</button>
<button onclick="parent.document.getElementById('marco-recaptcha').contentDocument
  .getElementById('recaptcha-anchor')
  .classList.add('recaptcha-checkbox-checked')">Skip</button>
</body></html>"""

CELDA = ('<td class="rc-imageselect-tile" '
         'onclick="fetch(\'/click?i={i}\')"></td>')


class Handler(BaseHTTPRequestHandler):
    """Sirve la pagina falsa y registra los clics de tiles."""

    clics = []
    sin_desc = False
    desc_alternativa = ""
    errores_antes_de_ok = 0

    def _responder(self, cuerpo, tipo="text/html; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        if self.path == "/":
            return self._responder(PAGINA_PRINCIPAL.encode())
        if self.path == "/recaptcha.html":
            return self._responder(PAGINA_RECAPTCHA.encode())
        if self.path == "/bframe.html":
            filas = "".join(
                "<tr>" + "".join(CELDA.format(i=f * 3 + c) for c in range(3)) + "</tr>"
                for f in range(3))
            desc = "" if Handler.sin_desc else (
                f'<div class="rc-imageselect-desc">{Handler.desc_alternativa}</div>'
                if Handler.desc_alternativa else
                '<div class="rc-imageselect-desc">select all buses</div>')
            pagina = PAGINA_BFRAME.format(filas=filas, desc=desc,
                                          fallos=Handler.errores_antes_de_ok)
            return self._responder(pagina.encode())
        if self.path.startswith("/click?"):
            i = int(self.path.split("i=")[1])
            Handler.clics.append(i)
            return self._responder(b"ok", "text/plain")
        if self.path == "/clics.json":
            return self._responder(json.dumps(sorted(Handler.clics)).encode(),
                                   "application/json")
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


class TestOrquestadorLocal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright no disponible en este python")
        cls.servidor = HTTPServer(("127.0.0.1", 0), Handler)
        cls.puerto = cls.servidor.server_address[1]
        cls.hilo = threading.Thread(target=cls.servidor.serve_forever, daemon=True)
        cls.hilo.start()

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.hilo.join(timeout=2)
        cls.servidor.server_close()

    def test_flujo_completo_contra_pagina_falsa(self):
        import captcha_web

        Handler.clics = []
        url = f"http://127.0.0.1:{self.puerto}/"
        buses = {(0, 1): [{"clase": "bus", "score": 0.9}],
                 (2, 2): [{"clase": "bus", "score": 0.55}]}

        def stub(celdas_pil):
            return {clave: buses.get(clave, [])
                    for clave in [(f, c) for f, c, _ in celdas_pil]}

        res = captcha_web.resolver_web(url, detectar_lote=stub, timeout_s=60)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["veredicto"], "ok")
        self.assertEqual(res["clase_objetivo"], "bus")
        self.assertEqual(sorted(res["seleccion"]), [(0, 1), (2, 2)])
        # tiles pulsados en la pagina falsa: (0,1)->i=1, (2,2)->i=8
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.puerto}/clics.json") as r:
            pulsados = json.loads(r.read().decode())
        self.assertEqual(pulsados, [1, 8])

    def test_fallback_ocr_sin_desc_en_dom(self):
        """Sin el div .rc-imageselect-desc, la instruccion viene del OCR
        (inyectado en el test); el flujo completo sigue funcionando."""
        import captcha_web

        Handler.clics = []
        Handler.sin_desc = True
        self.addCleanup(setattr, Handler, "sin_desc", False)
        url = f"http://127.0.0.1:{self.puerto}/"
        motos = {(0, 0): [{"clase": "motorcycle", "score": 0.6}],
                 (1, 2): [{"clase": "motorcycle", "score": 0.5}]}

        def stub_detector(celdas_pil):
            return {clave: motos.get(clave, [])
                    for clave in [(f, c) for f, c, _ in celdas_pil]}

        def stub_ocr(_bframe):
            return "select all motorcycles"

        res = captcha_web.resolver_web(url, detectar_lote=stub_detector,
                                       ocr_fallback=stub_ocr, timeout_s=60)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["veredicto"], "ok")
        self.assertEqual(res["clase_objetivo"], "motorcycle")
        self.assertEqual(sorted(res["seleccion"]), [(0, 0), (1, 2)])
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.puerto}/clics.json") as r:
            pulsados = json.loads(r.read().decode())
        self.assertEqual(pulsados, [0, 5])

    def test_reintento_tras_error_render(self):
        """Primer VERIFY falla (error + replaceimage, la instruccion cambia a
        'select all cars'); el orquestador reintenta y el segundo VERIFY
        exito. Cobertura del bucle de re-render del reto."""
        import captcha_web

        Handler.clics = []
        Handler.sin_desc = False
        Handler.errores_antes_de_ok = 1
        self.addCleanup(setattr, Handler, "sin_desc", False)
        self.addCleanup(setattr, Handler, "errores_antes_de_ok", 0)
        url = f"http://127.0.0.1:{self.puerto}/"

        llamadas = {"n": 0}

        def stub_detector(celdas_pil):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                esperadas = {(0, 1): [{"clase": "bus", "score": 0.9}]}
            else:
                esperadas = {(2, 0): [{"clase": "car", "score": 0.8}]}
            return {clave: esperadas.get(clave, [])
                    for clave in [(f, c) for f, c, _ in celdas_pil]}

        res = captcha_web.resolver_web(url, detectar_lote=stub_detector,
                                       timeout_s=60)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["veredicto"], "ok")
        self.assertEqual(res["intento"], 2)
        self.assertEqual(res["clase_objetivo"], "car")
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.puerto}/clics.json") as r:
            pulsados = json.loads(r.read().decode())
        self.assertEqual(pulsados, [1, 6])  # (0,1) y (2,0)

    def test_skip_cuando_instruccion_no_parseable(self):
        """Instruccion sin clase ('Select all images' -> None): se pulsa
        SKIP en lugar de VERIFY, sin tiles, y el reto se da por resuelto."""
        import captcha_web

        Handler.clics = []
        Handler.desc_alternativa = "Select all images"
        self.addCleanup(setattr, Handler, "desc_alternativa", "")
        url = f"http://127.0.0.1:{self.puerto}/"

        def stub_detector(celdas_pil):
            # aunque el detector viera algo, no debe pulsarse ninguna tile
            return {clave: [{"clase": "bus", "score": 0.9}]
                    for clave in [(f, c) for f, c, _ in celdas_pil]}

        res = captcha_web.resolver_web(url, detectar_lote=stub_detector,
                                       timeout_s=60)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["veredicto"], "ok")
        self.assertEqual(res["intento"], 1)
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.puerto}/clics.json") as r:
            pulsados = json.loads(r.read().decode())
        self.assertEqual(pulsados, [])

    def test_fallback_vlm_cuando_worker_no_encuentra_nada(self):
        """Worker sin detecciones (clase no-COCO): el orquestador parsea la
        clase de la instruccion y llama al hook fallback_vlm(celdas, clase);
        sus detecciones alimentan el decisor y los clics."""
        import captcha_web

        Handler.clics = []
        Handler.desc_alternativa = "select all crosswalks"
        self.addCleanup(setattr, Handler, "desc_alternativa", "")
        url = f"http://127.0.0.1:{self.puerto}/"

        def stub_detector(celdas_pil):
            return {}  # RT-DETR no ve crosswalks: clase no-COCO

        llamadas = []

        def stub_vlm(celdas_pil, clase):
            llamadas.append(clase)
            return {(1, 1): [{"clase": clase, "score": 1.0}]}

        res = captcha_web.resolver_web(url, detectar_lote=stub_detector,
                                       fallback_vlm=stub_vlm, timeout_s=60)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["veredicto"], "ok")
        self.assertEqual(llamadas, ["crosswalk"])
        self.assertEqual(sorted(res["seleccion"]), [(1, 1)])
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.puerto}/clics.json") as r:
            pulsados = json.loads(r.read().decode())
        self.assertEqual(pulsados, [4])  # (1,1)

    def test_variante_none_click_skip(self):
        """'If there are no crosswalks, click skip' (leccion 20): el parser
        devuelve None (no hay clase) y el orquestador pulsa SKIP sin tiles."""
        import captcha_web

        Handler.clics = []
        Handler.desc_alternativa = "If there are no crosswalks, click skip"
        self.addCleanup(setattr, Handler, "desc_alternativa", "")
        url = f"http://127.0.0.1:{self.puerto}/"

        def stub_detector(celdas_pil):
            return {}  # no hay crosswalks en ninguna celda

        res = captcha_web.resolver_web(url, detectar_lote=stub_detector,
                                       timeout_s=60)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["veredicto"], "ok")
        self.assertEqual(res["camino"], "skip")
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.puerto}/clics.json") as r:
            pulsados = json.loads(r.read().decode())
        self.assertEqual(pulsados, [])


if __name__ == "__main__":
    unittest.main()
