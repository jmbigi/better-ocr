#!/usr/bin/env python3
"""Replay del corpus de fallos: re-evalua los casos guardados (caso_*.json +
capturas) con distintas configuraciones usando las detecciones RT-DETR ya
guardadas y las capturas del corpus — sin ejecuciones en vivo.

Configuraciones comparadas por caso:
  solo   : RT-DETR (sin VLM)
  docbee : + confirmacion de candidatos con PP-DocBee-2B (GPU, un lote)
  gemma  : + confirmacion de candidatos con gemma3:4b (ollama, opcional)

Referencia por clase (tamano tipico de seleccion, de los runs exitosos
2026-08-07): la config con mas selecciones "plausibles" es la mejor segun
este proxy (no hay ground truth por caso).

Uso: .venv/bin/python scripts/replay_fallos.py --corpus /var/tmp/captcha_fallos
     [--solo-casos caso_p2_i3.json ...] [--con-gemma] [--sin-docbee]
"""

import argparse
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import captcha_web  # noqa: E402
from captcha_ia import aumentar_escala, celdas_grid, resolver  # noqa: E402

# Tamano tipico de seleccion por clase (runs exitosos 2026-08-07)
TIPICOS = {"car": 3, "bus": 3, "bicycle": 3, "motorcycle": 3,
           "traffic light": 3, "crosswalk": 4, "fire hydrant": 3}


