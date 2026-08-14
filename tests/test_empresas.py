"""Pruebas de empresas.py (piezas puras: variantes de nombre, extraccion de
CUIT/razon social, parseo RDAP y sintesis del informe). Sin Playwright ni red.

Fixtures de CuitOnline derivados de HTML REAL capturado el 2026-08-12
(recortado, sin datos personales)."""

import json
import unittest

from empresas import (_capturas_html, _parsear_cdx, consultar_cdx,
                      dominios_candidatos, extraer_cuits_de_html,
                      extraer_emails_de_html, extraer_razon_social_de_html,
                      extraer_redes_de_html, extraer_whatsapp_de_html,
                      limpiar_sufijo_legal, parsear_rdap,
                      variantes_de_nombre)

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
        self.assertEqual(r, {"registrador": "", "creado": "", "expira": "",
                             "titular_tipo": "", "titular_org": ""})


class TestRdapTitular(unittest.TestCase):

    def test_titular_juridica_con_org(self):
        """P0.9: se expone el nombre de la ORGANIZACION (dato comercial),
        nunca contactos personales."""
        r = parsear_rdap({
            "entities": [
                {"roles": ["registrant"],
                 "vcardArray": ["vcard", [["fn", {}, "text",
                                            "NOMBRE PERSONA"]]]},
                {"roles": ["registrant"],
                 "vcardArray": ["vcard", [["org", {}, "text",
                                            "EMPRESA SRL"],
                                           ["email", {}, "text",
                                            "titular@personal.com"],
                                           ["tel", {}, "text",
                                            "+54 11 5555 5555"]]]},
            ]})
        self.assertEqual(r["titular_tipo"], "persona_juridica")
        self.assertEqual(r["titular_org"], "EMPRESA SRL")
        self.assertNotIn("NOMBRE PERSONA", json.dumps(r, ensure_ascii=False))
        self.assertNotIn("titular@personal.com",
                         json.dumps(r, ensure_ascii=False))

    def test_titular_no_publicado_handle_nic_ar(self):
        """Verificado en vivo 2026-08-12 con asistenciadelsol.com.ar:
        el registrant solo tiene handle numerico sin vcard."""
        r = parsear_rdap({
            "entities": [
                {"roles": ["registrant"], "handle": "27123456789"},
                {"roles": ["registrar"], "handle": "nicar"},
            ]})
        self.assertEqual(r["titular_tipo"], "no_publicado")
        self.assertEqual(r["registrador"], "nicar")

    def test_fn_sin_org_no_expone_nombre(self):
        r = parsear_rdap({"entities": [
            {"roles": ["registrant"],
             "vcardArray": ["vcard", [["fn", {}, "text", "JUAN PEREZ"]]]}]})
        self.assertEqual(r["titular_tipo"], "publicado_sin_org")
        self.assertNotIn("JUAN PEREZ", json.dumps(r, ensure_ascii=False))


class TestDominiosCandidatos(unittest.TestCase):

    def test_del_nombre_comercial(self):
        self.assertEqual(
            dominios_candidatos("Asistencia del Sol"),
            ["asistenciadelsol.com.ar", "asistenciadelsol.com"])

    def test_limpia_sufijo_legal_y_acentos(self):
        self.assertEqual(
            dominios_candidatos("Ferretería El Martillo SRL"),
            ["ferreteriaelmartillo.com.ar", "ferreteriaelmartillo.com"])

    def test_nombre_corto_no_genera_candidatos(self):
        self.assertEqual(dominios_candidatos("SA"), [])


# ---------------------------------------------------------------------------
# Fixtures de correos/canales de contacto (patrones reales de pymes: la
# landing de ejemplo asistenciadelsol.com.ar publica WhatsApp y redes, sin
# correo; la forma ofuscada ' [at] [dot] ' es el estandar anti-bots).
# ---------------------------------------------------------------------------

