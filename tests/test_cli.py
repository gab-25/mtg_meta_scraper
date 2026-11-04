from types import SimpleNamespace

import pytest

from mtg_meta_scraper import cli


class FakeCloseable:
    def __init__(self):
        self.closed = False

    def init_schema(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def calls(monkeypatch):
    calls = SimpleNamespace(log=[], backfill_aborted=False, update_ok=True, client=FakeCloseable(), repo=FakeCloseable())
    monkeypatch.setattr(cli, "_open", lambda settings: (calls.client, calls.repo))

    def fake_backfill(*_args):
        calls.log.append("backfill")
        return SimpleNamespace(aborted=calls.backfill_aborted)

    def fake_update(*_args):
        calls.log.append("update")
        return calls.update_ok

    monkeypatch.setattr(cli, "backfill", fake_backfill)
    monkeypatch.setattr(cli, "update_with_retries", fake_update)
    return calls


@pytest.mark.parametrize("command", ["backfill", "update"])
def test_command_runs_only_itself(calls, command):
    cli.main([command])
    assert calls.log == [command]
    assert calls.client.closed and calls.repo.closed


def test_backfill_exits_non_zero_when_aborted(calls):
    calls.backfill_aborted = True
    with pytest.raises(SystemExit) as exc:
        cli.main(["backfill"])
    assert exc.value.code == 1
    assert calls.repo.closed


def test_update_exits_non_zero_when_retries_are_exhausted(calls):
    calls.update_ok = False
    with pytest.raises(SystemExit) as exc:
        cli.main(["update"])
    assert exc.value.code == 1
    assert calls.repo.closed


def test_run_command_is_gone(calls):
    with pytest.raises(SystemExit) as exc:
        cli.main(["run"])
    assert exc.value.code == 2