def detecciones_rtdetr(caso: dict) -> dict:
    """Detecciones del worker SOLO (descarta los score 1.0 = adiciones VLM)."""
    out = {}
    for k, v in caso.get("detecciones_por_celda", {}).items():
        f, c = map(int, k.split(","))
        out[(f, c)] = [d for d in v if d.get("score", 0) < 1.0]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="/var/tmp/captcha_fallos")
    parser.add_argument("--solo-casos", default="",
                        help="subconjunto de archivos caso_*.json (separados por coma)")
    parser.add_argument("--con-gemma", action="store_true",
                        help="incluye la confirmacion con gemma3:4b (ollama)")
    parser.add_argument("--vlm-modelo", default="gemma3:4b",
                        help="modelo ollama para la confirmacion (p. ej. "
                             "qwen2.5vl:7b)")
    parser.add_argument("--sin-docbee", action="store_true",
                        help="omite la confirmacion con docbee")
    args = parser.parse_args()

    rutas = sorted(glob.glob(os.path.join(args.corpus, "caso_*.json")))
    if args.solo_casos:
        sel = set(args.solo_casos.split(","))
        rutas = [r for r in rutas if os.path.basename(r) in sel]
    casos = []
    for ruta in rutas:
        try:
            with open(ruta, encoding="utf-8") as f:
                caso = json.load(f)
            caso["_archivo"] = os.path.basename(ruta)
            casos.append(caso)
        except (OSError, json.JSONDecodeError):
            continue
    if not casos:
        print("sin casos en", args.corpus)
        return

    # 1) preparar celdas y candidatos por caso
    preparados = []
    candidatos_docbee = []  # (ruta_png, clase) para el lote unico
    for caso in casos:
        clase = caso.get("clase_objetivo")
        n = caso.get("n")
        captura = caso.get("captura", "")
        im = None
        celdas = []
        if captura and os.path.exists(os.path.join(args.corpus, captura)):
            im = Image.open(os.path.join(args.corpus, captura))
            celdas = [(f, c, aumentar_escala(celda))
                      for f, c, celda in celdas_grid(im, n=n)]
        base = detecciones_rtdetr(caso)
        umbral = captcha_web.umbral_objetivo_para(n)
        candidatas = [(f, c) for (f, c), v in base.items()
                      if any(d.get("clase") == clase and d.get("score", 0) >= umbral
                             for d in v)]
        preparados.append({
            "archivo": caso["_archivo"],
            "n": n, "clase": clase,
            "instruccion": caso.get("instruccion", ""),
            "orig": len(caso.get("seleccion", [])),
            "im": im, "celdas": celdas, "base": base, "umbral": umbral,
            "candidatas": candidatas,
        })
        if not args.sin_docbee and celdas and clase:
            mapa_celdas = {(f, c): celda for f, c, celda in celdas}
            for (f, c) in candidatas:
                ruta_png = os.path.join(
                    args.corpus, f"replay_{caso['_archivo']}_{f}_{c}.png")
                mapa_celdas[(f, c)].save(ruta_png)
                candidatos_docbee.append(
                    (ruta_png, clase, caso["_archivo"], f, c))

    # 2) docbee en UN lote (una carga del modelo)
    docbee_yes = {}
    if candidatos_docbee and not args.sin_docbee:
        script = captcha_web.WORKER_DOCBEE % {
            "raiz": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "patch": captcha_web.PATCH_DOCBEE,
        }
        proc = subprocess.run(
            [captcha_web.VENV_PYTHON, "-c", script],
            input=json.dumps([(r, c) for r, c, _, _, _ in candidatos_docbee]),
            capture_output=True, text=True, timeout=1800,
            env=captcha_web.env_worker())
        if proc.returncode != 0:
            print("docbee FALLO:", proc.stderr[-300:])
        else:
            try:
                salida = json.loads(proc.stdout)
                for ruta_png, clase, archivo, f, c in candidatos_docbee:
                    if captcha_web.parsear_respuesta_vlm(
                            salida.get(ruta_png, "")) is True:
                        docbee_yes[(archivo, f, c)] = True
            except json.JSONDecodeError:
                print("docbee salida invalida")

    # 3) gemma opcional (ollama, por celda)
    gemma_yes = {}
    if args.con_gemma:
        for p in preparados:
            if not p["celdas"]:
                continue
            mapa_celdas = {(f, c): celda for f, c, celda in p["celdas"]}
            for (f, c) in p["candidatas"]:
                try:
                    imagen = mapa_celdas[(f, c)]
                    texto = captcha_web._preguntar_ollama(
                        imagen, p["clase"], "127.0.0.1", args.vlm_modelo, 90)
                    if captcha_web.parsear_respuesta_vlm(texto) is True:
                        gemma_yes[(p["archivo"], f, c)] = True
                except Exception:
                    continue

    # 4) decision por config
    def decidir(p, confirmadas, aplicar_confirmacion=True):
        dets = dict(p["base"])
        if aplicar_confirmacion:
            for (f, c) in p["candidatas"]:
                if (p["archivo"], f, c) not in confirmadas:
                    dets[(f, c)] = []
        if p["im"] is None:
            return None

        def detectar_celda(_c, f, c):
            return dets.get((f, c), [])
        res = resolver(p["im"], p["instruccion"], detectar_celda, n=p["n"],
                       umbral_objetivo=p["umbral"], umbral_resto=0.6)
        return len(res.get("seleccion", [])) if res.get("ok") else -1

    print(f"{'caso':<28}{'n':<3}{'clase':<16}{'orig':<5}{'solo':<6}"
          f"{'docbee':<7}{'gemma':<6}  referencia")
    for p in preparados:
        solo = decidir(p, {}, aplicar_confirmacion=False)
        docbee = decidir(p, docbee_yes) if not args.sin_docbee else None
        gemma = decidir(p, gemma_yes) if args.con_gemma else None
        tip = TIPICOS.get(p["clase"], 3)
        print(f"{p['archivo']:<28}{p['n']:<3}{str(p['clase']):<16}"
              f"{p['orig']:<5}{str(solo):<6}{str(docbee):<7}{str(gemma):<6}  ~{tip}")

    # 5) resumen por config: plausible (tip±1) / bajo / sobre
    def resumen(clave, getter):
        buenas = bajas = sobre = 0
        for p in preparados:
            v = getter(p)
            if v is None or v < 0:
                continue
            tip = TIPICOS.get(p["clase"], 3)
            if v < tip - 1:
                bajas += 1
            elif v > tip + 1:
                sobre += 1
            else:
                buenas += 1
        return buenas, bajas, sobre

    print()
    for nombre, getter, aplica in (
            ("solo  ", lambda p: decidir(p, {}, aplicar_confirmacion=False), False),
            ("docbee", lambda p: decidir(p, docbee_yes), True),
            ("gemma ", lambda p: decidir(p, gemma_yes), True)):
        if nombre.strip() == "docbee" and args.sin_docbee:
            continue
        if nombre.strip() == "gemma" and not args.con_gemma:
            continue
        buenas, bajas, sobre = resumen(nombre, getter)
        print(f"{nombre}: plausibles={buenas} bajas={bajas} sobre={sobre}")


if __name__ == "__main__":
    main()
