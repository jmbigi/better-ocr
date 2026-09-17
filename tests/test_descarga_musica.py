"""Pruebas de descarga_musica.py: parseo de metadata de Archive.org,
construccion de URLs, filtrado de archivos MP3 y demo dry-run.
Sin red: la metadata de prueba es real de Archive.org (verificada
2026-09-05) pero se simula la respuesta HTTP."""

import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch, MagicMock

from descarga_musica import (
    archivos_mp3, construir_url_descarga, descargar_archivo,
    descargar_lista, descargar_partitura, demo, buscar_archive,
    listar_fuentes, PIEZAS_DEMO, DIR_MUSICA,
    parsear_pagina_mutopia, buscar_catalogo_mutopia, descargar_bytes,
    descargar_zip_pdfs, descargar_catalogo_mutopia, ESTILOS_MUTOPIA,
    SLUG_ESTILO, _nombre_archivo,
)

# Metadata real de Archive.org (Bach Goldberg Variations), verificada
# 2026-09-05. Solo incluimos los campos que usamos.
METADATA_GOLDBERG = {
    "server": "ia600804.us.archive.org",
    "dir": "/16/items/BachGoldbergVariations",
    "files": [
        {"name": "GoldbergVariations.mp3", "format": "VBR MP3",
         "size": "116260465", "length": "2906.51"},
        {"name": "GoldbergVariations.ogg", "format": "Ogg Vorbis",
         "size": "28612799", "length": "2906.47"},
        {"name": "__ia_thumb.jpg", "format": "JPEG", "size": "10530"},
    ],
    "metadata": {
        "title": "Bach: Goldberg Variations",
        "creator": "J.S.Bach",
        "licenseurl": "http://creativecommons.org/licenses/publicdomain/",
    },
}

# Metadata con multiples MP3s (Bach Partita No. 3), verificada 2026-09-05.
METADATA_PARTITA = {
    "server": "ia600702.us.archive.org",
    "dir": "/29/items/jaime-laredo-04-violin-partita-no.-3-in-e-major-bwv-1006-iv.-menuet-i-ii",
    "files": [
        {"name": "Jaime Laredo — 01 Violin Partita No. 3...Preludio.mp3",
         "format": "VBR MP3", "size": "8398933", "length": "209.95"},
        {"name": "Jaime Laredo — 02 Violin Partita No. 3...Loure.mp3",
         "format": "VBR MP3", "size": "8063521", "length": "201.56"},
        {"name": "Jaime Laredo — 03 Violin Partita No. 3...Gavotte.mp3",
         "format": "VBR MP3", "size": "8084419", "length": "202.08"},
        {"name": "Jaime Laredo — 04 Violin Partita No. 3...Menuet.mp3",
         "format": "VBR MP3", "size": "11201350", "length": "280.01"},
        {"name": "Jaime Laredo — 05 Violin Partita No. 3...Bourree.mp3",
         "format": "VBR MP3", "size": "4414738", "length": "110.34"},
        {"name": "Jaime Laredo — 06 Violin Partita No. 3...Gigue.mp3",
         "format": "VBR MP3", "size": "5055260", "length": "126.35"},
    ],
    "metadata": {"title": "Bach Violin Partita No. 3 BWV 1006"},
}


class TestArchivosMp3(unittest.TestCase):

    def test_un_solo_mp3(self):
        mp3s = archivos_mp3(METADATA_GOLDBERG)
        self.assertEqual(len(mp3s), 1)
        self.assertEqual(mp3s[0]["nombre"], "GoldbergVariations.mp3")
        self.assertEqual(mp3s[0]["size"], 116260465)
        self.assertIn("archive.org", mp3s[0]["url_descarga"])

    def test_multiples_mp3(self):
        mp3s = archivos_mp3(METADATA_PARTITA)
        self.assertEqual(len(mp3s), 6)
        for m in mp3s:
            self.assertTrue(m["nombre"].endswith(".mp3"))

    def test_excluye_no_audio(self):
        mp3s = archivos_mp3(METADATA_GOLDBERG)
        nombres = [m["nombre"] for m in mp3s]
        self.assertNotIn("__ia_thumb.jpg", nombres)

    def test_metadata_vacia(self):
        self.assertEqual(archivos_mp3({}), [])
        self.assertEqual(archivos_mp3(None), [])

    def test_formato_alternativo_mp3(self):
        metadata = {
            "server": "ia900000.us.archive.org",
            "dir": "/1/items/test",
            "files": [{"name": "track.mp3", "format": "MP3",
                       "size": "1000000", "length": "60"}],
        }
        mp3s = archivos_mp3(metadata)
        self.assertEqual(len(mp3s), 1)


