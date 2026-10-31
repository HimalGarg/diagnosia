import json

import pytest

from backend import private_files
from backend.config import settings
from backend.private_files import WINDOWS, create_private_dir, is_private, make_private
from backend.records import documents, vault


def test_a_restricted_file_is_private_and_still_readable(tmp_path, expose):
    path = tmp_path / "secret.txt"
    path.write_text("secret", encoding="utf-8")
    expose(path)
    assert not is_private(path)

    make_private(path)
    assert is_private(path)
    assert path.read_text(encoding="utf-8") == "secret"


def test_a_restricted_folder_is_private_and_still_usable(tmp_path, expose):
    folder = tmp_path / "secrets"
    folder.mkdir()
    expose(folder)
    assert not is_private(folder)

    make_private(folder)
    assert is_private(folder)
    (folder / "inside.txt").write_text("secret", encoding="utf-8")
    if WINDOWS:  # a Windows folder passes its access list down. On Linux, the folder itself keeps others out.
        assert is_private(folder / "inside.txt")


@pytest.mark.skipif(not WINDOWS, reason="Windows access lists")
def test_access_for_an_account_that_no_longer_exists_is_removed(tmp_path):
    path = tmp_path / "secret.txt"
    path.write_text("secret", encoding="utf-8")
    private_files._allow_only(path, "S-1-5-21-1-2-3-4000")
    assert not is_private(path)

    make_private(path)
    assert is_private(path)
    assert path.read_text(encoding="utf-8") == "secret"


def test_a_missing_file_raises_rather_than_looking_private(tmp_path):
    with pytest.raises(OSError):
        is_private(tmp_path / "missing.txt")


def test_a_new_folder_is_private_even_inside_an_open_one(tmp_path, expose):
    expose(tmp_path)
    folder = tmp_path / "a" / "new"
    create_private_dir(folder)
    assert is_private(folder)


def test_a_folder_that_already_exists_keeps_its_permissions(tmp_path, expose):
    existing = tmp_path / "home"
    existing.mkdir()
    expose(existing)
    create_private_dir(existing)
    assert not is_private(existing)


def test_private_writes_are_private_inside_an_open_folder(tmp_path, expose):
    expose(tmp_path)
    path = tmp_path / "keys.json"
    documents.write_private(path, b"secret")
    assert is_private(path)
    assert path.read_bytes() == b"secret"
    assert [item.name for item in tmp_path.iterdir()] == ["keys.json"]


def test_private_writes_replace_an_open_file_with_a_private_one(tmp_path, expose):
    path = tmp_path / "keys.json"
    path.write_bytes(b"old")
    expose(path)
    documents.write_private(path, b"new")
    assert is_private(path)
    assert path.read_bytes() == b"new"


def key_ring():
    return json.dumps({"active": "a", "keys": {"a": vault.new_key()}})


def test_the_vault_accepts_a_private_key_file(tmp_path_factory, monkeypatch):
    path = tmp_path_factory.mktemp("good") / "keys.json"
    path.write_text(key_ring(), encoding="utf-8")
    make_private(path)
    monkeypatch.setattr(settings, "document_key_file", str(path))
    assert vault.decrypt(vault.encrypt(b"x", "c"), "c") == b"x"


def test_the_vault_refuses_a_key_file_that_was_opened_up_later(tmp_path_factory, monkeypatch, expose):
    path = tmp_path_factory.mktemp("opened") / "keys.json"
    path.write_text(key_ring(), encoding="utf-8")
    make_private(path)
    monkeypatch.setattr(settings, "document_key_file", str(path))
    vault.encrypt(b"x", "c")

    expose(path)
    with pytest.raises(vault.VaultUnavailable, match="owner only"):
        vault.encrypt(b"x", "c")


def test_the_vault_fails_closed_when_the_key_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_key_file", str(tmp_path.parent / "no-such-keys.json"))
    with pytest.raises(vault.VaultUnavailable):
        vault.encrypt(b"x", "c")
