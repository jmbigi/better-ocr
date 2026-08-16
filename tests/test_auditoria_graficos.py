#!/usr/bin/env python3
"""Tests de auditoria_graficos.py: análisis determinista puro con imágenes
sintéticas generadas con PIL (sin modelos ni red).

Ejecutar: python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from auditoria_graficos import (clasificar_tipo, describir_determinista,
                                check_contraste, check_leyenda,
                                check_nitidez, check_resolucion,
                                check_superposiciones, check_zoom_cortes,
                                check_texto_pequeno, check_ruido,
                                _colores_series, _componentes, _ruido,
                                _maxima_densidad, _tinta, auditar,
                                vision_ia, _parsear_rubrica_vlm)


def _imagen_con(dibujar, w=900, h=600):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    dibujar(d)
    return img


def _grafico_limpio():
    """Barras nítidas, bien separadas, sin leyenda, sin etiquetas densas."""
    def dib(d):
        d.rectangle([100, 400, 220, 560], fill=(52, 101, 164))
        d.rectangle([280, 300, 400, 560], fill=(52, 101, 164))
        d.rectangle([460, 200, 580, 560], fill=(52, 101, 164))
        d.rectangle([640, 150, 760, 560], fill=(52, 101, 164))
        d.line([(60, 560), (850, 560)], fill="black", width=3)
    return _imagen_con(dib)


def _etiquetas_superpuestas():
    """Tres textos de valores superpuestos (glifos engrosados: densidad
    local de tinta tipo texto muy alta)."""
    def dib(d):
        d.rectangle([100, 400, 220, 560], fill=(52, 101, 164))
        for dx, dy in ((150, 300), (152, 302), (154, 304)):
            d.text((dx, dy), "1234", fill="black")
    return _imagen_con(dib)


def _borrosa():
    img = _grafico_limpio()
    return img.filter(ImageFilter.GaussianBlur(radius=4))


def _bajo_contraste():
    def dib(d):
        d.rectangle([100, 400, 220, 560], fill=(245, 245, 245), outline=(200, 200, 200))
        d.text((120, 300), "valor", fill=(230, 230, 230))
    return _imagen_con(dib)


def _tinta_en_bordes():
    def dib(d):
        # tinta pegada a los 4 bordes (recorte/zoom)
        d.rectangle([0, 200, 60, 240], fill="black")
        d.rectangle([850, 200, 900, 240], fill="black")
        d.rectangle([400, 0, 440, 60], fill="black")
        d.rectangle([400, 560, 440, 600], fill="black")
    return _imagen_con(dib)


def _con_leyenda_pegada():
    def dib(d):
        d.rectangle([100, 400, 220, 560], fill=(52, 101, 164))
        d.rectangle([870, 100, 930, 240], outline="black")
        # texto engrosado (doble trazo): el bitmap de PIL es muy fino
        for dx, dy in ((0, 0), (1, 0)):
            d.text((875 + dx, 110 + dy), "Serie A", fill="black")
            d.text((875 + dx, 170 + dy), "Serie B", fill="black")
    return _imagen_con(dib)


def _sin_leyenda_2_series():
    def dib(d):
        d.rectangle([100, 400, 220, 560], fill=(200, 30, 30))
        d.rectangle([300, 300, 420, 560], fill=(30, 120, 200))
    return _imagen_con(dib)


def _pastel():
    def dib(d):
        d.ellipse([200, 100, 700, 600], fill=(200, 30, 30), outline="black")
        d.ellipse([420, 100, 700, 600], fill=(30, 120, 200))
    return _imagen_con(dib)


def _scatter():
    rng = np.random.default_rng(7)
    arr = np.zeros((600, 900, 3), dtype=np.uint8)
    arr[:] = 255
    xs = rng.integers(8, 892, size=60)
    ys = rng.integers(8, 592, size=60)
    for x, y in zip(xs, ys):
        arr[y:y + 5, x:x + 5] = (40, 40, 40)
    return Image.fromarray(arr)


def _vacia():
    return Image.new("RGB", (900, 600), "white")


class TestUtils(unittest.TestCase):
    def test_tinta_umbrally(self):
        gris = np.full((10, 10), 255.0)
        gris[2:5, 2:5] = 10.0
        m = _tinta(gris, umbral=200.0)
        self.assertEqual(m.sum(), 9)

    def test_componentes_separa_blobs(self):
        m = np.zeros((50, 50), dtype=bool)
        m[5:10, 5:10] = True
        m[30:35, 30:35] = True
        blobs = _componentes(m)
        self.assertEqual(len(blobs), 2)

    def test_componentes_une_diagonales(self):
        m = np.zeros((20, 20), dtype=bool)
        for i in range(5, 12):
            m[i, i] = True
        m[11, 12] = True  # esquina: 8-conectividad une
        blobs = _componentes(m, min_area=1)
        self.assertEqual(len(blobs), 1)

    def test_maxima_densidad_bloque_lleno(self):
        m = np.zeros((100, 100), dtype=bool)
        m[40:60, 40:60] = True
        d, _ = _maxima_densidad(m, 20)
        self.assertGreaterEqual(d, 0.99)

    def test_maxima_densidad_escasa(self):
        m = np.zeros((100, 100), dtype=bool)
        m[5, 5] = True
        m[50, 50] = True
        d, _ = _maxima_densidad(m, 20)
        self.assertLess(d, 0.3)

    def test_ruido_pixeles_aislados(self):
        m = np.zeros((20, 20), dtype=bool)
        m[2, 2] = True
        m[10, 10] = True
        self.assertGreater(_ruido(m), 0.99)
        m2 = np.zeros((20, 20), dtype=bool)
        m2[5:10, 5:10] = True
        self.assertLess(_ruido(m2), 0.5)

    def test_colores_series_cuadrado_rojo(self):
        img = Image.new("RGB", (100, 100), "white")
        d = ImageDraw.Draw(img)
        d.rectangle([10, 10, 60, 60], fill=(200, 30, 30))
        gris = np.asarray(img.convert("L"), dtype=np.float32)
        rgb = np.asarray(img, dtype=np.uint8)
        m = _tinta(gris)
        series = _colores_series(rgb, m)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["color"], "#c00000")


class TestChecks(unittest.TestCase):
    def test_grafico_limpio_sin_problemas(self):
        res = describir_determinista(_grafico_limpio())
        self.assertEqual(res["tipo"], "barras")
        graves = [h for h in res["hallazgos"] if h["severidad"] == "problema"]
        self.assertEqual(graves, [])

    def test_etiquetas_superpuestas(self):
        res = describir_determinista(_etiquetas_superpuestas())
        tipos = {h["tipo"] for h in res["hallazgos"]}
        self.assertIn("superposicion", tipos)
        self.assertIn(
            "problema",
            {h["severidad"] for h in res["hallazgos"] if h["tipo"] == "superposicion"})

    def test_imagen_borrosa(self):
        res = describir_determinista(_borrosa())
        self.assertIn("nitidez", {h["tipo"] for h in res["hallazgos"]})

    def test_bajo_contraste(self):
        res = describir_determinista(_bajo_contraste())
        self.assertIn("contraste", {h["tipo"] for h in res["hallazgos"]})

    def test_tinta_en_bordes_zoom(self):
        res = describir_determinista(_tinta_en_bordes())
        self.assertIn("zoom", {h["tipo"] for h in res["hallazgos"]})

    def test_leyenda_pegada_al_borde(self):
        res = describir_determinista(_con_leyenda_pegada())
        leyenda = [h for h in res["hallazgos"] if h["tipo"] == "leyenda"]
        self.assertTrue(any("pegada" in h["mensaje"] for h in leyenda))

    def test_sin_leyenda_con_series(self):
        res = describir_determinista(_sin_leyenda_2_series())
        self.assertEqual(res["n_series"], 2)
        self.assertTrue(any("sin leyenda" in h["mensaje"] for h in res["hallazgos"]))

    def test_resolucion_baja(self):
        img = Image.new("RGB", (300, 200), "white")
        res = describir_determinista(img)
        self.assertIn("resolucion", {h["tipo"] for h in res["hallazgos"]})

    def test_imagen_vacia(self):
        res = describir_determinista(_vacia())
        self.assertEqual(res["tipo"], "indeterminado")
        self.assertEqual(res["hallazgos"], [])

    def test_imagen_negra_no_crashea(self):
        res = describir_determinista(Image.new("RGB", (200, 150), "black"))
        self.assertTrue(res["hallazgos"])

    def test_ruta_inexistente_falla_explicito(self):
        with self.assertRaises(FileNotFoundError):
            describir_determinista("/no/existe/grafico.png")


class TestTipoGrafico(unittest.TestCase):
    def test_clasifica_barras(self):
        res = describir_determinista(_grafico_limpio())
        self.assertEqual(res["tipo"], "barras")
        self.assertGreater(res["confianza_tipo"], 0)

    def test_clasifica_pastel(self):
        res = describir_determinista(_pastel())
        self.assertEqual(res["tipo"], "pastel")

    def test_clasifica_scatter(self):
        res = describir_determinista(_scatter())
        self.assertEqual(res["tipo"], "scatter")

    def test_indeterminado_sin_contenido(self):
        img = Image.new("RGB", (500, 400), "white")
        res = describir_determinista(img)
        self.assertEqual(res["tipo"], "indeterminado")
        self.assertEqual(res["confianza_tipo"], 0.0)

    def test_clasificar_tipo_directo_vacio(self):
        res = clasificar_tipo([], 900, 600, [])
        self.assertEqual(res["tipo"], "indeterminado")


class TestVLM(unittest.TestCase):
    def test_parsear_rubrica_dos_formatos(self):
        texto = ("Grafico de barras con tendencia creciente.\n"
                 "superposiciones: 3/10\n"
                 "6/10: leyenda\n"
                 "zoom: N/A\n"
                 "linea inventada fuera de rubrica")
        r = _parsear_rubrica_vlm(texto)
        self.assertEqual(r["notas"]["superposiciones"], 3.0)
        self.assertEqual(r["notas"]["leyenda"], 6.0)
        self.assertIsNone(r["notas"]["zoom"])
        # la línea descriptiva y la inventada quedan como no_conformes
        self.assertEqual(len(r["no_conformes"]), 2)

    def test_vision_ia_motor_simulado(self):
        # el VLM real emite líneas separadas: descripción + 'aspecto: nota/10'
        fake = "Grafico de barras.\nsuperposiciones: 1/10"
        r = _parsear_rubrica_vlm(fake)
        self.assertEqual(r["notas"]["superposiciones"], 1.0)

    def test_vision_ia_error_reportado(self):
        # el motor real NO se ejecuta en tests: se fuerza el fallo limpio
        img = _grafico_limpio()
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "g.png")
            img.save(ruta)
            with mock.patch("auditoria_graficos._cargar_motores",
                            return_value=(None, "motor no disponible")):
                res = vision_ia(ruta, motor="docbee", timeout_s=1)
            self.assertFalse(res["ok"])
            self.assertIn("error", res)
            self.assertIsNotNone(res["error"])

    def test_auditar_sin_vision(self):
        res = auditar(_grafico_limpio())
        self.assertIsNone(res["vision"])
        self.assertIn("resumen", res)

    def test_auditar_con_vision_simulado(self):
        # auditar() con motor simulado: se parchea vision_ia del módulo
        img = _grafico_limpio()
        with mock.patch("auditoria_graficos.vision_ia",
                        return_value={"ok": True, "texto": "Grafico de barras.",
                                      "rubrica": {"notas": {"zoom": 9.0},
                                                  "no_conformes": []},
                                      "error": None}):
            res = auditar(img, vision="ollama")
        self.assertTrue(res["vision"]["ok"])
        self.assertIn("resumen", res)


# ---------------------------------------------------------------------------
# Layouts multi-panel (grids NxN)
# ---------------------------------------------------------------------------

def _grid(desalinear_ejes=False, gutter_irregular=False, panel_vacio=False,
          con_titulo=True):
    """Grid sintético 2x2 de paneles con barras (lienzo 1000x760).

    Panel: 430x280 en (40, 60); gutters 40 px. Cada panel: 4 barras + eje X.
    """
    img = Image.new("RGB", (1000, 760), "white")
    d = ImageDraw.Draw(img)
    if con_titulo:
        d.text((400, 22), "Titulo general de los cuatro paneles", fill="black")
    for fila in range(2):
        for col in range(2):
            px = 40 + col * (430 + 40)
            py = 60 + fila * (280 + 40)
            if panel_vacio and fila == 1 and col == 1:
                continue
            alturas = [200, 140, 240, 90]
            for i, alt in enumerate(alturas):
                d.rectangle([px + 20 + i * 95, py + 260 - alt,
                             px + 75 + i * 95, py + 260], fill=(52, 101, 164))
            eje = py + 260
            if desalinear_ejes and col == 1:
                eje += 25
            d.line([(px, eje), (px + 430, eje)], fill="black", width=3)
    return img


class TestMultiPanel(unittest.TestCase):
    def test_detecta_grid_2x2(self):
        res = describir_determinista(_grid())
        multi = res["multi_panel"]
        self.assertIsNotNone(multi)
        self.assertEqual(multi["n_filas"], 2)
        self.assertEqual(multi["n_cols"], 2)
        self.assertEqual(len(multi["paneles"]), 4)

    def test_grafico_unico_no_es_multi(self):
        res = describir_determinista(_grafico_limpio())
        self.assertIsNone(res["multi_panel"])

    def test_barras_solas_no_generan_falsos_gutters(self):
        # un gráfico único de barras (huecos entre barras) NO debe parecer grid
        res = describir_determinista(_grafico_limpio())
        self.assertIsNone(res["multi_panel"])

    def test_ejes_desalineados_en_fila(self):
        res = describir_determinista(_grid(desalinear_ejes=True))
        tipos = {h["tipo"] for h in res["multi_panel"]["hallazgos_layout"]}
        self.assertIn("alineacion_ejes", tipos)

    def test_gutter_irregular(self):
        # grid 1x3 con gutters verticales de distinto ancho (14 vs 40 px):
        # la columna c empieza tras la anterior + su ancho + su gutter
        img = Image.new("RGB", (1000, 480), "white")
        d = ImageDraw.Draw(img)
        d.text((400, 15), "Titulo general", fill="black")
        px_cols = [40]
        for gutter in (14, 40):
            px_cols.append(px_cols[-1] + 290 + gutter)
        for px in px_cols:
            py = 80
            for i, alt in enumerate([200, 140, 90]):
                d.rectangle([px + 20 + i * 90, py + 260 - alt,
                             px + 75 + i * 90, py + 260], fill=(52, 101, 164))
            d.line([(px, py + 260), (px + 290, py + 260)], fill="black", width=3)
        res = describir_determinista(img)
        tipos = {h["tipo"] for h in res["multi_panel"]["hallazgos_layout"]}
        self.assertIn("gutter_irregular", tipos)

    def test_panel_vacio(self):
        res = describir_determinista(_grid(panel_vacio=True))
        layout = res["multi_panel"]["hallazgos_layout"]
        self.assertTrue(any(h["tipo"] == "panel_vacio"
                            and h["severidad"] == "problema" for h in layout))

    def test_sin_titulo_general(self):
        res = describir_determinista(_grid(con_titulo=False))
        layout = res["multi_panel"]["hallazgos_layout"]
        self.assertTrue(any(h["tipo"] == "titulo" for h in layout))

    def test_sugerencias_presentes(self):
        res = describir_determinista(_grid())
        self.assertIsInstance(res["sugerencias"], list)
        # el grid alineado solo genera sugerencias de disposición (info)
        for s in res["sugerencias"]:
            self.assertIn("sugerencia", s)

    def test_sugerencias_por_hallazgo(self):
        res = describir_determinista(_grid(panel_vacio=True))
        self.assertTrue(any(s["tipo"] == "panel_vacio" for s in res["sugerencias"]))

    def test_paneles_individuales_analizados(self):
        res = describir_determinista(_grid())
        for p in res["multi_panel"]["paneles"]:
            self.assertEqual(p["tipo"], "barras")
            self.assertIn("hallazgos", p)


if __name__ == "__main__":
    unittest.main()
