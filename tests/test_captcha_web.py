#!/usr/bin/env python3
"""Tests de captcha_web.py (partes puras; el orquestador Playwright se
valida en vivo). Ejecutar: python3 -m unittest discover -s tests -v
"""

import json
import os
import unittest
from unittest import mock

import captcha_web

from captcha_web import (
    _aplicar_fallback_vlm,
    fallback_vlm_docbee,
    fallback_vlm_ollama,
    indice_a_fila_col,
    n_desde_tiles,
    parsear_respuesta_vlm,
    resolver_offline,
    umbral_objetivo_para,
)


class TestParsearRespuestaVLM(unittest.TestCase):
    def test_si(self):
        self.assertIs(parsear_respuesta_vlm("yes"), True)
        self.assertIs(parsear_respuesta_vlm("Yes."), True)
        self.assertIs(parsear_respuesta_vlm("yes, there is"), True)
        self.assertIs(parsear_respuesta_vlm("Y"), True)

    def test_no(self):
        self.assertIs(parsear_respuesta_vlm("no"), False)
        self.assertIs(parsear_respuesta_vlm("No."), False)
        self.assertIs(parsear_respuesta_vlm("no, there is not"), False)

    def test_indeterminada(self):
        self.assertIsNone(parsear_respuesta_vlm(""))
        self.assertIsNone(parsear_respuesta_vlm("cannot tell"))
        self.assertIsNone(parsear_respuesta_vlm("yes and no"))


class TestFallbackVlmOllama(unittest.TestCase):
    def test_celdas_con_respuesta_si(self):
        from PIL import Image

        celdas = [(0, 0, Image.new("RGB", (30, 30))),
                  (1, 2, Image.new("RGB", (30, 30))),
                  (2, 1, Image.new("RGB", (30, 30)))]
        respuestas = iter(["yes", "no", "cannot tell"])
        falso = mock.Mock(side_effect=lambda *a, **k: next(respuestas))
        with mock.patch("captcha_web._garantizar_ollama", return_value=True), \
                mock.patch("captcha_web._preguntar_ollama", falso):
            res = fallback_vlm_ollama(celdas, "crosswalk")
        self.assertEqual(list(res.keys()), [(0, 0)])
        self.assertEqual(res[(0, 0)][0]["clase"], "crosswalk")
        self.assertEqual(falso.call_count, 3)

    def test_sin_clase_no_pregunta(self):
        res = fallback_vlm_ollama([], None)
        self.assertEqual(res, {})

    def test_sin_ollama_devuelve_vacio(self):
        with mock.patch("captcha_web._garantizar_ollama", return_value=False):
            res = fallback_vlm_ollama([], "crosswalk")
        self.assertEqual(res, {})


class TestNDesdeTiles(unittest.TestCase):
    def test_cuadriculas_validas(self):
        self.assertEqual(n_desde_tiles(9), 3)
        self.assertEqual(n_desde_tiles(16), 4)

    def test_cantidades_invalidas(self):
        self.assertIsNone(n_desde_tiles(0))
        self.assertIsNone(n_desde_tiles(1))
        self.assertIsNone(n_desde_tiles(8))
        self.assertIsNone(n_desde_tiles(12))
        self.assertIsNone(n_desde_tiles(25))  # 5x5 no existe en reCAPTCHA v2


class TestIndiceAFilaCol(unittest.TestCase):
    def test_orden_fila_mayor(self):
        # reCAPTCHA lista los tiles por fila: 0..n-1 fila 0, n..2n-1 fila 1...
        self.assertEqual(indice_a_fila_col(0, 3), (0, 0))
        self.assertEqual(indice_a_fila_col(2, 3), (0, 2))
        self.assertEqual(indice_a_fila_col(3, 3), (1, 0))
        self.assertEqual(indice_a_fila_col(8, 3), (2, 2))
        self.assertEqual(indice_a_fila_col(15, 4), (3, 3))


