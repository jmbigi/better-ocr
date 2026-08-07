#!/usr/bin/env python3
"""Tests de captcha_ia.py (piezas puras, sin Playwright ni motores).

Ejecutar: python3 -m unittest discover -s tests -v
"""

import unittest

from captcha_ia import parsear_instruccion, recortar_sufijos, singularizar


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


if __name__ == "__main__":
    unittest.main()