FIXTURE_EMAILS = """
<html><body>
  <a href="mailto:info@asistenciadelsol.com.ar?subject=Consulta">Escribinos</a>
  <p>Contacto general: contacto@asistenciadelsol.com.ar</p>
  <p>Forma ofuscada: escribinos a info [at] dominio [dot] com</p>
  <script>var fake = "noreply@falso.com"; var tpl = "user@plantilla.interna";</script>
  <style>.x{content:"estilo@falso.org"}</style>
</body></html>
"""

FIXTURE_SIN_EMAILS = "<html><body><p>Solo telefono y formulario</p></body></html>"

FIXTURE_WHATSAPP = """
<a href="https://wa.me/5491171212222">WhatsApp</a>
<a href="https://whatsapp.com/send?phone=5491122223333">Chat</a>
<img src="whatsapp-image-2025-10-07.webp"> <!-- no es un numero de contacto -->
"""

FIXTURE_REDES = """
<a href="https://www.instagram.com/asistenciadelsol/?utm_source=web">IG</a>
<a href="https://www.facebook.com/profile.php?id=61579661421315&amp;ref=x">FB</a>
<a href="https://ar.linkedin.com/company/asistencia-del-sol">LI</a>
<a href="https://x.com/asistenciadelsol">X</a>
<img src="https://www.facebook.com/tr?id=123&amp;ev=PageView"> <!-- pixel, no perfil -->
<a href="https://www.google.com/search?q=otra">fuera de alcance</a>
"""


class TestEmails(unittest.TestCase):

    def test_mailto_texto_plano_y_ofuscado(self):
        e = extraer_emails_de_html(FIXTURE_EMAILS)
        self.assertEqual(
            e,
            {"info@asistenciadelsol.com.ar",
             "contacto@asistenciadelsol.com.ar",
             "info@dominio.com"})

    def test_scripts_y_styles_excluidos(self):
        """P0.2: los correos de plantillas/analytics dentro de <script>/
        <style> NO son contacto publicado por la organizacion."""
        self.assertNotIn("noreply@falso.com",
                         extraer_emails_de_html(FIXTURE_EMAILS))
        self.assertNotIn("user@plantilla.interna",
                         extraer_emails_de_html(FIXTURE_EMAILS))
        self.assertNotIn("estilo@falso.org",
                         extraer_emails_de_html(FIXTURE_EMAILS))

    def test_sin_emails(self):
        self.assertEqual(extraer_emails_de_html(FIXTURE_SIN_EMAILS), set())

    def test_html_vacio(self):
        self.assertEqual(extraer_emails_de_html(""), set())
        self.assertEqual(extraer_emails_de_html(None), set())

    def test_minusculas_y_sin_duplicados(self):
        html = '<a href="mailto:INFO@Ejemplo.COM">A</a>' \
               "<p>info@ejemplo.com</p>"
        self.assertEqual(extraer_emails_de_html(html), {"info@ejemplo.com"})

    def test_ofuscado_arroba_y_punto_en_parentesis(self):
        html = "<p>info (arroba) dominio (punto) com (punto) ar</p>"
        self.assertEqual(extraer_emails_de_html(html),
                         {"info@dominio.com.ar"})


class TestWhatsapp(unittest.TestCase):

    def test_wa_me_y_send(self):
        self.assertEqual(extraer_whatsapp_de_html(FIXTURE_WHATSAPP),
                         {"5491171212222", "5491122223333"})

    def test_imagenes_no_son_numeros(self):
        self.assertNotIn("2025", extraer_whatsapp_de_html(FIXTURE_WHATSAPP))

    def test_html_vacio(self):
        self.assertEqual(extraer_whatsapp_de_html(""), set())


class TestRedes(unittest.TestCase):

    def test_perfiles_limpios_sin_query(self):
        r = extraer_redes_de_html(FIXTURE_REDES)
        self.assertEqual(
            r,
            ["https://ar.linkedin.com/company/asistencia-del-sol",
             "https://facebook.com/profile.php?id=61579661421315",
             "https://instagram.com/asistenciadelsol",
             "https://x.com/asistenciadelsol"])

    def test_html_vacio(self):
        self.assertEqual(extraer_redes_de_html(""), [])


