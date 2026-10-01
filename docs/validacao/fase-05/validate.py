"""Executar em /app no container de testes, com /evidence montado."""
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

out = Path('/evidence')
commands = {
    'nfse-tests': [sys.executable, '-m', 'pytest', *sorted(str(p) for p in Path('tests').glob('test_nfse*.py')),
                   'tests/test_fase05_postgres.py', '-q', '-p', 'no:cacheprovider'],
    'full-suite': [sys.executable, '-m', 'pytest', 'tests', '-q', '-p', 'no:cacheprovider',
                   '--junitxml=/evidence/full-suite.xml'],
    'baseline-gate': [sys.executable, 'scripts/checar_baseline_testes.py', '/evidence/full-suite.xml'],
}
results = {'sha': os.environ.get('VALIDATED_SHA', 'working-tree'), 'python': platform.python_version(),
           'versions': {name: importlib.metadata.version(name) for name in (
               'signxml', 'cryptography', 'pyOpenSSL', 'lxml', 'SQLAlchemy', 'psycopg', 'pytest', 'fastapi')},
           'commands': []}
nfse_only = '--nfse-only' in sys.argv
if nfse_only:
    commands = {'nfse-final-tests': commands['nfse-tests']}
result_file = 'nfse-final-results.json' if nfse_only else 'test-results.json'
for name, command in commands.items():
    with (out / f'{name}.txt').open('w', encoding='utf-8') as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
    results['commands'].append({'name': name, 'cwd': '/app', 'command': command, 'exit_code': result.returncode})
    print(name, 'exit', result.returncode, flush=True)
    (out / result_file).write_text(json.dumps(results, indent=2), encoding='utf-8')
if nfse_only:
    sys.exit(results['commands'][0]['exit_code'])
