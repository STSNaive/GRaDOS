from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

import grados.cli as cli
import grados.secrets as secrets
from grados.cli import main
from grados.config import GRaDOSPaths, generate_default_config, get_secret_summary, load_config


class FakeSecretStore:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.values = dict(initial or {})
        self.available = True
        self.backend_name = "FakeKeychain"
        self.error = ""
        self.service_name = "grados"

    def get(self, slug: str) -> str:
        return self.values.get(slug, "")

    def set(self, slug: str, value: str) -> None:
        self.values[slug] = value

    def delete(self, slug: str) -> bool:
        return self.values.pop(slug, None) is not None


def _clear_api_key_env(monkeypatch) -> None:
    for spec in secrets.iter_api_key_specs():
        monkeypatch.delenv(spec.field_name, raising=False)


def test_load_config_auto_migrates_plaintext_api_keys_to_keychain(tmp_path: Path, monkeypatch) -> None:
    _clear_api_key_env(monkeypatch)
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()

    config_data = generate_default_config(paths)
    config_data["api_keys"]["ELSEVIER_API_KEY"] = "elsevier-secret-1234"
    paths.config_file.write_text(json.dumps(config_data, indent=4, ensure_ascii=False), encoding="utf-8")

    fake_store = FakeSecretStore()
    monkeypatch.setattr(secrets, "build_secret_store", lambda service_name=secrets.KEYCHAIN_SERVICE_NAME: fake_store)

    config = load_config(paths)
    summary = get_secret_summary(config)

    assert config.api_keys.ELSEVIER_API_KEY == "elsevier-secret-1234"
    assert summary is not None
    assert summary.entries["ELSEVIER_API_KEY"].source == "keychain"
    assert fake_store.values["elsevier"] == "elsevier-secret-1234"

    raw = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert raw["api_keys"]["ELSEVIER_API_KEY"] == ""


def test_load_config_auto_migrates_mixed_case_plaintext_api_key(tmp_path: Path, monkeypatch) -> None:
    _clear_api_key_env(monkeypatch)
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()

    config_data = generate_default_config(paths)
    config_data["apiKeys"] = config_data.pop("api_keys")
    config_data["apiKeys"]["SPRINGER_meta_API_KEY"] = "springer-secret-1234"
    paths.config_file.write_text(json.dumps(config_data, indent=4, ensure_ascii=False), encoding="utf-8")

    fake_store = FakeSecretStore()
    monkeypatch.setattr(secrets, "build_secret_store", lambda service_name=secrets.KEYCHAIN_SERVICE_NAME: fake_store)

    config = load_config(paths)
    summary = get_secret_summary(config)

    assert config.api_keys.SPRINGER_meta_API_KEY == "springer-secret-1234"
    assert summary is not None
    assert summary.entries["SPRINGER_meta_API_KEY"].source == "keychain"
    assert fake_store.values["springer_meta"] == "springer-secret-1234"

    raw = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert raw["apiKeys"]["SPRINGER_meta_API_KEY"] == ""


def test_auth_commands_store_status_and_clear_keychain(tmp_path: Path, monkeypatch) -> None:
    _clear_api_key_env(monkeypatch)
    home = tmp_path / "grados-home"
    paths = GRaDOSPaths(home)
    paths.ensure_directories()
    paths.config_file.write_text(
        json.dumps(generate_default_config(paths), indent=4, ensure_ascii=False),
        encoding="utf-8",
    )

    fake_store = FakeSecretStore()
    monkeypatch.setattr(cli, "build_secret_store", lambda: fake_store)
    monkeypatch.setattr(secrets, "build_secret_store", lambda service_name=secrets.KEYCHAIN_SERVICE_NAME: fake_store)

    runner = CliRunner()
    env = {"GRADOS_HOME": str(home)}

    set_result = runner.invoke(main, ["auth", "set", "elsevier", "--value", "elsevier-secret-1234"], env=env)
    assert set_result.exit_code == 0
    assert fake_store.values["elsevier"] == "elsevier-secret-1234"
    assert "Stored" in set_result.output

    status_result = runner.invoke(main, ["auth", "status"], env=env, terminal_width=200)
    assert status_result.exit_code == 0
    assert "FakeKeychain" in status_result.output
    assert "Elsevier" in status_result.output
    assert "keychain ...1234" in status_result.output

    clear_result = runner.invoke(main, ["auth", "clear", "elsevier"], env=env)
    assert clear_result.exit_code == 0
    assert "Cleared" in clear_result.output
    assert "elsevier" not in fake_store.values
