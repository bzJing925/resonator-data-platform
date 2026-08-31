"""单文件 / 文件列表相关响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.curves import Port


class BatchFileItem(BaseModel):
    """批次内一个已解压 .s1p 文件的列表项。"""

    model_config = ConfigDict(from_attributes=True)

    relpath: str = Field(..., description="相对于批次 files_dir 的相对路径")
    name: str = Field(..., description="文件名")
    size: int = Field(..., description="字节数")
    modified_at: datetime | None = Field(None, description="文件修改时间（UTC）")
    deembedded: bool = Field(False, description="是否来自去嵌目录")
    computed: bool = Field(False, description="是否已有 Device 入库记录")
    device_id: int | None = Field(None, description="对应 Device 行 id（若已计算）")


class FileNodeItem(BaseModel):
    """虚拟文件树节点列表项。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="节点 ID")
    parent_id: int | None = Field(None, description="父节点 ID")
    node_type: str = Field(..., description="节点类型：root/zip/folder/file")
    name: str = Field(..., description="显示名称")
    relpath: str | None = Field(None, description="file 节点对应的磁盘相对路径")
    sort_order: int = Field(0, description="同父级排序权重")
    is_deleted: bool = Field(False, description="是否已被软删除")
    source_zip: str | None = Field(None, description="来源压缩包文件名")
    size: int | None = Field(None, description="file 节点文件大小（字节）")
    computed: bool = Field(False, description="file 节点是否已计算 Device")
    device_id: int | None = Field(None, description="对应 Device id")
    children_count: int = Field(0, description="非 file 节点的直接子节点数")


class FileTreeListRequest(BaseModel):
    """列出虚拟文件树子节点请求。"""

    batch_no: str = Field(..., description="批次号")
    parent_id: int | None = Field(None, description="父节点 ID；为空则返回根节点下子项")


class FileTreeMoveRequest(BaseModel):
    """批量移动节点到指定文件夹。"""

    node_ids: list[int] = Field(..., description="待移动节点 ID 列表")
    target_folder_id: int = Field(..., description="目标文件夹节点 ID")


class FileTreeReorderRequest(BaseModel):
    """同父级内重排节点。"""

    parent_id: int | None = Field(None, description="父节点 ID")
    node_ids: list[int] = Field(..., description="排序后的节点 ID 列表")


class FileTreeMkdirRequest(BaseModel):
    """新建虚拟文件夹。"""

    batch_no: str = Field(..., description="批次号")
    parent_id: int | None = Field(None, description="父节点 ID；为空则挂在根节点下")
    name: str = Field(..., description="文件夹名称")


class FileTreeRenameRequest(BaseModel):
    """重命名节点。"""

    node_id: int = Field(..., description="节点 ID")
    name: str = Field(..., description="新名称")


class FileTreeDeleteRequest(BaseModel):
    """软删除节点。"""

    node_ids: list[int] = Field(..., description="待删除节点 ID 列表")


class DownloadZipByNodesRequest(BaseModel):
    """按虚拟节点 ID 批量打包下载。"""

    batch_no: str = Field(..., description="批次号")
    node_ids: list[int] = Field(
        ...,
        description="file 或 folder 节点 ID 列表；folder 会自动展开其下所有 file",
    )


class DownloadZipRequest(BaseModel):
    """批量打包下载请求。relpaths 为空表示下载该批次全部文件。"""

    batch_no: str = Field(..., description="批次号")
    relpaths: list[str] = Field(default_factory=list, description="相对路径列表；空则全选")


class ComputeFileRequest(BaseModel):
    """单文件计算请求。"""

    batch_no: str = Field(..., description="批次号")
    relpath: str = Field(..., description="文件相对批次目录的路径")
    f_start_ghz: float | None = None
    f_end_ghz: float | None = None
    deembedded: bool = False


class SplitS2PRequest(BaseModel):
    """手动拆分 .s2p 请求。"""

    batch_no: str = Field(..., description="批次号")
    relpaths: list[str] = Field(..., description="待拆分的 .s2p 相对路径列表")
    lowercase: bool = Field(default=True, description="输出文件名是否使用小写 _s11/_s22 后缀")


class ComputeFileResponse(BaseModel):
    """单文件计算结果。"""

    model_config = ConfigDict(from_attributes=True)

    device_id: int | None
    batch_no: str
    relpath: str
    metrics: dict[str, float | int | str | None]


class FileCurveResponse(BaseModel):
    """直接从文件读取的 S 参数 / 阻抗曲线。"""

    batch_no: str
    relpath: str
    param: str
    port: Port = "S11"
    freq_ghz: list[float]
    values: list[float]
    values_re: list[float] | None = None
    values_im: list[float] | None = None
