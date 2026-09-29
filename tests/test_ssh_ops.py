from etc_reviewer.ssh_ops import (
    parse_os_release,
    parse_porcelain_z,
    is_untracked,
    needs_add,
    build_commit_command,
    read_machines,
    STATUS_SCRIPT,
    OS_MARKER,
    GIT_MARKER,
)


def test_parse_porcelain_z_basic():
    data = b" M modified.txt\x00?? new_file.txt\x00 D deleted.txt\x00"
    changes = parse_porcelain_z(data)
    assert changes == [
        {"status": " M", "path": "modified.txt", "orig_path": None},
        {"status": "??", "path": "new_file.txt", "orig_path": None},
        {"status": " D", "path": "deleted.txt", "orig_path": None},
    ]


def test_parse_porcelain_z_rename():
    data = b"RM renamed.txt\x00another.txt\x00?? brand_new.txt\x00"
    changes = parse_porcelain_z(data)
    assert changes[0] == {"status": "RM", "path": "renamed.txt", "orig_path": "another.txt"}
    assert changes[1] == {"status": "??", "path": "brand_new.txt", "orig_path": None}


def test_parse_porcelain_z_empty():
    assert parse_porcelain_z(b"") == []


def test_is_untracked():
    assert is_untracked("??")
    assert not is_untracked(" M")
    assert not is_untracked("RM")


def test_needs_add():
    assert needs_add("??")
    assert needs_add(" M")
    assert needs_add(" D")
    assert needs_add("MM")
    assert not needs_add("D ")
    assert not needs_add("M ")
    assert not needs_add("A ")
    assert not needs_add("R ")


def test_build_commit_command_skips_add_for_staged_deletion():
    deleted = "ananicy.d/00-default/System Utilities & Maintenance/clamd.rules"
    cmd = build_commit_command("msg", [
        {"path": deleted, "status": "D "},
        {"path": "other.conf", "status": " M"},
    ])
    assert cmd == (
        "git -C /etc add -- other.conf && "
        "git -C /etc commit -m msg -- "
        "'ananicy.d/00-default/System Utilities & Maintenance/clamd.rules' other.conf"
    )


def test_build_commit_command_only_staged_changes_has_no_add():
    cmd = build_commit_command("msg", [{"path": "gone.conf", "status": "D "}])
    assert cmd == "git -C /etc commit -m msg -- gone.conf"


def test_build_commit_command_gitignore_only():
    cmd = build_commit_command("msg", [], add_gitignore=True)
    assert cmd == "git -C /etc add -- .gitignore && git -C /etc commit -m msg -- .gitignore"


def test_build_commit_command_nothing_to_do():
    assert build_commit_command("msg", []) is None


def test_parse_os_release_pretty_name():
    blob = (
        'PRETTY_NAME="Ubuntu 22.04.3 LTS"\n'
        "NAME=\"Ubuntu\"\n"
        'VERSION_ID="22.04"\n'
    )
    assert parse_os_release(blob) == "Ubuntu 22.04.3 LTS"


def test_parse_os_release_no_pretty_name_falls_back_to_name_version():
    blob = 'NAME="Alpine Linux"\nVERSION="3.19"\n'
    assert parse_os_release(blob) == "Alpine Linux 3.19"


def test_parse_os_release_uname_fallback():
    blob = "Linux myhost 6.5.0-1-amd64 #1 SMP x86_64 GNU/Linux\n"
    assert parse_os_release(blob) == "Linux myhost 6.5.0-1-amd64 #1 SMP x86_64 GNU/Linux"


def test_parse_os_release_empty():
    assert parse_os_release("") == "Unknown"


def test_read_machines(tmp_path):
    p = tmp_path / "machines.txt"
    p.write_text("host1\n# a comment\n\nhost2 # inline comment\n  host3  \n")
    assert read_machines(str(p)) == ["host1", "host2", "host3"]


def test_status_script_markers_match_constants():
    assert STATUS_SCRIPT.encode().count(OS_MARKER.strip()) >= 1
    assert STATUS_SCRIPT.encode().count(GIT_MARKER.strip()) >= 1
