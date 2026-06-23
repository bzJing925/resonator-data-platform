"""单文件 / 文件列表相关响应模型。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


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
    lowercase: bool = Field(
        default=True, description="输出文件名是否使用小写 _s11/_s22 后缀"
    )


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
    freq_ghz: list[float]
    values: list[float]
    values_re: list[float] | None = None
    values_im: list[float] | None = None
