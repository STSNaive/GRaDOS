"""Install and inspect GRaDOS integrations for supported AI clients."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

SUPPORTED_CLIENTS = ("claude", "codex")
_SKILL_NAMES = ("grados",)


@dataclass
class ClientStatus:
    name: str
    cli_available: bool
    mcp_registered: bool
    skill_root: Path
    installed_skills: list[str] = field(default_factory=list)
    command_path: str = ""
    warnings: list[str] = field(default_factory=list)


def resolve_requested_clients(requested: Iterable[str]) -> list[str]:
    names = [name.strip().lower() for name in requested if name.strip()]
    if not names:
        raise ValueError("Please specify at least one client: claude, codex, or all.")

    resolved: list[str] = []
    for name in names:
        expanded = SUPPORTED_CLIENTS if name == "all" else (name,)
        for item in expanded:
            if item not in SUPPORTED_CLIENTS:
                raise ValueError(f"Unsupported client '{item}'. Supported values: claude, codex, all.")
            if item not in resolved:
                resolved.append(item)
    return resolved


def install_clients(requested: Iterable[str]) -> list[ClientStatus]:
    statuses: list[ClientStatus] = []
    for client in resolve_requested_clients(requested):
        if client == "claude":
            statuses.append(_install_claude())
        elif client == "codex":
            statuses.append(_install_codex())
    return statuses


def remove_clients(requested: Iterable[str]) -> list[ClientStatus]:
    statuses: list[ClientStatus] = []
    for client in resolve_requested_clients(requested):
        if client == "claude":
            statuses.append(_remove_claude())
        elif client == "codex":
            statuses.append(_remove_codex())
    return statuses


def inspect_clients(requested: Iterable[str] | None = None) -> list[ClientStatus]:
    names = list(SUPPORTED_CLIENTS if requested is None else resolve_requested_clients(requested))
    statuses: list[ClientStatus] = []
    for client in names:
        command_path = shutil.which(client) or ""
        if client == "claude":
            skill_root = Path.home() / ".claude" / "skills"
            statuses.append(
                ClientStatus(
                    name=client,
                    cli_available=bool(command_path),
                    mcp_registered=_claude_mcp_registered(command_path),
                    skill_root=skill_root,
                    installed_skills=_installed_skills(skill_root),
                    command_path=command_path,
                )
            )
        elif client == "codex":
            skill_root = Path.home() / ".agents" / "skills"
            statuses.append(
                ClientStatus(
                    name=client,
                    cli_available=bool(command_path),
                    mcp_registered=_codex_mcp_registered(command_path),
                    skill_root=skill_root,
                    installed_skills=_installed_skills(skill_root),
                    command_path=command_path,
                )
            )
    return statuses


def _install_claude() -> ClientStatus:
    command_path = _require_command("claude")
    skill_root = Path.home() / ".claude" / "skills"
    _install_skills(skill_root)

    _run_command([command_path, "mcp", "remove", "-s", "user", "grados"], check=False)
    add_cmd = [command_path, "mcp", "add", "-s", "user"]
    add_cmd.extend(_grados_env_args(client="claude"))
    add_cmd.extend(["grados", "--", *_grados_launch_command()])
    _run_command(add_cmd, check=True)

    return ClientStatus(
        name="claude",
        cli_available=True,
        mcp_registered=True,
        skill_root=skill_root,
        installed_skills=_installed_skills(skill_root),
        command_path=command_path,
    )


def _install_codex() -> ClientStatus:
    command_path = _require_command("codex")
    skill_root = Path.home() / ".agents" / "skills"
    _install_skills(skill_root)

    _run_command([command_path, "mcp", "remove", "grados"], check=False)
    add_cmd = [command_path, "mcp", "add", "grados"]
    add_cmd.extend(_grados_env_args(client="codex"))
    add_cmd.extend(["--", *_grados_launch_command()])
    _run_command(add_cmd, check=True)

    return ClientStatus(
        name="codex",
        cli_available=True,
        mcp_registered=True,
        skill_root=skill_root,
        installed_skills=_installed_skills(skill_root),
        command_path=command_path,
    )


def _remove_claude() -> ClientStatus:
    command_path = shutil.which("claude") or ""
    skill_root = Path.home() / ".claude" / "skills"
    if command_path:
        _run_command([command_path, "mcp", "remove", "-s", "user", "grados"], check=False)
    _remove_skills(skill_root)
    return ClientStatus(
        name="claude",
        cli_available=bool(command_path),
        mcp_registered=False,
        skill_root=skill_root,
        installed_skills=_installed_skills(skill_root),
        command_path=command_path,
    )


def _remove_codex() -> ClientStatus:
    command_path = shutil.which("codex") or ""
    skill_root = Path.home() / ".agents" / "skills"
    if command_path:
        _run_command([command_path, "mcp", "remove", "grados"], check=False)
    _remove_skills(skill_root)
    return ClientStatus(
        name="codex",
        cli_available=bool(command_path),
        mcp_registered=False,
        skill_root=skill_root,
        installed_skills=_installed_skills(skill_root),
        command_path=command_path,
    )


def _claude_mcp_registered(command_path: str) -> bool:
    if not command_path:
        return False
    result = _run_command([command_path, "mcp", "list"], check=False)
    return result.returncode == 0 and any(line.startswith("grados:") for line in result.stdout.splitlines())


def _codex_mcp_registered(command_path: str) -> bool:
    if not command_path:
        return False
    result = _run_command([command_path, "mcp", "get", "grados"], check=False)
    return result.returncode == 0


def _install_skills(target_root: Path) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    source_root = _skills_source_root()
    for skill_name in _SKILL_NAMES:
        target_dir = target_root / skill_name
        if target_dir.exists():
            shutil.rmtree(target_dir)
        _copy_tree(source_root.joinpath(skill_name), target_dir)


def _remove_skills(target_root: Path) -> None:
    for skill_name in _SKILL_NAMES:
        skill_dir = target_root / skill_name
        if skill_dir.exists():
            shutil.rmtree(skill_dir)


def _installed_skills(skill_root: Path) -> list[str]:
    installed: list[str] = []
    for skill_name in _SKILL_NAMES:
        if (skill_root / skill_name / "SKILL.md").is_file():
            installed.append(skill_name)
    return installed


def _skills_source_root() -> Traversable:
    repo_root = Path(__file__).resolve().parents[3] / "skills"
    if repo_root.is_dir():
        return repo_root
    return resources.files("grados.client_assets").joinpath("skills")


def _copy_tree(source: Traversable, target: Path) -> None:
    if not source.is_dir() and not source.is_file():
        raise FileNotFoundError(f"Missing packaged skill assets: {source}")
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            _copy_tree(child, target / child.name)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def _grados_launch_command() -> list[str]:
    if executable := shutil.which("grados"):
        return [executable]

    sibling = Path(sys.executable).with_name("grados")
    if sibling.is_file():
        return [str(sibling.resolve())]

    argv0 = Path(sys.argv[0]).expanduser()
    if argv0.name == "grados" and argv0.exists():
        return [str(argv0.resolve())]

    return ["uvx", "grados"]


def _grados_env_args(*, client: str) -> list[str]:
    if "GRADOS_HOME" not in os.environ:
        return []
    value = f"GRADOS_HOME={os.environ['GRADOS_HOME']}"
    if client == "claude":
        return ["-e", value]
    if client == "codex":
        return ["--env", value]
    return []


def _require_command(name: str) -> str:
    if executable := shutil.which(name):
        return executable
    raise RuntimeError(f"'{name}' CLI was not found on PATH.")


def _run_command(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or f"{command[0]} exited with code {result.returncode}"
        raise RuntimeError(detail)
    return result
