"""Return original SNP bytes even for compressed historical server files."""
import gzip
import shutil
import tempfile
from pathlib import Path

from fastapi.responses import FileResponse
from starlette.background import BackgroundTask


def raw_file_response(path: Path) -> FileResponse:
    if path.suffix.lower() != '.gz':
        return FileResponse(path, media_type='application/octet-stream', filename=path.name)
    directory = Path(tempfile.mkdtemp(prefix='aln-download-'))
    output = directory / path.stem
    try:
        with gzip.open(path, 'rb') as source, output.open('wb') as dest:
            shutil.copyfileobj(source, dest)
    except Exception:
        shutil.rmtree(directory)
        raise
    return FileResponse(output, media_type='application/octet-stream', filename=path.stem,
                        background=BackgroundTask(shutil.rmtree, directory))
