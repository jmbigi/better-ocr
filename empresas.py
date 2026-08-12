#!/usr/bin/env python3
"""CLI de busqueda de EMPRESAS: razon social, CUIT, senales de actividad y
juicios, reutilizando el motor multi-motor de buscador.py (Playwright del
python del sistema; piezas puras testeables sin navegador).

Pasos por empresa (cada uno independiente; el fallo de uno no corta el resto):
  1. CuitOnline con variantes automaticas del nombre (limpieza de sufijos
     legales SRL/SA/SAS/SH) + Dateas (hoy reporta 404, verificado 2026-08-12).
  2. Web oficial si se conoce (--sitio): vigencia, titulo, CUITs y razon
     social declarada en el HTML.
  3. RDAP de NIC.AR (si --sitio): registrador y fechas del dominio, SIN
     datos personales del titular (P0.9).
  4. Busqueda web general (motores de buscador.py, default Bing) con la
     razon social + dorks de juicios ("X" juicio/fallo/demanda/sentencia).
     LIMITACION HONESTA (P1.6): los expedientes laborales argentinos (CNAT,
     SECLO, juzgados) no son buscables publicamente por razon social; la
     ausencia en buscadores NO prueba que no haya juicios.

Uso:
    python3 empresas.py "Permanencia Salud" --sitio permanencia.com.ar
    python3 empresas.py "Asistencia del Sol" --salida /var/tmp/empresas
"""

import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request

__all__ = [
    "variantes_de_nombre", "limpiar_sufijo_legal", "extraer_cuits_de_html",
    "extraer_razon_social_de_html", "parsear_rdap", "rdap_dominio",
    "buscar_empresa",
]

# ---------------------------------------------------------------------------
# Piezas puras
# ---------------------------------------------------------------------------

# Sufijos legales ANCLADOS al final del nombre (la alternativa SAS antes que
# SA para que "SAS" no se consuma parcialmente; seguidos de separadores y
# fin de cadena). Sin el ancla, "Sa Salud Srl" perdia el "Sa" inicial.
SUFIJOS_LEGALES = re.compile(
    r"(s\.?\s*r\.?\s*l\.?|s\.?\s*a\.?\s*s\.?|s\.?\s*a\.?|s\.?\s*h\.?|"
    r"sociedad de responsabilidad limitada|sociedad anonima)\s*\.?\s*$",
    re.IGNORECASE)

# Prefijos validos de CUIT/CUIL/CUCDI de personas y empresas (AFIP).
RE_CUIT = re.compile(r"\b(?:20|23|24|27|30|33|34)-\d{8}-\d\b")

RE_PIE_COPYRIGHT = re.compile(
    r"©\s*(?:20\d{2}(?:\s*[-–]\s*20\d{2})?)\s*:?\s*([^<|•\n]{3,80})",
    re.IGNORECASE)

# Frases comunes de cierre del pie de pagina que no son parte de la razon
# social ("Permanencia Salud Srl. Todos los derechos reservados.").
RE_CIERRE_PIE = re.compile(
    r"\s+(?:todos los derechos|copyright|all rights|derechos reservados)"
    r"[\s\S]*$", re.IGNORECASE)


def limpiar_sufijo_legal(nombre: str) -> str:
    """Quita el sufijo legal del FINAL del nombre ('Permanencia Salud Srl'
    -> 'Permanencia Salud'). Devuelve el nombre limpio; si no hay sufijo,
    devuelve el nombre original."""
    n = re.sub(r"\s+", " ", nombre.strip())
    sin = SUFIJOS_LEGALES.sub("", n).strip()
    return sin or n


def variantes_de_nombre(nombre: str) -> list:
    """Variantes de busqueda ordenadas: nombre original y nombre sin sufijo
    legal, sin duplicados. CuitOnline indexa por el nombre como se declaro
    en AFIP, asi que probar ambas cubre el caso del nombre legal distinto al
    comercial (verificado: 'Permanencia Salud SRL' y 'Permanencia Salud'
    dan el mismo resultado de busqueda)."""
    original = re.sub(r"\s+", " ", nombre.strip())
    sin = limpiar_sufijo_legal(original)
    variantes = []
    for v in (original, sin):
        if v and v not in variantes:
            variantes.append(v)
    return variantes


def extraer_cuits_de_html(html: str) -> set:
    """Todos los CUIT/CUIL (XX-XXXXXXXX-X) presentes en el HTML."""
    return set(RE_CUIT.findall(html or ""))


