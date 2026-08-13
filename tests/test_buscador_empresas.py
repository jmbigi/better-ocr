"""Pruebas de buscador_empresas.py (piezas puras de la TABLA: tipo, CUIT,
razon social, empleadora, condicion, fuente). Sin red ni navegador."""

import unittest

from buscador_empresas import (generar_tabla, _cuit_fila, _empleadora_fila,
                               _razon_fila, _tipo_fila)

INFORME_CUIT_FISICA = {
    "entrada": "ASISTENCIA DEL SOL", "tipo_entrada": "cuit",
    "cuit": "27-12345678-9",
    "clasificacion": {"tipo": "persona_fisica", "confianza": 0.95},
    "cuitonline": {"estado": "ok", "ficha": {
        "razon_social": "TITULAR PERSONA FISICA",
        "empleador": "No",
        "condicion": "Responsable Inscripto (senal: impuestos activos)",
        "impuestos_activos": ["GANANCIAS PERSONAS FISICAS", "IVA"],
        "provincia_localidad": "Buenos Aires - Castelar",
        "posee_cuit": True, "posee_cuil": None}},
    "dominio_titular": [{"ok": True, "dominio": "asistenciadelsol.com.ar",
                         "titular_tipo": "no_publicado"}],
}

INFORME_CUIT_SRL = {
    "entrada": "INTEGRAR CUIDADOS SRL", "tipo_entrada": "cuit",
    "cuit": "30-12345678-9",
    "clasificacion": {"tipo": "persona_juridica", "confianza": 0.95},
    "cuitonline": {"estado": "ok", "ficha": {
        "razon_social": "INTEGRAR CUIDADOS SRL",
        "empleador": "", "condicion": ""}},
    "dominio_titular": [],
}

INFORME_NOMBRE_SIN_CUIT = {
    "entrada": "CUIDARTE SIEMPRE", "tipo_entrada": "nombre",
    "cuit": "No consta — pedir por escrito", "perfil_cuit": None,
    "empresa_base": {"sitio_oficial": None,
                     "sintesis": {"cuits": []}},
}


class TestTipoFila(unittest.TestCase):

    def test_fisica_por_prefijo(self):
        self.assertEqual(_tipo_fila(INFORME_CUIT_FISICA), "PERSONA FÍSICA")

    def test_juridica_con_tipo_societario(self):
        t = _tipo_fila(INFORME_CUIT_SRL)
        self.assertIn("PERSONA JURÍDICA", t)
        self.assertIn("SRL", t)

    def test_sin_cuit_no_inventa_tipo(self):
        t = _tipo_fila(INFORME_NOMBRE_SIN_CUIT)
        self.assertIn("No consta", t)


class TestCuitFila(unittest.TestCase):

    def test_por_cuit_directo(self):
        self.assertEqual(_cuit_fila(INFORME_CUIT_FISICA), "27-12345678-9")

    def test_sin_cuit(self):
        self.assertEqual(_cuit_fila(INFORME_NOMBRE_SIN_CUIT),
                         "No consta — pedir por escrito")


class TestRazonFila(unittest.TestCase):

    def test_persona_fisica_no_expone_nombre(self):
        """P0.9: el nombre del titular persona fisica NO sale en la tabla,
        aunque la ficha lo tenga."""
        r = _razon_fila(INFORME_CUIT_FISICA)
        self.assertIn("titular no expuesto", r)
        self.assertNotIn("TITULAR PERSONA FISICA", r)

    def test_empresa_con_razon(self):
        self.assertEqual(_razon_fila(INFORME_CUIT_SRL), "INTEGRAR CUIDADOS SRL")

    def test_sin_razon(self):
        self.assertEqual(_razon_fila(INFORME_NOMBRE_SIN_CUIT),
                         "No consta — pedir por escrito")


class TestEmpleadoraFila(unittest.TestCase):

    def test_verificado_en_cuitonline(self):
        self.assertEqual(_empleadora_fila(INFORME_CUIT_FISICA),
                         "Empleador: No (CuitOnline)")

    def test_no_consta(self):
        self.assertEqual(_empleadora_fila(INFORME_CUIT_SRL),
                         "No consta — pedir por escrito")


class TestGenerarTabla(unittest.TestCase):

    def test_tabla_con_todas_las_columnas_y_regla(self):
        t = generar_tabla([INFORME_CUIT_FISICA, INFORME_CUIT_SRL,
                           INFORME_NOMBRE_SIN_CUIT])
        self.assertIn("# TABLA-EMPRESAS-CUIT-TIPO", t)
        self.assertIn("| Empresa | CUIT |", t)
        self.assertIn("TODO REAL", t)
        self.assertIn("27-12345678-9", t)
        self.assertIn("30-12345678-9", t)
        self.assertIn("No consta — pedir por escrito", t)
        self.assertNotIn("TITULAR PERSONA FISICA", t)  # P0.9

    def test_tabla_vacia(self):
        t = generar_tabla([])
        self.assertIn("| Empresa | CUIT |", t)
        self.assertIn("TODO REAL", t)


if __name__ == "__main__":
    unittest.main()
