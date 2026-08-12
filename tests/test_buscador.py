"""Pruebas de buscador.py (piezas puras: parsers, normalizacion, ranking y
deteccion de bloqueos). Sin Playwright ni navegador.

Los fixtures de Bing y CuitOnline se derivan de HTML REAL capturado el
2026-08-12 (recortado, sin datos personales); Google y DDG usan fixtures
sinteticos de su estructura documentada porque esta IP no devuelve
resultados reales de esos motores (bloqueada, leccion 20 hallazgo 8)."""

import unittest

from buscador import (armar_sintesis, deduplicar, detectar_bloqueo,
                      detectar_recaptcha, extraer_resultados_bing,
                      extraer_resultados_cuitonline, extraer_resultados_ddg,
                      extraer_resultados_google, normalizar_url)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Derivado del HTML real de Bing capturado 2026-08-12 (estructura fiel:
# li.b_algo > h2 > a[href=ck/a?u=a1<base64>] + div.b_caption > p.b_lineclamp2).
FIXTURE_BING = """
<li class="b_algo" data-id="" iid="SERP.5346"><div class="b_tpcn"><a
 class="tilk" href="https://www.bing.com/ck/a?!&amp;&amp;p=7a99&amp;u=a1aHR0cHM6Ly93d3cud29yZHBsYXlzLmNvbS9jcm9zc3dvcmQtc29sdmVyL3BsYWNlLWhvbGRlci1xdWVyeS1mb3ItJTIycG9sbCUyMg&amp;ntb=1"></a></div>
 <h2 class=""><a target="_blank" href="https://www.bing.com/ck/a?!&amp;&amp;p=7a99c4480a9e38a4656b915bbe10a6974da63c4636d154d2215aa37588b2a90bJmltdHM9MTc4NjQ5MjgwMA&amp;ptn=3&amp;ver=2&amp;hsh=4&amp;fclid=1855be66-6337-6ba5-2d57-a9d5629c6a4c&amp;u=a1aHR0cHM6Ly93d3cud29yZHBsYXlzLmNvbS9jcm9zc3dvcmQtc29sdmVyL3BsYWNlLWhvbGRlci1xdWVyeS1mb3ItJTIycG9sbCUyMg&amp;ntb=1" h="ID=SERP,5123.2"><strong>place holder query for "poll</strong>" Crossword Clue - Wordplays.com</a></h2>
 <div class="b_caption"><p class="b_lineclamp2">Answers for place holder query for %22poll crossword clue, 5 letters. Search for crossword clues found in the Daily Celebrity, NY ...</p></div></li>
<li class="b_algo" data-id="" iid="SERP.5347">
 <h2 class=""><a target="_blank" href="https://www.bing.com/ck/a?!&amp;&amp;p=7ebddfc1&amp;u=a1aHR0cHM6Ly93d3cud29yZHBsYXlzLmNvbS9jcm9zc3dvcmQtc29sdmVyL3BsYWNlLWhvbGRlci1xdWVyeS1mb3ItcXVpdDtwb2xsLXF1aXQ&amp;ntb=1"><strong>place holder query</strong> for quit;<strong>poll</strong> quit Crossword Clue</a></h2>
 <div class="b_caption"><p class="b_lineclamp2">Answers for place holder query for quit;poll quit crossword clue, 8 letters.</p></div></li>
"""

FIXTURE_BING_URL1 = "https://www.wordplays.com/crossword-solver/place-holder-query-for-%22poll%22"
FIXTURE_BING_URL2 = "https://www.wordplays.com/crossword-solver/place-holder-query-for-quit;poll-quit"

# Estructura estandar de Google (div#search, enlace que envuelve h3, snippet
# en div.VwiC3b). NO verificada en vivo en esta IP (siempre pagina "sorry").
FIXTURE_GOOGLE = """
<div id="search">
  <div class="g"><div class="tF2Cxc">
    <a href="https://permanenciasalud.example/"><h3>Permanencia Salud Srl - Sitio</h3></a>
    <div class="VwiC3b">Empresa de servicios de salud en Belgrano.</div>
  </div></div>
  <div class="g"><div class="tF2Cxc">
    <a href="https://www.empresa2.com.ar/permanencia"><h3>Permanencia - Otro resultado</h3></a>
  </div></div>
</div>
"""

# Estructura de la version html de DuckDuckGo (div.result, a.result__a,
# a.result__snippet). NO verificada en vivo en esta IP (challenge propio).
FIXTURE_DDG = """
<div class="result results_links results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <a rel="nofollow" class="result__a" href="https://example.org/permanencia">Permanencia Salud - Resultado DDG</a>
    <a class="result__snippet" href="https://example.org/permanencia">Snippet de ejemplo de DuckDuckGo.</a>
  </div>
</div>
<div class="result results_links results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <a rel="nofollow" class="result__a" href="https://otro.example/x">Segundo resultado</a>
  </div>
</div>
"""

# Derivado del HTML real de CuitOnline (busqueda "ypf", 2026-08-12):
# div#searchResults > div.hit > a.denominacion[href=detalle/...] + span.cuit.
FIXTURE_CUITONLINE_OK = """
<div class="results">
  <div id="searchResults">
    <div class="hit">
      <div class="denominacion">
        <a href="detalle/20123456789/asoc-mutual-del-personal-ypf.html" title="Ver detalles de ASOC MUTUAL DEL PERSONAL YPF" class="denominacion">
          <h2 class="denominacion" style="margin-bottom:10px; font-size:16px;">ASOC MUTUAL DEL PERSONAL YPF</h2>
        </a>
      </div>
      <div class="doc-facets" style="font-size:14px;">
        <span class="linea-cuit-persona">
          <span class="bullet">•</span>&nbsp;CUIL:&nbsp;<span class="cuit">20-12345678-9</span>
        </span>
      </div>
    </div>
  </div>
</div>
"""

FIXTURE_CUITONLINE_VACIO = """
<div class="results">
  <div id="searchResults">
    <div class="paywalled-content">
      <p class="p_title title-no-results">Su búsqueda no obtuvo resultados, verifique las palabras ingresadas e inténtelo nuevamente.</p>
    </div>
  </div>
</div>
"""

# Pagina "sorry" de Google (recortada y ANONIMIZADA: sin IP ni hora).
FIXTURE_GOOGLE_CAPTCHA = """
<html><head><title>Google</title></head><body>
<iframe title="reCAPTCHA" width="304" height="78" name="a-abc"
 src="https://www.google.com/recaptcha/enterprise/anchor?ar=1&amp;k=6LdLLIMbAAAAA"
 frameborder="0"></iframe>
<p>Nuestros sistemas han detectado tráfico inusual procedente de tu red de computadoras.</p>
</body></html>
"""

FIXTURE_DDG_CHALLENGE = """
<html><body>
<p>Unfortunately, bots use DuckDuckGo too.</p>
<p>Please complete the following challenge to confirm this search was made by a human.</p>
<p>Select all squares containing a duck:</p>
</body></html>
"""

FIXTURE_BRAVE_CAPTCHA = """
<html><head><title>Captcha - Brave Search</title></head><body>
<p>Verificando que no eres un bot</p><p>Arrastra el control deslizante</p>
</body></html>
"""

FIXTURE_ECOSIA_CHALLENGE = """
<html><head><title>Un momento...</title></head><body>
<p>Confirm you're not a robot</p>
<p>Our system has detected unusual traffic from your network.</p>
</body></html>
"""

FIXTURE_STARTPAGE_BLOQUEADA = """
<html><body><title>Conexion suspendida</title>
<p>Conexión suspendida.</p>
<p>Nuestro sistema ha detectado un alto volumen de tráfico procedente de su conexión.</p>
</body></html>
"""

FIXTURE_MOJEEK_403 = """
<html><head><title>403 - Forbidden</title></head><body>Forbidden</body></html>
"""

FIXTURE_DATEAS_404 = """
<html><head><title>Página no encontrada | Dateas.com</title></head>
<body>La página solicitada no existe.</body></html>
"""


# ---------------------------------------------------------------------------
# Parser de Bing (verificado con HTML real)
# ---------------------------------------------------------------------------

class TestBing(unittest.TestCase):

    def test_extrae_dos_resultados(self):
        res = extraer_resultados_bing(FIXTURE_BING)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["posicion"], 1)
        self.assertEqual(res[1]["posicion"], 2)

    def test_titulo_limpio(self):
        res = extraer_resultados_bing(FIXTURE_BING)
        self.assertEqual(res[0]["titulo"],
                         'place holder query for "poll" Crossword Clue - Wordplays.com')

    def test_url_decodificada_del_redirector(self):
        """El href es bing.com/ck/a?u=a1<base64>: la URL real debe
        decodificarse (verificado contra el par base64->destino real)."""
        res = extraer_resultados_bing(FIXTURE_BING)
        self.assertEqual(normalizar_url(res[0]["url"]), FIXTURE_BING_URL1)
        self.assertEqual(normalizar_url(res[1]["url"]), FIXTURE_BING_URL2)

    def test_snippet(self):
        res = extraer_resultados_bing(FIXTURE_BING)
        self.assertTrue(res[0]["snippet"].startswith("Answers for place holder"))