class TestConstruirUrl(unittest.TestCase):

    def test_primer_mp3(self):
        url, nombre = construir_url_descarga(METADATA_GOLDBERG, 0)
        self.assertIsNotNone(url)
        self.assertIn("GoldbergVariations.mp3", url)
        self.assertEqual(nombre, "GoldbergVariations.mp3")

    def test_indice_fuera_de_rango(self):
        url, nombre = construir_url_descarga(METADATA_GOLDBERG, 999)
        # Debe usar el primer MP3 disponible
        self.assertIsNotNone(url)
        self.assertIn("GoldbergVariations.mp3", url)

    def test_indice_especifico(self):
        url, nombre = construir_url_descarga(METADATA_PARTITA, 2)
        self.assertIsNotNone(url)
        self.assertIn("Gavotte", nombre)

    def test_sin_mp3s(self):
        metadata = {
            "server": "ia900000.us.archive.org",
            "dir": "/1/items/test",
            "files": [{"name": "cover.jpg", "format": "JPEG"}],
        }
        url, nombre = construir_url_descarga(metadata, 0)
        self.assertIsNone(url)
        self.assertIsNone(nombre)


class TestDescargarArchivo(unittest.TestCase):

    @patch("descarga_musica.urllib.request.urlopen")
    def test_descarga_exitosa(self, mock_urlopen):
        contenido = b"datos de prueba"
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.headers = {"Content-Length": str(len(contenido))}
        mock_resp.read.side_effect = [contenido, b""]
        mock_urlopen.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmpdir:
            destino = os.path.join(tmpdir, "test.mp3")
            resultado = descargar_archivo(
                "https://example.com/test.mp3", destino)
            self.assertTrue(resultado)
            self.assertTrue(os.path.exists(destino))
            with open(destino, "rb") as f:
                self.assertEqual(f.read(), contenido)

    @patch("descarga_musica.urllib.request.urlopen")
    def test_archivo_demasiado_grande(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        # 600 MB > MAX_TAMANIO_MB (500)
        mock_resp.headers = {"Content-Length": str(600 * 1024 * 1024)}
        mock_urlopen.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmpdir:
            destino = os.path.join(tmpdir, "test.mp3")
            resultado = descargar_archivo(
                "https://example.com/big.mp3", destino)
            self.assertFalse(resultado)

    def test_url_none(self):
        resultado = descargar_archivo(None, "/tmp/test.mp3")
        self.assertFalse(resultado)


class TestDescargarLista(unittest.TestCase):

    @patch("descarga_musica.obtener_metadata")
    @patch("descarga_musica.descargar_archivo")
    def test_dry_run(self, mock_descargar, mock_metadata):
        mock_metadata.return_value = METADATA_GOLDBERG
        items = [{"titulo": "Test", "identifier": "BachGoldbergVariations"}]
        exitosas, fallidas = descargar_lista(items, dry_run=True)
        self.assertEqual(exitosas, 1)
        self.assertEqual(fallidas, 0)
        mock_descargar.assert_not_called()

    @patch("descarga_musica.obtener_metadata")
    def test_metadata_fallida(self, mock_metadata):
        mock_metadata.return_value = {}
        items = [{"titulo": "Test", "identifier": "noexiste"}]
        exitosas, fallidas = descargar_lista(items, dry_run=True)
        self.assertEqual(exitosas, 0)
        self.assertEqual(fallidas, 1)


class TestDemo(unittest.TestCase):

    @patch("descarga_musica.descargar_lista")
    def test_demo_dry_run(self, mock_lista):
        mock_lista.return_value = (10, 0)
        exitosas, fallidas = demo(dry_run=True)
        self.assertEqual(exitosas, 10)
        self.assertEqual(fallidas, 0)
        # Verificar que se pasan las 10 piezas demo
        args = mock_lista.call_args
        self.assertEqual(len(args[0][0]), 10)

    def test_piezas_demo_cantidad(self):
        self.assertEqual(len(PIEZAS_DEMO), 10)

    def test_piezas_demo_campos(self):
        for p in PIEZAS_DEMO:
            self.assertIn("titulo", p)
            self.assertIn("identifier", p)
            self.assertIn("licencia", p)


class TestListarFuentes(unittest.TestCase):

    def test_listar_fuentes(self):
        # Solo verificar que no lanza excepciones
        listar_fuentes()


class TestBuscarArchive(unittest.TestCase):

    @patch("descarga_musica.urllib.request.urlopen")
    def test_respuesta_valida(self, mock_urlopen):
        respuesta = {
            "response": {
                "docs": [
                    {"identifier": "test1", "title": "Bach Test",
                     "creator": "Bach"},
                ]
            }
        }
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps(respuesta).encode()
        mock_urlopen.return_value = mock_resp

        from descarga_musica import buscar_archive
        resultados = buscar_archive("Bach")
        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0]["identifier"], "test1")


