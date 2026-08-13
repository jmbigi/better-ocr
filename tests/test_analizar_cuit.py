"""Pruebas de analizar_cuit.py (piezas puras: clasificacion por prefijo,
banda de emision del DNI, tipo por razon social, parser de ficha de
CuitOnline, dorks de recomendadores y perfil por reglas). Sin red.

El parser de la ficha se prueba contra un fixture derivado del HTML REAL
capturado el 2026-08-12 (ficha de CUIL sin CUIT); la estructura de ficha de
empresa queda marcada NO verificada en el codigo."""

import unittest

from analizar_cuit import (banda_emision_dni, clasificar_cuit,
                           extraer_ficha_cuitonline, perfil_por_reglas,
                           tipo_por_razon_social)

FIXTURE_FICHA_CUIL = """
<!DOCTYPE html>
<html><head><title>ASOC MUTUAL DEL PERSONAL YPF (20-12345678-9) - Cuit Online
</title></head><body>
<div class="main-container">
<h1>ASOC MUTUAL DEL PERSONAL YPF (20-12345678-9) - Cuit Online</h1>
<p>La información aquí mostrada corresponde a un CUIL (Código Único de
identificación Laboral) y no a un CUIT (Código Único de identificación
Tributaria).</p>
<p>ASOC MUTUAL DEL PERSONAL YPF no posee CUIT dado de alta en AFIP, pero sí
posee número de CUIL dado de alta o actualizado en ANSES</p>
</div></body></html>
"""


class TestClasificarCuit(unittest.TestCase):

    def test_persona_fisica_por_prefijo(self):
        for cuit in ("20-12345678-9", "23-12345678-9", "24-12345678-9",
                     "25-12345678-9", "26-12345678-9", "27-12345678-9"):
            r = clasificar_cuit(cuit)
            self.assertEqual(r["tipo"], "persona_fisica")
            self.assertGreaterEqual(r["confianza"], 0.9)

    def test_persona_juridica_por_prefijo(self):
        for cuit in ("30-12345678-9", "33-12345678-9", "34-12345678-9"):
            r = clasificar_cuit(cuit)
            self.assertEqual(r["tipo"], "persona_juridica")

    def test_prefijo_no_asignado(self):
        for cuit in ("21-12345678-9", "28-12345678-9", "32-12345678-9"):
            self.assertEqual(clasificar_cuit(cuit)["tipo"], "otro")

    def test_formato_invalido(self):
        self.assertEqual(clasificar_cuit("123")["tipo"], "invalido")
        self.assertEqual(clasificar_cuit("")["tipo"], "invalido")
        self.assertEqual(clasificar_cuit(None)["tipo"], "invalido")


class TestBandaDni(unittest.TestCase):

    def test_dni_de_la_entrada_emision_2020s(self):
        r = banda_emision_dni("20-45000000-9")
        self.assertTrue(r["aplica"])
        self.assertIn("2020s", r["banda"])

    def test_solo_aplica_a_persona_fisica(self):
        r = banda_emision_dni("30-12345678-9")
        self.assertFalse(r["aplica"])

    def test_advertencia_de_imprecision_siempre_presente(self):
        """P1.10: la banda nunca se presenta como edad."""
        r = banda_emision_dni("20-12345678-9")
        self.assertIn("no permite calcular la EDAD", r["advertencia"])


class TestTipoPorRazonSocial(unittest.TestCase):

    def test_tipos_societarios(self):
        self.assertIn("SA", tipo_por_razon_social("Algo S.A.")["tipo"])
        self.assertIn("SRL", tipo_por_razon_social("Algo S.R.L.")["tipo"])
        self.assertIn("SAS", tipo_por_razon_social("Algo SAS")["tipo"])
        self.assertIn("Mutual", tipo_por_razon_social(
            "ASOC MUTUAL DEL PERSONAL")["tipo"])

    def test_sin_sufijo_no_inventa(self):
        r = tipo_por_razon_social("Asistencia del Sol")
        self.assertEqual(r["tipo"], "sin_tipo")
        self.assertEqual(r["confianza"], 0.0)

    def test_razon_vacia(self):
        self.assertEqual(tipo_por_razon_social("")["tipo"],
                         "sin_razon_social")


class TestFichaCuitonline(unittest.TestCase):

    def test_ficha_cuil_real_distingue_cuit_cuil(self):
        """Verificado contra HTML real: la ficha dice textualmente que NO
        posee CUIT y SI posee CUIL."""
        f = extraer_ficha_cuitonline(FIXTURE_FICHA_CUIL)
        self.assertEqual(f["posee_cuit"], False)
        self.assertEqual(f["posee_cuil"], True)
        self.assertIn("ASOC MUTUAL", f["razon_social"])
        self.assertEqual(f["tipo_documento"], "20-12345678-9")

    def test_ficha_empresa_marcada_no_verificada(self):
        f = extraer_ficha_cuitonline(FIXTURE_FICHA_CUIL)
        self.assertFalse(f["parser_verificado"])

    def test_html_vacio(self):
        f = extraer_ficha_cuitonline("")
        self.assertEqual(f["posee_cuit"], None)
        self.assertEqual(f["razon_social"], "")


