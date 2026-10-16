"""Pruebas de academico.py (búsqueda académica arXiv + Crossref).

Sin red: las respuestas de prueba son recortes con la ESTRUCTURA REAL
verificada en vivo el 2026-10-16 — Atom de arXiv (``entry`` con ``id``,
``title``, ``summary`` multilínea, ``published``, ``author`` y ``arxiv:doi``
opcional) y JSON de Crossref con ``status`` en la RAÍZ y ``message.items``
con ``DOI``/``title``/``abstract`` JATS/``container-title``/``issued``.
"""

import contextlib
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

import buscador
from academico import (buscar_academico, buscar_arxiv, buscar_crossref,
                       parsear_arxiv, parsear_crossref)

XML_ARXIV = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns="http://www.w3.org/2005/Atom">
  <id>https://arxiv.org/api/ejemplo</id>
  <title>arXiv Query: search_query=ti:"chart understanding"</title>
  <opensearch:totalResults>41</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2412.19146v1</id>
    <title>AskChart: Universal Chart Understanding through Textual Enhancement</title>
    <updated>2024-12-26T09:59:43Z</updated>
    <published>2024-12-26T09:59:43Z</published>
    <summary>Chart understanding tasks such as ChartQA and Chart-to-Text involve
automatically extracting information from charts.</summary>
    <author>
      <name>Alice Example</name>
    </author>
    <author>
      <name>Bob Example</name>
    </author>
    <arxiv:doi>10.9999/ejemplo</arxiv:doi>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2001.00001v2</id>
    <title>Segundo trabajo</title>
    <published>2020-01-01T00:00:00Z</published>
    <summary>Resumen breve.</summary>
  </entry>