# ---------------------------------------------------------------------------
# Parser de Google (estructura estandar, no verificado en vivo)
# ---------------------------------------------------------------------------

class TestGoogle(unittest.TestCase):

    def test_extrae_resultados(self):
        res = extraer_resultados_google(FIXTURE_GOOGLE)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["titulo"], "Permanencia Salud Srl - Sitio")
        self.assertEqual(res[0]["url"], "https://permanenciasalud.example/")

    def test_snippet_del_primer_resultado(self):
        res = extraer_resultados_google(FIXTURE_GOOGLE)
        self.assertIn("Empresa de servicios", res[0]["snippet"])


# ---------------------------------------------------------------------------
# Parser de DDG (estructura documentada, no verificado en vivo)
# ---------------------------------------------------------------------------

class TestDDG(unittest.TestCase):

    def test_extrae_resultados(self):
        res = extraer_resultados_ddg(FIXTURE_DDG)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["titulo"], "Permanencia Salud - Resultado DDG")
        self.assertEqual(res[0]["url"], "https://example.org/permanencia")
        self.assertIn("Snippet de ejemplo", res[0]["snippet"])


# ---------------------------------------------------------------------------
# Parser de CuitOnline (verificado con HTML real)
# ---------------------------------------------------------------------------

class TestCuitOnline(unittest.TestCase):

    def test_extrae_cuit_y_razon_social(self):
        res = extraer_resultados_cuitonline(FIXTURE_CUITONLINE_OK)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["titulo"], "ASOC MUTUAL DEL PERSONAL YPF")
        self.assertEqual(res[0]["cuit"], "20-12345678-9")
        self.assertEqual(res[0]["url"],
                         "https://www.cuitonline.com/detalle/20123456789/asoc-mutual-del-personal-ypf.html")

    def test_caso_vacio_no_extrae_nada(self):
        res = extraer_resultados_cuitonline(FIXTURE_CUITONLINE_VACIO)
        self.assertEqual(res, [])
        self.assertEqual(detectar_bloqueo("cuitonline", FIXTURE_CUITONLINE_VACIO),
                         "sin_resultados")