class TestAplicarFallbackVLM(unittest.TestCase):
    def _celdas(self, n=3):
        from PIL import Image
        return [(f, c, Image.new("RGB", (30, 30)))
                for f in range(n) for c in range(n)]

    def test_confirma_solo_candidatos(self):
        # patron DDG: 2 candidatos de la clase -> el VLM confirma 1; las 7
        # celdas sin deteccion se re-evaluan por separado (recall)
        det = {(0, 1): [{"clase": "bus", "score": 0.9}],
               (2, 2): [{"clase": "bus", "score": 0.6}]}
        recibidas = []

        def vlm(celdas, clase):
            recibidas.append((clase, sorted((f, c) for f, c, _ in celdas)))
            return {(0, 1): [{"clase": "bus", "score": 1.0}]}

        res = _aplicar_fallback_vlm(det, self._celdas(), "bus", 0.45, vlm)
        # llamada 1: candidatos; llamada 2: las 7 celdas vacias
        self.assertEqual(recibidas[0], ("bus", [(0, 1), (2, 2)]))
        self.assertEqual(len(recibidas[1][1]), 7)
        self.assertEqual(res[(0, 1)][0]["clase"], "bus")
        self.assertEqual(res[(2, 2)], [])  # descartada por el VLM

    def test_recall_vlm_encuentra_objetos_en_celdas_vacias(self):
        # 4x4 de traffic lights (medido en vivo): 13/16 celdas sin deteccion;
        # el VLM encuentra objetos perdidos en las celdas vacias
        det = {(0, 0): [{"clase": "traffic light", "score": 0.88}]}
        celdas = self._celdas(n=4)  # 16 celdas, 15 vacias

        def vlm(celdas, clase):
            if len(celdas) == 1:  # candidatos
                return {(0, 0): [{"clase": "traffic light", "score": 1.0}]}
            # celdas vacias: encuentra una mas
            return {(3, 3): [{"clase": "traffic light", "score": 1.0}]}

        res = _aplicar_fallback_vlm(det, celdas, "traffic light", 0.30, vlm)
        self.assertEqual(res[(0, 0)][0]["clase"], "traffic light")
        self.assertEqual(res[(3, 3)][0]["clase"], "traffic light")
        self.assertEqual(sum(1 for v in res.values() if v), 2)

    def test_sin_detecciones_vlm_cubre_todas(self):
        recibidas = []

        def vlm(celdas, clase):
            recibidas.append(len(celdas))
            return {(1, 1): [{"clase": "crosswalk", "score": 1.0}]}

        res = _aplicar_fallback_vlm({}, self._celdas(), "crosswalk", 0.45, vlm)
        self.assertEqual(recibidas, [9])
        self.assertEqual(res[(1, 1)][0]["clase"], "crosswalk")

    def test_sin_candidatos_con_recall_cubre_todas(self):
        # con --vlm-recall: sin candidatos del objetivo, el VLM cubre TODAS
        # las celdas (clases no-COCO) y fusiona
        det = {(0, 0): [{"clase": "bicycle", "score": 0.8}]}
        recibidas = []

        def vlm(celdas, clase):
            recibidas.append((clase, len(celdas)))
            return {(1, 1): [{"clase": "mountains or hills", "score": 1.0}]}

        res = _aplicar_fallback_vlm(det, self._celdas(), "mountains or hills",
                                    0.45, vlm, recall=True)
        self.assertEqual(recibidas, [("mountains or hills", 9)])
        self.assertEqual(res[(1, 1)][0]["clase"], "mountains or hills")
        self.assertEqual(res[(0, 0)][0]["clase"], "bicycle")  # se conserva

    def test_sin_candidatos_sin_recall_no_anade_nada(self):
        # default conservador (recall OFF): sin candidatos del objetivo, el
        # VLM NO cubre las celdas (evita la sobre-seleccion medida en vivo)
        det = {(0, 0): [{"clase": "bicycle", "score": 0.8}]}
        res = _aplicar_fallback_vlm(
            det, self._celdas(), "mountains or hills", 0.45,
            lambda celdas, clase: (_ for _ in ()).throw(
                AssertionError("no debe llamarse")), recall=False)
        self.assertEqual(res, det)

    def test_sin_fallback_no_cambia_nada(self):
        det = {(0, 1): [{"clase": "bus", "score": 0.9}]}
        res = _aplicar_fallback_vlm(det, self._celdas(), "bus", 0.45, None)
        self.assertEqual(res, det)


    def test_recall_desactivable(self):
        # --sin-vlm-recall: la pasada sobre celdas vacias no se hace
        det = {(0, 1): [{"clase": "bus", "score": 0.9}]}
        recibidas = []

        def vlm(celdas, clase):
            recibidas.append(len(celdas))
            return {(0, 1): [{"clase": "bus", "score": 1.0}]}

        res = _aplicar_fallback_vlm(det, self._celdas(), "bus", 0.45, vlm,
                                    recall=False)
        self.assertEqual(recibidas, [1])  # solo candidatos, sin vacias
        self.assertEqual(res[(0, 1)][0]["clase"], "bus")

    def test_recall_excluido_por_clase_car(self):
        # politica por clase (datos en vivo: car 2/2 fallos con celda VLM):
        # en 'car' la pasada de recall no se hace; en 'bus' si
        det = {(0, 1): [{"clase": "car", "score": 0.9}]}
        recibidas = []

        def vlm(celdas, clase):
            recibidas.append(clase)
            return {}

        _aplicar_fallback_vlm(det, self._celdas(), "car", 0.45, vlm)
        self.assertEqual(recibidas, ["car"])  # solo candidatos (1 llamada)
        recibidas.clear()
        det_bus = {(0, 1): [{"clase": "bus", "score": 0.9}]}
        _aplicar_fallback_vlm(det_bus, self._celdas(), "bus", 0.45, vlm)
        self.assertEqual(len(recibidas), 2)  # candidatos + recall


