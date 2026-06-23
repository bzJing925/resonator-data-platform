# 算法移植规格

> 阶段 1（算法层移植）的工作单。把客户的 `VNA analysis v5.4_SITRI.py` + `de.py`
> 拆解、清洗、纯函数化，落到 `backend/app/core/`，供 FastAPI / Celery 直接调用。

---

## 1. 概览

客户脚本是一份 3082 行、面向交互式 CLI 的单文件流程：从用户 `input()` 收集参数 →
扫描文件夹 → 拆 S2P → 加载 OPEN/SHORT 校准 → 去嵌 → 提取 fs/fp/Q/BodeQ →
写 Excel。文件中 27–1196 与 1224–2440 行为重复粘贴，实际有效行约 1500。

移植目标：

- 把"读输入 / 跑算法 / 写文件"完全解耦，算法部分变成可被任意上层（HTTP / Celery /
  CLI / pytest）调用的纯函数。
- 输入：内存中的 numpy 数组、pathlib.Path、dataclass；输出：dataclass / pydantic
  模型。**不打印、不写文件、不调用 input()。**
- 把所有硬编码魔数集中到 `backend/app/config.py` 的 `AlgorithmConfig`，单元测试
  可以注入不同 config 验证行为。
- 修掉 §2 列出的 5 个已知 bug，行为对齐"客户预期"而非"客户脚本现状"。

### 决策更新（2026-05-09）

- **mBVD 等效电路参数提取已废弃**（用户决策）：原脚本中的
  `calculate_mbvd_parameters` 及其产出的 C0/Cm/Lm/Rm/R0/Rs 全部不再实现、不再入库。
  相关字段、配置项、bug 讨论（如 R0 公式 A/B 选型）一并从本文档移除。
- **de-embedding v1 默认关闭**：去嵌模块仍按规范封装、修复缓存键 bug，但
  `deembed_enabled_default = False`，等到第二阶段再打开。
- **输出列从 30+ 减到 24**：`ResonatorRow` 不再包含 mBVD 六个字段，列结构以
  §5 的最终 dataclass 为准。

代码组织：

```
backend/app/
├── config.py                      # AlgorithmConfig dataclass
└── core/
    ├── __init__.py
    ├── touchstone.py              # S2P→S1P 拆分
    ├── deembed.py                 # ShortOpen 去嵌封装
    ├── extract.py                 # fs/fp/Q/BodeQ 提取
    ├── mapping.py                 # 对照表加载 + Description 解析
    ├── filename.py                # 文件名 → device_letter / coord / keywords
    └── models.py                  # ResonatorRow / 中间数据结构
```

---

## 2. 已知 Bug 清单（必修）