class TestPartituras(unittest.TestCase):

    def test_piezas_con_partitura(self):
        con_pdf = [p for p in PIEZAS_DEMO if "partitura" in p]
        self.assertEqual(len(con_pdf), 3)
        nombres = [p["titulo"] for p in con_pdf]
        self.assertIn("Goldberg Variations BWV 988 (completas)", nombres)
        self.assertIn("Piano Sonata No. 14 'Claro de Luna'", nombres)
        self.assertIn("Clair de Lune", nombres)

    def test_piezas_sin_partitura(self):
        sin_pdf = [p for p in PIEZAS_DEMO if "partitura" not in p]
        self.assertEqual(len(sin_pdf), 7)

    @patch("descarga_musica.descargar_archivo")
    def test_descargar_partitura_ok(self, mock_descargar):
        mock_descargar.return_value = True
        ok = descargar_partitura(
            "BeethovenLv/O27/moonlight/moonlight-a4.pdf",
            "/tmp/moonlight.pdf")
        self.assertTrue(ok)
        mock_descargar.assert_called_once_with(
            "https://www.mutopiaproject.org/ftp/"
            "BeethovenLv/O27/moonlight/moonlight-a4.pdf",
            "/tmp/moonlight.pdf")

    def test_descargar_partitura_none(self):
        ok = descargar_partitura(None, "/tmp/test.pdf")
        self.assertFalse(ok)

    @patch("descarga_musica.descargar_archivo")
    def test_descargar_partitura_vacia(self, mock_descargar):
        ok = descargar_partitura("", "/tmp/test.pdf")
        self.assertFalse(ok)
        mock_descargar.assert_not_called()

    @patch("descarga_musica.descargar_archivo")
    @patch("descarga_musica.descargar_partitura")
    @patch("descarga_musica.obtener_metadata")
    def test_descarga_con_partitura(self, mock_metadata, mock_partitura,
                                   mock_archivo):
        mock_metadata.return_value = METADATA_GOLDBERG
        mock_archivo.return_value = True
        mock_partitura.return_value = True
        items = [{
            "titulo": "Test",
            "identifier": "BachGoldbergVariations",
            "partitura": "BachJS/BWV988/bwv-988-aria/bwv-988-aria-a4.pdf",
        }]
        with tempfile.TemporaryDirectory() as tmpdir:
            exitosas, fallidas = descargar_lista(
                items, tmpdir, con_partituras=True)
            self.assertEqual(exitosas, 1)
            mock_partitura.assert_called_once()

    @patch("descarga_musica.descargar_archivo")
    @patch("descarga_musica.descargar_partitura")
    @patch("descarga_musica.obtener_metadata")
    def test_descarga_sin_partituras(self, mock_metadata, mock_partitura,
                                    mock_archivo):
        mock_metadata.return_value = METADATA_GOLDBERG
        mock_archivo.return_value = True
        items = [{
            "titulo": "Test",
            "identifier": "BachGoldbergVariations",
            "partitura": "BachJS/BWV988/bwv-988-aria/bwv-988-aria-a4.pdf",
        }]
        with tempfile.TemporaryDirectory() as tmpdir:
            exitosas, fallidas = descargar_lista(
                items, tmpdir, con_partituras=False)
            self.assertEqual(exitosas, 1)
            mock_partitura.assert_not_called()

    @patch("descarga_musica.descargar_lista")
    def test_demo_sin_partituras(self, mock_lista):
        mock_lista.return_value = (10, 0)
        demo(dry_run=True, con_partituras=False)
        args = mock_lista.call_args
        # con_partituras es el 4to argumento posicional
        self.assertFalse(args[0][3])