class TestResolverOffline(unittest.TestCase):
    def _ruta_grid(self):
        import tempfile
        from PIL import Image

        ruta = os.path.join(tempfile.mkdtemp(), "reto.png")
        Image.new("RGB", (120, 120), (240, 240, 240)).save(ruta)
        return ruta

    def test_fallback_vlm_cuando_worker_vacio(self):
        # clase no-COCO: el worker no ve nada -> el VLM la resuelve
        llamadas = []

        def vlm(celdas_pil, clase):
            llamadas.append(clase)
            return {(0, 1): [{"clase": clase, "score": 1.0}]}

        with mock.patch("captcha_web.detectar_batch_worker",
                        return_value={}):
            res = resolver_offline(
                self._ruta_grid(), n=3,
                instruccion="select all crosswalks", fallback_vlm=vlm,
                vlm_recall=True)
        self.assertTrue(res["ok"], res)
        self.assertEqual(llamadas, ["crosswalk"])
        self.assertEqual(sorted(res["seleccion"]), [(0, 1)])
        self.assertEqual(res["detecciones_por_celda"]["0,1"][0]["clase"],
                         "crosswalk")

    def test_sin_fallback_no_se_llama_al_vlm(self):
        with mock.patch("captcha_web.detectar_batch_worker",
                        return_value={}):
            res = resolver_offline(
                self._ruta_grid(), n=3,
                instruccion="select all crosswalks")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["seleccion"], [])
        self.assertEqual(res["celdas_detectadas"], 0)


class TestPreguntarOllama(unittest.TestCase):
    def test_payload_del_api_generate(self):
        """Contrato de la API /api/generate: modelo, prompt binario, imagen
        en base64, temperatura 0 (determinista) y sin stream."""
        from PIL import Image
        import base64 as b64mod

        with mock.patch("captcha_web.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = \
                b'{"response": "YES"}'
            texto = captcha_web._preguntar_ollama(
                Image.new("RGB", (20, 20), (255, 0, 0)), "bus",
                "127.0.0.1", "gemma3:4b", 90)

        self.assertEqual(texto, "YES")
        req = urlopen.call_args[0][0]
        self.assertIn("http://127.0.0.1:11434/api/generate", req.full_url)
        cuerpo = json.loads(req.data.decode())
        self.assertEqual(cuerpo["model"], "gemma3:4b")
        self.assertIn("bus", cuerpo["prompt"])
        self.assertIn("yes or no", cuerpo["prompt"].lower())
        self.assertEqual(cuerpo["options"]["temperature"], 0)
        self.assertIs(cuerpo["stream"], False)
        # la imagen viaja en base64 PNG
        b64 = cuerpo["images"][0]
        self.assertIsInstance(b64, str)
        import io
        self.assertEqual(
            Image.open(io.BytesIO(b64mod.b64decode(b64))).size, (20, 20))


class TestFallbackVlmDocbee(unittest.TestCase):
    def _celdas(self, n=3):
        from PIL import Image
        return [(f, c, Image.new("RGB", (30, 30)))
                for f in range(n) for c in range(n)]

    def test_respuestas_si_se_mapean_por_celda(self):
        # subprocess simulado: docbee responde Yes/No por ruta
        salida = {
            "/tmp/f0c0.png": "Yes.",
            "/tmp/f0c1.png": "No.",
            "/tmp/f0c2.png": "cannot tell",
        }
        falso = mock.Mock(returncode=0, stdout=json.dumps(salida))

        with mock.patch("captcha_web.subprocess.run",
                        return_value=falso) as run_mock, \
                mock.patch("captcha_web.tempfile.mkdtemp",
                           return_value="/tmp"):
            res = fallback_vlm_docbee(self._celdas()[:3], "crosswalk")
        self.assertEqual(list(res.keys()), [(0, 0)])
        self.assertEqual(res[(0, 0)][0]["clase"], "crosswalk")
        # el worker recibe rutas + clase y el env de la leccion 17
        args, kwargs = run_mock.call_args
        self.assertIn("crosswalk", kwargs["input"])
        self.assertIn("LD_LIBRARY_PATH", kwargs["env"])

    def test_fallo_del_worker_devuelve_vacio(self):
        falso = mock.Mock(returncode=1, stdout="")
        with mock.patch("captcha_web.subprocess.run", return_value=falso), \
                mock.patch("captcha_web.tempfile.mkdtemp",
                           return_value="/tmp"):
            res = fallback_vlm_docbee(self._celdas()[:2], "crosswalk")
        self.assertEqual(res, {})

    def test_sin_clase_no_pregunta(self):
        self.assertEqual(fallback_vlm_docbee([], None), {})


class TestUmbralObjetivo(unittest.TestCase):
    def test_adaptativo_por_tamano(self):
        # leccion 20 hallazgo 4: los tiles 4x4 (mas chicos) puntuan mas bajo
        # (motos reales 0.24-0.28); 3x3 conserva 0.45
        self.assertEqual(umbral_objetivo_para(3), 0.45)
        self.assertEqual(umbral_objetivo_para(4), 0.30)


if __name__ == "__main__":
    unittest.main()
