#!/usr/bin/env bash
# Verificación de coherencia del proyecto better-ocr (patrón better-ai: sin
# dependencia de GitHub/CI; verificación local previa a cada commit).
# Uso: bash scripts/verificar-proyecto.sh [--pre-commit]
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0

check() {
    local desc="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "  [OK] $desc"
        PASS=$((PASS + 1))
    else
        echo "  [FALLO] $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "== 1. Reglas =="
check "13 reglas P0 definidas en AGENTS.md" bash -c "test \$(grep -cE '^### P0' AGENTS.md) -eq 13"
check "21 reglas P1 definidas en AGENTS.md" bash -c "test \$(grep -cE '^### P1' AGENTS.md) -eq 21"
check "IDs identicos en REGLAS-COMPLETAS" bash -c "diff <(grep -oE '^### P[0-2]\\.[0-9]+' AGENTS.md | sort -V) <(grep -oE '^### P[0-2]\\.[0-9]+' docs/REGLAS-COMPLETAS.md | sort -V)"
check "titulos de reglas identicos en REGLAS-COMPLETAS" bash -c "diff <(grep -E '^### P0|^### P1' AGENTS.md) <(grep -E '^### P0|^### P1' docs/REGLAS-COMPLETAS.md)"
check "referencias a rutas docs/ scripts/ y ejemplos/ existen" python3 -c "
import re, os
files = ['AGENTS.md', 'README.md', 'CHECKLIST.md', 'docs/REGLAS-COMPLETAS.md', 'docs/GUIA_OCR_VISION.md', 'docs/LECCIONES-APRENDIDAS.md']
externos = {
    'docs/PRUEBAS.md',
    'docs/version3.x/installation.md',
    'docs/version3.x/pipeline_usage/OCR.en.md',
    'docs/version3.x/pipeline_usage/doc_understanding.md',
    'docs/version3.x/module_usage',
}
rutas = set()
for f in files:
    if not os.path.exists(f):
        continue
    # las URLs externas (https://.../docs/...) NO son rutas locales: se quitan
    # antes del scan (p. ej. https://opencode.ai/docs/rules/ en REGLAS-COMPLETAS)
    contenido = re.sub(r'https?://\S+', '', open(f).read())
    for m in re.findall(r'(?:docs/|scripts/|ejemplos/)[A-Za-z0-9_./-]+', contenido):
        if m in externos:
            continue
        rutas.add(m.rstrip('/'))
faltan = [r for r in sorted(rutas) if not os.path.exists(r)]
assert not faltan, 'referencias rotas: ' + str(faltan)
"
check "ningun .env versionado en git" bash -c "test -z \"\$(git ls-files | grep -E '\\.env(\$|\\.)' | grep -v '\\.env\\.example')\""
check "sin dependencia de GitHub Actions (.github eliminado)" bash -c "test ! -d .github"
check "IDs citados en CHECKLIST y README existen en AGENTS.md" bash -c "test -z \"\$(comm -23 <(grep -ohE 'P[0-2]\\.[0-9]+' CHECKLIST.md README.md | sort -u) <(grep -ohE 'P[0-2]\\.[0-9]+' AGENTS.md | sort -u))\""
check "sin referencias obsoletas en AGENTS.md/README.md (master, GitHub Actions, better-ia)" bash -c "! grep -E '(GitHub Actions|branches: \\[master\\]|push a \`master\`|better-ia|\\.github/workflows)' AGENTS.md README.md"

echo "== 2. Sintaxis y pruebas =="
check "sintaxis: extractor_final.py" python3 -m py_compile extractor_final.py
check "sintaxis: chart_server.py" python3 -m py_compile chart_server.py
check "sintaxis: ocr_rapido.py" python3 -m py_compile ocr_rapido.py
check "sintaxis: vision.py" python3 -m py_compile vision.py
check "sintaxis: captcha_ia.py" python3 -m py_compile captcha_ia.py
check "sintaxis: captcha_web.py" python3 -m py_compile captcha_web.py
check "sintaxis: revision.py" python3 -m py_compile revision.py
check "sintaxis: buscador.py" python3 -m py_compile buscador.py
check "sintaxis: empresas.py" python3 -m py_compile empresas.py
check "sintaxis: judiciales.py" python3 -m py_compile judiciales.py
check "sintaxis: analizar_cuit.py" python3 -m py_compile analizar_cuit.py
check "sintaxis: buscador_empresas.py" python3 -m py_compile buscador_empresas.py
check "sintaxis: rns.py" python3 -m py_compile rns.py
check "sintaxis: auditoria_graficos.py" python3 -m py_compile auditoria_graficos.py
check "tests unitarios (stdlib + pandas)" python3 -m unittest discover -s tests -q

echo "== 3. Config =="
check "opencode.json es JSON valido" python3 -c "import json; json.load(open('opencode.json'))"
check "245 patrones de permisos bash (159 deny, 85 ask)" python3 -c "
import json
b = json.load(open('opencode.json'))['permission']['bash']
assert len(b) == 245, len(b)
assert sum(1 for v in b.values() if v == 'deny') == 159
assert sum(1 for v in b.values() if v == 'ask') == 85
"
check "edit/read bloquean claves y credenciales" python3 -c "
import json
p = json.load(open('opencode.json'))['permission']
# deny: patrones de claves y credenciales (listados para el scan de seguridad)
for sec in ('edit', 'read'):
    for pat in ('~/.ssh/*', '*.ssh/*', '~/.aws/*', '*.aws/*', '*.pem', '*id_rsa*', '*id_ed25519*', '*credentials*'):  # deny: patrones
        assert p[sec].get(pat) == 'deny', (sec, pat)
"
check "enabled_providers restringe a opencode y opencode-go" python3 -c "
import json
c = json.load(open('opencode.json'))
assert c.get('enabled_providers') == ['opencode', 'opencode-go'], c.get('enabled_providers')
"
check "edit/read bloquean .env y permiten .env.example" python3 -c "
import json
p = json.load(open('opencode.json'))['permission']
assert p['edit'].get('*.env') == 'deny'
assert p['edit'].get('*.env.*') == 'deny'
assert p['edit'].get('*.env.example') == 'allow'
assert p['read'].get('*.env') == 'deny'
assert p['read'].get('*.env.*') == 'deny'
assert p['read'].get('*.env.example') == 'allow'
"
check "pares criticos deny presentes" python3 -c "
import json
k = list(json.load(open('opencode.json'))['permission']['bash'])
pares = [
    ('rm *', 'rm -rf *'), ('rm *', 'rm -r *'), ('rm *', 'rm -f *'),
    ('git reset *', 'git reset --hard*'),
    ('git push *', 'git push --force*'),
    ('mv *', 'mv --force*'), ('mv *', 'mv -f *'),
    ('rsync *', 'rsync --delete*'),
    ('docker compose down*', 'docker compose down -v*'),
    ('pip install *', 'pip install --user *'),
    ('psql -c *', 'psql * *DROP*'), ('psql -c *', 'psql * *TRUNCATE*'),
    ('psql -c *', 'psql * *DELETE*'), ('psql -c *', 'psql * *ALTER*'),
    ('mysql -e *', 'mysql * *DROP*'), ('mysql -e *', 'mysql * *TRUNCATE*'),
    ('mysql -e *', 'mysql * *DELETE*'), ('mysql -e *', 'mysql * *ALTER*'),
    ('sqlite3 *', 'sqlite3 * *DROP*'), ('sqlite3 *', 'sqlite3 * *TRUNCATE*'),
    ('sqlite3 *', 'sqlite3 * *DELETE*'), ('sqlite3 *', 'sqlite3 * *ALTER*'),
    ('redis-cli *', 'redis-cli FLUSHALL*'),
    ('redis-cli *', 'redis-cli * FLUSHALL*'),
    ('redis-cli *', 'redis-cli * *DEL*'),
]
for ask, deny in pares:
    assert ask in k, 'falta ask: ' + ask
    assert deny in k, 'falta deny: ' + deny
"
check "ningun ask posterior anula un deny (todas las familias)" python3 -c "
import json, re
# Mini-matcher que replica la semantica del matcher de opencode (doc oficial de
# Permissions: wildcard '*' = cero o mas caracteres; lecciones de las rondas 3/4/8/28:
# se evalua el PRIMER segmento del pipeline; 'rm *' matchea 'rm -rf x'; 'redis-cli *
# FLUSHALL*' NO matchea 'redis-cli FLUSHALL').
def matchea(patron, comando):
    segmento = comando.split('|')[0]
    regex = '^' + re.escape(patron).replace('\\\\*', '.*') + '$'
    return re.match(regex, segmento) is not None
_cfg = json.load(open('opencode.json'))['permission']['bash']
k = list(_cfg)
rellenos = ['X', '-C', '--', 'x']
fallos = []
for i, deny in enumerate(k):
    if _cfg[deny] != 'deny':
        continue
    variantes = set()
    for r in rellenos:
        v = ' '.join(r if t == '*' else t for t in deny.split())
        if matchea(deny, v):
            variantes.add(v)
    for j in range(i + 1, len(k)):
        ask = k[j]
        if _cfg[ask] != 'ask':
            continue
        for v in variantes:
            if matchea(ask, v):
                fallos.append((deny, ask, v))
assert not fallos, 'ask posterior anula deny: ' + '; '.join(f'{d} / {a} para {v}' for d, a, v in fallos)
"

echo "== 4. Seguridad (P0.9/P0.10) =="
# Nota: se excluyen los placeholders del red-team (scripts/probar-denies.sh):
# 'dummy*' (archivos de prueba), '127.0.0.1' (loopback de la comprobacion de redis,
# no es informacion personal) y 'dummy@example.com' (email del git dummy).
check "sin IPs, claves, rutas .ssh o rutas de usuario en archivos" python3 -c "
import os, re, ipaddress
pat = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
# rutas de usuario reales: /home/<nombre>/ (el placeholder /home/<usuario>/ no matchea)
pat_home = re.compile(r'/home/[A-Za-z0-9_.-]+/')
excl = re.compile(r'(deny|patrones|claves SSH|no leas|comitees|dummy|BLOQUEADO|127\.0\.0\.1)')
faltas = []
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in ('.git', '.venv', 'node_modules', '__pycache__', 'datos-rns')]
    for f in files:
        if not f.endswith(('.md', '.json', '.sh')):
            continue
        ruta = os.path.join(root, f)
        for i, linea in enumerate(open(ruta, errors='ignore'), 1):
            if excl.search(linea):
                continue
            if pat_home.search(linea):
                faltas.append((ruta, i, 'ruta de usuario: ' + pat_home.search(linea).group(0)))
            for m in pat.findall(linea):
                try:
                    ip = ipaddress.ip_address(m)
                except ValueError:
                    continue  # no es una IP real (p. ej. numero de version 1.18.10)
                if not ip.is_loopback:
                    faltas.append((ruta, i, 'IP: ' + m))
