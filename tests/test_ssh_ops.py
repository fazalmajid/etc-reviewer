from etc_reviewer.ssh_ops import (
    parse_os_release,
    parse_porcelain_z,
    is_untracked,
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
