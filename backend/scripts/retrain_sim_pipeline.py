"""仿真清洗数据集的重训串联：Stage 1 × 3 种子 → 选最优 → Step B → 评估。

用法：
    uv run python scripts/retrain_sim_pipeline.py
产物：ckpt_sim/（stage1.pt + stage2_ft.pt + stage2_config.json）、eval_out_sim/
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "surrogate_data_sim"
OUT = ROOT / "ckpt_sim"
SEEDS = [0, 1, 2]


def run(cmd: list[str], log_name: str) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    with open(f"/tmp/{log_name}", "w") as f:
        r = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0:
        print(f"失败，见 /tmp/{log_name}", flush=True)
        print(open(f"/tmp/{log_name}").read()[-3000:])
        raise SystemExit(1)


def main() -> None:
    py = sys.executable
    vals = {}
    for seed in SEEDS:
        out_s = ROOT / f"ckpt_sim_s{seed}"
        if not (out_s / "stage1.pt").exists():
            run(
                [
                    py,
                    "scripts/train_surrogate_stage1.py",
                    "--data",
                    str(DATA),
                    "--out",
                    str(out_s),
                    "--seed",
                    str(seed),
                ],
                f"s1_sim_s{seed}.log",
            )
        hist = json.loads((out_s / "stage1_history.json").read_text())
        vals[seed] = min(h["val"] for h in hist)
        print(f"seed {seed}: best val {vals[seed]:.5f}", flush=True)

    best = min(vals, key=vals.get)  # type: ignore[arg-type]
    print(f"选 seed {best}", flush=True)
    OUT.mkdir(exist_ok=True)
    shutil.copy(ROOT / f"ckpt_sim_s{best}" / "stage1.pt", OUT / "stage1.pt")
    shutil.copy(ROOT / "checkpoints_surrogate" / "stage2_config.json", OUT / "stage2_config.json")

    if not (OUT / "stage2_ft.pt").exists():
        run(
            [
                py,
                "scripts/finetune_stage2_surrogate.py",
                "--data",
                str(DATA),
                "--ckpt",
                str(OUT),
                "--from-scratch",
            ],
            "s2b_sim.log",
        )

    run(
        [py, "scripts/zp_peak_diagnosis.py", "--data", str(DATA), "--ckpt", str(OUT)],
        "zp_sim.log",
    )
    print(open("/tmp/zp_sim.log").read())
    run(
        [
            py,
            "scripts/evaluate_surrogate.py",
            "--data",
            str(DATA),
            "--ckpt",
            str(OUT),
            "--out",
            str(ROOT / "eval_out_sim"),
        ],
        "eval_sim.log",
    )
    print(open("/tmp/eval_sim.log").read()[-4000:])


if __name__ == "__main__":
    main()
