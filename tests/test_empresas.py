"""Pruebas de empresas.py (piezas puras: variantes de nombre, extraccion de
CUIT/razon social, parseo RDAP y sintesis del informe). Sin Playwright ni red.

Fixtures de CuitOnline derivados de HTML REAL capturado el 2026-08-12
(recortado, sin datos personales)."""

import json
import unittest

from empresas import (extraer_cuits_de_html, extraer_razon_social_de_html,
                      limpiar_sufijo_legal, parsear_rdap, variantes_de_nombre)

FIXTURE_CUITONLINE_OK = """
<div class="results">
  <div id="searchResults">
    <div class="hit">
      <div class="denominacion">
        <a href="detalle/20123456789/asoc-mutual-del-personal-ypf.html" title="Ver detalles de ASOC MUTUAL DEL PERSONAL YPF" class="denominacion">
          <h2 class="denominacion" style="margin-bottom:10px;">ASOC MUTUAL DEL PERSONAL YPF</h2>
        </a>
      </div>
      <div class="doc-facets">
        <span class="linea-cuit-persona"><span class="bullet">•</span>&nbsp;CUIL:&nbsp;<span class="cuit">20-12345678-9</span></span>
      </div>
    </div>
  </div>
</div>
"""

FIXTURE_WEB_FOOTER = """
<html><body>
<footer>© 2025 Permanencia Salud Srl. Todos los derechos reservados.</footer>
</body></html>
"""

FIXTURE_WEB_SIN_FOOTER = "<html><body><p>Sin pie de pagina</p></body></html>"

FIXTURE_RDAP = {
    "events": [
        {"eventAction": "registration", "eventDate": "2013-11-13T00:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2027-11-13T00:00:00Z"},
    ],
    "entities": [
        {"roles": ["registrant"],
         "vcardArray": ["vcard", [["fn", {}, "text", "TITULAR PERSONAL"]]]},
        {"roles": ["registrar"],
         "vcardArray": ["vcard", [["fn", {}, "text", "nicar"]]]},
    ],
}


class TestSufijosLegales(unittest.TestCase):

    def test_quita_srl_al_final(self):
        self.assertEqual(limpiar_sufijo_legal("Permanencia Salud Srl"),
                         "Permanencia Salud")

    def test_quita_srl_mayusculas_y_puntos(self):
        self.assertEqual(limpiar_sufijo_legal("Permanencia Salud S.R.L."),
                         "Permanencia Salud")

    def test_sin_sufijo_queda_igual(self):
        self.assertEqual(limpiar_sufijo_legal("Asistencia del Sol"),
                         "Asistencia del Sol")

    def test_sufijo_en_medio_no_se_toca(self):
        self.assertEqual(limpiar_sufijo_legal("Sa Salud Srl"),
                         "Sa Salud")

    def test_sa_y_sas(self):
        self.assertEqual(limpiar_sufijo_legal("Algo S.A."), "Algo")
        self.assertEqual(limpiar_sufijo_legal("Algo SAS"), "Algo")
        self.assertEqual(limpiar_sufijo_legal("Algo SH"), "Algo")


class TestVariantes(unittest.TestCase):

    def test_con_sufijo_genera_original_y_limpia(self):
        v = variantes_de_nombre("Permanencia Salud Srl")
        self.assertEqual(v, ["Permanencia Salud Srl", "Permanencia Salud"])

    def test_sin_sufijo_solo_original(self):
        v = variantes_de_nombre("Asistencia del Sol")
        self.assertEqual(v, ["Asistencia del Sol"])

    def test_sin_duplicados(self):
        v = variantes_de_nombre("Cuidarte Siempre SRL")
        self.assertEqual(v, ["Cuidarte Siempre SRL", "Cuidarte Siempre"])


class TestExtraerCuits(unittest.TestCase):

    def test_cuit_del_fixture_real(self):
        self.assertEqual(extraer_cuits_de_html(FIXTURE_CUITONLINE_OK),
                         {"20-12345678-9"})

    def test_html_sin_cuit(self):
        self.assertEqual(extraer_cuits_de_html(FIXTURE_WEB_SIN_FOOTER), set())

    def test_html_vacio(self):
        self.assertEqual(extraer_cuits_de_html(""), set())
        self.assertEqual(extraer_cuits_de_html(None), set())


class TestRazonSocial(unittest.TestCase):

    def test_footer_copyright(self):
        self.assertEqual(extraer_razon_social_de_html(FIXTURE_WEB_FOOTER),
                         "Permanencia Salud Srl")

    def test_sin_footer(self):
        self.assertEqual(extraer_razon_social_de_html(FIXTURE_WEB_SIN_FOOTER),
                         "")

    def test_html_vacio(self):
        self.assertEqual(extraer_razon_social_de_html(""), "")


class TestRdap(unittest.TestCase):

    def test_parsea_solo_campos_no_personales(self):
        """P0.9: el nombre del titular (vcard fn del registrant) NO debe
        salir en el resultado."""
        r = parsear_rdap(FIXTURE_RDAP)
        self.assertEqual(r["registrador"], "nicar")
        self.assertEqual(r["creado"], "2013-11-13T00:00:00Z")
        self.assertEqual(r["expira"], "2027-11-13T00:00:00Z")
        self.assertNotIn("TITULAR PERSONAL", json.dumps(r, ensure_ascii=False))

    def test_sin_entidades(self):
        r = parsear_rdap({"events": [], "entities": []})
        self.assertEqual(r, {"registrador": "", "creado": "", "expira": ""})


if __name__ == "__main__":
    unittest.main()