assert not faltas, faltas
"
check "sin emails personales en archivos" bash -c "! grep -rnE --exclude-dir=.venv --exclude-dir=node_modules '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}' --include='*.md' --include='*.json' --include='*.sh' . | grep -v '\\.git/' | grep -qvE '(youremail@example|creativecommons|dummy@example)'"
check "sin formatos de claves API en archivos" bash -c "! grep -rnE --exclude-dir=.venv --exclude-dir=node_modules '(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|xox[baprs]-[0-9A-Za-z-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)' --include='*.md' --include='*.json' --include='*.sh' . | grep -v '\\.git/'"
# Los unicos 'eval'/'exec' en scripts son: la VARIANTE de prueba del deny 'eval *' en
# probar-denies.sh (el comando bajo prueba, no codigo que el script ejecute), el
# 'exec bwrap' de opencode-sandbox.sh (exec estandar de bash para sustituir el
# proceso) y el propio patron de este check en verificar-proyecto.sh.
check "sin eval/exec en scripts" python3 -c "
import os, re
pat = re.compile(r'\b(eval|exec)\b')
for f in sorted(os.listdir('scripts')):
    if not f.endswith('.sh'):
        continue
    if f in ('probar-denies.sh', 'opencode-sandbox.sh', 'verificar-proyecto.sh'):
        continue  # documentados arriba; este check vigila los demas scripts
    for i, linea in enumerate(open('scripts/' + f), 1):
        if linea.lstrip().startswith('#'):
            continue
        assert not pat.search(linea), (f, i, linea)
