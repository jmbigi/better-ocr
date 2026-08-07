#!/usr/bin/env python3
"""Tests de captcha_ia.py (piezas puras, sin Playwright ni motores).

Ejecutar: python3 -m unittest discover -s tests -v
"""

import unittest

from PIL import Image

from captcha_ia import (
    aumentar_escala,
    celdas_grid,
    parsear_instruccion,
    recortar_sufijos,
    singularizar,
)


def imagen_sintetica(n=3, tam=300, color=(200, 50, 50)):
    """Imagen de prueba: n x n bloques de color con borde oscuro (cuadricula)."""
    im = Image.new("RGB", (tam, tam), (0, 0, 0))
    for fila in range(n):
        for col in range(n):
            for y in range(fila * tam // n + 2, (fila + 1) * tam // n - 2):
                for x in range(col * tam // n + 2, (col + 1) * tam // n - 2):
                    im.putpixel((x, y), color)
    return im


class TestSingularizar(unittest.TestCase):
    def test_plurales_regulares(self):
        self.assertEqual(singularizar("cars"), "car")
        self.assertEqual(singularizar("bicycles"), "bicycle")
        self.assertEqual(singularizar("boats"), "boat")

    def test_plurales_irregulares(self):
        self.assertEqual(singularizar("buses"), "bus")
        self.assertEqual(singularizar("motorcycles"), "motorcycle")
        self.assertEqual(singularizar("traffic lights"), "traffic light")
        self.assertEqual(singularizar("fire hydrants"), "fire hydrant")
        self.assertEqual(singularizar("stop signs"), "stop sign")

    def test_invariantes(self):
        self.assertEqual(singularizar("stairs"), "stairs")
        self.assertEqual(singularizar("sheep"), "sheep")
        self.assertEqual(singularizar("bus"), "bus")


class TestRecortarSufijos(unittest.TestCase):
    def test_sufijo_con_espacio(self):
        self.assertEqual(recortar_sufijos("traffic lights if there are none"),
                         "traffic lights")

    def test_sufijo_pegado_sin_espacio(self):
        # caso real reportado: el OCR pega el texto concatenado
        self.assertEqual(recortar_sufijos("traffic lightsIf there are none..."),
                         "traffic lights")


class TestParsearInstruccion(unittest.TestCase):
    def test_casos_reales_reportados(self):
        casos = {
            "traffic lightsIf there are none...": "traffic light",
            "select all buses": "bus",
            "Click every motorcycle": "motorcycle",
            "click all cars": "car",
            "Select all images with a bus": "bus",
            "click all stop signs": "stop sign",
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(parsear_instruccion(texto), esperado)

    def test_condicion_prefija(self):
        self.assertEqual(parsear_instruccion("If there are crosswalks, select them"),
                         "crosswalk")
        self.assertEqual(parsear_instruccion("if there are traffic lights, click all"),
                         "traffic light")

    def test_articulos_y_conectores(self):
        self.assertEqual(parsear_instruccion("select squares with cars"), "car")
        self.assertEqual(parsear_instruccion("click all pictures of motorcycles"),
                         "motorcycle")
        self.assertEqual(parsear_instruccion("select all the buses"), "bus")

    def test_sin_clase(self):
        for texto in ["", "   ", "select all images", "click here"]:
            with self.subTest(texto=repr(texto)):
                self.assertIsNone(parsear_instruccion(texto))

    def test_singular_no_se_toca(self):
        self.assertEqual(parsear_instruccion("select the bus"), "bus")


class TestGeometriaGrid(unittest.TestCase):
    def test_celdas_3x3(self):
        im = imagen_sintetica(n=3, tam=300)
        celdas = celdas_grid(im, n=3, margen_frac=0.06)
        self.assertEqual(len(celdas), 9)
        filas_cols = [(f, c) for f, c, _ in celdas]
        self.assertEqual(sorted(filas_cols),
                         sorted((f, c) for f in range(3) for c in range(3)))
        tam_celda = min(im.size) // 3
        margen = max(1, int(tam_celda * 0.06))
        ancho_esperado = tam_celda - 2 * margen
        for _, _, celda in celdas:
            self.assertEqual(celda.size, (ancho_esperado, ancho_esperado))

    def test_margen_excluye_borde_de_cuadricula(self):
        # el borde de la cuadricula es negro; con margen, el pixel central de
        # la celda (0,0) debe ser del color de relleno, no del borde
        im = imagen_sintetica(n=3, tam=300, color=(200, 50, 50))
        _, _, celda = celdas_grid(im, n=3)[0]
        self.assertEqual(celda.getpixel((2, 2)), (200, 50, 50))

    def test_cuadricula_4x4(self):
        im = imagen_sintetica(n=4, tam=400)
        self.assertEqual(len(celdas_grid(im, n=4)), 16)

    def test_n_no_soportado(self):
        im = imagen_sintetica(n=3)
        with self.assertRaises(AssertionError):
            celdas_grid(im, n=7)

    def test_upscale_lanczos_2x(self):
        im = Image.new("RGB", (60, 60), (10, 20, 30))
        grande = aumentar_escala(im, factor=2)
        self.assertEqual(grande.size, (120, 120))
        self.assertEqual(grande.getpixel((60, 60)), (10, 20, 30))


if __name__ == "__main__":
    unittest.main()