class TestBusquedasContacto(unittest.TestCase):

    def test_con_dominio_primero_el_dork_at(self):
        from empresas import _armar_busquedas_contacto
        c = _armar_busquedas_contacto("Asistencia del Sol",
                                      "asistenciadelsol.com.ar")
        self.assertEqual(c[0], '"@asistenciadelsol.com.ar"')
        self.assertIn('"Asistencia del Sol" email OR correo OR contacto', c)

    def test_sin_dominio_solo_nombre(self):
        from empresas import _armar_busquedas_contacto
        c = _armar_busquedas_contacto("Asistencia del Sol")
        self.assertEqual(len(c), 1)


class TestSintesisEmails(unittest.TestCase):

    def setUp(self):
        from empresas import _sintetizar
        self._sintetizar = _sintetizar

    def informe(self, **kwargs):
        base = {
            "cuitonline": [],
            "sitio_oficial": None,
            "rdap": None,
            "web_general": [],
            "correos_web": [],
        }
        base.update(kwargs)
        return base

    def test_emails_del_sitio_y_web(self):
        informe = self.informe(
            sitio_oficial={
                "ok": True, "cuits": [], "razon_social": "",
                "emails": ["info@empresa.com.ar"],
                "whatsapp": ["5491111111111"],
                "redes": ["https://instagram.com/empresa"],
            },
            correos_web=[{
                "consulta": '"@empresa.com.ar"',
                "emails": ["info@empresa.com.ar", "ventas@empresa.com.ar"],
            }])
        sin = self._sintetizar(informe)
        self.assertEqual(
            {e["email"] for e in sin["emails"]},
            {"info@empresa.com.ar", "ventas@empresa.com.ar"})
        self.assertIn("correo publicado en la web oficial",
                      sin["canales_contacto"])
        self.assertIn("WhatsApp 5491111111111", sin["canales_contacto"])
        self.assertIn("red social https://instagram.com/empresa",
                      sin["canales_contacto"])

    def test_sin_emails_ni_canales(self):
        sin = self._sintetizar(self.informe())
        self.assertEqual(sin["emails"], [])
        self.assertEqual(sin["canales_contacto"], [])
        self.assertTrue(any("correo publicado" in lim
                            for lim in sin["limitaciones"]))

    def test_rns_indexada_sin_resultados_agrega_limitacion(self):
        sin = self._sintetizar(self.informe(
            rns={"base": "rns.db", "indexada": True, "resultados": [],
                 "error": ""}))
        self.assertIn("NO consta en el Registro Nacional de Sociedades",
                      sin["limitaciones"][0])
        self.assertTrue(any("NO consta en el RNS" in s
                            for s in sin["senales_actividad"]))

    def test_rns_con_resultados_aporta_cuit_y_senal(self):
        sin = self._sintetizar(self.informe(
            rns={"base": "rns.db", "indexada": True,
                 "resultados": [{
                     "cuit": "30-12345678-9",
                     "razon_social": "PERMANENCIA SALUD S.R.L.",
                     "tipo_societario": "S.R.L.", "fecha_contrato": "2018-03-15",
                     "dom_provincia": "BUENOS AIRES",
                     "dom_localidad": "QUILMES", "origen": "sociedades",
                     "coincidencia": 2}],
                 "error": ""}))
        self.assertIn({"cuit": "30-12345678-9",
                       "razon_social": "PERMANENCIA SALUD S.R.L.",
                       "fuente": "RNS"}, sin["cuits"])
        self.assertTrue(any("registrada en el RNS" in s
                            for s in sin["senales_actividad"]))

    def test_rns_no_indexada_avisa_como_crearla(self):
        sin = self._sintetizar(self.informe(
            rns={"base": "rns.db", "indexada": False, "resultados": [],
                 "error": ""}))
        self.assertIn("python3 rns.py descargar", sin["limitaciones"][0])