"
check "agentes de solo lectura con edit deny" bash -c "for a in security-auditor code-reviewer; do grep -q 'edit: deny' .opencode/agents/\$a.md && grep -q 'mode: subagent' .opencode/agents/\$a.md || exit 1; done"

echo "== 5. Repositorio =="
check "hook pre-commit instalado identico al script" bash -c "cmp -s scripts/hooks/pre-commit .git/hooks/pre-commit"
check "sin objetos huerfanos en git (fsck)" bash -c "test -z \"\$(git fsck --unreachable 2>&1)\""
if [ "${1:-}" = "--pre-commit" ]; then
    echo "  [SKIP] comprobaciones de repositorio (modo pre-commit: los archivos staged son el cambio)"
else
    check "arbol de trabajo limpio" bash -c "test -z \"\$(git status --porcelain)\""
    check "rama main sincronizada con origin" bash -c "test -z \"\$(git status --porcelain --branch | grep -E 'adelant|ahead|behind|adelanta')\""
    check "HEAD remoto apunta a main" bash -c "test \"\$(git ls-remote origin HEAD | cut -f1)\" = \"\$(git ls-remote origin refs/heads/main | cut -f1)\""
fi

echo
echo "Resultado: $PASS OK, $FAIL FALLOS"
[ "$FAIL" -eq 0 ] || exit 1
