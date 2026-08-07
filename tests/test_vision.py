#!/usr/bin/env python3
"""Tests de vision.py: clasificador puro y formato de salida (sin modelos).

Ejecutar: python3 -m unittest discover -s tests -v
"""

import unittest
from unittest import mock

from vision import (clasificar, _salida, cargar_perfil, chequear_ram, ejecutar,
                    _parsear_detecciones, modo_objetos_lote)
import pandas as pd


def caja(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


class TestClasificar(unittest.TestCase):

    def construir_grafico(self):
        textos, polis = [], []
        for i, anio in enumerate(range(2018, 2024)):
            x = 80 + i * 120
            textos += [str(anio), "104.22", "9.87"]
            polis += [caja(x, 500, x + 50, 520),
                      caja(x - 30, 100, x - 10, 120),
                      caja(x + 30, 200, x + 50, 220)]
        return textos, polis

    def test_grafico_detectado(self):
        textos, polis = self.construir_grafico()
        modo, _ = clasificar(textos, polis, ancho=800, alto=600)
        self.assertEqual(modo, "graficos")

    def test_documento_denso(self):
        textos = [f"línea {i}" for i in range(60)]
        polis = [caja(50, i * 9, 700, i * 9 + 6) for i in range(60)]
        modo, _ = clasificar(textos, polis, ancho=800, alto=600)
        self.assertEqual(modo, "doc")

    def test_foto_sin_texto(self):
        modo, motivo = clasificar(["", ""], [caja(0, 0, 5, 5), caja(6, 6, 10, 10)],
                                  ancho=800, alto=600)
        self.assertEqual(modo, "objetos")
        self.assertIn("2 líneas", motivo)

    def test_texto_disperso(self):
        textos = [f"palabra {i}" for i in range(8)]
        polis = [caja(100, i * 60, 400, i * 60 + 20) for i in range(8)]
        modo, _ = clasificar(textos, polis, ancho=800, alto=600)
        self.assertEqual(modo, "texto")

    def test_sin_lineas_es_objetos(self):
        modo, _ = clasificar([], [], ancho=800, alto=600)
        self.assertEqual(modo, "objetos")


class TestPerfil(unittest.TestCase):

    def test_perfil_ligero_bloquea_doc(self):
        with mock.patch.dict("os.environ", {"BETTER_OCR_PERFIL": "ligero"}):
            perfil = cargar_perfil()
        self.assertEqual(perfil["nombre"], "ligero")
        self.assertEqual(perfil["ram_max_mb"], 3500)
        error = chequear_ram("doc", perfil)
        self.assertIsNotNone(error)
        self.assertIn("limite del perfil", error["error"])
        self.assertIsNone(chequear_ram("objetos", perfil))

    def test_perfil_completo_sin_limite(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            perfil = cargar_perfil()
        self.assertEqual(perfil["nombre"], "completo")
        self.assertIsNone(perfil["ram_max_mb"])
        self.assertIsNone(chequear_ram("doc", perfil))

    def test_ejecutar_bloqueado_por_perfil(self):
        with mock.patch.dict("os.environ", {"BETTER_OCR_PERFIL": "ligero"}):
            resultado = ejecutar("x.png", "doc", con_fallback=False)
        self.assertFalse(resultado["ok"])
        self.assertIn("limite del perfil", resultado["error"])
        self.assertEqual(resultado["perfil"]["nombre"], "ligero")


class TestSalida(unittest.TestCase):

    def test_csv(self):
        datos = {"ok": True, "columnas": ["a", "b"], "tabla": [["1", "2"]]}
        salida = _salida(datos, "csv")
        self.assertIn("a,b", salida)

    def test_markdown(self):
        datos = {"ok": True, "columnas": ["a", "b"], "tabla": [["1", "2"]]}
        salida = _salida(datos, "md")
        self.assertIn("---", salida)
        self.assertIn("| 1 | 2 |", salida)


class TestParsearDetecciones(unittest.TestCase):
    def test_campos_esperados(self):
        res = {"res": {"boxes": [
            {"label": "bus", "score": 0.9, "coordinate": [1, 2, 3, 4]},
            {"class_name": "person", "score": 0.7,
             "bbox": [5, 6, 7, 8]},
        ]}}
        dets = _parsear_detecciones(res, solo_personas=False)
        self.assertEqual(len(dets), 2)
        self.assertEqual(dets[0]["clase"], "bus")
        self.assertEqual(dets[0]["score"], 0.9)
        self.assertEqual(dets[0]["bbox"], [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(dets[1]["clase"], "person")

    def test_filtro_personas(self):
        res = {"res": {"boxes": [
            {"label": "bus", "score": 0.9, "coordinate": []},
            {"label": "person", "score": 0.8, "coordinate": []},
        ]}}
        dets = _parsear_detecciones(res, solo_personas=True)
        self.assertEqual([d["clase"] for d in dets], ["person"])

    def test_sin_boxes(self):
        self.assertEqual(_parsear_detecciones({"res": {}}, False), [])
        self.assertEqual(_parsear_detecciones({}, False), [])


class TestModoObjetosLote(unittest.TestCase):
    def test_una_sola_carga_del_modelo(self):
        """El lote debe cargar RT-DETR una vez y predecir todas las rutas
        (create_model no cachea; cargar por imagen rompe los timeouts)."""
        modelo_fake = mock.Mock()
        modelo_fake.predict.side_effect = lambda ruta: iter([
            mock.Mock(json={"res": {"boxes": [
                {"label": "bus", "score": 0.9, "coordinate": [1, 2, 3, 4]}]}})
        ])
        with mock.patch.dict("sys.modules", {"paddlex": mock.Mock(
                create_model=mock.Mock(return_value=modelo_fake))}):
            salida = modo_objetos_lote(["a.png", "b.png", "c.png"])
        self.assertEqual(modelo_fake.predict.call_count, 3)
        self.assertEqual(len(salida), 3)
        self.assertEqual(salida["b.png"][0]["clase"], "bus")

    def test_error_por_ruta_no_rompe_el_lote(self):
        modelo_fake = mock.Mock()
        modelo_fake.predict.side_effect = [
            iter([mock.Mock(json={"res": {"boxes": []}})]),
            Exception("boom"),
            iter([mock.Mock(json={"res": {"boxes": []}})]),
        ]
        with mock.patch.dict("sys.modules", {"paddlex": mock.Mock(
                create_model=mock.Mock(return_value=modelo_fake))}):
            salida = modo_objetos_lote(["a.png", "b.png", "c.png"])
        self.assertEqual(salida["a.png"], [])
        self.assertEqual(salida["b.png"], [])
        self.assertEqual(salida["c.png"], [])


if __name__ == "__main__":
    unittest.main()
