#!/usr/bin/env python3
"""Tests de captcha_ia.py (piezas puras, sin Playwright ni motores).

Ejecutar: python3 -m unittest discover -s tests -v
"""

import unittest

from PIL import Image

from captcha_ia import (
    aumentar_escala,
    celdas_grid,
    clasificar_celda,
    decidir_celdas,
    generar_demo_local,
    parsear_instruccion,
    recortar_sufijos,
    resolver,
    singularizar,
    stub_detector,
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

    def test_sufijo_click_verify(self):
        # formato actual del DOM: "… Click verify once there are none left"
        self.assertEqual(parsear_instruccion(
            "Select all images with bicycles Click verify once there are none left"),
            "bicycle")
        self.assertEqual(parsear_instruccion(
            "Select all images with bicyclesClick verify once there are none left"),
            "bicycle")
        self.assertEqual(parsear_instruccion(
            "Select all images with bridges Then click verify once there are none left"),
            "bridge")

    def test_articulos_y_conectores(self):
        self.assertEqual(parsear_instruccion("select squares with cars"), "car")
        self.assertEqual(parsear_instruccion("click all pictures of motorcycles"),
                         "motorcycle")
        self.assertEqual(parsear_instruccion("select all the buses"), "bus")

    def test_sin_clase(self):
        for texto in ["", "   ", "select all images", "click here"]:
            with self.subTest(texto=repr(texto)):
                self.assertIsNone(parsear_instruccion(texto))

    def test_variante_skip_explicito(self):
        # leccion 20: "If there are none, click skip" no tiene clase que
        # seleccionar -> None (el orquestador pulsa SKIP)
        for texto in ["If there are no crosswalks, click skip",
                      "If there are none, click skip",
                      "click skip once there are none left"]:
            with self.subTest(texto=texto):
                self.assertIsNone(parsear_instruccion(texto))

    def test_verify_no_se_confunde_con_skip(self):
        self.assertEqual(parsear_instruccion(
            "Select all images with bicycles Click verify once there are none left"),
            "bicycle")

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


class TestClasificarCelda(unittest.TestCase):
    def test_objetivo_umbral_bajo_0_45(self):
        # los objetos pequenos puntuan ~0.5-0.6: 0.52 basta
        self.assertEqual(clasificar_celda(
            [{"clase": "bus", "score": 0.52}], "bus"), "objetivo")

    def test_objetivo_score_alto(self):
        self.assertEqual(clasificar_celda(
            [{"clase": "bus", "score": 0.9}], "bus"), "objetivo")

    def test_otra_clase_por_encima_del_umbral_resto(self):
        self.assertEqual(clasificar_celda(
            [{"clase": "car", "score": 0.7}], "bus"), "otra")

    def test_otra_clase_bajo_umbral_resto_es_incierta(self):
        # deteccion debil de otra clase: no prueba que sea negativa
        self.assertEqual(clasificar_celda(
            [{"clase": "car", "score": 0.4}], "bus"), "incierta")

    def test_objetivo_bajo_umbral_y_otra_alta(self):
        # bus a 0.40 (< 0.45) y coche a 0.7: la evidencia fuerte es otra clase
        self.assertEqual(clasificar_celda(
            [{"clase": "bus", "score": 0.40},
             {"clase": "car", "score": 0.7}], "bus"), "otra")

    def test_sin_detecciones_es_incierta(self):
        self.assertEqual(clasificar_celda([], "bus"), "incierta")


class TestDecidirCeldas(unittest.TestCase):
    def test_seleccion_descartadas_e_inciertas(self):
        det = {
            (0, 0): [{"clase": "bus", "score": 0.9}],       # seleccion
            (0, 1): [{"clase": "car", "score": 0.75}],      # descartada
            (0, 2): [],                                     # incierta
            (1, 0): [{"clase": "bus", "score": 0.5}],       # seleccion
            (1, 1): [{"clase": "bus", "score": 0.3}],       # incierta
        }
        res = decidir_celdas(det, "bus")
        self.assertEqual(sorted(res["seleccion"]), [(0, 0), (1, 0)])
        self.assertEqual(res["descartadas"], [(0, 1)])
        self.assertEqual(sorted(res["inciertas"]), [(0, 2), (1, 1)])

    def test_vacio(self):
        res = decidir_celdas({}, "bus")
        self.assertEqual(res, {"seleccion": [], "descartadas": [], "inciertas": []})


class TestDemoLocal(unittest.TestCase):
    def test_determinista_misma_semilla(self):
        a = generar_demo_local(n=3, semilla=7, salida="/tmp/opencode/demo_a.png")
        b = generar_demo_local(n=3, semilla=7, salida="/tmp/opencode/demo_b.png")
        with open(a["ruta"], "rb") as fa, open(b["ruta"], "rb") as fb:
            self.assertEqual(fa.read(), fb.read())
        self.assertEqual(a["clases_por_celda"], b["clases_por_celda"])
        self.assertGreaterEqual(len(a["clases_por_celda"]), 0)

    def test_resolver_stub_clica_solo_buses(self):
        demo = generar_demo_local(n=3, salida="/tmp/opencode/demo_c.png")
        res = resolver(Image.open(demo["ruta"]), demo["instruccion"],
                       stub_detector(demo["clases_por_celda"]), n=3)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["clase_objetivo"], "bus")
        esperadas = sorted((f, c) for (f, c), clases in demo["clases_por_celda"].items()
                           if "bus" in clases)
        self.assertEqual(sorted(res["seleccion"]), esperadas)
        self.assertEqual(res["celdas_total"], 9)

    def test_resolver_instruccion_no_parseable(self):
        demo = generar_demo_local(n=3, salida="/tmp/opencode/demo_d.png")
        res = resolver(Image.open(demo["ruta"]), "select all images",
                       stub_detector(demo["clases_por_celda"]), n=3)
        self.assertFalse(res["ok"])
        self.assertIn("no parseable", res["error"])

    def test_demo_4x4(self):
        demo = generar_demo_local(n=4, salida="/tmp/opencode/demo_e.png")
        res = resolver(Image.open(demo["ruta"]), demo["instruccion"],
                       stub_detector(demo["clases_por_celda"]), n=4)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["celdas_total"], 16)


if __name__ == "__main__":
    unittest.main()
