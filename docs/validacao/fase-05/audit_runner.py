"""Executar no host Docker; captura exit code e JSON sem formatação de shell."""
import json
from pathlib import Path
import subprocess

out = Path(__file__).parent
command = ['docker', 'run', '--rm', 'mastersat-f05-audit']
result = subprocess.run(command, capture_output=True)
(out / 'pip-audit.json').write_bytes(result.stdout)
(out / 'pip-audit.stderr.txt').write_bytes(result.stderr)
(out / 'audit-result.json').write_text(json.dumps({
    'command': command, 'scanner': 'pip-audit 2.10.0',
    'target': '/usr/local/lib/python3.12/site-packages da imagem runtime Fase 05',
    'source_sha': 'a011aa1b00fe42505b67d09b5ab1cbafa28027b5',
    'exit_code': result.returncode,
}, indent=2), encoding='utf-8')
print('pip-audit exit', result.returncode)
