#!/usr/bin/env python3
"""Tests de captcha_web.py (partes puras; el orquestador Playwright se
valida en vivo). Ejecutar: python3 -m unittest discover -s tests -v
"""

import os
import unittest
from unittest import mock

from captcha_web import (
    _aplicar_fallback_vlm,
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
        # patron DDG: 2 candidatos de la clase -> el VLM confirma 1
        det = {(0, 1): [{"clase": "bus", "score": 0.9}],
               (2, 2): [{"clase": "bus", "score": 0.6}]}
        recibidas = []

        def vlm(celdas, clase):
            recibidas.append((clase, sorted((f, c) for f, c, _ in celdas)))
            return {(0, 1): [{"clase": "bus", "score": 1.0}]}

        res = _aplicar_fallback_vlm(det, self._celdas(), "bus", 0.45, vlm)
        self.assertEqual(recibidas, [("bus", [(0, 1), (2, 2)])])
        self.assertEqual(res[(0, 1)][0]["clase"], "bus")
        self.assertEqual(res[(2, 2)], [])  # descartada por el VLM

    def test_sin_detecciones_vlm_cubre_todas(self):
        recibidas = []

        def vlm(celdas, clase):
            recibidas.append(len(celdas))
            return {(1, 1): [{"clase": "crosswalk", "score": 1.0}]}

        res = _aplicar_fallback_vlm({}, self._celdas(), "crosswalk", 0.45, vlm)
        self.assertEqual(recibidas, [9])
        self.assertEqual(res[(1, 1)][0]["clase"], "crosswalk")

    def test_detecciones_sin_la_clase_no_llama_al_vlm(self):
        det = {(0, 0): [{"clase": "car", "score": 0.8}]}
        res = _aplicar_fallback_vlm(det, self._celdas(), "bus", 0.45,
                                    lambda celdas, clase: (_ for _ in ()).throw(
                                        AssertionError("no debe llamarse")))
        self.assertEqual(res, det)

    def test_sin_fallback_no_cambia_nada(self):
        det = {(0, 1): [{"clase": "bus", "score": 0.9}]}
        res = _aplicar_fallback_vlm(det, self._celdas(), "bus", 0.45, None)
        self.assertEqual(res, det)


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
                instruccion="select all crosswalks", fallback_vlm=vlm)
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


class TestUmbralObjetivo(unittest.TestCase):
    def test_adaptativo_por_tamano(self):
        # leccion 20 hallazgo 4: los tiles 4x4 (mas chicos) puntuan mas bajo
        # (motos reales 0.24-0.28); 3x3 conserva 0.45
        self.assertEqual(umbral_objetivo_para(3), 0.45)
        self.assertEqual(umbral_objetivo_para(4), 0.30)


if __name__ == "__main__":
    unittest.main()