def extraer_razon_social_de_html(html: str, nombre_buscado: str = "") -> str:
    """Razon social declarada en el pie de pagina ('© 2025 <razon social>'),
    comun en las webs de empresas. Devuelve el texto tras el copyright,
    recortado por las frases de cierre tipicas, o '' si no hay pie."""
    m = RE_PIE_COPYRIGHT.search(html or "")
    if not m:
        return ""
    texto = RE_CIERRE_PIE.sub("", m.group(1))
    return re.sub(r"\s+", " ", texto).strip().rstrip(".,")


def parsear_rdap(datos: dict) -> dict:
    """Campos NO personales del JSON RDAP de NIC.AR: registrador, fecha de
    creacion y expiracion. Los contactos (nombre, email, telefono del
    titular) se EXCLUYEN a proposito (P0.9: nunca difundir datos personales
    aunque sean publicos en el whois)."""
    res = {"registrador": "", "creado": "", "expira": ""}
    eventos = {e.get("eventAction", ""): e.get("eventDate", "")
               for e in datos.get("events", [])}
    res["creado"] = eventos.get("registration", "") or eventos.get("created", "")
    res["expira"] = eventos.get("expiration", "")
    for entidad in datos.get("entities", []):
        rol = " ".join(entidad.get("roles", []))
        if "registrar" in rol.lower():
            vcard = entidad.get("vcardArray", [[], []])[1]
            for item in vcard:
                if item and item[0] in ("fn", "org"):
                    res["registrador"] = str(item[3])
                    break
            if res["registrador"]:
                break
    return res


def rdap_dominio(dominio: str, timeout_s: float = 20.0) -> dict:
    """Consulta RDAP de NIC.AR para el dominio (sin datos personales).
    Devuelve {"ok": bool, ...} con registrador/fechas o el error."""
    dominio = dominio.lower().rstrip(".")
    if not dominio or "." not in dominio:
        return {"ok": False, "error": "dominio invalido"}
    try:
        req = urllib.request.Request(
            f"https://rdap.nic.ar/domain/{urllib.parse.quote(dominio)}",
            headers={"User-Agent": "better-ocr-empresas"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return {"ok": True, **parsear_rdap(json.loads(r.read().decode()))}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:90]}"}


# ---------------------------------------------------------------------------
# Orquestacion (Playwright perezoso + buscador.py)
# ---------------------------------------------------------------------------

def _verificar_sitio(pagina, sitio: str, timeout_s: float = 45.0,
                     reintentos: int = 2) -> dict:
    """Visita la web oficial: vigencia, titulo, CUITs y razon social del
    HTML. Reintenta la navegacion (la red de esta maquina es intermitente,
    leccion 20: ERR_NETWORK_CHANGED y timeouts transitorios). Devuelve el
    estado con los hallazgos (el HTML crudo se guarda en --salida por el
    llamador)."""
    url = sitio if "://" in sitio else "https://" + sitio
    error = ""
    for _ in range(reintentos + 1):
        try:
            pagina.goto(url, timeout=timeout_s * 1000,
                        wait_until="domcontentloaded")
            pagina.wait_for_timeout(2500)
            error = ""
            break
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:90]}"
            time.sleep(2)
    if error:
        return {"ok": False, "error": error}
    try:
        html = pagina.content()
    except Exception:
        return {"ok": False, "error": "no se pudo leer el contenido"}
    titulo = ""
    try:
        titulo = (pagina.title() or "").strip()
    except Exception:
        pass
    return {
        "ok": True,
        "url": pagina.url,
        "titulo": titulo,
        "cuits": sorted(extraer_cuits_de_html(html)),
        "razon_social": extraer_razon_social_de_html(html),
        "_html": html,
    }


def _armar_juicios(consulta: str) -> list:
    """Consultas de dorks de juicios sobre la razon social."""
    base = f'"{consulta}"'
    return [f"{base} juicio", f"{base} fallo", f"{base} demanda",
            f"{base} sentencia"]


