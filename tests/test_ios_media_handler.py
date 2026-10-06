import sqlite3
from types import SimpleNamespace

from Whatsapp_Chat_Exporter.ios_media_handler import BackupExtractor


def extract(tmp_path, monkeypatch, rows):
    """Run the media extraction over a Manifest.db holding `rows`.

    Each row is (relativePath, flags) in the "WhatsApp.shared" domain.
    """
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    with sqlite3.connect(backup_dir / "Manifest.db") as manifest:
        manifest.execute(
            "CREATE TABLE Files "
            "(fileID TEXT, domain TEXT, relativePath TEXT, "
            "flags INTEGER, file BLOB)"
        )
        manifest.executemany(
            "INSERT INTO Files VALUES (?, ?, ?, ?, ?)",
            [("unused", "WhatsApp.shared", path, flags, None)
             for path, flags in rows],
        )

    monkeypatch.chdir(tmp_path)
    identifiers = SimpleNamespace(DOMAIN="WhatsApp.shared")

    extractor = BackupExtractor(backup_dir, identifiers, decrypt_chunk_size=0)
    extractor._extract_media_files()


def test_extract_media_files_creates_nested_directories(tmp_path, monkeypatch):
    extract(tmp_path, monkeypatch, [("parent/child", 2)])

    assert (tmp_path / "WhatsApp.shared" / "parent" / "child").is_dir()


def test_extract_media_files_goes_on_past_a_file_at_a_directory_path(
        tmp_path, monkeypatch):
    extracted = tmp_path / "WhatsApp.shared"
    extracted.mkdir()
    (extracted / "a").write_bytes(b"left from a run")

    extract(tmp_path, monkeypatch, [("a", 2), ("b", 2)])

    assert (extracted / "a").read_bytes() == b"left from a run"
    assert (extracted / "b").is_dir()
