#!/usr/bin/env python3
"""Tests de captcha_web.py (partes puras; el orquestador Playwright se
valida en vivo). Ejecutar: python3 -m unittest discover -s tests -v
"""

import unittest

from captcha_web import indice_a_fila_col, n_desde_tiles


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


if __name__ == "__main__":
    unittest.main()
