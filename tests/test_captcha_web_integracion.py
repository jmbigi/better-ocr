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
<div id="recaptcha-anchor" class="recaptcha-checkbox"
     style="width:28px;height:28px;border:1px solid #999;background:#fff"
     onclick="this.classList.toggle('recaptcha-checkbox-checked')"></div>
</body></html>"""

PAGINA_BFRAME = """<!doctype html><html><body>
<div id="rc-imageselect">
  <div class="rc-imageselect-desc">select all buses</div>
</div>
<div class="rc-imageselect-payload">
  <table class="rc-imageselect-table"><tbody>
    {filas}
  </tbody></table>
</div>
<button class="rc-button-go" onclick="parent.document
  .getElementById('marco-recaptcha').contentDocument
  .getElementById('recaptcha-anchor')
  .classList.add('recaptcha-checkbox-checked')">Verify</button>
</body></html>"""

CELDA = ('<td class="rc-imageselect-tile" '
         'onclick="fetch(\'/click?i={i}\')"></td>')


class Handler(BaseHTTPRequestHandler):
    """Sirve la pagina falsa y registra los clics de tiles."""

    clics = []

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
            return self._responder(PAGINA_BFRAME.format(filas=filas).encode())
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


if __name__ == "__main__":
    unittest.main()
