"""Pruebas de rns.py (Registro Nacional de Sociedades offline): parseo de
CSV real, deduplicacion por actividad, busqueda FTS5 y variantes de nombre.
Sin red: los CSVs de prueba son sinteticos con la cabecera REAL del dataset
(verificada en vivo el 2026-08-14: cuit, razon_social, ... dom_legal_*)."""

import os
import sqlite3
import tempfile
import unittest

from rns import (armar_query_fts, buscar, crear_base, indexar_csv,
                 normalizar_razon, sin_sufijo_legal)

CABECERA = ("cuit,razon_social,fecha_hora_contrato_social,tipo_societario,"
            "fecha_hora_actualizacion,numero_inscripcion,dom_fiscal_provincia,"
            "dom_fiscal_localidad,dom_fiscal_calle,dom_fiscal_numero,"
            "dom_fiscal_piso,dom_fiscal_departamento,dom_fiscal_cp,"
            "dom_fiscal_estado_domicilio,dom_legal_provincia,dom_legal_localidad,"
            "dom_legal_calle,dom_legal_numero,dom_legal_piso,"
            "dom_legal_departamento,dom_legal_cp,dom_legal_estado_domicilio")

# Con actividad_* (como el CSV real de asociaciones, que repite filas por
# actividad: el indexador debe deduplicar por cuit+razon_social).
CABECERA_ASOC = CABECERA + (",actividad_codigo,actividad_descripcion,"
                            "actividad_orden,actividad_estado,actividad_vigencia")

FILAS_SOCIEDADES = [
    # (cuit, razon, tipo, fecha, nro_insc, prov, loc)
    ("30-12345678-9", "INTEGRAR CUIDADOS S.R.L.", "S.R.L.",
     "2021-10-26-11:00", "12345", "CIUDAD AUTONOMA BUENOS AIRES", "CAPITAL FEDERAL"),
    ("30-12345678-9", "PERMANENCIA SALUD S.R.L.", "S.R.L.",
     "2018-03-15-10:00", "23456", "BUENOS AIRES", "QUILMES"),
    ("27-98765432-1", "ASISTENCIA DEL SOL", "SOCIEDAD ANONIMA",
     "2015-07-01-00:00", "34567", "SANTA FE", "ROSARIO"),
    ("20-55555555-5", "ASISTENCIA MISIONERA", "S.A.",
     "2019-01-20-00:00", "45678", "MISIONES", "POSADAS"),
    ("27-11111111-1", "CUIDADOS GERIÁTRICOS DEL NORTE", "S.R.L.",
     "2017-04-04-00:00", "99999", "SALTA", "SALTA"),
]

FILAS_ASOCIACIONES = [
    ("30-88888888-8", "ASOCIACION MIS ABUELOS EN CASA", "ASOCIACION CIVIL",
     "2016-05-10-00:00", "56789", "CIUDAD AUTONOMA BUENOS AIRES",
     "CAPITAL FEDERAL", "851111", "Actividades de asistencia social", "1"),
    # la misma fila duplicada por otra actividad: no debe indexarse 2 veces
    ("30-88888888-8", "ASOCIACION MIS ABUELOS EN CASA", "ASOCIACION CIVIL",
     "2016-05-10-00:00", "56789", "CIUDAD AUTONOMA BUENOS AIRES",
     "CAPITAL FEDERAL", "852222", "Otras actividades", "2"),
    ("30-99999999-9", "FUNDACION CUIDAR", "FUNDACION",
     "2020-09-09-00:00", "67890", "CORDOBA", "CORDOBA",
     "853333", "Servicios sociales", "1"),
]


def _escribir_csv(ruta: str, cabecera: str, filas: list) -> None:
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(cabecera + "\n")
        for fila in filas:
            f.write(",".join(fila) + "\n")


