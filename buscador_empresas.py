#!/usr/bin/env python3
"""BUSCADOR INTELIGENTE DE EMPRESAS por CAMPOS + TABLA-EMPRESAS-CUIT-TIPO.

Suite de búsqueda completa de better-ocr: para cada empresa (por CUIT o por
nombre) ejecuta el ciclo integrado — clasificación por prefijo, ficha de
CuitOnline (condición tributaria, empleador, impuestos activos, ciudad),
titular de dominios RDAP, correos/canales de contacto, judiciales, dorks de
opiniones — y emite la TABLA-EMPRESAS-CUIT-TIPO.md en el formato estándar
del dominio:

    CUIT · Razón social (legal/comercial) · Tipo de empresa · Empleadora ·
    Fuente

REGLAS DEL DOMINIO (TABLA-EMPRESAS-CUIT-TIPO.md, creada 12/8/2026):
  - TODO REAL: lo que no consta va "No consta — pedir por escrito".
  - El prefijo del CUIT indica el tipo: 20/23/24/25/26/27 = PERSONA FÍSICA;
    30/33/34 = PERSONA JURÍDICA.
  - La condición (Monotributo vs Responsable Inscripto) y "Empleador" salen
    de la ficha de CuitOnline (señales textuales verificadas en vivo).
  - "Empleadora" marca lo VERIFICADO (CuitOnline "Empleador: Sí/No"); lo no
    verificado va "No consta" hasta pedirlo por escrito.
  - P0.9: los datos personales del titular (nombre, sexo, contactos) NO se
    vuelcan en la tabla ni en los informes: solo tipo y datos comerciales.

Uso:
    python3 buscador_empresas.py 27-12345678-9 --salida /var/tmp/tabla
    python3 buscador_empresas.py "Asistencia del Sol" --salida /var/tmp/tabla
    python3 buscador_empresas.py --lista empresas.txt --salida /var/tmp/tabla
    # --lista: una empresa por línea (CUIT o nombre); genera la TABLA
    # completa + resultado.json. --con-dorks agrega judiciales/correos/
    # recomendadores por empresa (más lento).
"""

import argparse
import json
import os
import re
import time

__all__ = ["buscar_por_cuit", "buscar_por_nombre", "generar_tabla"]

NO_CONSTA = "No consta — pedir por escrito"


def buscar_por_cuit(cuit: str, motores: list = None, captcha: bool = False,
                    headed: bool = False, salida_dir: str = "",
                    locale: str = "es-AR", con_dorks: bool = False,
                    sitio: str = "") -> dict:
    """Perfil completo de una empresa/persona por CUIT (analizar_cuit.py).
    Si se conoce la web oficial (--sitio) se pasa para el dominio/correos."""
    from analizar_cuit import analizar_cuit
    informe = analizar_cuit(
        cuit, motores=motores, captcha=captcha, headed=headed,
        salida_dir=salida_dir, locale=locale,
        con_dorks=con_dorks, con_recomendadores=con_dorks,
        con_judiciales=con_dorks, vision="")
    informe["entrada"] = cuit
    informe["tipo_entrada"] = "cuit"
    return informe


