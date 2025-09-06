# NOTE: Test framework: pytest (with unittest.mock for mocking)
# These tests focus on functions changed/added in the PR: setup_readline, tab_completer, save_history, and main loop behaviors.
# ruff: noqa: S101

import builtins
import os
import sys
import types
import io
import contextlib
from unittest import mock
import pytest

# Attempt to import target module adaptively.
# Prefer "main" at repo root; if not found, fall back to src.main or crust.main if present.
def _import_main_module():
    candidates = ["main", "src.main", "crust.main", "app.main"]
    last_err = None
    for name in candidates:
        try:
            __import__(name)
            return sys.modules[name]
        except ImportError as e:
            last_err = e
    # If still not importable, raise the last error for visibility.
    raise last_err if last_err else ImportError("Could not import main module")

# Lazily resolve to allow per-test monkeypatching of sys.modules before import
@pytest.fixture
def main_mod(monkeypatch):
    # Provide stub external modules used inside main() to avoid hard dependencies
    stubs = {
        "base": types.SimpleNamespace(
            Table=types.SimpleNamespace,
            console=types.SimpleNamespace(
                print=lambda *_, **__: None,
                file=types.SimpleNamespace(flush=lambda: None),
            ),
        ),
        "prompt_module": types.SimpleNamespace(main=lambda: None),
        "aur_check": types.SimpleNamespace(main=lambda *_, **__: None),
        "capk": types.SimpleNamespace(search=lambda *_, **__: None),
        "cd": types.SimpleNamespace(main=lambda *_, **__: None),
        "ctnp": types.SimpleNamespace(python=lambda *_, **__: None),
        "cohere": types.SimpleNamespace(
            Client=lambda *_, **__: types.SimpleNamespace(
                chat=lambda *_, **__: types.SimpleNamespace(text="")
            )
        ),
        "aliases": types.SimpleNamespace(),  # can be empty; hasattr checks will be False
    }
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)

    # Provide a safe stub for readline with attributes used across functions
    readline_stub = types.SimpleNamespace(
        read_history_file=lambda *_, **__: None,
        write_history_file=lambda *_, **__: None,
        set_history_length=lambda *_, **__: None,
        set_completer=lambda *_, **__: None,
        parse_and_bind=lambda *_, **__: None,
        add_history=lambda *_, **__: None,
        get_line_buffer=lambda: "",
        get_begidx=lambda: 0,
        get_endidx=lambda: 0,
    )
    monkeypatch.setitem(sys.modules, "readline", readline_stub)

    # Import the main module after stubbing dependencies
    mod = _import_main_module()
    return mod

# --------------------------
# Tests: setup_readline
# --------------------------
def test_setup_readline_success(monkeypatch, tmp_path, main_mod):
    # Arrange: make expanduser return a file within tmp HOME
    target_hist = tmp_path / ".crust_history"
    monkeypatch.setenv("HOME", str(tmp_path))
    # Track calls to readline methods
    calls = {}
    def _track(name):
        def _fn(*_, **__):
            calls.setdefault(name, 0)
            calls[name] += 1
        return _fn

    readline = sys.modules["readline"]
    readline.read_history_file = _track("read_history_file")
    readline.set_history_length = _track("set_history_length")
    readline.set_completer = _track("set_completer")
    readline.parse_and_bind = _track("parse_and_bind")

    # Act
    history_file = main_mod.setup_readline()

    # Assert
    assert os.path.expanduser("~/.crust_history") == str(target_hist)
    assert history_file == str(target_hist)
    # Should attempt to read existing history and set lengths
    assert calls.get("read_history_file", 0) == 1
    assert calls.get("set_history_length", 0) == 1
    # Completer and bindings configured
    assert calls.get("set_completer", 0) == 1
    # parse_and_bind called at least three times: tab + 2 arrows
    assert calls.get("parse_and_bind", 0) >= 3

def test_setup_readline_file_not_found(monkeypatch, tmp_path, main_mod):
    # Simulate FileNotFoundError while reading history; rest of setup should still succeed
    monkeypatch.setenv("HOME", str(tmp_path))
    def raise_fnf(*_):
        raise FileNotFoundError
    readline = sys.modules["readline"]
    readline.read_history_file = raise_fnf

    pb_calls = 0
    def count_bind(_):
        nonlocal pb_calls
        pb_calls += 1
    readline.parse_and_bind = count_bind
    # set_history_length shouldn't be called because it's inside the try block
    set_hist_called = False
    def set_hist(_):
        nonlocal set_hist_called
        set_hist_called = True
    readline.set_history_length = set_hist

    history_file = main_mod.setup_readline()
    assert history_file.endswith(".crust_history")
    assert pb_calls >= 3
    # Because read_history_file failed, set_history_length should not have been called
    assert set_hist_called is False

# --------------------------
# Tests: tab_completer
# --------------------------
def test_tab_completer_first_word_command_completion(main_mod):
    readline = sys.modules["readline"]

    # Simulate typing "g" as the first token
    readline.get_line_buffer = lambda: "g"
    readline.get_begidx = lambda: 1
    readline.get_endidx = lambda: 1

    results = []
    # Collect multiple states until None
    state = 0
    while True:
        res = main_mod.tab_completer("g", state)
        if res is None:
            break
        results.append(res)
        state += 1

    # Should include common commands starting with 'g'
    # Order is unspecified due to set usage; assert membership and non-empty
    assert results, "Expected at least one completion for 'g'"
    assert any(x.startswith("git") for x in results) or any(x.startswith("grep") for x in results)

