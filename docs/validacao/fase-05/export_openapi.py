import json
from pathlib import Path
from app.main import app

Path('/evidence/openapi.json').write_text(json.dumps(app.openapi(), ensure_ascii=False), encoding='utf-8')
