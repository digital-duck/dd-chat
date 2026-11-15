"""Key precedence: .env file > environment variable > key typed in the UI."""
import importlib

import pytest

import env_keys


@pytest.fixture
def keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env_keys, "ENV_FILES", (tmp_path / ".env", tmp_path / "home" / ".env"))
    for var in ("A_KEY", "B_KEY", "C_KEY"):
        monkeypatch.setenv(var, "x")       # so monkeypatch restores "absent" afterwards, whatever load() sets
        monkeypatch.delenv(var)
    for d in (env_keys._typed, env_keys._process, env_keys._dotenv):
        d.clear()
    env_keys._applied.clear()
    return tmp_path


def test_typed_beats_environment_beats_dotenv(keys, monkeypatch):
    (keys / ".env").write_text("A_KEY=from-file\nB_KEY=from-file\nC_KEY=\n")
    monkeypatch.setenv("A_KEY", "from-env")
    env_keys.load()
    assert env_keys.source("A_KEY") == "environment" and env_keys.os.environ["A_KEY"] == "from-env"
    assert env_keys.source("B_KEY") == ".env" and env_keys.os.environ["B_KEY"] == "from-file"
    assert env_keys.source("C_KEY") is None                # an empty .env value is "not set"
    env_keys.set_typed("A_KEY", "typed")
    assert env_keys.source("A_KEY") == "typed here" and env_keys.os.environ["A_KEY"] == "typed"
    env_keys.set_typed("A_KEY", "")                         # forgetting it brings the environment's value back
    assert env_keys.os.environ["A_KEY"] == "from-env"


def test_typed_key_is_used_when_nothing_else_has_it(keys):
    env_keys.load()
    assert env_keys.source("C_KEY") is None
    env_keys.set_typed("C_KEY", " typed ")
    assert env_keys.source("C_KEY") == "typed here" and env_keys.os.environ["C_KEY"] == "typed"
    env_keys.set_typed("C_KEY", "")
    assert env_keys.source("C_KEY") is None and "C_KEY" not in env_keys.os.environ


def test_the_working_folder_env_beats_the_home_env(keys):
    (keys / "home").mkdir()
    (keys / "home" / ".env").write_text("A_KEY=home\nB_KEY=home\n")
    (keys / ".env").write_text("A_KEY=cwd\n")
    env_keys.load()
    assert env_keys.os.environ["A_KEY"] == "cwd" and env_keys.os.environ["B_KEY"] == "home"