# HTML real de Mutopia (bloque de "Possum And Taters", PDF unico),
# recortado de make-table.cgi?Style=Jazz el 2026-09-17.
BLOQUE_POSSUM = """
<table class="table-bordered result-table">
<tr><td>Possum And Taters</td>
<td>by C. Hunter (1876–1906)</td>
<td>&nbsp;</td>
<td>&nbsp;</td>
</tr><tr>
<td>for Piano</td>
<td>1900</td>
<td>Jazz</td>
<td></td>
</tr><tr>
<td>Henry A. French, April 20, 1900</td>
<td><a href="../legal.html#publicdomain">Public Domain</a></td>
<td><a href="piece-info.cgi?id=2012">More Information</a></td>
<td>2015/08/13</td>
</tr><tr>
<td>Download: <a href="https://www.mutopiaproject.org/ftp/HunterC/PossumAndTaters/PossumAndTaters.ly">.ly file</a></td>
<td><a href="https://www.mutopiaproject.org/ftp/HunterC/PossumAndTaters/PossumAndTaters.mid">.mid file</a></td>
<td><a href="https://www.mutopiaproject.org/ftp/HunterC/PossumAndTaters/PossumAndTaters-preview.svg">Preview image</a></td>
<td><a href="https://www.mutopiaproject.org/ftp/HunterC/PossumAndTaters/">Appropriate FTP area</a></td>
</tr><tr>
<td><a href="https://www.mutopiaproject.org/ftp/HunterC/PossumAndTaters/PossumAndTaters-a4.ps.gz">A4 .ps file (gzipped)</a></td>
<td><a href="https://www.mutopiaproject.org/ftp/HunterC/PossumAndTaters/PossumAndTaters-a4.pdf">A4 .pdf file</a></td>
<td><a href="https://www.mutopiaproject.org/ftp/HunterC/PossumAndTaters/PossumAndTaters-let.ps.gz">Letter .ps file (gzipped)</a></td>
<td><a href="https://www.mutopiaproject.org/ftp/HunterC/PossumAndTaters/PossumAndTaters-let.pdf">Letter .pdf file</a></td>
</tr>
</table>
"""