| # | Bug 描述 | 位置 | 影响 | 修复方案 |
|---|---|---|---|---|
| B1 | 整段代码重复粘贴 | 27–1196 与 1224–2440 行 | 同名函数被定义两次，第二份悄悄覆盖第一份，阅读和定位行号都很容易出错 | 重构时**只保留一份**算法实现，由本文档 §3 的拆分模块承接；阅读对照行号一律以第一份（27–1196）为准。 |
| B2 | `column_order` 中 `Qs/Qp/Qs_BodeQ/Qp_BodeQ/dbqs/dbqp` 重复了一次 | 3000–3001 行 | 输出 Excel 这 6 列出现两次，pandas 会创建重名列 → 后续 `has_na_in_key_params` 不得不写 `if hasattr(value, 'iloc')` 兜底 | 拆 `ResonatorRow` dataclass，列顺序由 dataclass 字段决定，物理上不可能重复。pandas 输出阶段直接 `[asdict(r) for r in rows]`。 |
| B3 | 去嵌缓存键名不一致 | `preprocess_calibration_data` 把数据存到 `cache[key]['s11']` / `['s22']`（488–602 行）；但 `process_device_file` 读的是 `cache[device_key].get('open', {}).get(port_type)` 和 `cache[device_key].get('short', {}).get(port_type)`（2562–2563 行） | **去嵌实际上从不命中**，整个去嵌分支永远 `port_calib` 取出来但 `open_ntw / short_ntw` 都是 None → 走警告分支。客户 `deembed_flag=1` 形同摆设。 | 重新设计缓存结构：`cache[(device_letter, coord)] = {'open': {'s11': Network, 's22': Network}, 'short': {...}}`。预处理和读取使用同一份 dataclass：`CalibrationCache`。 |
| B4 | OPEN/SHORT 过滤错位 | 2935–2936 行：`s_files = [f for f in s_files if f not in all_open_files]` 与 `s2p_files = [f for f in s2p_files if f not in all_short_files]` | OPEN 文件只从 S1P 列表剔除，SHORT 文件只从 S2P 列表剔除，两边都漏 → 校准文件本身可能被当 DUT 处理一遍。 | 重构成 `dut_files = [f for f in all_files if f not in calibration_paths]`，单一集合 `calibration_paths = open_paths | short_paths` 同时过滤 S1P / S2P。 |
| B5 | `mark_match` 与 `alt_mark_match` 正则完全相同 | `get_display_name` 第 265 行和 279 行均为 `r'([A-Za-z]\d+-\d+)'` | 第二个分支永远命中不到，注释说"备选格式"但其实没起作用 | 移除 `alt_mark_match` 分支；如果客户本意是匹配 `S22` 文件中其它格式（如 `C7_2`、`C7.2`），需要先与客户确认真实备选格式再实现。**当前先按"删除冗余分支"处理。** |

附加发现（非阻塞，重构时顺手清掉）：

- B6: `find_resonances` 用纯 Python `for i in range(...)` 找极值，可改成
  `np.diff(np.sign(np.diff(z_db)))` 向量化，性能 ×10。但**算法等价**，是否改属
  优化范畴。
- B7: `calculate_bodeq` 异常分支会再算一遍 `BodeQ_raw_array`（782–814 行），逻辑
  与主分支重复。重构时抽 `_compute_raw_bodeq()` 工具函数，主分支和 fallback 共用。
- B8: 硬编码 Area 表（2493 / 2495 行：`{1:700, 2:900, ...}` 和
  `{1:5500, 2:5000, ...}`）。客户已确认从 mapping 的 Description 列直接 split `&`
  取两个值，**这两张表整个删掉**。
- B9: `extract_keywords` 里 `pf_flag = 'Y' if re.search(r'\+PF', name) or re.search(r'PF', name) else 'N'` —— 第二个 `re.search(r'PF', name)` 会让任何包含 `PF` 子串的文件都判 Y（第一个 `+PF` 已被覆盖），逻辑冗余。改成 `'Y' if '+PF' in name or 'PF_' in name else 'N'`，或与客户确认正确语义。

---

## 3. 模块拆分

### 3.1 `core/touchstone.py`

**职责**：把 2 端口 S2P 文件拆成两个 1 端口 S1P 文件（S11 + S22），保留头部
metadata；不做去嵌、不做参数提取。

**输入/输出契约**

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

@dataclass(frozen=True)
class S2PSplitResult:
    s11_path: Path
    s22_path: Path

def split_s2p(
    s2p_path: Path,
    out_dir: Path,
    *,
    overwrite: bool = True,
) -> S2PSplitResult: ...

