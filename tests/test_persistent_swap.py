import runpy
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def helper():
    script = Path(__file__).parents[1] / "scripts/serverwatch-swap-persistent"
    lock = MagicMock()
    with patch.dict(sys.modules, {"fcntl": lock}):
        provision = runpy.run_path(str(script))["provision"]
    namespace = provision.__globals__
    system = MagicMock(O_CREAT=1, O_RDWR=2, O_NOFOLLOW=4, O_WRONLY=8, O_EXCL=16)
    system.geteuid.return_value = 0
    system.sysconf.return_value = 4096
    system.statvfs.return_value = SimpleNamespace(f_bavail=4 * 1024**3, f_frsize=1)
    directory = MagicMock()
    directory.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
    swap = MagicMock()
    swap.__str__.return_value = "/var/lib/serverwatch-swap/persistent"
    swap.lstat.side_effect = FileNotFoundError
    namespace.update(
        os=system,
        sys=SimpleNamespace(argv=["serverwatch-swap-persistent"]),
        DIRECTORY=directory,
        SWAP=swap,
        active_swap=MagicMock(return_value={}),
        persist=MagicMock(),
        run=MagicMock(return_value="ext4"),
    )
    return provision, namespace


def test_persistent_helper_creates_and_verifies_one_2gib_file(helper):
    provision, context = helper
    swap = str(context["SWAP"])
    context["active_swap"].side_effect = [{}, {swap: context["SIZE"] - 4096}]
    assert provision() == "created"
    handle = context["os"].fdopen.return_value.__enter__.return_value
    assert handle.write.call_count == 2048
    assert context["run"].call_args.args == ("/usr/sbin/swapon", swap)
    context["persist"].assert_called_once()


def test_persistent_helper_leaves_existing_adequate_swap_untouched(helper):
    provision, context = helper
    context["active_swap"].return_value = {"/dev/zram0": context["SIZE"]}
    assert provision() == "already_active"
    context["run"].assert_not_called()
    context["persist"].assert_not_called()
    assert context["os"].open.call_count == 1


@pytest.mark.parametrize(
    "filesystem,free,error",
    [
        ("overlay", 4 * 1024**3, "unsupported_filesystem"),
        ("ext4", 1 * 1024**3, "insufficient_space"),
    ],
)
def test_persistent_helper_refuses_unsafe_storage(helper, filesystem, free, error):
    provision, context = helper
    context["run"].return_value = filesystem
    context["os"].statvfs.return_value.f_bavail = free
    with pytest.raises(ValueError, match=error):
        provision()
    assert context["os"].open.call_count == 1
    context["persist"].assert_not_called()