# HTML real de Mutopia (bloque de "Rialto Ripples", zip multi-parte),
# recortado de make-table.cgi?Style=Jazz el 2026-09-17.
BLOQUE_RIALTO = """
<table class="table-bordered result-table">
<tr><td>Rialto Ripples</td>
<td>by G. Gershwin (1898–1937)</td>
<td>&nbsp;</td>
<td>&nbsp;</td>
</tr><tr>
<td>for Guitar</td>
<td>1916</td>
<td>Jazz</td>
<td>arranged for 3 guitars by jeff covey</td>
</tr><tr>
<td>Manuscript</td>
<td><a href="../legal.html#ccasa">Creative Commons Attribution-ShareAlike 3.0</a></td>
<td><a href="piece-info.cgi?id=1200">More Information</a></td>
<td>2008/01/04</td>
</tr><tr>
<td class="zipped">Download: <a href="https://www.mutopiaproject.org/ftp/GershwinG/rialto_ripples/rialto_ripples-lys.zip">.ly files (zipped)</a></td>
<td class="zipped"><a href="https://www.mutopiaproject.org/ftp/GershwinG/rialto_ripples/rialto_ripples-mids.zip">.mid files (zipped)</a></td>
<td><a href="https://www.mutopiaproject.org/ftp/GershwinG/rialto_ripples/rialto_ripples-preview.png">Preview image</a></td>
<td><a href="https://www.mutopiaproject.org/ftp/GershwinG/rialto_ripples/">Appropriate FTP area</a></td>
</tr><tr>
<td class="zipped"><a href="https://www.mutopiaproject.org/ftp/GershwinG/rialto_ripples/rialto_ripples-a4-pss.zip">A4 .ps files (zipped)</a></td>
<td class="zipped"><a href="https://www.mutopiaproject.org/ftp/GershwinG/rialto_ripples/rialto_ripples-a4-pdfs.zip">A4 .pdf files (zipped)</a></td>
<td class="zipped"><a href="https://www.mutopiaproject.org/ftp/GershwinG/rialto_ripples/rialto_ripples-let-pss.zip">Letter .ps files (zipped)</a></td>
<td class="zipped"><a href="https://www.mutopiaproject.org/ftp/GershwinG/rialto_ripples/rialto_ripples-let-pdfs.zip">Letter .pdf files (zipped)</a></td>
</tr>
</table>
"""


# HTML real de Mutopia con compositor SIN prefijo "by" ("Anonymous"),
# recortado de make-table.cgi?Style=Baroque el 2026-09-17. Regresion:
# el parser exigia "by " y descartaba estos bloques.
BLOQUE_ANONIMO = """
<table class="table-bordered result-table">
<tr><td>Ich ruf zu dir, Herr Jesus Christ</td>
<td>Anonymous</td>
<td>&nbsp;</td>
<td><i>n/a</i></td>
</tr><tr>
<td>for Organ</td>
<td>18th C.</td>
<td>Baroque</td>
<td><i>n/a</i></td>
</tr><tr>
<td>Audio transcription</td>
<td><a href="../legal.html#publicdomain">Public Domain</a></td>
<td><a href="piece-info.cgi?id=578">More Information</a></td>
<td>2005/08/07</td>
</tr><tr>
<td><a href="https://www.mutopiaproject.org/ftp/Anon/ich_ruf/ich_ruf-a4.pdf">A4 .pdf file</a></td>
</tr>
</table>
"""


def _bloque_falso(id_pieza, titulo, pdf=True, estilo="Classical"):
    """Genera un bloque result-table con la misma estructura que Mutopia."""
    if pdf:
        enlace = (f'<a href="https://www.mutopiaproject.org/ftp/X/p{id_pieza}/'
                  f'p{id_pieza}-a4.pdf">A4 .pdf file</a>')
    else:
        enlace = (f'<a href="https://www.mutopiaproject.org/ftp/X/p{id_pieza}/'
                  f'p{id_pieza}-a4-pdfs.zip">A4 .pdf files (zipped)</a>')
    return (
        f'<table class="table-bordered result-table">'
        f'<tr><td>{titulo}</td><td>by A. Compositor (1800–1900)</td>'
        f'<td>&nbsp;</td><td>&nbsp;</td></tr>'
        f'<tr><td>for Piano</td><td>1900</td><td>{estilo}</td><td></td></tr>'
        f'<tr><td>Fuente</td><td>Public Domain</td>'
        f'<td><a href="piece-info.cgi?id={id_pieza}">More Information</a></td>'
        f'<td>2020/01/01</td></tr>'
        f'<tr><td>{enlace}</td></tr></table>'
    )