</feed>
"""

JSON_CROSSREF = json.dumps({
    "status": "ok",
    "message-type": "work-list",
    "message": {
        "total-results": 2,
        "items": [
            {
                "DOI": "10.5220/0011729300003411",
                "title": ["A Semantic Frame Graph for Information Extraction"],
                "container-title": ["Proceedings of Ejemplo"],
                "issued": {"date-parts": [[2023]]},
                "abstract": "<jats:p>We present a <jats:italic>frame</jats:italic> graph.</jats:p>",
            },
            {
                "DOI": "10.1234/sin-abstract",
                "title": ["Otro trabajo"],
                "container-title": ["Revista de Ejemplo"],
                "issued": {"date-parts": [[2019, 5]]},
            },
        ],
    },
}).encode("utf-8")


class TestParsearArxiv(unittest.TestCase):
    def test_campos_exactos_del_primer_resultado(self):
        resultados = parsear_arxiv(XML_ARXIV.encode("utf-8"))

        primero = resultados[0]
        self.assertEqual(primero["motor"], "arxiv")
        self.assertEqual(primero["posicion"], 1)
        self.assertEqual(primero["titulo"],
                         "AskChart: Universal Chart Understanding through "
                         "Textual Enhancement")
        self.assertEqual(primero["url"], "http://arxiv.org/abs/2412.19146v1")
        self.assertEqual(primero["anio"], "2024")
        self.assertEqual(primero["snippet"],
                         "Chart understanding tasks such as ChartQA and "
                         "Chart-to-Text involve automatically extracting "
                         "information from charts.")
        self.assertEqual(primero["autores"], ["Alice Example", "Bob Example"])
        self.assertEqual(primero["doi"], "10.9999/ejemplo")

    def test_sin_doi_ni_autores_no_revienta(self):
        segundo = parsear_arxiv(XML_ARXIV.encode("utf-8"))[1]

        self.assertEqual(segundo["posicion"], 2)
        self.assertEqual(segundo["anio"], "2020")
        self.assertEqual(segundo["doi"], "")
        self.assertEqual(segundo["autores"], [])

    def test_respeta_max_resultados(self):
        self.assertEqual(len(parsear_arxiv(XML_ARXIV.encode("utf-8"), 1)), 1)

    def test_xml_invalido_eleva_runtime_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            parsear_arxiv(b"<feed><entry>")
        self.assertIn("no es XML válido", str(ctx.exception))


class TestParsearCrossref(unittest.TestCase):
    def test_campos_exactos_y_abstract_limpio(self):
        resultados = parsear_crossref(JSON_CROSSREF)

        primero = resultados[0]
        self.assertEqual(primero["motor"], "crossref")
        self.assertEqual(primero["posicion"], 1)
        self.assertEqual(primero["titulo"],
                         "A Semantic Frame Graph for Information Extraction")
        self.assertEqual(primero["url"],
                         "https://doi.org/10.5220/0011729300003411")
        self.assertEqual(primero["anio"], "2023")
        self.assertEqual(primero["snippet"], "We present a frame graph.")
        self.assertEqual(primero["doi"], "10.5220/0011729300003411")

    def test_sin_abstract_usa_la_revista_como_snippet(self):
        segundo = parsear_crossref(JSON_CROSSREF)[1]

        self.assertEqual(segundo["snippet"], "Revista de Ejemplo")
        self.assertEqual(segundo["anio"], "2019")

    def test_status_distinto_de_ok_eleva_error(self):
        malo = json.dumps({"status": "failed", "message": {}}).encode("utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            parsear_crossref(malo)
        self.assertIn("status=", str(ctx.exception))

    def test_json_invalido_eleva_runtime_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            parsear_crossref(b"no es json")
        self.assertIn("no es JSON válido", str(ctx.exception))


class TestConsultas(unittest.TestCase):
    def test_consultas_vacias_elevan_value_error_sin_red(self):
        for funcion in (buscar_arxiv, buscar_crossref):
            with self.subTest(funcion=funcion.__name__):
                with self.assertRaises(ValueError):
                    funcion("   ")

    def test_fuente_desconocida_eleva_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            buscar_academico("tema", fuentes=("scholar",))
        self.assertIn("Fuente desconocida", str(ctx.exception))

    def test_buscar_arxiv_construye_la_url_correcta(self):
        capturado = {}

        def falso_pedir(url, timeout=30):
            capturado["url"] = url
            return XML_ARXIV.encode("utf-8")

        with patch("academico._pedir", side_effect=falso_pedir):
            resultados = buscar_arxiv("extracción de gráficos", max_resultados=3)

        self.assertIn("export.arxiv.org/api/query", capturado["url"])
        self.assertIn("max_results=3", capturado["url"])
        self.assertIn("sortBy=relevance", capturado["url"])
        self.assertIn("ti%3A%22", capturado["url"])  # ti:"..." codificado
        self.assertEqual(len(resultados), 2)

    def test_buscar_crossref_construye_la_url_correcta(self):
        capturado = {}

        def falso_pedir(url, timeout=30):
            capturado["url"] = url
            return JSON_CROSSREF

        with patch("academico._pedir", side_effect=falso_pedir):
            resultados = buscar_crossref("grafos y extracción", max_resultados=2)

        self.assertIn("api.crossref.org/works", capturado["url"])
        self.assertIn("query.bibliographic=", capturado["url"])
        self.assertIn("rows=2", capturado["url"])
        self.assertEqual(len(resultados), 2)

    def test_buscar_academico_concatena_las_fuentes(self):
        def falso_pedir(url, timeout=30):
            return XML_ARXIV.encode("utf-8") if "arxiv" in url else JSON_CROSSREF

        with patch("academico._pedir", side_effect=falso_pedir):
            resultados = buscar_academico("tema", max_resultados=1)

        self.assertEqual([r["motor"] for r in resultados], ["arxiv", "crossref"])

    def test_error_de_red_eleva_runtime_error_con_contexto(self):
        with patch("academico.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("sin ruta al host")):
            with self.assertRaises(RuntimeError) as ctx:
                buscar_arxiv("tema")

        self.assertIn("Fallo consultando", str(ctx.exception))
        self.assertIn("URLError", str(ctx.exception))


class TestCompatibilidadConBuscador(unittest.TestCase):
    """Los resultados académicos deben encajar en el motor de buscador.py."""

    def test_deduplicar_fusiona_arxiv_y_crossref(self):
        arxiv = parsear_arxiv(XML_ARXIV.encode("utf-8"))
        crossref = parsear_crossref(JSON_CROSSREF)

        fusionados = buscador.deduplicar(arxiv + crossref)

        urls = {r["url"] for r in fusionados}
        self.assertIn("http://arxiv.org/abs/2412.19146v1", urls)
        self.assertIn("https://doi.org/10.5220/0011729300003411", urls)
        self.assertEqual(len(fusionados), 4)

    def test_misma_entrada_repetida_se_fusiona_en_una(self):
        doble = parsear_arxiv(XML_ARXIV.encode("utf-8")) * 2

        fusionados = buscador.deduplicar(doble)

        self.assertEqual(len(fusionados), 2)
        self.assertEqual(fusionados[0]["fuentes"], ["arxiv"])


class TestCli(unittest.TestCase):
    def _ejecutar(self, argv, resultados=None, error=None):
        buffer = io.StringIO()
        parche = (patch("academico.buscar_academico", side_effect=error)
                  if error else
                  patch("academico.buscar_academico", return_value=resultados or []))
        with parche, contextlib.redirect_stdout(buffer):
            with contextlib.redirect_stderr(io.StringIO()):
                codigo = main_con_argv(argv)
        return codigo, buffer.getvalue()

    def test_salida_json(self):
        resultados = parsear_arxiv(XML_ARXIV.encode("utf-8"))

        codigo, salida = self._ejecutar(["tema", "--json"], resultados=resultados)

        self.assertEqual(codigo, 0)
        datos = json.loads(salida)
        self.assertEqual(len(datos), 2)
        self.assertEqual(datos[0]["motor"], "arxiv")

    def test_salida_de_consola(self):
        resultados = parsear_crossref(JSON_CROSSREF)

        codigo, salida = self._ejecutar(["tema", "--fuente", "crossref"],
                                        resultados=resultados)

        self.assertEqual(codigo, 0)
        self.assertIn("[crossref]", salida)
        self.assertIn("https://doi.org/10.5220/0011729300003411", salida)

    def test_sin_resultados_avisa(self):
        codigo, salida = self._ejecutar(["tema"], resultados=[])

        self.assertEqual(codigo, 0)
        self.assertIn("Sin resultados.", salida)

    def test_error_del_proveedor_devuelve_1(self):
        codigo, _ = self._ejecutar(["tema"], error=RuntimeError("caído"))

        self.assertEqual(codigo, 1)


def main_con_argv(argv):
    """Ejecuta `academico.main()` con argumentos fijos (sin tocar sys.argv)."""
    with patch("sys.argv", ["academico.py"] + argv):
        return __import__("academico").main()


if __name__ == "__main__":
    unittest.main()
