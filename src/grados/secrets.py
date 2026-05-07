"""Secret resolution and keychain-backed API key management."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KEYCHAIN_SERVICE_NAME = "grados"


@dataclass(frozen=True)
class ApiKeySpec:
    field_name: str
    slug: str
    display_name: str


API_KEY_SPECS: tuple[ApiKeySpec, ...] = (
    ApiKeySpec("ELSEVIER_API_KEY", "elsevier", "Elsevier"),
    ApiKeySpec("PUBMED_API_KEY", "pubmed", "PubMed"),
    ApiKeySpec("WOS_API_KEY", "wos", "Web of Science"),
    ApiKeySpec("SPRINGER_meta_API_KEY", "springer_meta", "Springer Meta"),
    ApiKeySpec("SPRINGER_OA_API_KEY", "springer_oa", "Springer OA"),
    ApiKeySpec("LLAMAPARSE_API_KEY", "llamaparse", "LlamaParse"),
    ApiKeySpec("ZOTERO_API_KEY", "zotero", "Zotero"),
)


class SecretStoreError(RuntimeError):
    """Raised when the keychain backend cannot satisfy a secret operation."""


@dataclass
class ApiKeyStatus:
    spec: ApiKeySpec
    value: str = ""
    source: str = "missing"
    env_present: bool = False
    keychain_present: bool = False
    config_present: bool = False
    conflict: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def present(self) -> bool:
        return bool(self.value)


@dataclass
class SecretResolutionSummary:
    entries: dict[str, ApiKeyStatus]
    keychain_available: bool
    keychain_backend: str
    keychain_error: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class SecretMigrationSummary:
    migrated: list[str] = field(default_factory=list)
    cleared: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class KeychainStore:
    """Small adapter around the optional `keyring` dependency."""

    def __init__(self, *, service_name: str = KEYCHAIN_SERVICE_NAME) -> None:
        self.service_name = service_name
        self._keyring: Any | None = None
        self.backend_name = ""
        self.error = ""
        try:
            import keyring
        except ImportError:
            self.error = "Python dependency `keyring` is not installed."
            return

        self._keyring = keyring
        try:
            backend = keyring.get_keyring()
        except Exception as exc:  # pragma: no cover - extremely backend-specific
            self.error = f"Unable to initialize keychain backend: {exc}"
            return

        self.backend_name = backend.__class__.__name__
        priority = getattr(backend, "priority", 1)
        if isinstance(priority, (int, float)) and priority <= 0:
            self.error = f"No usable keychain backend ({self.backend_name})."

    @property
    def available(self) -> bool:
        return self._keyring is not None and not self.error

    def _require_keyring(self) -> Any:
        if not self.available or self._keyring is None:
            raise SecretStoreError(self.error or "Keychain backend is unavailable.")
        return self._keyring

    def get(self, slug: str) -> str:
        keyring = self._require_keyring()
        try:
            value = keyring.get_password(self.service_name, slug)
        except Exception as exc:  # pragma: no cover - backend-specific
            raise SecretStoreError(f"Failed to read keychain value for {slug}: {exc}") from exc
        return value or ""

    def set(self, slug: str, value: str) -> None:
        keyring = self._require_keyring()
        try:
            keyring.set_password(self.service_name, slug, value)
        except Exception as exc:  # pragma: no cover - backend-specific
            raise SecretStoreError(f"Failed to write keychain value for {slug}: {exc}") from exc

    def delete(self, slug: str) -> bool:
        keyring = self._require_keyring()
        try:
            keyring.delete_password(self.service_name, slug)
            return True
        except Exception as exc:  # pragma: no cover - backend-specific
            delete_error = getattr(getattr(keyring, "errors", None), "PasswordDeleteError", None)
            if delete_error is not None and isinstance(exc, delete_error):
                return False
            raise SecretStoreError(f"Failed to delete keychain value for {slug}: {exc}") from exc


def build_secret_store(*, service_name: str = KEYCHAIN_SERVICE_NAME) -> KeychainStore:
    return KeychainStore(service_name=service_name)


def iter_api_key_specs() -> tuple[ApiKeySpec, ...]:
    return API_KEY_SPECS


def resolve_api_key_spec(identifier: str) -> ApiKeySpec:
    token = identifier.strip().lower().replace("-", "_").replace(" ", "_")
    for spec in API_KEY_SPECS:
        aliases = {
            spec.slug,
            spec.field_name.lower(),
            spec.display_name.lower().replace(" ", "_"),
        }
        if token in aliases:
            return spec
    valid = ", ".join(spec.slug for spec in API_KEY_SPECS)
    raise KeyError(f"Unknown provider '{identifier}'. Valid providers: {valid}")


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"...{value[-4:]}"


def _load_raw_config(config_file: Path) -> dict[str, Any]:
    if not config_file.is_file():
        return {}
    data = json.loads(config_file.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data
    return {}


def _find_api_keys_section(data: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    for key in ("apiKeys", "api_keys"):
        section = data.get(key)
        if isinstance(section, dict):
            return key, section
    return None, None


def read_plaintext_api_keys(config_file: Path) -> dict[str, str]:
    raw = _load_raw_config(config_file)
    _, section = _find_api_keys_section(raw)
    if section is None:
        return {}
    values: dict[str, str] = {}
    for spec in API_KEY_SPECS:
        value = section.get(spec.field_name, "")
        if isinstance(value, str) and value.strip():
            values[spec.field_name] = value.strip()
    return values


def clear_plaintext_api_keys(config_file: Path, field_names: set[str]) -> list[str]:
    if not field_names or not config_file.is_file():
        return []

    raw = _load_raw_config(config_file)
    key_name, section = _find_api_keys_section(raw)
    if key_name is None or section is None:
        return []

    cleared: list[str] = []
    for field_name in sorted(field_names):
        value = section.get(field_name, "")
        if isinstance(value, str) and value:
            section[field_name] = ""
            cleared.append(field_name)

    if not cleared:
        return []

    raw[key_name] = section
    config_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=config_file.parent,
        prefix=f"{config_file.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)
        handle.write(json.dumps(raw, indent=4, ensure_ascii=False))
        handle.write("\n")
    tmp_path.replace(config_file)
    return cleared


def resolve_api_keys(
    *,
    config_file: Path,
    config_values: dict[str, str],
    auto_migrate: bool = True,
    store: KeychainStore | None = None,
) -> SecretResolutionSummary:
    store = store or build_secret_store()
    statuses: dict[str, ApiKeyStatus] = {}
    warnings: list[str] = []
    fields_to_clear: set[str] = set()

    for spec in API_KEY_SPECS:
        env_value = os.environ.get(spec.field_name, "").strip()
        config_value = str(config_values.get(spec.field_name, "") or "").strip()
        keychain_value = ""

        entry = ApiKeyStatus(
            spec=spec,
            env_present=bool(env_value),
            config_present=bool(config_value),
        )

        if store.available:
            try:
                keychain_value = store.get(spec.slug).strip()
            except SecretStoreError as exc:
                entry.warnings.append(str(exc))
                warnings.append(str(exc))
            entry.keychain_present = bool(keychain_value)
        elif config_value:
            message = (
                f"{spec.display_name}: keychain backend unavailable, using config.json fallback."
                if store.error
                else f"{spec.display_name}: keychain backend unavailable."
            )
            entry.warnings.append(message)
            warnings.append(message)

        if auto_migrate and config_value and store.available:
            try:
                if keychain_value and keychain_value != config_value:
                    entry.conflict = True
                    message = (
                        f"{spec.display_name}: config.json value differs from keychain; "
                        "skipped automatic migration."
                    )
                    entry.warnings.append(message)
                    warnings.append(message)
                else:
                    if not keychain_value:
                        store.set(spec.slug, config_value)
                        readback = store.get(spec.slug).strip()
                        if readback != config_value:
                            raise SecretStoreError("Keychain readback mismatch after write.")
                        keychain_value = readback
                        entry.keychain_present = True
                    fields_to_clear.add(spec.field_name)
            except SecretStoreError as exc:
                message = f"{spec.display_name}: failed to migrate config.json secret to keychain: {exc}"
                entry.warnings.append(message)
                warnings.append(message)

        entry.value = env_value or keychain_value or config_value
        if env_value:
            entry.source = "env"
        elif keychain_value:
            entry.source = "keychain"
        elif config_value:
            entry.source = "config"
        else:
            entry.source = "missing"

        statuses[spec.field_name] = entry

    if fields_to_clear:
        try:
            cleared = clear_plaintext_api_keys(config_file, fields_to_clear)
            for field_name in cleared:
                entry = statuses[field_name]
                entry.config_present = False
                if entry.source == "config" and entry.keychain_present:
                    entry.source = "keychain"
        except OSError as exc:
            warnings.append(f"Failed to clear migrated plaintext API keys from config.json: {exc}")

    return SecretResolutionSummary(
        entries=statuses,
        keychain_available=store.available,
        keychain_backend=store.backend_name,
        keychain_error=store.error,
        warnings=warnings,
    )


def migrate_plaintext_config_secrets(
    *,
    config_file: Path,
    provider: str | None = None,
    force: bool = False,
    store: KeychainStore | None = None,
) -> SecretMigrationSummary:
    store = store or build_secret_store()
    if not store.available:
        raise SecretStoreError(store.error or "Keychain backend is unavailable.")

    raw_values = read_plaintext_api_keys(config_file)
    specs = (resolve_api_key_spec(provider),) if provider else API_KEY_SPECS
    summary = SecretMigrationSummary()
    fields_to_clear: set[str] = set()

    for spec in specs:
        config_value = raw_values.get(spec.field_name, "").strip()
        if not config_value:
            summary.skipped.append(spec.slug)
            continue

        existing = store.get(spec.slug).strip()
        if existing and existing != config_value and not force:
            summary.warnings.append(
                f"{spec.display_name}: keychain already contains a different value; skipped."
            )
            summary.skipped.append(spec.slug)
            continue

        store.set(spec.slug, config_value)
        readback = store.get(spec.slug).strip()
        if readback != config_value:
            raise SecretStoreError(f"{spec.display_name}: keychain readback mismatch after write.")
        summary.migrated.append(spec.slug)
        fields_to_clear.add(spec.field_name)

    if fields_to_clear:
        summary.cleared = [resolve_api_key_spec(field_name).slug for field_name in clear_plaintext_api_keys(
            config_file,
            fields_to_clear,
        )]
    return summary