class TestParsearMutopia(unittest.TestCase):

    def test_pdf_unico(self):
        piezas = parsear_pagina_mutopia(BLOQUE_POSSUM)
        self.assertEqual(len(piezas), 1)
        p = piezas[0]
        self.assertEqual(p["id"], 2012)
        self.assertEqual(p["titulo"], "Possum And Taters")
        self.assertEqual(p["compositor"], "C. Hunter (1876–1906)")
        self.assertEqual(p["instrumento"], "Piano")
        self.assertEqual(p["estilo"], "Jazz")
        self.assertTrue(p["pdf"].endswith("PossumAndTaters-a4.pdf"))
        self.assertIsNone(p["zip"])

    def test_zip_multiparte(self):
        piezas = parsear_pagina_mutopia(BLOQUE_RIALTO)
        self.assertEqual(len(piezas), 1)
        p = piezas[0]
        self.assertEqual(p["id"], 1200)
        self.assertEqual(p["instrumento"], "Guitar")
        self.assertTrue(p["zip"].endswith("rialto_ripples-a4-pdfs.zip"))
        self.assertIsNone(p["pdf"])

    def test_dos_bloques(self):
        piezas = parsear_pagina_mutopia(BLOQUE_POSSUM + BLOQUE_RIALTO)
        self.assertEqual([p["id"] for p in piezas], [2012, 1200])

    def test_compositor_sin_prefijo_by(self):
        piezas = parsear_pagina_mutopia(BLOQUE_ANONIMO)
        self.assertEqual(len(piezas), 1)
        p = piezas[0]
        self.assertEqual(p["id"], 578)
        self.assertEqual(p["compositor"], "Anonymous")
        self.assertEqual(p["instrumento"], "Organ")
        self.assertEqual(p["estilo"], "Baroque")
        self.assertTrue(p["pdf"].endswith("ich_ruf-a4.pdf"))

    def test_mezcla_con_y_sin_by(self):
        piezas = parsear_pagina_mutopia(
            BLOQUE_ANONIMO + BLOQUE_POSSUM + BLOQUE_RIALTO)
        self.assertEqual([p["id"] for p in piezas], [578, 2012, 1200])
        self.assertEqual(piezas[1]["compositor"], "C. Hunter (1876–1906)")

    def test_html_vacio(self):
        self.assertEqual(parsear_pagina_mutopia(""), [])
        self.assertEqual(parsear_pagina_mutopia("<html>nada</html>"), [])

    def test_bloque_sin_id_se_omite(self):
        bloque = _bloque_falso(1, "Pieza").replace(
            '<a href="piece-info.cgi?id=1">More Information</a>', "sin id")
        self.assertEqual(parsear_pagina_mutopia(bloque), [])


class TestNombreArchivo(unittest.TestCase):

    def test_espacios_a_guion_bajo(self):
        self.assertEqual(_nombre_archivo("Clair de Lune"), "Clair_de_Lune")

    def test_sin_acentos(self):
        self.assertEqual(_nombre_archivo("Müller Études"), "Muller_Etudes")

    def test_quita_caracteres_invalidos(self):
        self.assertEqual(_nombre_archivo("Sonata / No. 14: 'Claro'"),
                         "Sonata_No_14_Claro")

    def test_limite_longitud(self):
        self.assertLessEqual(len(_nombre_archivo("x" * 500)), 80)


class TestSlugsEstilo(unittest.TestCase):

    def test_todos_los_estilos_tienen_slug(self):
        for estilo in ESTILOS_MUTOPIA:
            self.assertIn(estilo, SLUG_ESTILO)
            slug = SLUG_ESTILO[estilo]
            self.assertEqual(slug, slug.lower())
            self.assertNotIn(" ", slug)
            self.assertEqual(_nombre_archivo(slug), slug)

    def test_slugs_unicos(self):
        slugs = list(SLUG_ESTILO.values())
        self.assertEqual(len(slugs), len(set(slugs)))