# Respuesta REAL de la CDX API de web.archive.org para
# asistenciamisabuelos.com (capturada en vivo 2026-08-14, recortada):
# formato JSON con cabecera en la primera fila.
FIXTURE_CDX = [
    ["timestamp", "original", "statuscode", "mimetype", "length"],
    ["20150505002220", "http://www.asistenciamisabuelos.com:80/", "200",
     "text/html", "3008"],
    ["20160515135437", "http://asistenciamisabuelos.com/a1.jpg", "200",
     "image/jpeg", "14836"],
    ["20150505141501", "http://www.asistenciamisabuelos.com:80/actualidad.html",
     "200", "text/html", "1917"],
    ["20150505142831", "http://www.asistenciamisabuelos.com:80/contacto.html",
     "200", "text/html", "2015"],
    ["20160121131742", "http://asistenciamisabuelos.com/asi.jpg", "200",
     "image/jpeg", "5075"],
]


class TestWayback(unittest.TestCase):

    def test_parsear_cdx(self):
        filas = _parsear_cdx(FIXTURE_CDX)
        self.assertEqual(len(filas), 5)
        self.assertEqual(filas[0]["timestamp"], "20150505002220")
        self.assertEqual(filas[0]["original"],
                         "http://www.asistenciamisabuelos.com:80/")
        self.assertEqual(filas[0]["statuscode"], "200")

    def test_parsear_cdx_vacio_o_malformado(self):
        self.assertEqual(_parsear_cdx([]), [])
        self.assertEqual(_parsear_cdx([["a", "b"]]), [])
        self.assertEqual(_parsear_cdx(None), [])

    def test_capturas_html_prioriza_home_y_contacto(self):
        cdx = {"ok": True, "capturas": _parsear_cdx(FIXTURE_CDX)}
        caps = _capturas_html(cdx, max_n=10)
        # solo HTML (sin imagenes), con home y contacto primero
        self.assertTrue(all("html" in c["mimetype"] for c in caps))
        self.assertEqual(len(caps), 3)
        import urllib.parse as _up
        paths = [_up.urlsplit(c["original"]).path.rstrip("/") or "/"
                 for c in caps]
        self.assertIn("/", paths)
        self.assertTrue(any("contacto.html" in p for p in paths))
        # la home primero (prioridad 0), el contacto antes que otras paginas
        self.assertEqual(paths[0], "/")

    def test_capturas_html_limite_y_prioridad(self):
        cdx = {"ok": True, "capturas": _parsear_cdx(FIXTURE_CDX)}
        caps = _capturas_html(cdx, max_n=1)
        self.assertEqual(len(caps), 1)
        self.assertTrue(caps[0]["original"].endswith("/") or
                        "contacto" in caps[0]["original"])

    def test_sintesis_wayback_senal_y_cuits_historicos(self):
        from empresas import _sintetizar
        informe = {
            "cuitonline": [], "sitio_oficial": None, "rdap": None,
            "web_general": [], "correos_web": [],
            "wayback": {"ok": True, "n": 5, "primera": "20150505002220",
                        "ultima": "20160515135437"},
            "wayback_capturas": [{
                "timestamp": "20150505002220", "fecha": "2015-05-05",
                "url": "http://www.asistenciamisabuelos.com:80/",
                "cuits": ["30-12345678-9"],
                "razon_social": "Asistencia Mis Abuelos S.R.L.",
                "emails": []}],
        }
        sin = _sintetizar(informe)
        self.assertIn({"cuit": "30-12345678-9", "razon_social": "",
                       "fuente": "wayback (2015-05-05)"}, sin["cuits"])
        self.assertTrue(any("historial en Wayback" in s
                            for s in sin["senales_actividad"]))


if __name__ == "__main__":
    unittest.main()
