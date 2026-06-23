"""批量导入历史批次 zip 到运行中的 API 服务。"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_ALL_FAILED = 2
EXIT_USAGE = 64

POLL_INTERVAL_SEC = 5.0
TASK_TIMEOUT_SEC = 60 * 60 * 6  # 6h hard ceiling per task
HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=10.0)


@dataclass
class UploadResult:
    zip_path: Path
    batch_no: str
    status: str  # "success" | "skipped" | "failed"
    device_count: int = 0
    elapsed_sec: float = 0.0
    error_msg: str | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="bulk_upload",
        description="批量上传历史批次 zip 到 aln-data 后端。",
    )
    p.add_argument("--base-url", required=True, help="API 基础 URL，例如 http://localhost:8000")
    p.add_argument("--zip-dir", required=True, type=Path, help="含 *.zip 的目录")

    g = p.add_mutually_exclusive_group()
    g.add_argument("--mapping", type=Path, help="对照表 xlsx/xls/csv 路径")
    g.add_argument("--mapping-id", type=int, help="已有对照表 ID")

    p.add_argument("--mapping-name", help="对照表名（不给则从文件名推）")
    p.add_argument("--f-start", type=float, default=None, dest="f_start", help="起始频率 GHz")
    p.add_argument("--f-end", type=float, default=None, dest="f_end", help="结束频率 GHz")
    p.add_argument(
        "--process-type",
        choices=["S1P", "S2P", "BOTH"],
        default="BOTH",
        help="处理类型，默认 BOTH",
    )
    p.add_argument("--workers", type=int, default=1, help="并发上传数，默认 1")
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="批次号已存在则跳过该 zip",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出要做什么，不实际上传",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="任意一个上传失败即中断整批",
    )

    return p.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if not args.zip_dir.exists() or not args.zip_dir.is_dir():
        raise SystemExit(f"--zip-dir 不存在或不是目录: {args.zip_dir}")
    if args.mapping is None and args.mapping_id is None:
        raise SystemExit("必须提供 --mapping 或 --mapping-id 之一")
    if args.mapping is not None and not args.mapping.exists():
        raise SystemExit(f"--mapping 文件不存在: {args.mapping}")
    if args.workers < 1:
        raise SystemExit("--workers 必须 >= 1")


def list_zips(zip_dir: Path) -> list[Path]:
    return sorted(p for p in zip_dir.glob("*.zip") if p.is_file())


def list_batches(client: httpx.Client) -> set[str]:
    seen: set[str] = set()
    page = 1
    while True:
        r = client.get("/api/batches", params={"page": page, "size": 200})
        r.raise_for_status()
        body = r.json()
        for item in body.get("items", []):
            seen.add(item["batch_no"])
        total = int(body.get("total", 0))
        size = int(body.get("size", 200))
        if page * size >= total:
            break
        page += 1
    return seen


def find_mapping_by_name(client: httpx.Client, name: str) -> int | None:
    r = client.get("/api/mappings")
    r.raise_for_status()
    for m in r.json():
        if m.get("name") == name:
            return int(m["id"])
    return None


def create_mapping(client: httpx.Client, mapping_path: Path, name: str) -> int:
    with mapping_path.open("rb") as fh:
        files = {"file": (mapping_path.name, fh, "application/octet-stream")}
        data = {"name": name}
        r = client.post("/api/mappings", files=files, data=data)
    if r.status_code >= 400:
        raise RuntimeError(f"上传对照表失败: {r.status_code} {r.text}")
    return int(r.json()["id"])


def resolve_mapping_id(client: httpx.Client, args: argparse.Namespace) -> int:
    if args.mapping_id is not None:
        return args.mapping_id
    assert args.mapping is not None
    name = args.mapping_name or args.mapping.stem
    existing = find_mapping_by_name(client, name)
    if existing is not None:
        print(f"对照表 {name} 已存在，复用 id={existing}")
        return existing
    new_id = create_mapping(client, args.mapping, name)
    print(f"已创建对照表 {name}，id={new_id}")
    return new_id


def upload_zip(
    client: httpx.Client,
    zip_path: Path,
    mapping_id: int,
    f_start: float | None,
    f_end: float | None,
    process_type: str,
) -> dict[str, Any]:
    data: dict[str, Any] = {"mapping_id": str(mapping_id), "process_type": process_type}
    if f_start is not None:
        data["f_start_ghz"] = str(f_start)
    if f_end is not None:
        data["f_end_ghz"] = str(f_end)
    with zip_path.open("rb") as fh:
        files = {"file": (zip_path.name, fh, "application/zip")}
        r = client.post("/api/uploads", files=files, data=data)
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code} {r.text}")
    return r.json()


def poll_task(client: httpx.Client, task_id: int) -> dict[str, Any]:
    last_pct = -1
    deadline = time.monotonic() + TASK_TIMEOUT_SEC
    while True:
        r = client.get(f"/api/tasks/{task_id}")
        r.raise_for_status()
        body = r.json()
        pct = int(body.get("progress_pct") or 0)
        status = body.get("status", "")
        if pct != last_pct:
            msg = body.get("progress_msg") or ""
            print(f"       progress {pct}% {msg}")
            last_pct = pct
        if status in ("success", "failed"):
            return body
        if time.monotonic() > deadline:
            raise RuntimeError(f"任务 {task_id} 超时未完成")
        time.sleep(POLL_INTERVAL_SEC)


def get_batch_device_count(client: httpx.Client, batch_no: str) -> int:
    r = client.get(f"/api/batches/{batch_no}")
    if r.status_code >= 400:
        return 0
    return int(r.json().get("device_count") or 0)


def process_one(
    client: httpx.Client,
    zip_path: Path,
    mapping_id: int,
    args: argparse.Namespace,
    existing_batches: set[str],
) -> UploadResult:
    batch_no = zip_path.stem
    if args.skip_existing and batch_no in existing_batches:
        return UploadResult(zip_path=zip_path, batch_no=batch_no, status="skipped")

    started = time.monotonic()
    try:
        accepted = upload_zip(
            client, zip_path, mapping_id, args.f_start, args.f_end, args.process_type
        )
    except Exception as exc:
        return UploadResult(
            zip_path=zip_path,
            batch_no=batch_no,
            status="failed",
            elapsed_sec=time.monotonic() - started,
            error_msg=f"upload 失败: {exc}",
        )

    task_id = int(accepted["task_id"])
    print(f"       task_id={task_id}  batch_no={accepted.get('batch_no', batch_no)}")

    try:
        final = poll_task(client, task_id)
    except Exception as exc:
        return UploadResult(
            zip_path=zip_path,
            batch_no=batch_no,
            status="failed",
            elapsed_sec=time.monotonic() - started,
            error_msg=f"poll 失败: {exc}",
        )

    elapsed = time.monotonic() - started
    if final.get("status") == "success":
        device_count = get_batch_device_count(client, batch_no)
        return UploadResult(
            zip_path=zip_path,
            batch_no=batch_no,
            status="success",
            device_count=device_count,
            elapsed_sec=elapsed,
        )
    return UploadResult(
        zip_path=zip_path,
        batch_no=batch_no,
        status="failed",
        elapsed_sec=elapsed,
        error_msg=final.get("error_msg") or "任务失败",
    )


def format_elapsed(sec: float) -> str:
    sec = int(sec)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def run(args: argparse.Namespace) -> int:
    zips = list_zips(args.zip_dir)
    if not zips:
        print("未发现 zip 文件")
        return EXIT_OK

    if args.dry_run:
        print(f"[dry-run] 找到 {len(zips)} 个 zip：")
        for z in zips:
            print(f"  - {z.name}  -> batch_no={z.stem}")
        if args.mapping_id is not None:
            print(f"[dry-run] 将使用 mapping_id={args.mapping_id}")
        elif args.mapping is not None:
            name = args.mapping_name or args.mapping.stem
            print(f"[dry-run] 将上传/复用对照表 {name}（来自 {args.mapping}）")
        return EXIT_OK

    base_url = args.base_url.rstrip("/")
    started_total = time.monotonic()

    with httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT) as client:
        try:
            mapping_id = resolve_mapping_id(client, args)
        except Exception as exc:
            print(f"resolve mapping 失败: {exc}", file=sys.stderr)
            return EXIT_ALL_FAILED

        existing_batches: set[str] = set()
        if args.skip_existing:
            try:
                existing_batches = list_batches(client)
            except Exception as exc:
                print(f"获取已有批次失败: {exc}", file=sys.stderr)
                return EXIT_ALL_FAILED

        results: list[UploadResult] = []
        total = len(zips)

        def announce(idx: int, z: Path) -> None:
            print(f"[{idx}/{total}] Uploading {z.name}...")

        if args.workers <= 1:
            for i, z in enumerate(zips, 1):
                announce(i, z)
                if args.skip_existing and z.stem in existing_batches:
                    print("       skip: batch already exists")
                    results.append(UploadResult(zip_path=z, batch_no=z.stem, status="skipped"))
                    continue
                result = process_one(client, z, mapping_id, args, existing_batches)
                report_result(result)
                results.append(result)
                if args.strict and result.status == "failed":
                    break
        else:
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futures = {
                    ex.submit(
                        process_one, client, z, mapping_id, args, existing_batches
                    ): (i, z)
                    for i, z in enumerate(zips, 1)
                }
                for fut in as_completed(futures):
                    i, z = futures[fut]
                    announce(i, z)
                    try:
                        result = fut.result()
                    except Exception as exc:
                        result = UploadResult(
                            zip_path=z, batch_no=z.stem, status="failed", error_msg=str(exc)
                        )
                    report_result(result)
                    results.append(result)
                    if args.strict and result.status == "failed":
                        for f in futures:
                            f.cancel()
                        break

    elapsed_total = time.monotonic() - started_total
    succeeded = sum(1 for r in results if r.status == "success")
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = sum(1 for r in results if r.status == "failed")
    print()
    print(f"总结：成功 {succeeded} / 跳过 {skipped} / 失败 {failed}")
    print(f"   总耗时: {format_elapsed(elapsed_total)}")

    if failed == 0:
        return EXIT_OK
    if succeeded == 0 and skipped == 0:
        return EXIT_ALL_FAILED
    return EXIT_PARTIAL


def report_result(r: UploadResult) -> None:
    if r.status == "skipped":
        print("       skip: batch already exists")
    elif r.status == "success":
        print(
            f"       done: device_count={r.device_count}, "
            f"time={format_elapsed(r.elapsed_sec)}"
        )
    else:
        print(f"       FAILED: {r.error_msg}")


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        validate_args(args)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        print(str(exc.code), file=sys.stderr)
        return EXIT_USAGE
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
