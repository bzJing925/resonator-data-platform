"""单文件计算与文件列表接口。

- GET  /api/files?batch_no=...        列出批次已解压的 .s1p 文件
- POST /api/files/compute             对单个文件执行指标计算并入库/更新
"""

from __future__ import annotations

import gzip
import logging
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import skrf
import zipstream
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import DEVICE_COLUMNS, DbSession
from app.core.curves import PARAM_CHOICES, compute_sparam_curve
from app.core.extract import ExtractError
from app.core.touchstone import split_s2p_to_s1p
from app.models import Batch, Device, FileNode
from app.schemas.file import (
    BatchFileItem,
    ComputeFileRequest,
    ComputeFileResponse,
    DownloadZipByNodesRequest,
    DownloadZipRequest,
    FileCurveResponse,
    FileNodeItem,
    FileTreeDeleteRequest,
    FileTreeMkdirRequest,
    FileTreeMoveRequest,
    FileTreeRenameRequest,
    FileTreeReorderRequest,
    SplitS2PRequest,
)
from app.services.batch_stats_service import refresh_mv_batch_stats
from app.services.compute_service import (
    ComputeServiceError,
    compute_single_device,
    sync_batch_device_count,
)
from app.services.file_tree_service import (
    batch_files_dir,
    build_file_tree_from_disk,
    get_or_create_root_node,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files", tags=["files"])


def _safe_resolve(base_dir: Path, relpath: str) -> Path:
    """把相对路径解析为 base_dir 下的真实路径，并防止目录穿越。"""
    target = (base_dir / relpath).resolve()
    base = base_dir.resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法文件路径") from exc
    return target


def _find_actual_path(base_dir: Path, relpath: str) -> Path:
    """解析相对路径；若原文件不存在但存在 .gz 版本，则返回 .gz 路径。"""
    target = _safe_resolve(base_dir, relpath)
    if target.exists():
        return target
    gz_target = target.with_suffix(target.suffix + ".gz")
    if gz_target.exists():
        return gz_target
    raise HTTPException(status_code=404, detail=f"文件不存在: {relpath}")


def _copy_maybe_gz(src: Path, dst: Path) -> None:
    """复制文件；若源为 .gz 则先解压。"""
    if src.suffix.lower() == ".gz":
        with gzip.open(src, "rb") as f_in, open(dst, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    else:
        shutil.copy2(src, dst)


def _read_network(target_path: Path, process_type: str = "S1P"):
    """读取 s1p/s2p/snp 文件；.snp 按 process_type 临时改名后读取。

    支持透明读取 .s1p.gz / .s2p.gz：先解压到临时文件再交给 skrf。
    """
    import skrf

    suffix = target_path.suffix.lower()
    is_gz = False
    real_suffix = suffix
    if suffix == ".gz":
        is_gz = True
        real_suffix = Path(target_path.stem).suffix.lower()

    if real_suffix == ".snp":
        new_ext = ".s1p" if process_type == "S1P" else ".s2p"
        tmp_dir = Path(tempfile.mkdtemp(prefix="aln_snp_"))
        tmp_path = tmp_dir / (target_path.stem + new_ext)
        _copy_maybe_gz(target_path, tmp_path)
        try:
            return skrf.Network(str(tmp_path))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if is_gz:
        tmp_dir = Path(tempfile.mkdtemp(prefix="aln_gz_"))
        tmp_path = tmp_dir / target_path.stem
        _copy_maybe_gz(target_path, tmp_path)
        try:
            return skrf.Network(str(tmp_path))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return skrf.Network(str(target_path))


@router.get("", response_model=list[BatchFileItem])
def list_batch_files(
    db: DbSession,
    batch_no: Annotated[str, Query(..., description="批次号")],
    include_snp: Annotated[bool, Query()] = False,
) -> list[BatchFileItem]:
    """列出批次解压后的 snp 文件；默认仅 .s1p，include_snp=true 时包含 .s2p/.snp。"""
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    base_dir = batch_files_dir(batch_no)
    if not base_dir.exists():
        return []

    # 已计算 device 的 (s_param_path, s_param_port) → device_id
    existing = {
        (d.s_param_path or "", d.s_param_port): d.id
        for d in db.scalars(select(Device).where(Device.batch_id == batch.id)).all()
    }

    patterns = ["*.s1p", "*.s1p.gz"]
    if include_snp:
        patterns.extend(["*.s2p", "*.s2p.gz", "*.snp"])

    files: list[BatchFileItem] = []
    seen: set[str] = set()
    for pattern in patterns:
        for p in sorted(base_dir.rglob(pattern)):
            if not p.is_file():
                continue
            try:
                relpath = str(p.relative_to(base_dir))
            except ValueError:
                continue
            if relpath in seen:
                continue
            seen.add(relpath)
            stat = p.stat()
            deembedded = "S11_de" in relpath or "S22_de" in relpath or relpath.endswith("_de.s1p")
            device_id = existing.get((relpath, "S11")) or existing.get((relpath, "S22"))
            files.append(
                BatchFileItem(
                    relpath=relpath,
                    name=p.name,
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    deembedded=deembedded,
                    computed=device_id is not None,
                    device_id=device_id,
                )
            )
    return files


@router.post("/split-s2p")
def split_s2p_files(db: DbSession, body: SplitS2PRequest) -> Response:
    """把选中的 .s2p 文件拆成 S11/S22 两个 .s1p 并流式打包返回。"""
    batch = db.scalar(select(Batch).where(Batch.batch_no == body.batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {body.batch_no} 不存在")

    base_dir = batch_files_dir(body.batch_no)
    if not base_dir.exists():
        raise HTTPException(status_code=404, detail="批次解压目录不存在")

    selected: list[tuple[Path, str]] = []
    for relpath in body.relpaths:
        target = _safe_resolve(base_dir, relpath)
        if not target.is_file() or target.suffix.lower() != ".s2p":
            raise HTTPException(
                status_code=400, detail=f"非法或不存在文件（必须为 .s2p）: {relpath}"
            )
        selected.append((target, relpath))

    if not selected:
        raise HTTPException(status_code=400, detail="未选择任何 .s2p 文件")

    tmp_root = Path(tempfile.mkdtemp(prefix="aln_split_"))
    s11_root = tmp_root / "s11"
    s22_root = tmp_root / "s22"

    zs = zipstream.ZipStream(compress_type=zipstream.ZIP_DEFLATED)
    for target, relpath in selected:
        try:
            split = split_s2p_to_s1p(
                target,
                out_dir_s11=s11_root,
                out_dir_s22=s22_root,
                lowercase=body.lowercase,
            )
        except Exception as exc:
            shutil.rmtree(tmp_root, ignore_errors=True)
            raise HTTPException(status_code=422, detail=f"拆分失败 {relpath}: {exc}") from exc

        # 保持原相对路径目录结构，仅把 .s2p 替换为 _s11/_s22.s1p
        rel_stem = relpath[:-4]  # 去掉 .s2p
        s11_arc = f"{rel_stem}{'_s11' if body.lowercase else '_S11'}.s1p"
        s22_arc = f"{rel_stem}{'_s22' if body.lowercase else '_S22'}.s1p"
        zs.add_path(str(split.s11_path), arcname=s11_arc, recurse=False)
        zs.add_path(str(split.s22_path), arcname=s22_arc, recurse=False)

    background = BackgroundTasks()
    background.add_task(lambda: shutil.rmtree(tmp_root, ignore_errors=True))

    filename = f"{body.batch_no}_split_s2p.zip"
    return StreamingResponse(
        zs,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=background,
    )


@router.post("/download-zip")
def download_files_zip(db: DbSession, body: DownloadZipRequest) -> Response:
    """把选中的 snp 文件打包成 zip 流式下载；relpaths 为空时下载该批次全部文件。"""
    batch = db.scalar(select(Batch).where(Batch.batch_no == body.batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {body.batch_no} 不存在")

    base_dir = batch_files_dir(body.batch_no)
    if not base_dir.exists():
        raise HTTPException(status_code=404, detail="批次解压目录不存在")

    selected: list[tuple[Path, str]] = []

    if body.relpaths:
        for relpath in body.relpaths:
            target = _find_actual_path(base_dir, relpath)
            if target.suffix.lower() == ".gz":
                actual_suffix = Path(target.stem).suffix.lower()
            else:
                actual_suffix = target.suffix.lower()
            if actual_suffix not in {".s1p", ".s2p", ".snp"}:
                raise HTTPException(status_code=400, detail=f"非法文件类型: {relpath}")
            selected.append((target, relpath))
    else:
        for p in sorted(base_dir.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() == ".gz":
                actual_suffix = Path(p.stem).suffix.lower()
            else:
                actual_suffix = p.suffix.lower()
            if actual_suffix in {".s1p", ".s2p", ".snp"}:
                selected.append((p, str(p.relative_to(base_dir))))

    if not selected:
        raise HTTPException(status_code=404, detail="没有可下载的 snp 文件")

    zs = zipstream.ZipStream(compress_type=zipstream.ZIP_DEFLATED)
    for target, arcname in selected:
        zs.add_path(str(target), arcname=arcname, recurse=False)

    filename = f"{body.batch_no}_files.zip"
    return StreamingResponse(
        zs,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/curve", response_model=FileCurveResponse)
def get_file_curve(
    db: DbSession,
    batch_no: Annotated[str, Query(...)],
    relpath: Annotated[str, Query(...)],
    param: Annotated[str, Query()] = "z_mag_db",
) -> FileCurveResponse:
    """直接从批次解压目录读取指定文件的 S 参数 / 阻抗曲线（无需先入库）。"""
    if param not in PARAM_CHOICES:
        raise HTTPException(status_code=400, detail=f"param 必须是 {','.join(PARAM_CHOICES)} 之一")

    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    base_dir = batch_files_dir(batch_no)
    target_path = _find_actual_path(base_dir, relpath)

    try:
        net = _read_network(target_path, batch.process_type or "S1P")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取 S 参数文件失败: {exc}") from exc

    try:
        curve = compute_sparam_curve(net, param)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return FileCurveResponse(
        batch_no=batch_no,
        relpath=relpath,
        param=param,
        freq_ghz=curve["freq_ghz"],
        values=curve.get("values", []),
        values_re=curve.get("values_re"),
        values_im=curve.get("values_im"),
    )


@router.post("/compute", response_model=ComputeFileResponse)
def compute_single_file(db: DbSession, body: ComputeFileRequest) -> ComputeFileResponse:
    """对指定批次内的单个 .s1p 文件执行指标计算，并写入/更新 devices 表。"""
    batch = db.scalar(select(Batch).where(Batch.batch_no == body.batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {body.batch_no} 不存在")

    base_dir = batch_files_dir(body.batch_no)
    target_path = _find_actual_path(base_dir, body.relpath)

    try:
        device = compute_single_device(
            db,
            batch=batch,
            relpath=body.relpath,
            target_path=target_path,
            f_start_ghz=body.f_start_ghz,
            f_end_ghz=body.f_end_ghz,
            deembedded=body.deembedded,
        )
    except ComputeServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ExtractError as exc:
        raise HTTPException(status_code=422, detail=f"指标计算失败: {exc}") from exc

    sync_batch_device_count(db, batch.id)
    refresh_mv_batch_stats(db)

    metrics = {col: getattr(device, col) for col in DEVICE_COLUMNS if col != "batch_id"}
    return ComputeFileResponse(
        device_id=device.id,
        batch_no=body.batch_no,
        relpath=body.relpath,
        metrics=metrics,
    )


# =============================================================================
# 虚拟文件树（Finder 式文件管理）
# =============================================================================


def _get_batch_or_404(db: DbSession, batch_no: str) -> Batch:
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")
    return batch


def _device_lookup_for_batch(db: DbSession, batch_id: int) -> dict[tuple[str, str], int]:
    return {
        (d.s_param_path or "", d.s_param_port): d.id
        for d in db.scalars(select(Device).where(Device.batch_id == batch_id)).all()
    }


def _node_to_item(
    node: FileNode,
    base_dir: Path,
    device_lookup: dict[tuple[str, str], int],
) -> FileNodeItem:
    size: int | None = None
    computed = False
    device_id: int | None = None
    children_count = 0

    if node.node_type == "file" and node.relpath:
        target = _safe_resolve(base_dir, node.relpath)
        gz_target = target.with_suffix(target.suffix + ".gz")
        for p in (target, gz_target):
            if p.exists():
                size = p.stat().st_size
                break
        if (node.relpath, "S11") in device_lookup:
            computed = True
            device_id = device_lookup[(node.relpath, "S11")]
        elif (node.relpath, "S22") in device_lookup:
            computed = True
            device_id = device_lookup[(node.relpath, "S22")]

    if node.node_type in ("root", "zip", "folder"):
        children_count = sum(1 for c in node.children if not c.is_deleted)

    return FileNodeItem(
        id=node.id,
        parent_id=node.parent_id,
        node_type=node.node_type,
        name=node.name,
        relpath=node.relpath,
        sort_order=node.sort_order,
        is_deleted=node.is_deleted,
        source_zip=node.source_zip,
        size=size,
        computed=computed,
        device_id=device_id,
        children_count=children_count,
    )


def _collect_file_relpaths_from_nodes(
    db: DbSession, batch_id: int, node_ids: list[int]
) -> list[str]:
    """把选中的 folder/zip/file 节点展开为所有 file 节点的 relpath 列表。"""
    relpaths: list[str] = []
    nodes = db.scalars(
        select(FileNode).where(FileNode.batch_id == batch_id, FileNode.id.in_(node_ids))
    ).all()

    queue: list[FileNode] = list(nodes)
    seen: set[int] = set()
    while queue:
        node = queue.pop(0)
        if node.id in seen:
            continue
        seen.add(node.id)
        if node.node_type == "file" and node.relpath:
            relpaths.append(node.relpath)
        else:
            children = db.scalars(
                select(FileNode).where(
                    FileNode.parent_id == node.id, FileNode.is_deleted.is_(False)
                )
            ).all()
            queue.extend(children)

    return relpaths


@router.get("/tree", response_model=list[FileNodeItem])
def list_file_tree(
    db: DbSession,
    batch_no: Annotated[str, Query(...)],
    parent_id: Annotated[int | None, Query()] = None,
) -> list[FileNodeItem]:
    """列出虚拟文件树某个父节点下的非删除子节点。首次访问会自动从磁盘初始化树。"""
    batch = _get_batch_or_404(db, batch_no)
    build_file_tree_from_disk(db, batch)

    if parent_id is None:
        root = get_or_create_root_node(db, batch)
        parent_id = root.id

    parent = db.scalar(
        select(FileNode).where(
            FileNode.batch_id == batch.id,
            FileNode.id == parent_id,
        )
    )
    if parent is None:
        raise HTTPException(status_code=404, detail="父节点不存在")

    children = db.scalars(
        select(FileNode)
        .where(
            FileNode.batch_id == batch.id,
            FileNode.parent_id == parent_id,
            FileNode.is_deleted.is_(False),
        )
        .order_by(FileNode.sort_order, FileNode.name)
    ).all()

    base_dir = batch_files_dir(batch_no)
    device_lookup = _device_lookup_for_batch(db, batch.id)
    return [_node_to_item(c, base_dir, device_lookup) for c in children]


@router.post("/tree/move")
def move_file_tree_nodes(db: DbSession, body: FileTreeMoveRequest) -> dict[str, int]:
    """批量移动节点到目标文件夹。"""
    target = db.get(FileNode, body.target_folder_id)
    if target is None or target.node_type not in ("root", "zip", "folder"):
        raise HTTPException(status_code=400, detail="目标文件夹不存在或类型错误")

    nodes = db.scalars(select(FileNode).where(FileNode.id.in_(body.node_ids))).all()
    if len(nodes) != len(body.node_ids):
        raise HTTPException(status_code=404, detail="部分节点不存在")

    # 防止把父节点移动到自身子树下
    forbidden_ids = {target.id}
    queue: list[int] = [target.id]
    while queue:
        pid = queue.pop(0)
        children = db.scalars(select(FileNode.id).where(FileNode.parent_id == pid)).all()
        for cid in children:
            forbidden_ids.add(cid)
            queue.append(cid)

    moved = 0
    for node in nodes:
        if node.id in forbidden_ids:
            raise HTTPException(
                status_code=400,
                detail=f"不能把节点 {node.name} 移动到自身子树下",
            )
        node.parent_id = target.id
        moved += 1

    db.commit()
    return {"moved": moved}


@router.post("/tree/reorder")
def reorder_file_tree_nodes(db: DbSession, body: FileTreeReorderRequest) -> dict[str, int]:
    """同父级内按 node_ids 顺序重排。"""
    nodes = db.scalars(select(FileNode).where(FileNode.id.in_(body.node_ids))).all()
    if len(nodes) != len(body.node_ids):
        raise HTTPException(status_code=404, detail="部分节点不存在")

    parent_ids = {n.parent_id for n in nodes}
    if len(parent_ids) != 1:
        raise HTTPException(status_code=400, detail="只能对同一父节点下的节点排序")
    if body.parent_id is not None and body.parent_id not in parent_ids:
        raise HTTPException(status_code=400, detail="parent_id 与节点实际父级不一致")

    order_map = {nid: idx for idx, nid in enumerate(body.node_ids)}
    for node in nodes:
        node.sort_order = order_map[node.id]

    db.commit()
    return {"reordered": len(nodes)}


@router.post("/tree/mkdir", response_model=FileNodeItem)
def mkdir_file_tree(db: DbSession, body: FileTreeMkdirRequest) -> FileNodeItem:
    """在指定父节点下新建虚拟文件夹。"""
    batch = _get_batch_or_404(db, body.batch_no)
    build_file_tree_from_disk(db, batch)

    if body.parent_id is None:
        root = get_or_create_root_node(db, batch)
        parent_id = root.id
    else:
        parent_id = body.parent_id

    parent = db.scalar(
        select(FileNode).where(
            FileNode.batch_id == batch.id,
            FileNode.id == parent_id,
            FileNode.is_deleted.is_(False),
        )
    )
    if parent is None or parent.node_type not in ("root", "zip", "folder"):
        raise HTTPException(status_code=400, detail="父节点不存在或不能创建子文件夹")

    # 同一父级下名称唯一
    existing = db.scalar(
        select(FileNode).where(
            FileNode.batch_id == batch.id,
            FileNode.parent_id == parent_id,
            FileNode.node_type == "folder",
            FileNode.name == body.name,
            FileNode.is_deleted.is_(False),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="同目录下已存在同名文件夹")

    # 简单分配一个较大的 sort_order 放在末尾
    max_order = (
        db.scalar(
            select(func.max(FileNode.sort_order)).where(
                FileNode.batch_id == batch.id,
                FileNode.parent_id == parent_id,
            )
        )
        or 0
    )

    folder = FileNode(
        batch_id=batch.id,
        parent_id=parent_id,
        node_type="folder",
        name=body.name,
        sort_order=max_order + 1,
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)

    base_dir = batch_files_dir(batch.batch_no)
    device_lookup = _device_lookup_for_batch(db, batch.id)
    return _node_to_item(folder, base_dir, device_lookup)


@router.post("/tree/rename", response_model=FileNodeItem)
def rename_file_tree_node(db: DbSession, body: FileTreeRenameRequest) -> FileNodeItem:
    """重命名节点（仅 folder/zip，file 节点不建议重命名，避免与磁盘不一致）。"""
    node = db.get(FileNode, body.node_id)
    if node is None or node.is_deleted:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node.node_type == "root":
        raise HTTPException(status_code=400, detail="不能重命名根节点")

    node.name = body.name
    db.commit()
    db.refresh(node)

    base_dir = batch_files_dir(node.batch.batch_no)
    device_lookup = _device_lookup_for_batch(db, node.batch_id)
    return _node_to_item(node, base_dir, device_lookup)


@router.post("/tree/delete")
def delete_file_tree_nodes(db: DbSession, body: FileTreeDeleteRequest) -> dict[str, int]:
    """软删除节点及其所有后代。"""
    nodes = db.scalars(select(FileNode).where(FileNode.id.in_(body.node_ids))).all()
    if len(nodes) != len(body.node_ids):
        raise HTTPException(status_code=404, detail="部分节点不存在")

    # 级联软删除
    deleted = 0
    queue: list[FileNode] = list(nodes)
    seen: set[int] = set()
    while queue:
        node = queue.pop(0)
        if node.id in seen:
            continue
        seen.add(node.id)
        if not node.is_deleted:
            node.is_deleted = True
            deleted += 1
        children = db.scalars(select(FileNode).where(FileNode.parent_id == node.id)).all()
        queue.extend(children)

    db.commit()
    return {"deleted": deleted}


@router.post("/download-zip-nodes")
def download_file_tree_nodes_zip(db: DbSession, body: DownloadZipByNodesRequest) -> Response:
    """按虚拟节点 ID 打包下载文件；folder/zip 节点会自动展开其下所有 file 节点。"""
    batch = _get_batch_or_404(db, body.batch_no)
    build_file_tree_from_disk(db, batch)

    relpaths = _collect_file_relpaths_from_nodes(db, batch.id, body.node_ids)
    if not relpaths:
        raise HTTPException(status_code=404, detail="没有可下载的文件")

    return _stream_zip_from_relpaths(batch.batch_no, relpaths)


def _stream_zip_from_relpaths(batch_no: str, relpaths: list[str]) -> StreamingResponse:
    """根据相对路径列表流式打包。"""
    base_dir = batch_files_dir(batch_no)
    selected: list[tuple[Path, str]] = []
    for relpath in relpaths:
        target = _find_actual_path(base_dir, relpath)
        if target.suffix.lower() == ".gz":
            actual_suffix = Path(target.stem).suffix.lower()
        else:
            actual_suffix = target.suffix.lower()
        if actual_suffix not in {".s1p", ".s2p", ".snp"}:
            raise HTTPException(status_code=400, detail=f"非法文件类型: {relpath}")
        selected.append((target, relpath))

    zs = zipstream.ZipStream(compress_type=zipstream.ZIP_DEFLATED)
    for target, arcname in selected:
        zs.add_path(str(target), arcname=arcname, recurse=False)

    filename = f"{batch_no}_files.zip"
    return StreamingResponse(
        zs,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