def buscar_por_nombre(nombre: str, motores: list = None, captcha: bool = False,
                      headed: bool = False, salida_dir: str = "",
                      locale: str = "es-AR", con_dorks: bool = False,
                      sitio: str = "") -> dict:
    """Empresa por NOMBRE comercial: primero empresas.py (CuitOnline + web
    oficial + correos + RDAP); si se obtiene un CUIT, se completa el perfil
    con analizar_cuit (condición, empleador). Lo que no consta queda en
    'No consta — pedir por escrito' (regla del dominio)."""
    from empresas import buscar_empresa
    base = buscar_empresa(nombre, sitio=sitio, motores=motores,
                          captcha=captcha, headed=headed,
                          salida_dir=salida_dir, locale=locale,
                          con_juicios=con_dorks, con_correos=con_dorks,
                          con_recomendadores=con_dorks)
    cuit = ""
    if base.get("sintesis", {}).get("cuits"):
        cuit = base["sintesis"]["cuits"][0].get("cuit", "")
    perfil = None
    if cuit:
        perfil = buscar_por_cuit(cuit, motores=motores, captcha=captcha,
                                 headed=headed, salida_dir=salida_dir,
                                 locale=locale, con_dorks=con_dorks)
    informe = {
        "entrada": nombre,
        "tipo_entrada": "nombre",
        "empresa_base": base,
        "cuit": cuit or NO_CONSTA,
        "perfil_cuit": perfil,
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return informe


# ---------------------------------------------------------------------------
# Tabla estándar TABLA-EMPRESAS-CUIT-TIPO.md (regla TODO REAL)
# ---------------------------------------------------------------------------

def _tipo_fila(informe: dict) -> str:
    """Tipo de empresa para la tabla: por prefijo del CUIT y, si consta la
    razon social, por su tipo societario. Regla: 20-27 fisica, 30/33/34
    juridica. Lo que no consta -> 'No consta (¿persona/empresa?)'."""
    if informe.get("tipo_entrada") == "cuit":
        c = informe.get("clasificacion", {})
        tipo = c.get("tipo", "")
        razon = (informe.get("cuitonline") or {}).get("ficha", {}).get(
            "razon_social", "")
        if tipo == "persona_fisica":
            return "PERSONA FÍSICA"
        if tipo == "persona_juridica":
            t = _tipo_razon(razon)
            return f"PERSONA JURÍDICA · {t}" if t else "PERSONA JURÍDICA"
        return NO_CONSTA
    perfil = informe.get("perfil_cuit") or {}
    if perfil:
        return _tipo_fila(perfil)
    base = informe.get("empresa_base", {})
    sit = base.get("sitio_oficial") or {}
    razon = sit.get("razon_social", "")
    t = _tipo_razon(razon)
    return f"{t} (razón social declarada en web)" if t else \
        "No consta (¿persona/empresa?)"


def _tipo_razon(razon: str) -> str:
    """Tipo societario por la razon social (sufijo legal). '' si no consta."""
    from analizar_cuit import tipo_por_razon_social
    t = tipo_por_razon_social(razon or "")["tipo"]
    return "" if t.startswith("sin_") else t


def _cuit_fila(informe: dict) -> str:
    if informe.get("tipo_entrada") == "cuit":
        return informe["cuit"]
    perfil = informe.get("perfil_cuit") or {}
    if perfil:
        return perfil["cuit"]
    return informe.get("cuit") or NO_CONSTA


def _razon_fila(informe: dict) -> str:
    """Razón social (legal/comercial) que CONSTA. Para persona física el
    nombre del titular NO se expone (P0.9): se indica el tipo."""
    if informe.get("tipo_entrada") == "cuit":
        f = (informe.get("cuitonline") or {}).get("ficha") or {}
        if f.get("razon_social"):
            c = informe.get("clasificacion", {}).get("tipo", "")
            if c == "persona_fisica":
                return "Persona física (titular no expuesto — P0.9)"
            return f["razon_social"]
        return NO_CONSTA
    perfil = informe.get("perfil_cuit") or {}
    if perfil:
        return _razon_fila(perfil)
    base = informe.get("empresa_base", {})
    sit = base.get("sitio_oficial") or {}
    if sit.get("razon_social"):
        return f"{sit['razon_social']} (declarada en web)"
    return NO_CONSTA


def _empleadora_fila(informe: dict) -> str:
    """Empleadora: SOLO lo verificado en CuitOnline ('Empleador: Sí/No').
    Lo no verificado va 'No consta' (regla del dominio)."""
    perfil = informe.get("perfil_cuit") or {}
    if informe.get("tipo_entrada") == "cuit":
        perfil = informe
    f = (perfil.get("cuitonline") or {}).get("ficha") or {}
    if f.get("empleador") in ("Si", "No"):
        return f"Empleador: {f['empleador']} (CuitOnline)"
    return NO_CONSTA


def _condicion_fila(informe: dict) -> str:
    perfil = informe.get("perfil_cuit") or {}
    if informe.get("tipo_entrada") == "cuit":
        perfil = informe
    f = (perfil.get("cuitonline") or {}).get("ficha") or {}
    if f.get("condicion"):
        return f["condicion"]
    return NO_CONSTA


def _fuente_fila(informe: dict) -> str:
    """Fuentes que aportaron datos REALES (con fecha)."""
    fuentes = []
    hoy = time.strftime("%d/%m")
    if informe.get("tipo_entrada") == "cuit":
        co = informe.get("cuitonline") or {}
        if co.get("estado") == "ok":
            fuentes.append(f"CuitOnline ({hoy})")
        if informe.get("dominio_titular"):
            fuentes.append("RDAP NIC.AR")
    else:
        base = informe.get("empresa_base", {})
        if base.get("sitio_oficial") and base["sitio_oficial"].get("ok"):
            fuentes.append(f"Web oficial ({hoy})")
        if base.get("sintesis", {}).get("cuits"):
            fuentes.append(f"CuitOnline ({hoy})")
    return "; ".join(fuentes) if fuentes else NO_CONSTA


def generar_tabla(informes: list) -> str:
    """Genera el markdown de TABLA-EMPRESAS-CUIT-TIPO.md en el formato del
    dominio: Empresa | CUIT | Razón social (legal/comercial) | Tipo de
    empresa | Empleadora | Fuente. Regla TODO REAL: todo lo no constatado
    va 'No consta — pedir por escrito'."""
    lineas = [
        "# TABLA-EMPRESAS-CUIT-TIPO",
        "",
        "CUIT · Razón social (legal/comercial) · Tipo de empresa · "
        "Condición · Empleadora · Fuente",
        "",
        "Regla: **TODO REAL** — lo que no consta va "
        "**\"No consta — pedir por escrito\"**. Prefijo del CUIT: "
        "20/23/24/25/26/27 = PERSONA FÍSICA · 30/33/34 = PERSONA JURÍDICA. "
        "Condición (Monotributo vs Responsable Inscripto) y \"Empleador\" "
        "salen de CuitOnline (verificado). P0.9: los datos personales del "
        "titular no se publican.",
        "",
        "| Empresa | CUIT | Razón social (legal/comercial) | Tipo de "
        "empresa | Condición | Empleadora | Fuente |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for inf in informes:
        nombre = inf.get("entrada", "")
        lineas.append(
            f"| {nombre} | {_cuit_fila(inf)} | {_razon_fila(inf)} | "
            f"{_tipo_fila(inf)} | {_condicion_fila(inf)} | "
            f"{_empleadora_fila(inf)} | {_fuente_fila(inf)} |")
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Buscador inteligente de empresas por campos + "
                    "TABLA-EMPRESAS-CUIT-TIPO.md (suite de búsqueda completa "
                    "de better-ocr: CUIT, razón social, tipo, condición, "
                    "empleadora, correos, judiciales, RDAP)")
    parser.add_argument("criterio", nargs="?", default="",
                        help="CUIT (XX-XXXXXXXX-X) o nombre comercial")
    parser.add_argument("--lista", default="",
                        help="archivo con una empresa por línea (CUIT o "
                             "nombre); genera la TABLA completa")
    parser.add_argument("--sitio", default="",
                        help="web oficial de la empresa (para nombre)")
    parser.add_argument("--motores", default="",
                        help="motores de busqueda web separados por comas")
    parser.add_argument("--captcha", action="store_true",
                        help="intentar resolver reCAPTCHA v2 en motores")
    parser.add_argument("--headed", action="store_true",
                        help="navegador visible (default: headless)")
    parser.add_argument("--salida", default="",
                        help="directorio: TABLA-EMPRESAS-CUIT-TIPO.md + "
                             "resultado.json por empresa")
    parser.add_argument("--con-dorks", action="store_true",
                        help="incluir judiciales, correos y recomendadores "
                             "por empresa (más lento)")
    parser.add_argument("--locale", default="es-AR",
                        help="locale del navegador")
    args = parser.parse_args()

    if not args.criterio and not args.lista:
        parser.error("falta el CUIT/nombre o --lista")

    motores = [m.strip() for m in args.motores.split(",") if m.strip()] or None

    empresas = []
    if args.lista:
        if not os.path.exists(args.lista):
            print(f"[ERROR] No existe --lista {args.lista}")
            return
        with open(args.lista, encoding="utf-8") as f:
            empresas = [l.strip() for l in f if l.strip()]
    else:
        empresas = [args.criterio]

    informes = []
    for e in empresas:
        print(f"\n=== {e}")
        try:
            if re.match(r"^\d{2}-\d{8}-\d$", e):
                inf = buscar_por_cuit(e, motores=motores,
                                      captcha=args.captcha, headed=args.headed,
                                      salida_dir=args.salida,
                                      locale=args.locale,
                                      con_dorks=args.con_dorks)
            else:
                inf = buscar_por_nombre(e, motores=motores,
                                        captcha=args.captcha,
                                        headed=args.headed,
                                        salida_dir=args.salida,
                                        locale=args.locale,
                                        con_dorks=args.con_dorks,
                                        sitio=args.sitio)
            informes.append(inf)
        except Exception as exc:
            print(f"  [ERROR] {type(exc).__name__}: {str(exc)[:100]}")
            informes.append({"entrada": e, "error": str(exc)[:100],
                             "tipo_entrada": "error"})

    tabla = generar_tabla(informes)
    print("\n" + tabla)
    if args.salida:
        os.makedirs(args.salida, exist_ok=True)
        with open(os.path.join(args.salida, "TABLA-EMPRESAS-CUIT-TIPO.md"),
                  "w", encoding="utf-8") as f:
            f.write(tabla + "\n")
        with open(os.path.join(args.salida, "resultado.json"), "w",
                  encoding="utf-8") as f:
            json.dump(informes, f, ensure_ascii=False, indent=2,
                      default=str)
        print(f"\n[OK] Tabla: {args.salida}/TABLA-EMPRESAS-CUIT-TIPO.md")
        print(f"[OK] Informe completo: {args.salida}/resultado.json")


if __name__ == "__main__":
    main()