class TestBuscarCatalogo(unittest.TestCase):

    @patch("descarga_musica.time.sleep")
    @patch("descarga_musica._fetch_tabla_mutopia")
    def test_pagina_unica_corta(self, mock_fetch, mock_sleep):
        mock_fetch.return_value = _bloque_falso(1, "Pieza 1")
        piezas = buscar_catalogo_mutopia("Classical")
        self.assertEqual(len(piezas), 1)
        mock_fetch.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("descarga_musica.time.sleep")
    @patch("descarga_musica._fetch_tabla_mutopia")
    def test_paginacion_completa(self, mock_fetch, mock_sleep):
        pagina1 = "".join(_bloque_falso(i, f"Pieza {i}")
                          for i in range(1, 11))
        pagina2 = "".join(_bloque_falso(i, f"Pieza {i}")
                          for i in range(11, 14))
        mock_fetch.side_effect = [pagina1, pagina2]
        piezas = buscar_catalogo_mutopia("Classical")
        self.assertEqual(len(piezas), 13)
        self.assertEqual(mock_fetch.call_count, 2)
        mock_sleep.assert_called_once()

    @patch("descarga_musica.time.sleep")
    @patch("descarga_musica._fetch_tabla_mutopia")
    def test_max_piezas_corta(self, mock_fetch, mock_sleep):
        pagina1 = "".join(_bloque_falso(i, f"Pieza {i}")
                          for i in range(1, 11))
        mock_fetch.return_value = pagina1
        piezas = buscar_catalogo_mutopia("Classical", max_piezas=3)
        self.assertEqual(len(piezas), 3)
        mock_fetch.assert_called_once()

    @patch("descarga_musica._fetch_tabla_mutopia")
    def test_fetch_fallido_detiene(self, mock_fetch):
        mock_fetch.return_value = ""
        self.assertEqual(buscar_catalogo_mutopia("Classical"), [])
        mock_fetch.assert_called_once()


class TestDescargarBytes(unittest.TestCase):

    def test_url_none(self):
        self.assertIsNone(descargar_bytes(None))

    @patch("descarga_musica.urllib.request.urlopen")
    def test_descarga_ok(self, mock_urlopen):
        contenido = b"PK\x03\x04datos"
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.headers = {"Content-Length": str(len(contenido))}
        mock_resp.read.return_value = contenido
        mock_urlopen.return_value = mock_resp
        self.assertEqual(descargar_bytes("https://example.com/a.zip"),
                         contenido)

    @patch("descarga_musica.urllib.request.urlopen")
    def test_demasiado_grande(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.headers = {"Content-Length": str(600 * 1024 * 1024)}
        mock_urlopen.return_value = mock_resp
        self.assertIsNone(descargar_bytes("https://example.com/big.zip"))


class TestDescargarZipPdfs(unittest.TestCase):

    def _zip_bytes(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("pieza.pdf", b"%PDF-1.4 uno")
            z.writestr("partes/segundo.pdf", b"%PDF-1.4 dos")
            z.writestr("notas.txt", b"texto")
        return buffer.getvalue()

    @patch("descarga_musica.descargar_bytes")
    def test_extrae_pdfs(self, mock_bytes):
        mock_bytes.return_value = self._zip_bytes()
        with tempfile.TemporaryDirectory() as tmpdir:
            ok = descargar_zip_pdfs("https://example.com/a.zip", tmpdir)
            self.assertTrue(ok)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "pieza.pdf")))
            # path traversal: se guarda por nombre base, sin subcarpetas
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "segundo.pdf")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "notas.txt")))

    @patch("descarga_musica.descargar_bytes")
    def test_sin_pdfs(self, mock_bytes):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("notas.txt", b"texto")
        mock_bytes.return_value = buffer.getvalue()
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(
                descargar_zip_pdfs("https://example.com/a.zip", tmpdir))

    @patch("descarga_musica.descargar_bytes")
    def test_descarga_fallida(self, mock_bytes):
        mock_bytes.return_value = None
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(
                descargar_zip_pdfs("https://example.com/a.zip", tmpdir))


