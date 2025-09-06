# ruff: noqa: S101

import importlib.util
import types
import sys
from pathlib import Path
from unittest.mock import patch, Mock

import pytest


def load_prompt_module():
    """
    Dynamically load the source module from tests/test_prompt.py without importing
    it as a test module to avoid pytest collection issues.
    """
    src_path = Path(__file__).with_name("test_prompt.py")
    spec = importlib.util.spec_from_file_location("prompt_src", src_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["prompt_src"] = mod
    if spec.loader is None:
        raise AssertionError
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mock_base_console():
    # Provide a minimal stub for base.console.print used by the source
    fake_console = types.SimpleNamespace(print=Mock())
    base_stub = types.SimpleNamespace(console=fake_console)
    with patch.dict(sys.modules, {"base": base_stub}):
        yield fake_console


@pytest.fixture
def common_os_patches(monkeypatch):
    # Stable defaults; individual tests can override as needed
    monkeypatch.setenv("VIRTUAL_ENV", "", prepend=False)
    monkeypatch.setattr("sys.prefix", "/usr", raising=False)  # typical non-venv prefix
    monkeypatch.setattr("os.getlogin", lambda: "me", raising=True)
    monkeypatch.setattr("os.getcwd", lambda: "/home/me/sample-repo", raising=True)


def _git_result(stdout: str = "", returncode: int = 0):
    m = Mock()
    m.stdout = stdout
    m.returncode = returncode
    return m


@pytest.mark.usefixtures("common_os_patches")
def test_main_with_git_repo_and_custom_venv_shows_all_sections(mock_base_console, monkeypatch):
    # Arrange: inside git repo with branch; custom VIRTUAL_ENV should be shown and colorized
    monkeypatch.setenv("VIRTUAL_ENV", "/home/me/.venvs/myenv")
    # git rev-parse --show-toplevel -> repo root
    # git rev-parse --abbrev-ref HEAD -> branch name

    def fake_run(args, capture_output=True, text=True):
        _ = capture_output
        _ = text
        if args[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _git_result("/home/me/sample-repo\n", 0)
        if args[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return _git_result("feature/xyz\n", 0)
        return _git_result("", 1)

    monkeypatch.setattr("subprocess.run", fake_run, raising=True)

    mod = load_prompt_module()

    # Act
    mod.main()

    # Assert
    assert mock_base_console.print.call_count == 1
    args, kwargs = mock_base_console.print.call_args
    out = "".join(args)
    # Repo and branch
    assert "\uf1d3 sample-repo " in out
    assert "] feature/xyz[/]" in out
    # Venv tag colorized; note leading space before env name is preserved by the source
    assert "[pink] myenv[/]" in out
    # Path uses leading-space tilde replacement
    assert "[bright_cyan] ~[/]" in out or " ~" in out
    # Ends with plus glyph and no newline (end="")
    assert out.strip().endswith("+")
    assert kwargs.get("end", None) == ""


@pytest.mark.usefixtures("common_os_patches")
def test_main_suppresses_system_prefix_venv_when_no_virtual_env(mock_base_console, monkeypatch):
    # Arrange: No VIRTUAL_ENV; sys.prefix is /usr -> source should blank venv_name
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    # Simulate not in a git repo
    def fake_run(args, capture_output=True, text=True):
        _ = capture_output
        _ = text
        if args[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _git_result("", 128)  # non-zero -> not a repo
        return _git_result("", 1)

    monkeypatch.setattr("subprocess.run", fake_run, raising=True)

    mod = load_prompt_module()

    # Act
    mod.main()

    # Assert: no [pink] venv section present
    args, _ = mock_base_console.print.call_args
    out = "".join(args)
    assert "[pink]" not in out
    # Starts with just path info (no repo icon)
    assert "\uf1d3" not in out
    assert "[bright_cyan]" in out


@pytest.mark.usefixtures("common_os_patches")
def test_main_handles_git_branch_failure_gracefully(mock_base_console, monkeypatch):
    # Arrange: repo root resolves, but branch fails -> branch shown as '?'
    def fake_run(args, capture_output=True, text=True):
        _ = capture_output
        _ = text
        if args[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _git_result("/path/to/repo\n", 0)
        if args[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return _git_result("", 1)  # fail branch
        return _git_result("", 1)

    monkeypatch.setattr("subprocess.run", fake_run, raising=True)

    mod = load_prompt_module()

    mod.main()

    args, _ = mock_base_console.print.call_args
    out = "".join(args)
    assert "\uf1d3 repo " in out  # repo name derived from basename 'repo'
    assert " ?[/]" in out  # branch_name placeholder
    # Default venv suppressed given sys.prefix '/usr'
    assert "[pink]" not in out


@pytest.mark.usefixtures("common_os_patches")
def test_main_replaces_home_prefix_in_path_for_current_user(mock_base_console, monkeypatch):
    # Arrange specific path to ensure replacement logic kicks in
    monkeypatch.setattr("os.getcwd", lambda: "/home/me/projects/fun", raising=True)

    # No git info to simplify assertion focus
    monkeypatch.setattr("subprocess.run", lambda *_, **__: _git_result("", 128), raising=True)

    mod = load_prompt_module()

    mod.main()

    args, _ = mock_base_console.print.call_args
    out = "".join(args)
    # Replacement is " /home/<user>" -> " ~"
    assert " ~/projects/fun" in out or "[bright_cyan] ~/projects/fun" in out


@pytest.mark.usefixtures("common_os_patches")
def test_main_ignores_git_exceptions(mock_base_console, monkeypatch):
    # Force subprocess.run to raise unexpectedly; function should swallow and continue
    def raising_run(*_, **__):
        raise RuntimeError

    monkeypatch.setattr("subprocess.run", raising_run, raising=True)

    mod = load_prompt_module()

    # Should not raise; should still print prompt without git info
    mod.main()

    args, _ = mock_base_console.print.call_args
    out = "".join(args)
    assert "\uf1d3" not in out  # no repo icon present
    # Path still shown
    assert "[bright_cyan]" in out


@pytest.mark.usefixtures("common_os_patches")
@pytest.mark.parametrize(
    "venv_path, expected_fragment",
    [
        ("/home/me/.venvs/env42", "[pink] env42[/]"),
        ("/home/me/work/env", "[pink] env[/]"),
        ("", None),  # empty -> derived from sys.prefix '/usr' then suppressed
    ],
)
def test_main_formats_venv_name_when_enabled(mock_base_console, monkeypatch, venv_path, expected_fragment):
    # Arrange
    if venv_path:
        monkeypatch.setenv("VIRTUAL_ENV", venv_path)
    else:
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr("subprocess.run", lambda *_, **__: _git_result("", 128), raising=True)

    mod = load_prompt_module()

    mod.main()
    out = "".join(mock_base_console.print.call_args[0])

    if expected_fragment is None:
        assert "[pink]" not in out
    else:
        assert expected_fragment in out