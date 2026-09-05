"""Pruebas de descarga_musica.py: parseo de metadata de Archive.org,
construccion de URLs, filtrado de archivos MP3 y demo dry-run.
Sin red: la metadata de prueba es real de Archive.org (verificada
2026-09-05) pero se simula la respuesta HTTP."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from descarga_musica import (
    archivos_mp3, construir_url_descarga, descargar_archivo,
    descargar_lista, demo, buscar_archive, listar_fuentes,
    PIEZAS_DEMO, DIR_MUSICA,
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


if __name__ == "__main__":
    unittest.main()