def split_header_data(content: list[str]) -> tuple[list[str], list[str]]: ...
def modify_header(header: list[str], parameter: Literal["S11", "S22"]) -> list[str]: ...
def extract_s11_data(data_lines: list[str]) -> list[str]: ...
def extract_s22_data(data_lines: list[str]) -> list[str]: ...
```

**搬运对照**

| 客户脚本函数 | 行号 | 改造点 |
|---|---|---|
| `split_header_data` | 343–357 | 直接搬，已经是纯函数 |
| `modify_header` | 359–395 | 直接搬 |
| `extract_s11_data` | 397–404 | 直接搬 |
| `extract_s22_data` | 406–413 | 直接搬 |
| `process_s2p_file` | 287–341 | **重写为 `split_s2p`**：去掉 `print` 与 `os.remove` 兜底；`s11_dir / s22_dir` 改成由调用方传入的 `out_dir`，子目录名从 `AlgorithmConfig` 来；不再依赖 `mapping`（重命名是上层职责） |

**纯函数化要点**

- 去掉 `print(f"已生成: ...")` 和 `print(f"❌ 处理失败 ...")`。错误一律 `raise`；
  上层捕获后写 logger / 错误表。
- 去掉异常分支里的 `os.remove(s11_path)` —— 文件不完整应让上层决定，core 层不
  做副作用清理。
- `sanitize_filename` 不放在 touchstone，移到 `mapping.py`（命名是 mapping 的事）。

---

### 3.2 `core/deembed.py`

**职责**：封装 scikit-rf 的 `ShortOpen`，对 DUT Network 做去嵌，返回新 Network。
仅是个轻量 wrapper，但加上参数校验和频率对齐。

**输入/输出契约**

```python
import skrf as rf
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class CalibrationPair:
    open: rf.Network
    short: rf.Network

@dataclass(frozen=True)
class CalibrationCache:
    """以 (device_letter, coord, port_type) 为键的两端校准查找表"""
    pairs: dict[tuple[str, str, str], CalibrationPair]

    def lookup(
        self,
        device_letter: str,
        coord: str,
        port_type: str,           # 's11' | 's22'
    ) -> Optional[CalibrationPair]: ...


def deembed_dut(
    dut: rf.Network,
    calibration: CalibrationPair,
) -> rf.Network:
    """对单端口 DUT 应用 ShortOpen 去嵌；自动插值到 DUT 频率轴"""
```

**搬运对照**

| 来源 | 行号 | 改造点 |
|---|---|---|
| `de.py` 第 159–166 行 `ShortOpen(...).deembed(dut)` | de.py 159–166 | 抽成 `deembed_dut`，加频率轴对齐：若 `not np.array_equal(dut.f, op.f)` 则 `op = op.interpolate(dut.frequency)` |
| `preprocess_calibration_data` | VNA 488–602 | **完全重写**为 `build_calibration_cache(open_paths, short_paths) -> CalibrationCache`。修复 B3：键名统一为 `(device_letter, coord, port_type)` 元组；S2P 自动拆 S11/S22；多组同键样本用 `rf.NetworkSet.mean_s` 平均。 |

**纯函数化要点**

- 不调用 `input()`、不 `os.walk`。`open_paths`/`short_paths` 由上层 `mapping.py` /
  `filename.py` 解析后传入。
- 不 `print(f"成功加载 {len(...)} 组校准数据")`；返回值自带统计信息（`len(cache.pairs)`）。
- de.py 的 `position_index` / `GLOBAL fallback` / `(num_3, sxx)` 备用逻辑可作为
  `core/calibration_match.py` 单独一支（**默认不开**，先实现 v5.4 那套基于
  `(device_letter, coord)` 的简单匹配，确认能跑通后再决定是否需要 de.py 那套
  position 备用查找）。

---

### 3.3 `core/extract.py`

**职责**：核心算法。给定一条 S 参数曲线（频率轴 + S 复数 + 阻抗复数），返回一行
`ResonatorRow`。这是整个项目的"重力中心"，最值得测试。

**输入/输出契约**

```python
from dataclasses import dataclass
import numpy as np
from typing import Optional

@dataclass(frozen=True)
class IntermediatePeak:
    fs2: float           # Hz
    fp2: float           # Hz
    Zs2_db: float        # dB
    Zp2_db: float        # dB
    score: float         # Zp2 - Zs2 (dB)


@dataclass(frozen=True)
class BodeQResult:
    fitted: float
    smooth: float
    raw: float
    f_bode: float        # Hz
    smooth_curve: np.ndarray   # 全频段平滑后的 BodeQ 曲线（用于绘图与 Qs_BodeQ）


def find_resonances(
    z_mag: np.ndarray,
    freq: np.ndarray,
    *,
    min_separation: float,        # Hz
) -> tuple[int, int]: ...


