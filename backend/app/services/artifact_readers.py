"""ASGI leases span the complete file response, including disconnect cleanup."""
import asyncio
import json
import logging
from urllib.parse import parse_qs, unquote
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import get_settings
from app.models import Batch, Device
from app.services.artifact_lifecycle import acquire_lease, release_lease, renew_lease
from app.services.batch_scope import active_batch_clause

logger = logging.getLogger(__name__)


class ArtifactReaderMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path, method = scope.get('path', ''), scope.get('method', '')
        reads = ((path.startswith('/api/export/') and not path.startswith('/api/export/jobs')) or
                 (method == 'GET' and path.startswith('/api/devices/')) or
                 (method == 'GET' and path.endswith('/download-zip')) or
                 (method == 'GET' and path == '/api/files/curve') or
                 path in {'/api/files/download-zip', '/api/files/download-zip-nodes'})
        if scope['type'] != 'http' or not reads:
            return await self.app(scope, receive, send)
        from app.db import engine

        engine = getattr(scope['app'].state, 'artifact_engine', engine)
        params = parse_qs(scope.get('query_string', b'').decode())
        name = params.get('batch_no', [None])[0]
        if method == 'POST' and path.startswith('/api/files/'):
            raw = await Request(scope, receive).body()
            try:
                name = json.loads(raw).get('batch_no')
            except (ValueError, AttributeError):
                return await JSONResponse({'detail': '非法下载请求'}, 400)(scope, receive, send)
            original_receive, replayed = receive, False
            async def replay():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {'type': 'http.request', 'body': raw, 'more_body': False}
                return await original_receive()
            receive = replay
        elif path.startswith('/api/batches/'):
            name = unquote(path[len('/api/batches/'):].rsplit('/', 1)[0])
        owner, leases = str(uuid4()), []
        def acquire():
            with Session(engine) as db:
                if path.startswith('/api/export/'):
                    ids = db.scalars(select(Batch.id).where(active_batch_clause())).all()
                elif path.startswith('/api/devices/'):
                    try:
                        device = db.get(Device, int(path.split('/')[3]))
                    except ValueError:
                        device = None
                    ids = [device.batch_id] if device else []
                else:
                    batch_id = db.scalar(select(Batch.id).where(Batch.batch_no == name))
                    ids = [batch_id] if batch_id is not None else []
            for batch_id in ids:
                leases.append(acquire_lease(engine, batch_id, '*',
                    'export' if path.startswith('/api/export/') else 'download', owner))
        renewal = response = None
        try:
            await run_in_threadpool(acquire)
            async def renew():
                while True:
                    await asyncio.sleep(max(1, get_settings().ARTIFACT_LEASE_SECONDS / 3))
                    for lease in leases:
                        if not await run_in_threadpool(renew_lease, engine, lease, owner):
                            raise RuntimeError('artifact reader lease expired')
            renewal = asyncio.create_task(renew())
            response = asyncio.create_task(self.app(scope, receive, send))
            done, _ = await asyncio.wait({renewal, response}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        except HTTPException as exc:
            await JSONResponse({'detail': exc.detail}, exc.status_code)(scope, receive, send)
        finally:
            for task in (renewal, response):
                if task is not None:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, RuntimeError):
                        pass
            for lease in leases:
                try:
                    await run_in_threadpool(release_lease, engine, lease, owner)
                except Exception:
                    logger.exception('文件租约释放失败；将在到期后解除 owner=%s', owner)