def buscar_empresa(nombre: str, sitio: str = "", motores: list = None,
                   captcha: bool = False, headed: bool = False,
                   salida_dir: str = "", locale: str = "es-AR",
                   con_juicios: bool = True) -> dict:
    """Ciclo completo de verificacion de una empresa. Devuelve el informe
    con secciones: variantes, cuitonline, dateas, sitio_oficial, rdap,
    web_general, juicios, sintesis. Cada paso es independiente."""
    from buscador import (buscar_en_web, buscar_recetas,
                          extraer_resultados_cuitonline, detectar_bloqueo)

    if motores is None:
        motores = ["bing"]

    informe = {
        "empresa": nombre,
        "variantes": variantes_de_nombre(nombre),
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cuitonline": [],
        "dateas": None,
        "sitio_oficial": None,
        "rdap": None,
        "web_general": [],
        "juicios": [],
        "sintesis": {},
    }

    # 1) CuitOnline con variantes + Dateas (via recetas de buscador.py).
    # La red de esta maquina es intermitente (leccion 20: ERR_NETWORK_CHANGED
    # transitorio): se reintenta cada variante antes de reportar error.
    for variante in informe["variantes"]:
        estados = []
        for _ in range(3):
            estados = buscar_recetas(variante, recetas=["cuit"],
                                     headed=headed, salida_dir=salida_dir,
                                     locale=locale)
            if all(e.get("estado") != "error" for e in estados):
                break
            time.sleep(2)
        for e in estados:
            if e["fuente"] == "cuitonline":
                informe["cuitonline"].append({"consulta": variante, **e})
            elif e["fuente"] == "dateas" and informe["dateas"] is None:
                informe["dateas"] = e

    # 2) Web oficial
    if sitio:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            navegador = pw.chromium.launch(headless=not headed)
            contexto = navegador.new_context(locale=locale,
                                             viewport={"width": 1280,
                                                       "height": 900})
            pagina = contexto.new_page()
            try:
                sit = _verificar_sitio(pagina, sitio)
                html = sit.pop("_html", "")
                if salida_dir:
                    os.makedirs(salida_dir, exist_ok=True)
                    try:
                        with open(os.path.join(salida_dir, "sitio.html"),
                                  "w", encoding="utf-8") as f:
                            f.write(html)
                    except OSError:
                        pass
                informe["sitio_oficial"] = sit
                # 3) RDAP del dominio de la web oficial (sin el www: el
                # subdominio www de nic.ar es un host distinto del dominio)
                dominio = urllib.parse.urlsplit(
                    sit.get("url") or sitio).netloc
                if dominio.startswith("www."):
                    dominio = dominio[4:]
                if dominio:
                    informe["rdap"] = rdap_dominio(dominio)
            finally:
                navegador.close()

    # 4) Busqueda web general con la razon social
    informe["web_general"] = buscar_en_web(
        nombre, motores=motores, captcha=captcha, headed=headed,
        salida_dir=salida_dir, locale=locale)

    # 5) Dorks de juicios
    if con_juicios:
        for consulta in _armar_juicios(informe["variantes"][0]):
            estados = buscar_en_web(consulta, motores=motores,
                                    captcha=False, headed=headed,
                                    salida_dir=salida_dir, locale=locale)
            informe["juicios"].append({"consulta": consulta,
                                       "motores": estados})

    # 6) Sintesis
    informe["sintesis"] = _sintetizar(informe)
    return informe


