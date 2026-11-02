import runpy
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def helper():
    # Exercise the real fixed-operation logic without Linux, disk writes or sudo.
    with patch.dict(sys.modules, {"fcntl": MagicMock()}):
        namespace = runpy.run_path(str(Path(__file__).parents[1] / "scripts/serverwatch-swap"))
    function = namespace["main"]
    context = function.__globals__
    operating_system = MagicMock()
    operating_system.geteuid.return_value = 0
    operating_system.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
    operating_system.path.lexists.return_value = False
    operating_system.statvfs.return_value = SimpleNamespace(f_bavail=3 * 1024**3, f_frsize=1)
    operating_system.fdopen.return_value.__enter__.return_value = MagicMock()
    context.update(
        os=operating_system,
        active=MagicMock(return_value=False),
        result=MagicMock(),
        sys=SimpleNamespace(argv=["serverwatch-swap"]),
        subprocess=MagicMock(),
    )
    context["subprocess"].run.return_value.returncode = 0
    return function, context


def test_helper_refuses_existing_file(helper):
    main, context = helper
    context["os"].path.lexists.return_value = True
    main()
    assert context["result"].call_args.args[0] == "denied"
    context["subprocess"].run.assert_not_called()
    assert context["os"].open.call_count == 1


def test_helper_refuses_low_disk(helper):
    main, context = helper
    context["os"].statvfs.return_value.f_bavail = 100
    main()
    assert "Недостатньо" in context["result"].call_args.args[1]
    context["subprocess"].run.assert_not_called()


def test_helper_existing_active_swap_is_idempotent(helper):
    main, context = helper
    context["active"].return_value = True
    main()
    assert context["result"].call_args.args[0] == "already_active"
    context["subprocess"].run.assert_not_called()


def test_helper_parallel_operation_refused(helper):
    main, context = helper
    context["fcntl"].flock.side_effect = BlockingIOError
    main()
    assert "уже виконується" in context["result"].call_args.args[1]
    context["subprocess"].run.assert_not_called()


def test_helper_success_verifies_swap(helper):
    main, context = helper
    context["active"].side_effect = [False, True]
    main()
    commands = [call.args[0] for call in context["subprocess"].run.call_args_list]
    assert commands == [
        ["/usr/sbin/mkswap", context["FILE"]],
        ["/usr/sbin/swapon", context["FILE"]],
    ]
    assert context["result"].call_args.args[0] == "created"
    target = context["os"].fdopen.return_value.__enter__.return_value
    assert target.write.call_count == 1024


def test_helper_cannot_accept_arguments(helper):
    main, context = helper
    context["sys"].argv.append("/arbitrary/file")
    main()
    context["os"].open.assert_not_called()
