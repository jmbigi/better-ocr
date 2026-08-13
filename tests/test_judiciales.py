"""Pruebas de judiciales.py (piezas puras: variantes de nombre, dorks de
litigio, parser del Boletin Oficial y sintesis). Sin Playwright ni red.

El parser del BO se prueba contra un fixture derivado del HTML REAL
capturado el 2026-08-13 (estructura div.linea-aviso + h5.seccion-rubro,
39 resultados para 'asistencia del sol')."""

import unittest

from judiciales import (armar_dorks_judiciales,
                        extraer_resultados_boletin_oficial,
                        variantes_de_nombre)

FIXTURE_BO = """
<div id="subLayouyContentDiv">
  <div class="row">
    <div class="col-md-12">
      <h5 class="seccion-rubro text-white bg-primary">EDICTOS JUDICIALES -
        CITACIONES Y NOTIFICACIONES. CONCURSOS Y QUIEBRAS. OTROS</h5>
    </div>
  </div>
  <div class="row">
    <div class="col-md-12">
      <a href="/detalleAviso/segunda/A1000001/20260812?busqueda=2">
        <div class="linea-aviso">
          <p class="item">ASISTENCIA DEL SOL S.R.L.</p>
          <p class="item-detalle"><small>El Juzgado Nacional de 1ra
            Instancia en lo Comercial N 12 declaro la quiebra de ASISTENCIA
            DEL SOL S.R.L. (CUIT 30-71234567-8) y ordeno la verificacion de
            creditos.</small></p>
        </div>
      </a>
    </div>
  </div>
  <div class="row">
    <div class="col-md-12">
      <h5 class="seccion-rubro text-white bg-primary">CONVOCATORIAS Y
        AV.COMERCIALES - AVISOS</h5>
    </div>
  </div>
  <div class="row">
    <div class="col-md-12">
      <a href="/detalleAviso/segunda/A1000002/20260811?busqueda=2">
        <div class="linea-aviso">
          <p class="item">OTRA EMPRESA S.A.</p>
          <p class="item-detalle"><small>Convocatoria a asamblea
            ordinaria.</small></p>
        </div>
      </a>
    </div>
  </div>
  <div class="row">
    <div class="col-md-12">
      <a href="/detalleAviso/segunda/A1000003/20260810?busqueda=2">
        <div class="linea-aviso">
          <p class="item">SECRETARIA DE EDUCACION</p>
          <p class="item-detalle"><small>Resolucion administrativa sin
            relacion judicial.</small></p>
        </div>
      </a>
    </div>
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

    def test_extrae_resultados_cuits_y_seccion(self):
        r = extraer_resultados_boletin_oficial(FIXTURE_BO)
        self.assertEqual(len(r), 3)
        self.assertEqual(r[0]["titulo"], "ASISTENCIA DEL SOL S.R.L.")
        self.assertEqual(r[0]["cuits"], ["30-71234567-8"])
        self.assertTrue(r[0]["litigio"])
        self.assertIn("CONCURSOS Y QUIEBRAS", r[0]["seccion"])
        self.assertIn("detalleAviso", r[0]["url"])
        self.assertFalse(r[2]["litigio"])

    def test_marca_litigio_solo_en_edictos_judiciales(self):
        r = extraer_resultados_boletin_oficial(FIXTURE_BO)
        self.assertTrue(r[0]["litigio"])
        self.assertFalse(r[1]["litigio"])
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

    def test_por_frase_completa_del_nombre(self):
        r = {"titulo": "EDICTO: Quiebra de ASISTENCIA DEL SOL S.R.L.",
             "snippet": "verificacion de creditos"}
        self.assertTrue(self._f(r, "Asistencia del Sol"))

    def test_palabra_comun_sola_no_alcanza(self):
        """Verificado 2026-08-13: 'asistencia del sol' devuelve 39 avisos
        con 'asistencia' como palabra comun; el filtro NO debe marcarlos."""
        r = {"titulo": "AVISO: asistencia en la sede social",
             "snippet": "Los accionistas..."}
        self.assertFalse(self._f(r, "Asistencia del Sol"))

    def test_dos_palabras_significativas_si_alcanzan(self):
        r = {"titulo": "EDICTO: ASISTENCIA SOL EMPRESA DE CUIDADOS",
             "snippet": ""}
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
