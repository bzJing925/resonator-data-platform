"""S2P → S1P 拆分，以及 .snp 通用扩展名自动识别。

来源：客户脚本 split_header_data / modify_header / extract_s11_data / extract_s22_data /
        write_output_file / process_s2p_file（27-1196 行去重后版本）。

S2P 文件每行：freq + S11_re + S11_im + S21_re + S21_im + S12_re + S12_im + S22_re + S22_im
- S11 拆分：保留前 3 列（freq, S11_re, S11_im）
- S22 拆分：保留第 1 列和最后 2 列（freq, S22_re, S22_im）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


@dataclass(frozen=True)
class SplitResult:
    s11_path: Path
    s22_path: Path


def detect_snp_type(path: str | Path) -> str:
    """通过首行数据列数判断 .snp 文件是 S1P 还是 S2P。

    - 3 列 → S1P（freq + S11_re + S11_im）
    - 9 列 → S2P（freq + 4 组 S 参数）
    其他列数抛异常。
    """
    path = Path(path)
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("!") or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) == 3:
                return "S1P"
            if len(parts) == 9:
                return "S2P"
            raise ValueError(
                f"无法识别 {path.name} 的端口类型："
                f"数据行列数为 {len(parts)}，期望 3（S1P）或 9（S2P）"
            )
    raise ValueError(f"{path.name} 没有有效数据行")


def split_s2p_to_s1p(
    s2p_path: str | Path,
    out_dir_s11: str | Path,
    out_dir_s22: str | Path,
    *,
    lowercase: bool = False,
) -> SplitResult:
    """把一个 .s2p 文件拆成 S11 和 S22 两个 .s1p 文件。

    返回两个输出文件路径。会创建 out_dir_s11 / out_dir_s22 目录。
    lowercase=True 时输出后缀为 `_s11.s1p` / `_s22.s1p`，否则为 `_S11.s1p` / `_S22.s1p`。
    """
    s2p_path = Path(s2p_path)
    out_dir_s11 = Path(out_dir_s11)
    out_dir_s22 = Path(out_dir_s22)
    out_dir_s11.mkdir(parents=True, exist_ok=True)
    out_dir_s22.mkdir(parents=True, exist_ok=True)

    stem = s2p_path.stem
    sfx_s11, sfx_s22 = ("_s11", "_s22") if lowercase else ("_S11", "_S22")
    s11_path = out_dir_s11 / f"{stem}{sfx_s11}.s1p"
    s22_path = out_dir_s22 / f"{stem}{sfx_s22}.s1p"

    header = _read_header(s2p_path)
    s11_header = _modify_header(header, "S11")
    s22_header = _modify_header(header, "S22")
    _split_data_streaming(s2p_path, s11_path, s22_path, s11_header, s22_header)

    return SplitResult(s11_path=s11_path, s22_path=s22_path)


def _read_header(path: Path) -> list[str]:
    """读取数据段前的 header，而不把整个 touchstone 文件留在内存中。"""
    header: list[str] = []
    with open(path) as source:
        for line in source:
            stripped = line.strip()
            if stripped.startswith("!") or stripped.startswith("#") or not stripped:
                header.append(line)
            else:
                break
    return header


def _modify_header(header: list[str], parameter: str) -> list[str]:
    new_header: list[str] = []
    if len(header) >= 2:
        new_header.extend(header[:2])

    for line in header:
        if line.strip().startswith(f"!Correction: {parameter}("):
            new_header.append(line)
            break

    for line in header:
        if line.strip().startswith(("!S2P File:", "!S1P File:")):
            new_line = line.replace("S2P", "S1P").replace("S11, S21, S12, S22", parameter)
            new_header.append(new_line)
            break

    option_line = unit_line = None
    for line in header:
        stripped = line.strip()
        if stripped.startswith("#"):
            option_line = line
        elif stripped.lower().startswith("hz"):
            unit_line = line
    if option_line:
        new_header.append(option_line)
    if unit_line:
        new_header.append(unit_line)
    return new_header


def _split_data_streaming(
    source_path: Path,
    s11_path: Path,
    s22_path: Path,
    s11_header: list[str],
    s22_header: list[str],
) -> None:
    """流式拆分数据，并在完整校验后原子发布两份输出文件。"""
    s11_temp = NamedTemporaryFile(mode="w", dir=s11_path.parent, delete=False, encoding="utf-8")
    s22_temp = NamedTemporaryFile(mode="w", dir=s22_path.parent, delete=False, encoding="utf-8")
    s11_temp_path = Path(s11_temp.name)
    s22_temp_path = Path(s22_temp.name)
    try:
        with s11_temp, s22_temp, open(source_path) as source:
            s11_temp.writelines(s11_header)
            s22_temp.writelines(s22_header)
            data_started = False
            for lineno, line in enumerate(source, start=1):
                stripped = line.strip()
                if not data_started:
                    if stripped.startswith(("!", "#")) or not stripped:
                        continue
                    data_started = True
                if not stripped:
                    continue
                parts = stripped.split()
                if len(parts) < 9:
                    raise ValueError(
                        f"S2P 数据行 {lineno} 列数 {len(parts)} < 9，文件可能截断或损坏"
                    )
                s11_temp.write(f"{parts[0]} {parts[1]} {parts[2]}\n")
                s22_temp.write(f"{parts[0]} {parts[-2]} {parts[-1]}\n")
        s11_temp_path.replace(s11_path)
        s22_temp_path.replace(s22_path)
    except Exception:
        s11_temp_path.unlink(missing_ok=True)
        s22_temp_path.unlink(missing_ok=True)
        raise
