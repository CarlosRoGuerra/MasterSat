"""Normaliza encoding, newlines, cores e espaços finais dos logs do PowerShell."""
from pathlib import Path
import re

for path in Path(__file__).parent.iterdir():
    if path.suffix not in ('.txt', '.json'):
        continue
    data = path.read_bytes()
    value = data.decode('utf-16') if data.startswith((b'\xff\xfe', b'\xfe\xff')) else data.decode('utf-8-sig')
    value = re.sub(r'\x1b\[[0-9;]*m', '', value).replace('\r\n', '\n')
    if path.suffix == '.txt':
        value = '\n'.join(line.rstrip() for line in value.split('\n')).strip('\n') + '\n'
    path.write_text(value, encoding='utf-8', newline='\n')