class TestDorksRecomendadores(unittest.TestCase):

    def test_armar(self):
        from analizar_cuit import armar_dorks_recomendadores
        d = armar_dorks_recomendadores("Asistencia del Sol")
        self.assertEqual(len(d), 4)
        self.assertIn('"Asistencia del Sol" opiniones OR rese[ñn]as', d)
        self.assertIn('"Asistencia del Sol" google maps', d)


class TestPerfilPorReglas(unittest.TestCase):

    def setUp(self):
        self._p = perfil_por_reglas

    def test_dominio_con_org_coincidente_es_senal_fuerte(self):
        ficha = {"posee_cuit": True, "posee_cuil": None,
                 "razon_social": "ASISTENCIA DEL SOL SRL"}
        perfil = self._p(ficha, clasificar_cuit("30-12345678-9"),
                         banda_emision_dni("30-12345678-9"),
                         tipo_por_razon_social("ASISTENCIA DEL SOL SRL"),
                         {"dominios": [{"ok": True,
                                        "dominio": "asistenciadelsol.com.ar",
                                        "titular_tipo": "persona_juridica",
                                        "titular_org":
                                            "ASISTENCIA DEL SOL SRL"}]})
        self.assertTrue(any("a nombre de la propia organizacion"
                            in s for s in perfil["senales"]))

    def test_dominio_con_org_distinta_se_reporta_diferencia(self):
        """P1.10: dominio con nombre similar pero titular distinto se
        reporta como diferencia, nunca como asociado."""
        ficha = {"posee_cuit": True, "posee_cuil": None,
                 "razon_social": "ASISTENCIA DEL SOL SRL"}
        perfil = self._p(ficha, clasificar_cuit("30-12345678-9"),
                         banda_emision_dni("30-12345678-9"),
                         tipo_por_razon_social("ASISTENCIA DEL SOL SRL"),
                         {"dominios": [{"ok": True,
                                        "dominio": "asistenciadelsol.com.ar",
                                        "titular_tipo": "persona_juridica",
                                        "titular_org": "OTRA EMPRESA SA"}]})
        self.assertTrue(any("no coincide" in s for s in perfil["senales"]))

    def test_dominio_titular_no_publicado(self):
        ficha = {"posee_cuit": True, "posee_cuil": None,
                 "razon_social": "ASISTENCIA DEL SOL SRL"}
        perfil = self._p(ficha, clasificar_cuit("30-12345678-9"),
                         banda_emision_dni("30-12345678-9"),
                         tipo_por_razon_social("ASISTENCIA DEL SOL SRL"),
                         {"dominios": [{"ok": True,
                                        "dominio": "asistenciadelsol.com.ar",
                                        "titular_tipo": "no_publicado"}]})
        self.assertTrue(any("titular no publicado" in s
                            for s in perfil["senales"]))

    def test_perfil_fisica_sin_actividad(self):
        ficha = {"posee_cuit": False, "posee_cuil": True,
                 "razon_social": "PEREZ JUAN"}
        perfil = self._p(ficha, clasificar_cuit("20-12345678-9"),
                         banda_emision_dni("20-12345678-9"),
                         tipo_por_razon_social("PEREZ JUAN"),
                         {"judiciales": [], "dorks_ok": True})
        self.assertIn("sin CUIT dado de alta", perfil["senales"][0])
        self.assertIn("sin resultados", perfil["riesgo"][0].lower()
                      if perfil["riesgo"] else "")

    def test_contradiccion_fisica_con_razon_societaria(self):
        """P1.10: prefijo fisico + razon social con SRL se REPORTA."""
        ficha = {"posee_cuit": None, "posee_cuil": None,
                 "razon_social": "ALGO SRL"}
        perfil = self._p(ficha, clasificar_cuit("20-12345678-9"),
                         banda_emision_dni("20-12345678-9"),
                         tipo_por_razon_social("ALGO SRL"), {})
        self.assertTrue(any("CONTRADICCION" in n for n in perfil["notas"]))

    def test_confianza_mayor_con_datos(self):
        ficha = {"posee_cuit": True, "posee_cuil": None,
                 "razon_social": "ALGO SA"}
        sin_datos = self._p({}, clasificar_cuit("30-12345678-9"),
                            banda_emision_dni("30-12345678-9"),
                            tipo_por_razon_social(""), {})
        con_datos = self._p(ficha, clasificar_cuit("30-12345678-9"),
                            banda_emision_dni("30-12345678-9"),
                            tipo_por_razon_social("ALGO SA"), {})
        self.assertGreater(con_datos["confianza_global"],
                           sin_datos["confianza_global"])


if __name__ == "__main__":
    unittest.main()
