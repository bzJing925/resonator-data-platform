"""Windows native server entry; Task Scheduler supervises the process."""
import json
import logging.handlers
import multiprocessing
import os
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    cfg = json.loads((root / 'server-config.json').read_text(encoding='utf-8-sig'))
    data = Path(cfg['data_dir']).resolve()
    data.mkdir(parents=True, exist_ok=True)
    os.environ.update(ALN_DESKTOP_MODE='true', ALN_SERVER_MODE='true',
                      ALN_DESKTOP_DIR=str(data), DATA_ROOT=str(data), DEBUG='false',
                      DOTENV_PATH=str(root / 'no-dotenv'), WATCH_ENABLED='false',
                      KEEP_RAW_ZIP='true', PIPELINE_COMPRESS_RAW='false',
                      ALN_DISABLE_TORCH=('1' if (root / 'torch-disabled.flag').exists() else '0'),
                      SURROGATE_DATA_DIR=str(root / 'backend/surrogate_data'),
                      SURROGATE_CKPT_DIR=str(root / 'backend/checkpoints_surrogate'))
    logs = data / 'logs'
    logs.mkdir(exist_ok=True)
    os.environ['MPLCONFIGDIR'] = str(data / 'matplotlib-cache')
    import sys
    sys.path.insert(0, str(root / 'backend'))
    handler = logging.handlers.RotatingFileHandler(logs / 'server.log', maxBytes=10_000_000,
                                                   backupCount=10, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()],
                        format='%(asctime)s %(levelname)s %(name)s %(message)s', force=True)
    import uvicorn
    uvicorn.run('app.main:app', host=cfg.get('host', '0.0.0.0'),
                port=int(cfg.get('port', 8000)), log_config=None)


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