# ---------------------------------------------------------------------------
# Normalizacion de URLs
# ---------------------------------------------------------------------------

class TestNormalizarUrl(unittest.TestCase):

    def test_quita_fragmento_y_utm(self):
        url = ("https://ejemplo.com/a/?utm_source=x&utm_medium=y&b=2#seccion")
        self.assertEqual(normalizar_url(url), "https://ejemplo.com/a?b=2")

    def test_protocolo_relativo(self):
        self.assertEqual(normalizar_url("//ejemplo.com/x"),
                         "https://ejemplo.com/x")

    def test_quita_slash_final(self):
        self.assertEqual(normalizar_url("https://ejemplo.com/x/"),
                         "https://ejemplo.com/x")

    def test_vacia(self):
        self.assertEqual(normalizar_url(""), "")
        self.assertEqual(normalizar_url("  "), "")


# ---------------------------------------------------------------------------
# Deduplicacion y ranking
# ---------------------------------------------------------------------------

class TestDedupeYRanking(unittest.TestCase):

    def test_dedupe_por_url_normalizada(self):
        entradas = [
            {"motor": "bing", "posicion": 1, "titulo": "A",
             "url": "https://ejemplo.com/x?utm_source=1", "snippet": "s"},
            {"motor": "google", "posicion": 5, "titulo": "A2",
             "url": "https://ejemplo.com/x#seccion", "snippet": "s2"},
        ]
        dedup = deduplicar(entradas)
        self.assertEqual(len(dedup), 1)
        self.assertEqual(sorted(dedup[0]["fuentes"]), ["bing", "google"])
        self.assertEqual(dedup[0]["mejor_posicion"], 1)

    def test_sintesis_multi_fuente_gana(self):
        estados = [
            {"motor": "bing", "resultados": [
                {"posicion": 1, "titulo": "Sitio", "url": "https://a.com/x",
                 "snippet": ""},
                {"posicion": 2, "titulo": "Otro", "url": "https://b.com/y",
                 "snippet": ""},
            ]},
            {"motor": "google", "resultados": [
                {"posicion": 1, "titulo": "Sitio (dup)", "url": "https://a.com/x",
                 "snippet": ""},
            ]},
        ]
        top = armar_sintesis(estados)
        self.assertEqual(top[0]["url"], "https://a.com/x")
        self.assertEqual(sorted(top[0]["fuentes"]), ["bing", "google"])
        self.assertGreater(top[0]["score"], top[1]["score"])

    def test_bonus_dominio_cuitonline(self):
        estados = [
            {"motor": "bing", "resultados": [
                {"posicion": 1, "titulo": "X",
                 "url": "https://www.cuitonline.com/detalle/123/x.html",
                 "snippet": ""},
                {"posicion": 1, "titulo": "Y", "url": "https://otro.com/y",
                 "snippet": ""},
            ]},
        ]
        top = armar_sintesis(estados)
        self.assertEqual(top[0]["url"],
                         "https://www.cuitonline.com/detalle/123/x.html")


