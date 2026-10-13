"""Validate ZIP members before any Python or external extractor writes files."""

import stat
import zipfile
from pathlib import Path, PurePosixPath

from app.config import get_settings


class ArchiveValidationError(ValueError):
    def __init__(self, code: str, message: str, member: str | None = None):
        self.code, self.member = code, member
        super().__init__(message)


def validate_archive_members(
    path: Path, target_dir: Path, progress_callback=None
) -> list[zipfile.ZipInfo]:
    budget = get_settings().UPLOAD_MAX_GB * 1024**3
    if path.stat().st_size > budget:
        raise ArchiveValidationError("UPLOAD_TOO_LARGE", "上传超过容量限制")
    root = target_dir.resolve()
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
    seen = set()
    total = 0
    count = len(members) * 2
    if progress_callback:
        progress_callback(0, count)
    for index, member in enumerate(members, 1):
        name = member.filename.rstrip("/")
        parts = PurePosixPath(name).parts
        if (
            not name
            or "\\" in name
            or ":" in name
            or name.startswith("/")
            or any(part in {".", ".."} for part in name.split("/"))
            or not (root / name).resolve().is_relative_to(root)
            or stat.S_ISLNK(member.external_attr >> 16)
        ):
            raise ArchiveValidationError("UNSAFE_ARCHIVE_PATH", "压缩包包含非法路径")
        key = "/".join(parts).casefold()
        if key in seen:
            raise ArchiveValidationError("DUPLICATE_ARCHIVE_MEMBER", "压缩包包含重复路径", name)
        seen.add(key)
        total += member.file_size
        if total > budget:
            raise ArchiveValidationError("EXTRACT_TOO_LARGE", "解压容量超过限制")
        if progress_callback:
            progress_callback(index, count)
    # A file cannot also be an ancestor directory on any supported executor.
    files = {m.filename.rstrip("/").casefold() for m in members if not m.is_dir()}
    for index, key in enumerate(seen, len(members) + 1):
        if any(str(parent) in files for parent in PurePosixPath(key).parents if str(parent) != "."):
            raise ArchiveValidationError("UNSAFE_ARCHIVE_PATH", "文件与目录路径冲突")
        if progress_callback:
            progress_callback(index, count)
    return members
