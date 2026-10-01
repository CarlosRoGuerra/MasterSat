"""
Compara o resultado do pytest (JUnit XML) com tests/baseline_falhas_conhecidas.txt.

Reprova quando:
  - um teste falha (ou dá erro) e não está na lista  → regressão nova;
  - um teste da lista passa                          → remova a linha;
  - um teste da lista não foi executado              → lista desatualizada.
Skips são listados com o motivo, para ficar explícito o que não rodou.

Uso (a partir de backend/):
  python -m pytest tests -q -p no:cacheprovider --junitxml=/tmp/junit.xml
  python scripts/checar_baseline_testes.py /tmp/junit.xml
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

_BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
BASELINE = os.path.join(_BACKEND_DIR, 'tests', 'baseline_falhas_conhecidas.txt')


def _nodeid(classname: str, name: str) -> str:
    # "tests.test_x.TestY" + "test_z" → "tests/test_x.py::TestY::test_z"
    partes = classname.split('.')
    i = next((n for n, p in enumerate(partes) if p.startswith('test_')), len(partes) - 1)
    return '::'.join(['/'.join(partes[: i + 1]) + '.py', *partes[i + 1:], name])


def ler_junit(caminho: str) -> tuple[set[str], set[str], dict[str, str]]:
    falhas: set[str] = set()
    executados: set[str] = set()
    skips: dict[str, str] = {}
    for caso in ET.parse(caminho).getroot().iter('testcase'):
        nodeid = _nodeid(caso.get('classname', ''), caso.get('name', ''))
        executados.add(nodeid)
        if caso.find('failure') is not None or caso.find('error') is not None:
            falhas.add(nodeid)
        skip = caso.find('skipped')
        if skip is not None:
            skips[nodeid] = skip.get('message', '')
    return falhas, executados, skips


def ler_baseline(caminho: str = BASELINE) -> dict[str, str]:
    conhecidas: dict[str, str] = {}
    with open(caminho, encoding='utf-8') as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha or linha.startswith('#'):
                continue
            nodeid, _, causa = linha.partition(' ')
            conhecidas[nodeid] = causa.strip()
    return conhecidas


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    falhas, executados, skips = ler_junit(argv[0])
    conhecidas = ler_baseline()

    novas = sorted(falhas - conhecidas.keys())
    resolvidas = sorted(t for t in conhecidas if t in executados and t not in falhas)
    ausentes = sorted(t for t in conhecidas if t not in executados)

    print(f'Executados: {len(executados)}  falhas: {len(falhas)}  '
          f'conhecidas: {len(falhas & conhecidas.keys())}  skips: {len(skips)}')
    for t, motivo in sorted(skips.items()):
        print(f'  SKIP {t}: {motivo}')
    for t in novas:
        print(f'  NOVA FALHA {t}')
    for t in resolvidas:
        print(f'  PASSOU (remova da lista) {t} [{conhecidas[t]}]')
    for t in ausentes:
        print(f'  NÃO EXECUTADO (lista desatualizada?) {t}')
    if novas or resolvidas or ausentes:
        print('BASELINE REPROVADO')
        return 1
    print('BASELINE OK — só falhas conhecidas e triadas')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