# ---------------------------------------------------------------------------
# Deteccion de bloqueos y captchas
# ---------------------------------------------------------------------------

class TestBloqueos(unittest.TestCase):

    def test_google_sorry_es_captcha_recaptcha(self):
        self.assertEqual(detectar_bloqueo("google", FIXTURE_GOOGLE_CAPTCHA),
                         "captcha")
        self.assertTrue(detectar_recaptcha(FIXTURE_GOOGLE_CAPTCHA))

    def test_ddg_challenge(self):
        self.assertEqual(detectar_bloqueo("ddg", FIXTURE_DDG_CHALLENGE),
                         "challenge_ddg")

    def test_brave_slider(self):
        self.assertEqual(detectar_bloqueo("brave", FIXTURE_BRAVE_CAPTCHA),
                         "captcha_slider")

    def test_ecosia_turnstile(self):
        self.assertEqual(detectar_bloqueo("ecosia", FIXTURE_ECOSIA_CHALLENGE),
                         "turnstile")

    def test_startpage_suspendida(self):
        self.assertEqual(detectar_bloqueo("startpage", FIXTURE_STARTPAGE_BLOQUEADA),
                         "suspendida")

    def test_mojeek_403(self):
        self.assertEqual(detectar_bloqueo("mojeek", FIXTURE_MOJEEK_403),
                         "http_403")

    def test_dateas_404(self):
        self.assertEqual(detectar_bloqueo("dateas", FIXTURE_DATEAS_404),
                         "pagina_no_encontrada")

    def test_html_limpio_no_detecta_bloqueo(self):
        self.assertEqual(detectar_bloqueo("bing", FIXTURE_BING), "")
        self.assertEqual(detectar_bloqueo("cuitonline", FIXTURE_CUITONLINE_OK), "")

    def test_google_sin_captcha_no_falso_positivo(self):
        self.assertFalse(detectar_recaptcha(FIXTURE_BING))


if __name__ == "__main__":
    unittest.main()