class TestDescargarCatalogo(unittest.TestCase):

    @patch("descarga_musica.time.sleep")
    @patch("descarga_musica.descargar_archivo")
    @patch("descarga_musica.buscar_catalogo_mutopia")
    def test_descarga_por_estilo(self, mock_buscar, mock_archivo, mock_sleep):
        mock_buscar.return_value = [
            {"id": 1, "titulo": "Pieza Uno", "compositor": "A. Uno",
             "instrumento": "Piano", "estilo": "Classical",
             "pdf": "https://www.mutopiaproject.org/ftp/X/1-a4.pdf",
             "zip": None},
        ]
        mock_archivo.return_value = True
        with tempfile.TemporaryDirectory() as tmpdir:
            ok, fallos, omit = descargar_catalogo_mutopia(
                tmpdir, ["Classical"], pausa=0)
        self.assertEqual((ok, fallos, omit), (1, 0, 0))
        destino = os.path.join(tmpdir, "clasico", "0001_Pieza_Uno_A_Uno.pdf")
        mock_archivo.assert_called_once()
        self.assertEqual(mock_archivo.call_args[0][1], destino)

    @patch("descarga_musica.time.sleep")
    @patch("descarga_musica.descargar_archivo")
    @patch("descarga_musica.buscar_catalogo_mutopia")
    def test_dry_run_no_descarga(self, mock_buscar, mock_archivo, mock_sleep):
        mock_buscar.return_value = [
            {"id": 1, "titulo": "Pieza Uno", "compositor": "A. Uno",
             "instrumento": "Piano", "estilo": "Classical",
             "pdf": "https://x/1-a4.pdf", "zip": None},
            {"id": 2, "titulo": "Pieza Dos", "compositor": "A. Dos",
             "instrumento": "Piano", "estilo": "Classical",
             "pdf": None, "zip": "https://x/2-a4-pdfs.zip"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            ok, fallos, omit = descargar_catalogo_mutopia(
                tmpdir, ["Classical"], dry_run=True, pausa=0)
        self.assertEqual((ok, fallos, omit), (2, 0, 0))
        mock_archivo.assert_not_called()

    @patch("descarga_musica.time.sleep")
    @patch("descarga_musica.descargar_archivo")
    @patch("descarga_musica.buscar_catalogo_mutopia")
    def test_sin_pdf_se_omite(self, mock_buscar, mock_archivo, mock_sleep):
        mock_buscar.return_value = [
            {"id": 9, "titulo": "Sin Partitura", "compositor": "A. Nadie",
             "instrumento": "Piano", "estilo": "Classical",
             "pdf": None, "zip": None},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            ok, fallos, omit = descargar_catalogo_mutopia(
                tmpdir, ["Classical"], pausa=0)
        self.assertEqual((ok, fallos, omit), (0, 0, 1))
        mock_archivo.assert_not_called()

    @patch("descarga_musica.time.sleep")
    @patch("descarga_musica.descargar_archivo")
    @patch("descarga_musica.buscar_catalogo_mutopia")
    def test_fallo_descarga(self, mock_buscar, mock_archivo, mock_sleep):
        mock_buscar.return_value = [
            {"id": 1, "titulo": "Pieza", "compositor": "A. Uno",
             "instrumento": "Piano", "estilo": "Classical",
             "pdf": "https://x/1-a4.pdf", "zip": None},
        ]
        mock_archivo.return_value = False
        with tempfile.TemporaryDirectory() as tmpdir:
            ok, fallos, omit = descargar_catalogo_mutopia(
                tmpdir, ["Classical"], pausa=0)
        self.assertEqual((ok, fallos, omit), (0, 1, 0))


if __name__ == "__main__":
    unittest.main()