class TestNormalizacion(unittest.TestCase):

    def test_normalizar_razon(self):
        self.assertEqual(normalizar_razon("Asistencia del Sol S.R.L."),
                         "ASISTENCIA DEL SOL S R L")
        self.assertEqual(normalizar_razon("  CUIDADOS  GERIÁTRICOS "),
                         "CUIDADOS GERIATRICOS")
        self.assertEqual(normalizar_razon(""), "")

    def test_sin_sufijo_legal(self):
        self.assertEqual(sin_sufijo_legal("PERMANENCIA SALUD S.R.L."),
                         "PERMANENCIA SALUD")
        self.assertEqual(sin_sufijo_legal("ASISTENCIA DEL SOL SA"),
                         "ASISTENCIA DEL SOL")
        self.assertEqual(sin_sufijo_legal("ASOCIACION MIS ABUELOS EN CASA"),
                         "ASOCIACION MIS ABUELOS EN CASA")
        self.assertEqual(sin_sufijo_legal("ASISTENCIA DEL SOL"),
                         "ASISTENCIA DEL SOL")
        # 'SA' al inicio no se toca: el sufijo esta anclado al final
        self.assertEqual(sin_sufijo_legal("SA SALUD SRL"), "SA SALUD")

    def test_armar_query_fts(self):
        # palabras cortas sin prefijo (ruido: 'mis' -> 'misionera')
        self.assertEqual(armar_query_fts("Asistencia Mis Abuelos"),
                         '"ASISTENCIA"* AND "MIS" AND "ABUELOS"*')
        # 4+ letras con prefijo
        self.assertEqual(armar_query_fts("Permanencia"),
                         '"PERMANENCIA"*')
        # tokens de 3 letras sin prefijo ('sol' no debe matchear 'solar')
        self.assertEqual(armar_query_fts("De la Sol"), '"SOL"')
        # tokens cortos se descartan (2 letras no aportan)
        self.assertEqual(armar_query_fts("El Rio"), '"RIO"')