def detect_intermediate_peak(
    freq: np.ndarray,
    z_mag_db: np.ndarray,
    *,
    smooth_window_ratio: float,
    prominence_db: float,
    min_peak_valley_sep_ratio: float,
) -> Optional[IntermediatePeak]: ...


def calculate_bodeq(
    s: np.ndarray,
    freq: np.ndarray,
    *,
    savgol_polyorder: int,
    peak_range_ratio: float,
) -> BodeQResult: ...


def extract_resonator_row(
    network: rf.Network,
    *,
    config: AlgorithmConfig,
    metadata: ResonatorMetadata,   # filename / coord / EG/FL/AG/PF/Area
) -> ResonatorRow: ...
```

**搬运对照**

| 客户脚本函数 | 行号 | 改造点 |
|---|---|---|
| `find_resonances` | 605–657 | 直接搬。`min_separation` 改为关键字参数（之前默认 `20e6`）。把 `for i in range(1, len(z_db)-1)` 替换为向量化 `np.where((z_db[1:-1] < z_db[:-2]) & (z_db[1:-1] < z_db[2:]))[0] + 1`（行为等价，性能 ×10）。 |
| `detect_intermediate_peak` | 825–924 | 直接搬。把 `verbose` 参数删掉（永远不打 logger）。三个魔数都来自 config。 |
| `calculate_bodeq` | 660–821 | 主体搬。**异常分支抽公共子函数 `_compute_raw_bodeq_array`** 给主分支和 fallback 共用（B7）。`return_fit_data` 标志删除，统一返回 `BodeQResult`（绘图所需的 `fit_freq / fit_curve` 在调用方需要时再算一次，或加一个独立的 `plot_bodeq` 函数，反正不在 core 里）。删除 `DEBUG_PLOT` / `plt.show()` 分支（759–774 行）。 |
| `process_device_file` | 2441–2875 | **拆成两个函数**：① `extract_resonator_row(network, metadata, config)` —— 纯算法，输入 Network 输出 ResonatorRow；② `apply_deembed_if_available(network, cal_cache, key)` —— 单独的去嵌应用步骤，可被 ① 之前调用。`process_device_file` 里所有的 `print` / `logger` / `plt.savefig` / `import traceback` / `time.time()` 全部移到上层（worker 层）。`calculate_area` 函数整段删除，Area 改从 mapping 取（见 §3.4）。 |

**纯函数化要点**

- core 函数不接受文件路径，只接受 `rf.Network`。文件加载（`rf.Network(fpath)`）
  是上层的事。
- 不 `import traceback`、不 `traceback.print_exc()`。异常一律 `raise`，自定义
  `ResonatorExtractionError(filename, stage, original_exc)`。
- 不写 PNG 图。`plot_bodeq` 抽到 `core/plotting.py`（或更上层），算法层不依赖
  matplotlib。
- 不读 `time.time()` 计时；耗时统计是 worker 层的事。
- 频率范围筛选（`f_start / f_end`）保留，但作为 `extract_resonator_row` 调用前的
  独立步骤：`network = filter_frequency_range(network, f_start, f_end)`。

---

### 3.4 `core/mapping.py`

**职责**：加载对照表（CSV / Excel），解析 Description 列，拿到
`{mark: {display_name, eg, fl, area_s11, area_s22}}` 映射。

**输入/输出契约**

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class MarkInfo:
    mark: str            # "A1-1"
    description: str     # "EG0 FL0 700&5500"  (raw)
    eg: float            # 0.0
    fl: float            # 0.0
    area_s11: int        # 700
    area_s22: int        # 5500


@dataclass(frozen=True)
class MappingTable:
    by_mark: dict[str, MarkInfo]

    def lookup(self, mark: str) -> Optional[MarkInfo]: ...


def load_mapping(file_path: Path) -> MappingTable: ...
def parse_description(description: str) -> dict: ...
def build_display_name(filename: str, mapping: MappingTable) -> str: ...
def sanitize_filename(name: str) -> str: ...
```

**搬运对照**