def _sintetizar(informe: dict) -> dict:
    """Resumen legible del informe: CUIT encontrado (primera aparicion en
    cuitonline/sitio), senales de actividad y limitaciones honestas."""
    cuits = []
    for e in informe["cuitonline"]:
        for r in e.get("resultados", []):
            if r.get("cuit"):
                cuits.append({"cuit": r["cuit"], "razon_social": r["titulo"],
                              "fuente": f"cuitonline ({e['consulta']})"})
    if informe["sitio_oficial"] and informe["sitio_oficial"].get("cuits"):
        for c in informe["sitio_oficial"]["cuits"]:
            cuits.append({"cuit": c, "razon_social": "",
                          "fuente": "sitio oficial"})
    razon_social_sitio = ""
    if informe["sitio_oficial"]:
        razon_social_sitio = informe["sitio_oficial"].get("razon_social", "")

    senales = []
    if informe["cuitonline"] and any(
            e.get("resultados") for e in informe["cuitonline"]):
        senales.append("indexada en CuitOnline")
    if informe["sitio_oficial"] and informe["sitio_oficial"].get("ok"):
        senales.append("web oficial activa")
    if informe["rdap"] and informe["rdap"].get("ok"):
        senales.append("dominio registrado (RDAP NIC.AR)")
    web_ok = [m for m in informe["web_general"]
              if m.get("estado") == "ok" and m.get("resultados")]
    if web_ok:
        senales.append("resultados en busqueda web general")

    return {
        "cuits": cuits,
        "razon_social_declarada_en_web": razon_social_sitio,
        "senales_actividad": senales,
        "limitaciones": [
            "CuitOnline no encontrada no prueba que el nombre legal difiera: "
            "puede no estar indexada. El CUIT exacto debe pedirse por escrito "
            "a la empresa.",
            "Los expedientes judiciales laborales argentinos (CNAT, SECLO, "
            "juzgados) no son buscables publicamente por razon social: la "
            "ausencia en buscadores NO prueba que no haya juicios. Chequeo "
            "real: informe de antecedentes judiciales con el CUIT exacto.",
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resumen_consola(informe: dict) -> None:
    print(f"== Empresa: {informe['empresa']}")
    print(f"   Variantes: {', '.join(informe['variantes'])}")
    print("\n== CuitOnline")
    for e in informe["cuitonline"]:
        n = len(e.get("resultados", []))
        extra = f" ({e.get('bloqueo', '')})" if e.get("bloqueo") else ""
        print(f"  [{e['consulta']:<28}] {e['estado']:<10} {n} resultados{extra}")
        for r in e.get("resultados", [])[:3]:
            print(f"      {r['titulo'][:55]} — {r.get('cuit', '')}")
    print("\n== Dateas")
    d = informe["dateas"]
    if d:
        extra = f" ({d.get('bloqueo', '')})" if d.get("bloqueo") else ""
        print(f"  [{d['estado']}] {d['tiempo_s']}s{extra}")
    print("\n== Sitio oficial")
    s = informe["sitio_oficial"]
    if s:
        if s.get("ok"):
            print(f"  activa — {s['titulo'][:70]}")
            if s.get("razon_social"):
                print(f"  razon social declarada: {s['razon_social']}")
            if s.get("cuits"):
                print(f"  CUITs en la web: {', '.join(s['cuits'])}")
            else:
                print("  sin CUIT publicado en el HTML")
        else:
            print(f"  no accesible — {s.get('error', '')[:70]}")
    else:
        print("  (sin --sitio)")
    print("\n== RDAP")
    r = informe["rdap"]
    if r:
        if r.get("ok"):
            print(f"  registrador: {r['registrador'] or 'n/d'} | "
                  f"creado: {r['creado'][:10]}")
        else:
            print(f"  {r.get('error', '')[:70]}")
    else:
        print("  (sin --sitio)")
    print("\n== Busqueda web general")
    for m in informe["web_general"]:
        extra = f" ({m.get('bloqueo', '')})" if m.get("bloqueo") else ""
        print(f"  [{m['motor']:<10}] {m['estado']:<10} "
              f"{len(m.get('resultados', []))} resultados{extra}")
    print("\n== Juicios (dorks; limitado, ver informe)")
    for j in informe["juicios"]:
        n = sum(len(m.get("resultados", [])) for m in j["motores"])
        print(f"  {j['consulta'][:55]:<57} -> {n} resultados en total")
    sin = informe["sintesis"]
    print("\n== Sintesis")
    for c in sin["cuits"]:
        print(f"  CUIT: {c['cuit']} ({c['razon_social'] or c['fuente']})")
    if not sin["cuits"]:
        print("  CUIT: NO ENCONTRADO")
    if sin["razon_social_declarada_en_web"]:
        print(f"  Razon social (pie de web): {sin['razon_social_declarada_en_web']}")
    if sin["senales_actividad"]:
        print("  Senales: " + "; ".join(sin["senales_actividad"]))
    for lim in sin["limitaciones"]:
        print(f"  ! {lim}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Busqueda de empresas: CUIT, razon social, senales de "
                    "actividad y juicios (multi-motor + CuitOnline + RDAP)")
    parser.add_argument("empresa", help="razon social o nombre comercial")
    parser.add_argument("--sitio", default="",
                        help="web oficial conocida (p. ej. permanencia.com.ar)")
    parser.add_argument("--motores", default="",
                        help="motores de busqueda web general separados por "
                             "comas (default: bing)")
    parser.add_argument("--captcha", action="store_true",
                        help="intentar resolver reCAPTCHA v2 con el stack "
                             "local en la busqueda web general")
    parser.add_argument("--headed", action="store_true",
                        help="navegador visible (default: headless)")
    parser.add_argument("--salida", default="",
                        help="directorio para guardar HTML crudos y el "
                             "informe JSON")
    parser.add_argument("--sin-juicios", action="store_true",
                        help="omitir los dorks de juicios (mas rapido)")
    parser.add_argument("--locale", default="es-AR",
                        help="locale del navegador (es-AR, en-US...)")
    args = parser.parse_args()

    motores = [m.strip() for m in args.motores.split(",") if m.strip()] or None
    informe = buscar_empresa(
        args.empresa, sitio=args.sitio, motores=motores,
        captcha=args.captcha, headed=args.headed, salida_dir=args.salida,
        locale=args.locale, con_juicios=not args.sin_juicios)

    if args.salida:
        os.makedirs(args.salida, exist_ok=True)
        with open(os.path.join(args.salida, "resultado.json"), "w",
                  encoding="utf-8") as f:
            json.dump(informe, f, ensure_ascii=False, indent=2)
    _resumen_consola(informe)
    if args.salida:
        print(f"\n[OK] Informe completo: {args.salida}/resultado.json")


if __name__ == "__main__":
    main()
