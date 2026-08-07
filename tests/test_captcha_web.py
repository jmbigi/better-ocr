#!/usr/bin/env python3
"""Tests de captcha_web.py (partes puras; el orquestador Playwright se
valida en vivo). Ejecutar: python3 -m unittest discover -s tests -v
"""

import unittest
from unittest import mock

from captcha_web import (
    fallback_vlm_ollama,
    indice_a_fila_col,
    n_desde_tiles,
    parsear_respuesta_vlm,
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


class TestUmbralObjetivo(unittest.TestCase):
    def test_adaptativo_por_tamano(self):
        # leccion 20 hallazgo 4: los tiles 4x4 (mas chicos) puntuan mas bajo
        # (motos reales 0.24-0.28); 3x3 conserva 0.45
        self.assertEqual(umbral_objetivo_para(3), 0.45)
        self.assertEqual(umbral_objetivo_para(4), 0.30)


if __name__ == "__main__":
    unittest.main()