| 客户脚本函数 | 行号 | 改造点 |
|---|---|---|
| `read_mapping` | 224–249 | 改成 `load_mapping`，返回 `MappingTable` 而非 `dict`。Description 列要在加载时解析（split 空格 → EG / FL / "700&5500" → split `&` → 700, 5500）。`parse_description` 单独可测。 |
| `get_display_name` | 251–285 | 修 B5（删除冗余 `alt_mark_match`）。前缀正则 `^(S11_2_|S22_2_)` 改为可配置（客户文件命名习惯可能变）。 |
| `sanitize_filename` | 421–426 | 直接搬 |

**Description 解析规则**（与客户确认过，不再使用硬编码 Area 表）：

```
"EG0 FL0.5 1200&4500"
   ↓
{
  "eg": 0.0,
  "fl": 0.5,
  "area_s11": 1200,
  "area_s22": 4500,
}
```

正则：`^EG(\d+(?:\.\d+)?)\s+FL(\d+(?:\.\d+)?)\s+(\d+)&(\d+)$`。客户文件 750 行已
全部符合此格式。若解析失败，记录 warning 并把 `area_s11/area_s22` 置为 None。

---

### 3.5 `core/filename.py`

**职责**：从原始文件名抠出"这个测量是什么"——device 字母、XY 坐标、关键词
（EG/FL/AG/PF）、文件类型（S11/S22）。

**输入/输出契约**

```python
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class ParsedFilename:
    original: str
    device_letter: Optional[str]    # "F"
    coord: str                       # "X0Y0" or filename if not matched
    x: Optional[int]                 # 0
    y: Optional[int]                 # 0
    port_type: str                   # "s11" | "s22"
    eg: Optional[float]
    fl: Optional[float]
    ag: Optional[float]
    pf_flag: bool                    # +PF


def parse_filename(filename: str) -> ParsedFilename: ...
def extract_device_key(filename: str) -> tuple[Optional[str], Optional[str]]: ...
def extract_coord(filename: str) -> str: ...
def extract_xy_from_coord(coord_str: str) -> tuple[Optional[int], Optional[int]]: ...
def extract_keywords(name: str) -> dict: ...
def extract_keyword_value(filename: str, keyword: str) -> Optional[str]: ...
def extract_suffix(filename: str) -> Optional[str]: ...
```

**搬运对照**

| 客户脚本函数 | 行号 | 改造点 |
|---|---|---|
| `extract_device_key` | 464–486 | 直接搬 |
| `extract_coord` | 927–933 | 直接搬 |
| `extract_xy_from_coord` | 935–961 | 改返回类型 `Optional[int]` 而非 `'NA'` 字符串；上层做 NA 转换 |
| `extract_keywords` | 963–985 | 修 B9（`+PF` / `PF` 二选一冗余）；返回 dataclass 而非 dict；数字保持 float 不格式化为 "0.50"（格式化是输出层的事） |
| `extract_keyword_value` | 2893–2896 | 直接搬，仅供 classify 用 |
| `extract_suffix` | 442–461 | 仅 de.py 风格的 OPEN/SHORT 匹配用，按需搬 |

`parse_filename`（新函数）把上述拆解函数串起来，给上层一个一站式 API。

---

## 4. AlgorithmConfig 字段清单

所有从客户脚本中抽出来的硬编码魔数。放在 `backend/app/config.py`：

