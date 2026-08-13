"""Pruebas de judiciales.py (piezas puras: variantes de nombre, dorks de
litigio, parser del Boletin Oficial y sintesis). Sin Playwright ni red.

El parser del BO se prueba contra un fixture sintetico de su plantilla
(documentada en el codigo como NO VERIFICADA contra HTML real: el BO sufrio
timeouts desde esta IP el 2026-08-12); los tests reflejan esa limitacion."""

import unittest

from judiciales import (armar_dorks_judiciales,
                        extraer_resultados_boletin_oficial,
                        variantes_de_nombre)

FIXTURE_BO = """
<div id="subLayouyContentDiv">
  <div class="resultado-buscador">
    <span class="fecha-publicacion">12/08/2026</span>
    <span class="seccion-publicacion">Edictos</span>
    <h4><a href="/sections/edictos/2026/08/12/1234.html">
      EDICTO: Quiebra de ASISTENCIA DEL SOL S.R.L. (CUIT 30-71234567-8)</a></h4>
    <p>El Juzgado Nacional de Primera Instancia en lo Comercial N° 12
       declaró la quiebra de ASISTENCIA DEL SOL S.R.L. y ordenó la
       verificación de créditos.</p>
  </div>
  <div class="resultado-buscador">
    <h4><a href="/sections/edictos/2026/08/11/5678.html">
      EDICTO: Concurso preventivo de Otra Empresa S.A.</a></h4>
    <p>Citase a los acreedores de OTRA EMPRESA S.A.</p>
  </div>
  <div class="resultado-buscador">
    <span class="fecha-publicacion">10/08/2026</span>
    <h4><a href="/sections/sociedades/2026/08/10/999.html">
      Sociedades: disolucion sin concurso</a></h4>
    <p>Edicto de disolucion voluntaria sin intervencion judicial.</p>
  </div>
</div>
"""


class TestVariantes(unittest.TestCase):

    def test_empresa_quita_sufijo(self):
        self.assertEqual(variantes_de_nombre("Asistencia del Sol SRL"),
                         ["Asistencia del Sol SRL", "Asistencia del Sol"])

    def test_persona_sin_sufijo(self):
        self.assertEqual(variantes_de_nombre("Perez Juan"),
                         ["Perez Juan"])

    def test_sin_duplicados(self):
        v = variantes_de_nombre("Algo SA")
        self.assertEqual(v, ["Algo SA", "Algo"])


class TestDorks(unittest.TestCase):

    def test_todos_los_dorks_con_nombre_entre_comillas(self):
        d = armar_dorks_judiciales("Asistencia del Sol")
        self.assertEqual(len(d), 6)
        self.assertIn('"Asistencia del Sol" juicio', d)
        self.assertIn('"Asistencia del Sol" CNAT', d)
        for consulta in d:
            self.assertTrue(consulta.startswith('"'))


class TestParserBO(unittest.TestCase):

    def test_extrae_resultados_y_cuits(self):
        r = extraer_resultados_boletin_oficial(FIXTURE_BO)
        self.assertEqual(len(r), 3)
        self.assertIn("Quiebra de ASISTENCIA DEL SOL", r[0]["titulo"])
        self.assertEqual(r[0]["cuits"], ["30-71234567-8"])
        self.assertTrue(r[0]["litigio"])
        self.assertFalse(r[2]["litigio"])
        self.assertIn("Concurso preventivo de Otra Empresa", r[1]["titulo"])

    def test_marca_litigio_solo_en_edictos_judiciales(self):
        r = extraer_resultados_boletin_oficial(FIXTURE_BO)
        self.assertTrue(r[0]["litigio"])
        self.assertTrue(r[1]["litigio"])
        self.assertFalse(r[2]["litigio"])

    def test_html_vacio(self):
        self.assertEqual(extraer_resultados_boletin_oficial(""), [])


class TestInteresante(unittest.TestCase):

    def setUp(self):
        from judiciales import _es_resultado_interesante
        self._f = _es_resultado_interesante

    def test_por_cuit_exacto(self):
        r = {"titulo": "EDICTO: Quiebra de X SRL",
             "snippet": "CUIT 30-71234567-8 declarada la quiebra"}
        self.assertTrue(self._f(r, "Otra Empresa", "30-71234567-8"))

    def test_por_palabra_del_nombre(self):
        r = {"titulo": "EDICTO: Quiebra de ASISTENCIA DEL SOL S.R.L.",
             "snippet": "verificacion de creditos"}
        self.assertTrue(self._f(r, "Asistencia del Sol"))

    def test_falso_positivo_descartado(self):
        r = {"titulo": "EDICTO: Quiebra de OTRA EMPRESA",
             "snippet": "sin coincidencia"}
        self.assertFalse(self._f(r, "Asistencia del Sol"))


class TestSintesis(unittest.TestCase):

    def setUp(self):
        from judiciales import _sintetizar
        self._s = _sintetizar

    def test_hallazgos_y_ausencia_honesta(self):
        informe = {
            "boletin_oficial": {"estado": "error", "bloqueo": "timeout"},
            "demandas_web": {"dorks": [{"consulta": '"X" juicio',
                                        "motores": [{"resultados": []}]}]},
        }
        sin = self._s(informe, "X", "20-12345678-9")
        texto = " ".join(sin["hallazgos"])
        self.assertIn("sin resultados", texto)
        self.assertTrue(any("NO prueba" in lim for lim in sin["limitaciones"]))

    def test_bo_ok_con_hallazgos(self):
        informe = {
            "boletin_oficial": {
                "estado": "ok", "parser_verificado": True,
                "resultados": [{"_interesante": True, "fecha": "12/08/2026",
                                "titulo": "EDICTO: Quiebra de X",
                                "litigio": True}]},
            "demandas_web": None,
        }
        sin = self._s(informe, "X")
        texto = " ".join(sin["hallazgos"])
        self.assertIn("parser verificado", texto)
        self.assertIn("EDICTO: Quiebra de X", texto)


if __name__ == "__main__":
    unittest.main()
