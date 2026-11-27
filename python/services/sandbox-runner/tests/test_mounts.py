"""Mounts (R10, F9): the mounters' folders, and their mounts read from mountinfo (escaped paths)."""

import uuid

from app.mounts import Mounts, mounter_command


def mountinfo(tmp_path, *targets):
    f = tmp_path / "mountinfo"
    f.write_text("".join(f"{40 + i} 1 0:{50 + i} / {t.replace(' ', chr(92) + '040')} rw,nosuid - fuse.rclone :webdav: rw\n"
                         for i, t in enumerate(targets)))
    return f


def test_mounted_is_read_from_mountinfo(tmp_path):
    a, b = uuid.uuid4(), uuid.uuid4()
    root = tmp_path / "mounts"
    m = Mounts(root, mountinfo(tmp_path, f"{root}/{a}/inputs", "/elsewhere"))
    assert m.mounted(a) and not m.mounted(b)


def test_escaped_paths_are_read(tmp_path):
    a = uuid.uuid4()
    root = tmp_path / "with space"
    assert Mounts(root, mountinfo(tmp_path, f"{root}/{a}/inputs")).mounted(a)


def test_remove_unmounts_what_is_left_then_the_folder(tmp_path, monkeypatch):
    a = uuid.uuid4()
    root = tmp_path / "mounts"
    m = Mounts(root, mountinfo(tmp_path, f"{root}/{a}/inputs"))
    m.create(a)
    gone = []
    monkeypatch.setattr(m, "_unmount", gone.append)
    m.remove(a)
    assert gone == [root / str(a) / "inputs"] and not m.folder(a).exists()


def test_sweep_unmounts_everything_under_the_root_deepest_first(tmp_path, monkeypatch):
    a, b = uuid.uuid4(), uuid.uuid4()
    root = tmp_path / "mounts"
    m = Mounts(root, mountinfo(tmp_path, f"{root}/{a}/inputs", f"{root}/{b}/inputs", "/not/ours"))
    m.create(a), m.create(b)
    gone = []
    monkeypatch.setattr(m, "_unmount", gone.append)
    assert m.sweep() == 2
    assert sorted(gone) == sorted([root / str(a) / "inputs", root / str(b) / "inputs"])
    assert list(root.iterdir()) == []


def test_the_mounter_reads_its_token_per_request_and_mounts_read_only():
    cmd = mounter_command("http://172.29.0.2:8080/")
    assert cmd[:3] == ["mount", ":webdav:", "/mnt/inputs"]
    assert cmd[cmd.index("--webdav-url") + 1] == "http://172.29.0.2:8080/dav/"
    assert cmd[cmd.index("--webdav-bearer-token-command") + 1] == "cat /run/session/capability"
    assert "--read-only" in cmd and "--allow-other" in cmd