```python
from dataclasses import dataclass, field

@dataclass
class AlgorithmConfig:
    # ===== 谐振点检测 =====
    min_separation_hz: float = 20e6            # 最小 fs/fp 间距，VNA 605 行
    boundary_warning_ratio: float = 0.05       # fs/fp 离频率边界多近时告警，VNA 2625 行

    # ===== Z dB 计算 =====
    z_mag_floor: float = 1e-12                 # log10 前的下限避免 -inf，VNA 2614 行

    # ===== 中间峰检测 =====
    intermediate_smooth_window_ratio: float = 0.01     # VNA 826 行
    intermediate_prominence_db: float = 3.0            # VNA 827 行
    intermediate_min_peak_valley_sep_ratio: float = 0.02   # VNA 828 行

    # ===== BodeQ Savitzky-Golay =====
    savgol_window_max: int = 51                # VNA 695 行
    savgol_window_ratio: int = 10              # window = len(freq)//10*2+1
    savgol_polyorder: int = 3                  # VNA 698 行
    bodeq_peak_range_ratio: float = 0.3        # 洛伦兹拟合左右各取 30% bandwidth, VNA 721 行
    bodeq_min_valid_points: int = 10           # VNA 687 行
    bodeq_denominator_min: float = 1e-6        # VNA 677 行
    bodeq_denominator_max: float = 1.0         # VNA 677 行
    bodeq_lorentz_amp_bounds: tuple[float, float] = (0.1, 10.0)   # VNA 741 行 [0.1*A0, 10*A0]
    bodeq_lorentz_f0_bounds: tuple[float, float] = (0.9, 1.1)
    bodeq_lorentz_gamma_bounds: tuple[float, float] = (0.1, 10.0)

    # ===== I/O / 并发 =====
    threadpool_max_workers: int = 4            # VNA 2944 / 2957 行

    # ===== Touchstone 拆分 =====
    s11_subdir_name: str = "S11"               # VNA 295 行
    s22_subdir_name: str = "S22"               # VNA 296 行
    sanitize_filename_max_length: int = 50     # VNA 426 行

    # ===== Deembed (默认关) =====
    deembed_enabled_default: bool = False
```

**字段调整指引**：

- 变更 `min_separation_hz`：当客户的器件 fs/fp 间距 < 20MHz（高频小型化器件）时
  调小到 5–10MHz；调太小会把噪声峰当 fp。
- 变更 `threadpool_max_workers`：迁到 Celery 后这个字段失效，并发由 Celery
  worker 数控制；保留只是为了 CLI 兼容。

---

## 5. 输出 ResonatorRow 数据类定义

完整列定义，覆盖客户脚本输出全部字段（去重并去掉等效电路参数后 24 列）：

```python
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class ResonatorRow:
    # ===== 元数据 =====
    original_filename: str
    display_name: str
    folder_name: str
    coord: str
    x: Optional[int]                  # X 坐标整数值，无法解析时 None
    y: Optional[int]
    device_letter: Optional[str]      # "F" 或 None（之前是 'N/A'）
    port_type: str                    # "s11" | "s22"

    # ===== 关键词（来自 mapping 解析） =====
    eg: Optional[float]
    fl: Optional[float]
    ag: Optional[float]
    pf: bool                          # 之前是 'Y' / 'N'，存 bool 更省事
    area: Optional[int]               # 来自 mapping description 的 700/900/... 或 5500/5000/...

    # ===== 谐振点 =====
    fs_ghz: float
    fp_ghz: float
    fp2_ghz: Optional[float]          # 中间峰，无则 None
    fs2_ghz: Optional[float]
    zs_ohm: float                     # |Z| at fs
    zp_ohm: float                     # |Z| at fp
    zp2_ohm: Optional[float]          # 10 ** (Zp2_db / 20)
    zs2_ohm: Optional[float]

    # ===== Q 值 =====
    qs: Optional[float]               # 相位法
    qp: Optional[float]
    qs_bodeq: Optional[float]         # bodeq_smooth[fs_idx]
    qp_bodeq: Optional[float]
    dbqs: Optional[float]             # 3dB 带宽法
    dbqp: Optional[float]
    bodeq_fitted: Optional[float]
    bodeq_smooth: Optional[float]
    bodeq_raw: Optional[float]
    f_bode_ghz: Optional[float]

    # ===== 耦合 =====
    k2eff_pct: Optional[float]

    # ===== 处理标记 =====
    deembedded: bool
    extraction_warning: Optional[str] = None   # "fs at boundary", "fs >= fp", etc.
```

**类型策略**：

- 计算成功 → `float`；任何一步失败（fs >= fp / 拟合失败 / 数据点不足）→ `None`。
  序列化层（pydantic / pandas）负责把 `None` 显示成 `NA` 或 `null`，core 不掺
  进字符串。
