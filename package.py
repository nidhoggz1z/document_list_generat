"""Build the deployment tar and a HiAgent OpenAPI import file."""
import json
import io
import os
import sys
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / 'src'))
# Always build from the user-provided list, regardless of local service overrides.
os.environ['DOCUMENT_DIR'] = str(root / 'list')
from main import app
from phrase_rules import snapshot_phrase_library

phrase_library = snapshot_phrase_library(root / 'list')

with tarfile.open(root / 'src.tar', 'w', format=tarfile.PAX_FORMAT) as archive:
    # Only root Python modules and explicit deployment files; never recurse src.
    sources = sorted((root / 'src').glob('*.py'))
    sources += [root / 'src' / name for name in ('requirements.txt', 'start.sh')]
    for path in sources:
        if path.is_symlink() or not path.is_file():
            raise ValueError('Deployment sources must be regular files')
        archive.add(path, arcname=path.name, recursive=False)
    # Package only the active checklists; legacy src/data documents are excluded.
    for path in app.state.store.by_id.values():
        source = path.relative_to(root / 'list').as_posix()
        archive.add(path, arcname='list/' + source, recursive=False)
    # Include the exact validated Excel version, never unrelated spreadsheets.
    info = tarfile.TarInfo('list/' + phrase_library['filename'])
    info.size = len(phrase_library['raw_bytes'])
    archive.addfile(info, io.BytesIO(phrase_library['raw_bytes']))

schema = app.openapi()
schema['servers'] = [{'url': 'http://YOUR_SERVER_IP:8000'}]
schema['paths'] = {'/retrieve': schema['paths']['/retrieve']}
(root / 'openapi-hiagent.json').write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding='utf-8')
print('Generated src.tar and openapi-hiagent.json; documents:', len(app.state.store.documents))