def test_tab_completer_files_and_dirs_in_cwd(monkeypatch, tmp_path, main_mod):
    # Prepare files and directories
    (tmp_path / "alpha.txt").write_text("a")
    (tmp_path / "beta.txt").write_text("b")
    (tmp_path / "apple").write_text("c")
    (tmp_path / "appdir").mkdir()

    # Work in tmp_path so '.' resolves there
    monkeypatch.chdir(tmp_path)

    readline = sys.modules["readline"]
    readline.get_line_buffer = lambda: "cat a"
    readline.get_begidx = lambda: 4  # start of "a"
    readline.get_endidx = lambda: 5  # end of "a"

    # Gather matches
    results = []
    state = 0
    while True:
        res = main_mod.tab_completer("a", state)
        if res is None:
            break
        results.append(res)
        state += 1

    # Expect entries starting with 'a'; directories should end with separator
    assert "alpha.txt" in results
    assert "apple" in results
    assert f"appdir{os.path.sep}" in results

def test_tab_completer_tilde_expansion(monkeypatch, tmp_path, main_mod):
    # Simulate HOME with two directories
    home = tmp_path / "homeuser"
    docs = home / "Docs"
    dls = home / "Downloads"
    docs.mkdir(parents=True)
    dls.mkdir()
    monkeypatch.setenv("HOME", str(home))

    readline = sys.modules["readline"]
    readline.get_line_buffer = lambda: "cd ~/D"
    # Buffer: "cd ~/D" -> indices where completion token is "D" in path "~/D"
    readline.get_begidx = lambda: 4
    readline.get_endidx = lambda: 5

    results = []
    state = 0
    while True:
        res = main_mod.tab_completer("~/D", state)
        if res is None:
            break
        results.append(res)
        state += 1

    # Should present "~" prefixed results and directories end with slash
    assert f"~/Docs{os.path.sep}" in results
    assert f"~/Downloads{os.path.sep}" in results

def test_tab_completer_unreadable_directory(main_mod):
    # Force listdir to raise PermissionError
    with mock.patch("os.listdir", side_effect=PermissionError("denied")):
        readline = sys.modules["readline"]
        readline.get_line_buffer = lambda: "cat secret/"
        readline.get_begidx = lambda: 4
        readline.get_endidx = lambda: 11

        # First call to build matches
        res0 = main_mod.tab_completer("secret/", 0)
        # No matches should be produced
        assert res0 is None

def test_tab_completer_out_of_range_state_returns_none(main_mod):
    readline = sys.modules["readline"]
    readline.get_line_buffer = lambda: "pwd"
    readline.get_begidx = lambda: 3
    readline.get_endidx = lambda: 3

    # Build matches on state==0
    _ = main_mod.tab_completer("p", 0)
    # Far out-of-range should give None
    assert main_mod.tab_completer("p", 999) is None

# --------------------------
# Tests: save_history
# --------------------------
def test_save_history_success(tmp_path, main_mod):
    called = {"write_history_file": 0}
    def whf(path):
        called["write_history_file"] += 1
        assert os.path.basename(path) == ".crust_history"
    sys.modules["readline"].write_history_file = whf

    hist = tmp_path / ".crust_history"
    main_mod.save_history(str(hist))
    assert called["write_history_file"] == 1

def test_save_history_exception_captured(capsys, main_mod):
    def whf(_):
        raise RuntimeError("boom")
    sys.modules["readline"].write_history_file = whf

    main_mod.save_history("/nonexistent/.crust_history")
    out = capsys.readouterr().out
    assert "Warning: Could not save command history: boom" in out

# --------------------------
# Tests: main loop (smoke) - one iteration, external effects mocked
# --------------------------
def test_main_runs_one_iteration_and_exits_cleanly(monkeypatch, main_mod, tmp_path):
    # Redirect HOME so setup_readline writes to a safe location
    monkeypatch.setenv("HOME", str(tmp_path))

    # Provide controlled readline.stub behaviors used by main()
    readline = sys.modules["readline"]
    # History functions as no-ops
    readline.read_history_file = lambda *_, **__: None
    readline.write_history_file = lambda *_, **__: None
    readline.set_history_length = lambda *_, **__: None

    # Simulate prompt_module.main raising to trigger fallback prompt once
    sys.modules["prompt_module"].main = lambda: (_ for _ in ()).throw(Exception("prompt err"))

    # Prepare input sequence: one shell command, then KeyboardInterrupt to exit loop
    inputs = iter(["echo hi", KeyboardInterrupt()])
    def fake_input(*_):
        val = next(inputs)
        if isinstance(val, BaseException):
            raise val
        return val
    monkeypatch.setattr(builtins, "input", fake_input)

    # Stub subprocess.run to capture the called command instead of executing
    called = {}
    def fake_run(args, **_):
        called["args"] = args
        # Simulate quick return
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(main_mod.subprocess, "run", fake_run)

    # Avoid time.sleep delays
    monkeypatch.setattr(main_mod.time, "sleep", lambda *_: None)

    # Capture console prints to avoid noisy output
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        main_mod.main()

    # Assert subprocess.run was invoked with our shell command
    assert called.get("args") == ["bash", "-c", "echo hi"]