- 单位后缀进字段名（`fs_ghz` 而非 `fs`）—— 调试时一眼看到单位，少踩雷。Excel
  导出层做列名翻译：`fs_ghz → "fs(GHz)"`。
- `pf: bool` 而非 `'Y'/'N'` 字符串 —— 数据库存 `BOOLEAN` 比 char 更省，前端筛选
  也方便。

注意 §2 B2 提到客户输出有 `Qs/Qp/Qs_BodeQ/Qp_BodeQ/dbqs/dbqp` 重复列，我们这里只
输出一份。客户验收时可临时在 Excel 导出层 duplicate 这 6 列直到确认不再依赖
重复列。

---

## 6. 测试 Fixture 设计

### 6.1 测试输入

- 主要 fixture：`tests/fixtures/T8901P.01/`（解压客户提供的 `T8901P.01.zip`，
  共 12 个 S2P 文件）。在 conftest.py 里写 `pytest.fixture(scope="session")`
  解压一次。
- mapping fixture：`tests/fixtures/mapping_ELB003.xlsx`（750 行，已确认 Description
  格式 `EG{x} FL{y} {a1}&{a2}`）。
- 单元测试微 fixture：手写 numpy 数组生成 known-resonance 信号
  （RLC 串并联模型，给定 fs=2GHz / fp=2.05GHz / Q=1000，反算 S11，再用
  `find_resonances` / `calculate_bodeq` 验证能恢复 fs/fp/Q）。

### 6.2 验收基准

客户暂不提供位对位 baseline xlsx。本阶段的验收以下面三条为准：

1. **能跑通**：`T8901P.01.zip` 解压后，pipeline 端到端不抛异常，所有 12 个 S2P 文件
   都能产出对应行（DUT 行 + 必要时 OPEN/SHORT 被正确过滤掉）。
2. **输出列结构正确**：列名、顺序、单位严格匹配 §5 中 `ResonatorRow` 的字段定义；
   空值统一表达（core 层 `None`，输出层翻译为 `NA`/`null`）。
3. **数值在合理物理范围内**：fs/fp 落在被测频段内、`fs < fp`、Qs/Qp 为正且量级
   ~10²–10⁴、k²eff 在 0–10% 之间、BodeQ 与 Qs 量级一致。任何越界都要在
   `extraction_warning` 字段里有解释。

`T8601K results.xlsx` 是客户用 v5.4 脚本跑出的另一批数据（**不是 T8901**）的
列结构样例，仅作字段名/顺序/单位的视觉对照，**不能直接 diff 数值**。

### 6.3 单元测试方案

```
tests/
├── conftest.py                        # fixture 解压、临时目录、numpy 信号生成
├── core/
│   ├── test_touchstone.py
│   │     - test_split_s2p_creates_two_files
│   │     - test_split_header_data_separates_correctly
│   │     - test_modify_header_replaces_s2p_to_s1p
│   │     - test_extract_s11_data_takes_first_three_columns
│   │     - test_extract_s22_data_takes_last_two_columns
│   │
│   ├── test_deembed.py
│   │     - test_deembed_dut_returns_network
│   │     - test_deembed_handles_freq_misalignment_via_interp
│   │     - test_build_calibration_cache_keys_consistent  # 验 B3 修复
│   │
│   ├── test_extract.py
│   │     - test_find_resonances_synthetic_rlc
│   │     - test_find_resonances_min_separation_filters_close_peaks
│   │     - test_calculate_bodeq_lorentz_recovers_known_q
│   │     - test_detect_intermediate_peak_finds_known_peak
│   │     - test_detect_intermediate_peak_returns_none_when_clean
│   │     - test_extract_resonator_row_t8901_dut_001  # 完整集成
│   │
│   ├── test_mapping.py
│   │     - test_load_mapping_parses_xlsx
│   │     - test_parse_description_eg_fl_areas
│   │     - test_parse_description_invalid_returns_none
│   │     - test_build_display_name_replaces_mark
│   │
│   └── test_filename.py
│         - test_extract_device_key_with_coord
│         - test_extract_coord_returns_xy
│         - test_extract_keywords_eg_fl_ag_pf
│         - test_extract_xy_int_or_none  # 改为 int，不是 'NA' 字符串
│
└── integration/
    └── test_t8901_pipeline.py
          - test_full_pipeline_runs_without_error          # 跑通即过
          - test_column_order_matches_spec                 # 与 §5 dataclass 一致
          - test_values_within_physical_ranges             # fs<fp / Q>0 / k²eff∈[0,10%]
          - test_output_excel_loads_back_with_pandas
```

