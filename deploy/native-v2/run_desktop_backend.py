import argparse
import multiprocessing
import os
import sys
from pathlib import Path

if __name__ == '__main__':
    multiprocessing.freeze_support()
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / 'backend'))
    # Explicit sys.path is inherited by multiprocessing spawn as well.
    os.environ['ALN_DESKTOP_MODE'] = 'true'
    os.environ['ALN_SERVER_MODE'] = 'false'
    os.environ['PIPELINE_COMPRESS_RAW'] = 'false'
    os.environ['ALN_DISABLE_TORCH'] = '1' if (root / 'torch-disabled.flag').exists() else '0'
    os.environ['SURROGATE_DATA_DIR'] = str(root / 'backend/surrogate_data')
    os.environ['SURROGATE_CKPT_DIR'] = str(root / 'backend/checkpoints_surrogate')
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run('app.main:app', host='127.0.0.1', port=args.port)