class TestBase(unittest.TestCase):

    def setUp(self):
        self.dir_tmp = tempfile.mkdtemp(prefix="rns_test_")
        self.db = os.path.join(self.dir_tmp, "rns.db")
        self.csv_soc = os.path.join(self.dir_tmp, "sociedades.csv")
        self.csv_asoc = os.path.join(self.dir_tmp, "asociaciones.csv")
        _escribir_csv(self.csv_soc, CABECERA, FILAS_SOCIEDADES)
        _escribir_csv(self.csv_asoc, CABECERA_ASOC, FILAS_ASOCIACIONES)

    def _indexar(self):
        """Base con sociedades + asociaciones, lista para buscar."""
        conn = crear_base(self.db)
        try:
            indexar_csv(conn, self.csv_soc, "sociedades")
            indexar_csv(conn, self.csv_asoc, "asociaciones")
        finally:
            conn.close()

    def test_indexar_y_buscar_exacta(self):
        conn = crear_base(self.db)
        try:
            n = indexar_csv(conn, self.csv_soc, "sociedades")
            self.assertEqual(n, 5)
            n2 = indexar_csv(conn, self.csv_asoc, "asociaciones")
            # 3 filas distintas (una duplicada por actividad -> 2 insertadas)
            self.assertEqual(n2, 2)
        finally:
            conn.close()

        r = buscar(self.db, "PERMANENCIA SALUD S.R.L.")
        self.assertTrue(r)
        self.assertEqual(r[0]["razon_social"], "PERMANENCIA SALUD S.R.L.")
        self.assertEqual(r[0]["cuit"], "30-12345678-9")
        self.assertEqual(r[0]["coincidencia"], 2)  # exacta

    def test_buscar_sin_sufijo_legal(self):
        self._indexar()
        # 'Permanencia Salud' sin sufijo -> la registrada con S.R.L. es
        # prefijo de la consulta... espera: consulta sin sufijo, registro
        # con sufijo: la razon 'PERMANENCIA SALUD S.R.L.' EMPIEZA con la
        # consulta 'PERMANENCIA SALUD' -> coincidencia 1 (prefijo)
        r = buscar(self.db, "Permanencia Salud")
        self.assertTrue(r)
        self.assertEqual(r[0]["razon_social"], "PERMANENCIA SALUD S.R.L.")
        self.assertEqual(r[0]["coincidencia"], 1)
        # y la consulta CON sufijo da exacta (2)
        r2 = buscar(self.db, "Permanencia Salud S.R.L.")
        self.assertEqual(r2[0]["coincidencia"], 2)

    def test_buscar_sin_acentos(self):
        self._indexar()
        # la consulta sin acentos matchea la razon con tilde (FTS
        # remove_diacritics 2 normaliza ambos lados)
        r = buscar(self.db, "geriatricos del norte")
        self.assertTrue(r)
        self.assertEqual(r[0]["razon_social"], "CUIDADOS GERIÁTRICOS DEL NORTE")
        # la consulta esta contenida como subcadena de palabras completas
        self.assertEqual(r[0]["coincidencia"], 1)

    def test_buscar_sin_coincidencia(self):
        self._indexar()
        r = buscar(self.db, "Asistencia Mis Abuelos")
        # 'MIS' sin prefijo: no matchea 'MISIONERA'; no existe coincidencia
        self.assertFalse(any("ABUELOS" in x["razon_social"] for x in r))
        # pero tampoco debe traer ruido de 'asistencia mis*' -> 'MISIONERA'
        self.assertFalse(any("MISIONERA" in x["razon_social"] for x in r))

    def test_buscar_sin_base(self):
        with self.assertRaises(FileNotFoundError):
            buscar(os.path.join(self.dir_tmp, "no-existe.db"), "X")

    def test_deduplicacion_por_actividad(self):
        conn = crear_base(self.db)
        try:
            indexar_csv(conn, self.csv_asoc, "asociaciones")
        finally:
            conn.close()
        conn2 = sqlite3.connect(self.db)
        try:
            total = conn2.execute("SELECT COUNT(*) FROM empresas").fetchone()[0]
            self.assertEqual(total, 2)  # la duplicada por actividad no duplica
        finally:
            conn2.close()

    def test_fusion_fila_sin_cuit_con_la_misma_entidad(self):
        # el dataset tiene, para algunas entidades, una fila sin CUIT junto
        # a otra con CUIT (misma razon/tipo/fecha/localidad): deben fundirse
        csv_f = os.path.join(self.dir_tmp, "fusion.csv")
        _escribir_csv(csv_f, CABECERA, [
            ("", "CENTRO DE JUBILADOS MIS ABUELOS", "ASOCIACION CIVIL",
             "1996-04-04-00:00", "111", "SAN JUAN", "ALTO DE SIERRA"),
            ("30987654322", "CENTRO DE JUBILADOS MIS ABUELOS",
             "ASOCIACION CIVIL", "1996-04-04-00:00", "111", "SAN JUAN",
             "ALTO DE SIERRA"),
        ])
        conn = crear_base(self.db)
        try:
            n = indexar_csv(conn, csv_f, "asociaciones")
            self.assertEqual(n, 1)
        finally:
            conn.close()
        r = buscar(self.db, "CENTRO DE JUBILADOS MIS ABUELOS")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["cuit"], "30-98765432-2")

    def test_cuit_sin_guiones_del_dataset(self):
        # el dataset RNS publica el CUIT como 11 digitos sin guiones
        # ("30987654323"): debe normalizarse a XX-XXXXXXXX-X
        csv_sg = os.path.join(self.dir_tmp, "sin-guiones.csv")
        _escribir_csv(csv_sg, CABECERA, [
            ("30987654323", "ABUELOS CLUB LA PLATA", "ASOCIACION CIVIL",
             "1978-11-18-00:00", "", "BUENOS AIRES", "LA PLATA")])
        conn = crear_base(self.db)
        try:
            indexar_csv(conn, csv_sg, "asociaciones")
        finally:
            conn.close()
        r = buscar(self.db, "ABUELOS CLUB")
        self.assertEqual(r[0]["cuit"], "30-98765432-3")

    def test_cuit_invalido_se_guarda_vacio(self):
        csv_raro = os.path.join(self.dir_tmp, "raro.csv")
        _escribir_csv(csv_raro, CABECERA, [
            ("999", "SIN CUIT VALIDO", "S.A.", "2020-01-01-00:00", "", "X", "Y")])
        conn = crear_base(self.db)
        try:
            n = indexar_csv(conn, csv_raro, "sociedades")
            self.assertEqual(n, 1)
        finally:
            conn.close()
        r = buscar(self.db, "SIN CUIT VALIDO")
        self.assertEqual(r[0]["cuit"], "")


if __name__ == "__main__":
    unittest.main()
