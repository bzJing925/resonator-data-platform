"""Bootstrap pip from a bundled wheel into the private CPython runtime."""
from pathlib import Path
import sys
import zipfile

root = Path(__file__).resolve().parent
site = root / 'runtime/Lib/site-packages'
site.mkdir(parents=True, exist_ok=True)
wheel = next((root / 'wheelhouse').glob('pip-*.whl'))
with zipfile.ZipFile(wheel) as archive:
    archive.extractall(site)
print('Private runtime ready:', sys.executable)