物理范围合理性（用于 `test_values_within_physical_ranges`）：

| 字段 | 合理范围 | 备注 |
|---|---|---|
| fs / fp | 在 DUT 频率轴 `[f_start, f_end]` 内，且 `fs < fp` | 边界容忍 5%（见 `boundary_warning_ratio`） |
| Qs / Qp | > 0，典型 10²–10⁴ | 异常时填 None 并写 `extraction_warning` |
| BodeQ_fitted | > 0，且与同行 Qs 同量级（差距 < 5×） | 拟合失败 → None |
| k2eff_pct | 0–10% | 越界一般是 fs/fp 检测错位 |

---

## 7. 移植任务拆分（建议工作单）

按依赖顺序排（前置任务必须先做完才能起后续任务）。"估时"按熟悉客户脚本的工程师
**净编码 + 测试**计算，不含 review / 部署。

| # | 任务 | 估时 | 依赖 | 备注 |
|---|---|---|---|---|
| T01 | 基础 dataclass：`AlgorithmConfig`、`ResonatorRow`、`ParsedFilename`、`MarkInfo`、`MappingTable`、`CalibrationCache` 落地到 `backend/app/config.py` 和 `core/models.py` | 0.5 d | — | 先把契约定下来；§4、§5 内容直接抄 |
| T02 | `core/filename.py`：搬 7 个解析函数 + B5 / B9 修复 + 单元测试（10 个 case） | 0.5 d | T01 | 全是字符串解析，最简单 |
| T03 | `core/mapping.py`：`load_mapping` + `parse_description` + 单元测试 | 0.5 d | T01, T02 | 重点测 Description 各种格式 |
| T04 | `core/touchstone.py`：搬 5 个函数 + 单元测试 | 0.5 d | T01 | 用 T8901 zip 中的真实 S2P 验证 |
| T05 | `core/extract.py` 之 `find_resonances` + `detect_intermediate_peak`：搬 + 向量化 + 测试 | 1 d | T01 | 用合成 RLC 信号验证；中间峰用真实数据手工标注一例 |
| T06 | `core/extract.py` 之 `calculate_bodeq`：搬 + B7 重构 + 测试 | 1 d | T01 | 用洛伦兹合成信号验证拟合精度 |
| T07 | `core/deembed.py`：`build_calibration_cache`（修 B3）+ `deembed_dut` + 测试 | 1 d | T01, T02 | 默认关，但代码必须留；测试要确认 B3 修复 |
| T08 | `core/extract.py` 之 `extract_resonator_row`：把 T04–T07 串起来；删 print/logger/plot | 1 d | T05, T06, T07 | 这一步会暴露 dataclass 字段是否够用 |
| T09 | `tests/integration/test_t8901_pipeline.py`：跑通 + 列结构断言 + 物理范围断言 | 1 d | T08 | **关键验收点**；不再做位对位 diff |
| T10 | 修 B2 / B4：在 worker 层（不在 core）确保 column_order 不重复、过滤集合统一 | 0.5 d | T09 | core 层已通过 dataclass 天然规避，这里只是清理上层 |

**总估时**：约 6.5–7 人天，含测试。

**建议执行顺序**：T01 → 并行 T02/T03/T04 → T05 → T06 → T07 → T08 → T09 → T10。
最容易被忽视的是 T07（去嵌缓存键名一致性，虽然默认关也要修对），最容易翻车的
是 T09（真实数据触发的边界条件）。

