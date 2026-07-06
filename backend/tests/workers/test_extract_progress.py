import zipfile

from app.workers.extract_batch import _extract_zip


def test_zipfile_progress_callback(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workers.extract_batch._find_7z", lambda: None)
    zip_path = tmp_path / "sample.zip"
    target_dir = tmp_path / "out"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.s1p", "dummy" * 100)
        zf.writestr("b.s1p", "dummy" * 100)
    progress = []
    _extract_zip(zip_path, target_dir, progress_callback=lambda c, t: progress.append((c, t)))
    assert progress[-1] == (2, 2)
    assert len(progress) >= 2
