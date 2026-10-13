"""Read-only upgrade guard: never stop a server with unfinished upload/export work."""

import argparse
import importlib.util
import json
from pathlib import Path


def inspect(root: Path) -> dict:
    # Embedded Python may exclude the script directory from sys.path.
    helper = Path(__file__).with_name("recover_native_precheck.py")
    spec = importlib.util.spec_from_file_location("native_precheck_paths", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, _, _, database, _ = module.installation(root)
    with module.connection(database) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "upload_tasks" not in tables:
            raise ValueError("UNRECOGNIZED_NATIVE_DATABASE")
        blockers = {}
        if "upload_drafts" in tables:
            blockers["upload_drafts"] = db.execute(
                "SELECT COUNT(*) FROM upload_drafts WHERE status = 'preparing'"
            ).fetchone()[0]
        for table in ("upload_tasks", "export_jobs"):
            if table in tables:
                blockers[table] = db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE status NOT IN "
                    "('success', 'failed', 'cancelled')"
                ).fetchone()[0]
    return {"read_only": True, "idle": not any(blockers.values()), "unfinished": blockers}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = inspect(args.root)
    except Exception as exc:
        print(json.dumps({"read_only": True, "idle": False, "error": str(exc)}))
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2))
    if not result["idle"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
