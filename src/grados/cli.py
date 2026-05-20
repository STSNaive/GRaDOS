"""GRaDOS CLI entry point."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from collections.abc import Callable, Sequence
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from grados import __version__
from grados._retry import install_runtime_defaults
from grados.config import GRaDOSPaths, generate_default_config, get_secret_summary, load_config
from grados.integrations import inspect_clients, install_clients, remove_clients
from grados.secrets import (
    ApiKeySpec,
    ApiKeyStatus,
    SecretStoreError,
    build_secret_store,
    clear_plaintext_api_keys,
    iter_api_key_specs,
    mask_secret,
    migrate_plaintext_config_secrets,
    resolve_api_key_spec,
)

console = Console()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _check_extra(module_name: str) -> bool:
    """Check if an optional dependency is importable."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


_EXTRAS: list[tuple[str, str, str]] = [
    # (display name, module to probe, extra name)
]


def _path_stat(p: Path) -> str:
    """Return a short status string for a path."""
    if not p.exists():
        return "—"
    if p.is_file():
        size = p.stat().st_size
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size // 1024} KB"
        return f"{size // (1024 * 1024)} MB"
    # Directory — count children
    children = list(p.iterdir())
    if not children:
        return "空"
    # Count meaningful items
    files = [c for c in children if c.is_file() and not c.name.startswith(".")]
    dirs = [c for c in children if c.is_dir() and not c.name.startswith(".")]
    parts: list[str] = []
    if files:
        parts.append(f"{len(files)} 个文件")
    if dirs:
        parts.append(f"{len(dirs)} 个子目录")
    return ", ".join(parts) if parts else "✓"


def _api_key_status(entry: ApiKeyStatus) -> str:
    if not entry.present:
        return "[dim]missing[/dim]"
    preview = mask_secret(entry.value)
    return f"[green]✓[/green] {entry.source} {preview}".rstrip()


def _keychain_status_line(keychain_available: bool, backend_name: str, error: str) -> str:
    if keychain_available:
        backend = backend_name or "available"
        return f"[green]✓[/green] keychain ({backend})"
    if error:
        return f"[yellow]![/yellow] keychain unavailable: {error}"
    return "[yellow]![/yellow] keychain unavailable"


def _print_embedding_runtime_warnings(runtime: dict[str, object]) -> None:
    warnings = runtime.get("warnings")
    if not isinstance(warnings, list):
        return
    for warning in warnings:
        console.print(f"[yellow]![/yellow] {warning}")


def _require_api_key_spec(provider: str) -> ApiKeySpec:
    try:
        return resolve_api_key_spec(provider)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc


# ── CLI Group ────────────────────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """GRaDOS — Academic research MCP server."""
    if ctx.invoked_subcommand is None:
        # Default: start MCP stdio server
        from grados.server import run_server

        run_server()


# ── grados version ───────────────────────────────────────────────────────────


@main.command()
def version() -> None:
    """Show GRaDOS version."""
    console.print(f"GRaDOS [bold]{__version__}[/bold]")
    try:
        console.print(f"  fastmcp  {pkg_version('fastmcp')}")
        console.print(f"  chromadb {pkg_version('chromadb')}")
    except Exception:
        pass


@main.group("auth")
def auth_group() -> None:
    """Manage API keys stored in the OS keychain."""


@auth_group.command("set")
@click.argument("provider")
@click.option("--value", help="API key value. Prompts securely when omitted.")
@click.option("--force", is_flag=True, help="Overwrite an existing different keychain value.")
def auth_set(provider: str, value: str | None, force: bool) -> None:
    """Store one provider API key in the OS keychain."""
    spec = _require_api_key_spec(provider)
    store = build_secret_store()
    if not store.available:
        raise click.ClickException(store.error or "Keychain backend is unavailable.")

    paths = GRaDOSPaths()
    paths.ensure_directories()

    resolved_value = value or click.prompt(f"Enter API key for {spec.display_name}", hide_input=True)
    resolved_value = resolved_value.strip()
    if not resolved_value:
        raise click.ClickException("API key cannot be empty.")

    try:
        existing = store.get(spec.slug).strip()
    except SecretStoreError as exc:
        raise click.ClickException(str(exc)) from exc
    if existing and existing != resolved_value and not force:
        raise click.ClickException(
            f"{spec.display_name} already has a different keychain value. Re-run with --force to overwrite."
        )

    try:
        store.set(spec.slug, resolved_value)
        readback = store.get(spec.slug).strip()
    except SecretStoreError as exc:
        raise click.ClickException(str(exc)) from exc
    if readback != resolved_value:
        raise click.ClickException(f"Keychain readback mismatch for {spec.display_name}.")

    cleared = clear_plaintext_api_keys(paths.config_file, {spec.field_name})
    console.print(f"[green]Stored[/green] {spec.display_name} in keychain ({store.backend_name or 'backend'}).")
    if cleared:
        console.print(f"[green]Cleared[/green] plaintext {spec.field_name} from {paths.config_file}.")


@auth_group.command("status")
def auth_status() -> None:
    """Show API key presence, source, and keychain health."""
    paths = GRaDOSPaths()
    config = load_config(paths)
    summary = get_secret_summary(config)

    console.print()
    console.print("[bold]GRaDOS Auth Status[/bold]")
    console.print(f"配置文件: [cyan]{paths.config_file}[/cyan]")
    if summary is not None:
        console.print(
            _keychain_status_line(
                summary.keychain_available,
                summary.keychain_backend,
                summary.keychain_error,
            )
        )
    console.print()

    for spec in iter_api_key_specs():
        if summary is not None:
            entry = summary.entries[spec.field_name]
            console.print(f"  {_api_key_status(entry)}  {spec.display_name}")
            if entry.conflict:
                console.print("     [yellow]![/yellow] config.json value differs from keychain")
        else:
            value = getattr(config.api_keys, spec.field_name, "")
            fallback = ApiKeyStatus(spec=spec, value=value, source="config" if value else "missing")
            console.print(f"  {_api_key_status(fallback)}  {spec.display_name}")

    if summary is not None and summary.warnings:
        console.print()
        console.print("[bold]Warnings[/bold]")
        for warning in summary.warnings:
            console.print(f"  - {warning}")
    console.print()


@auth_group.command("migrate")
@click.argument("provider", required=False)
@click.option("--force", is_flag=True, help="Overwrite existing different keychain values.")
def auth_migrate(provider: str | None, force: bool) -> None:
    """Import plaintext API keys from config.json into the keychain and clear them from disk."""
    paths = GRaDOSPaths()
    if not paths.config_file.is_file():
        raise click.ClickException(f"Config file not found: {paths.config_file}")

    try:
        summary = migrate_plaintext_config_secrets(
            config_file=paths.config_file,
            provider=provider,
            force=force,
        )
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc
    except SecretStoreError as exc:
        raise click.ClickException(str(exc)) from exc

    console.print()
    console.print("[bold]GRaDOS Auth Migrate[/bold]")
    console.print(f"配置文件: [cyan]{paths.config_file}[/cyan]")
    if summary.migrated:
        console.print(f"[green]Migrated[/green]: {', '.join(summary.migrated)}")
    if summary.cleared:
        console.print(f"[green]Cleared[/green]: {', '.join(summary.cleared)}")
    if summary.skipped:
        console.print(f"[yellow]Skipped[/yellow]: {', '.join(summary.skipped)}")
    if summary.warnings:
        console.print("[bold]Warnings[/bold]")
        for warning in summary.warnings:
            console.print(f"  - {warning}")
    console.print()


@auth_group.command("clear")
@click.argument("provider")
def auth_clear(provider: str) -> None:
    """Delete one provider API key from the OS keychain."""
    spec = _require_api_key_spec(provider)
    store = build_secret_store()
    if not store.available:
        raise click.ClickException(store.error or "Keychain backend is unavailable.")

    try:
        deleted = store.delete(spec.slug)
    except SecretStoreError as exc:
        raise click.ClickException(str(exc)) from exc
    if deleted:
        console.print(f"[green]Cleared[/green] {spec.display_name} from keychain.")
    else:
        console.print(f"[dim]No keychain entry found[/dim] for {spec.display_name}.")


# ── grados setup ─────────────────────────────────────────────────────────────


@main.command()
def setup() -> None:
    """Initialize GRaDOS: create directories, generate config, and prepare runtime assets."""
    paths = GRaDOSPaths()

    console.print()
    console.print(f"[bold]GRaDOS Setup[/bold]  v{__version__}")
    console.print(f"数据根目录: [cyan]{paths.root}[/cyan]")
    console.print()

    # 1. Create directory structure
    console.print("[bold]1/4[/bold] 创建目录结构...", end=" ")
    paths.ensure_directories()
    console.print("[green]✓[/green]")

    # 2. Generate config.json if missing
    console.print("[bold]2/4[/bold] 配置文件...", end=" ")
    if paths.config_file.is_file():
        console.print(f"[yellow]已存在[/yellow] {paths.config_file}")
    else:
        default_config = generate_default_config(paths)
        paths.config_file.write_text(
            json.dumps(default_config, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )
        console.print(f"[green]已生成[/green] {paths.config_file}")
        console.print("  [dim]优先使用 `grados auth set <provider>` 写入系统 keychain[/dim]")

    # 3. Check parser runtime + optional extras
    console.print("[bold]3/4[/bold] 检测解析器依赖...")
    docling_ok = _check_extra("docling")
    docling_mark = "[green]✓[/green]" if docling_ok else "[red]✗[/red]"
    docling_hint = "" if docling_ok else "  [red]docling 缺失，请重新运行 `uv tool install grados`[/red]"
    console.print(f"  {docling_mark} docling (默认解析器){docling_hint}")
    for display_name, module_name, extra in _EXTRAS:
        installed = _check_extra(module_name)
        mark = "[green]✓[/green]" if installed else "[dim]—[/dim]"
        hint = "" if installed else f'  [dim]uv tool install "grados\\[{extra}]"[/dim]'
        console.print(f"  {mark} {display_name}{hint}")

    # 4. Runtime assets
    console.print("[bold]4/4[/bold] 运行时资产...")
    _setup_browser(paths)
    _setup_models(paths)

    console.print()
    console.print("[green bold]Setup 完成！[/green bold]")
    console.print(f"  配置文件: {paths.config_file}")
    console.print("  API keys: [cyan]grados auth set elsevier[/cyan] / [cyan]grados auth status[/cyan]")
    console.print("  运行 [cyan]grados status[/cyan] 查看完整状态")
    console.print()


def _setup_browser(paths: GRaDOSPaths) -> None:
    """Download Chrome for Testing and create profile directory."""
    console.print("  下载 Chrome for Testing...", end=" ")
    paths.browser_chromium.mkdir(parents=True, exist_ok=True)
    paths.browser_profile.mkdir(parents=True, exist_ok=True)
    paths.browser_pdf_sessions.mkdir(parents=True, exist_ok=True)
    paths.chatgpt_browser_profile.mkdir(parents=True, exist_ok=True)
    paths.chatgpt_browser_sessions.mkdir(parents=True, exist_ok=True)
    paths.browser_extensions.mkdir(parents=True, exist_ok=True)
    try:
        import subprocess

        result = subprocess.run(
            [sys.executable, "-m", "patchright", "install", "chromium"],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "PLAYWRIGHT_BROWSERS_PATH": str(paths.browser_chromium)},
        )
        if result.returncode == 0:
            console.print("[green]✓[/green]")
        else:
            console.print("[red]失败[/red]")
            if result.stderr:
                console.print(f"  [dim]{result.stderr.strip()[:200]}[/dim]")
    except Exception as e:
        console.print(f"[red]错误: {e}[/red]")


def _setup_models(paths: GRaDOSPaths) -> None:
    """Pre-download parser and embedding runtimes used by the default local stack."""
    from grados.extract.parse import prewarm_docling_models
    from grados.storage.embedding import inspect_embedding_runtime, load_embedding_backend

    config = load_config(paths)
    install_runtime_defaults(config)
    runtime = inspect_embedding_runtime(paths, config.indexing)
    console.print("  预热 Docling 模型...", end=" ")
    try:
        docling = prewarm_docling_models()
        if docling.markdown:
            console.print("[green]✓[/green]")
        else:
            console.print("[yellow]跳过[/yellow]")
            for warning in docling.warnings[:2]:
                console.print(f"  [dim]{warning}[/dim]")
            for entry in docling.debug[:2]:
                console.print(f"  [dim]{entry}[/dim]")
    except Exception as e:
        console.print(f"[yellow]跳过: {e}[/yellow]")

    _print_embedding_runtime_warnings(runtime)
    console.print(
        "  预热嵌入模型 "
        f"({config.indexing.model_id}, max_length={runtime['max_length']}, batch={runtime['batch_size_hint']})...",
        end=" ",
    )
    try:
        backend = load_embedding_backend(paths=paths, config=config.indexing)
        backend.warmup()
        console.print("[green]✓[/green]")
    except Exception as e:
        console.print(f"[yellow]跳过: {e}[/yellow]")


# ── grados client ────────────────────────────────────────────────────────────


@main.group("client")
def client_group() -> None:
    """Install, inspect, and remove Claude/Codex integrations."""


@client_group.command("install")
@click.argument("clients", nargs=-1, required=True)
def client_install(clients: tuple[str, ...]) -> None:
    """Install GRaDOS into one or more clients: claude, codex, or all."""
    try:
        statuses = install_clients(clients)
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    console.print()
    console.print(f"[bold]GRaDOS Client Install[/bold]  v{__version__}")
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("客户端", style="bold")
    table.add_column("MCP")
    table.add_column("Skill 根目录", overflow="fold")
    table.add_column("已安装技能")
    for status in statuses:
        table.add_row(
            status.name,
            "[green]已注册[/green]" if status.mcp_registered else "[yellow]未注册[/yellow]",
            str(status.skill_root),
            ", ".join(status.installed_skills) or "—",
        )
    console.print(table)
    console.print()


@client_group.command("list")
def client_list() -> None:
    """List supported clients and whether GRaDOS is currently installed."""
    statuses = inspect_clients()

    console.print()
    console.print(f"[bold]GRaDOS Client List[/bold]  v{__version__}")
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("客户端", style="bold")
    table.add_column("CLI")
    table.add_column("MCP")
    table.add_column("技能")
    for status in statuses:
        table.add_row(
            status.name,
            "[green]可用[/green]" if status.cli_available else "[red]缺失[/red]",
            "[green]已注册[/green]" if status.mcp_registered else "[dim]—[/dim]",
            ", ".join(status.installed_skills) or "[dim]—[/dim]",
        )
    console.print(table)
    console.print()


@client_group.command("doctor")
@click.argument("clients", nargs=-1, required=False)
def client_doctor(clients: tuple[str, ...]) -> None:
    """Run a lightweight health check for supported clients."""
    try:
        statuses = inspect_clients(clients or None)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    console.print()
    console.print(f"[bold]GRaDOS Client Doctor[/bold]  v{__version__}")
    for status in statuses:
        console.print(f"[bold]{status.name}[/bold]")
        console.print(f"  CLI: {'可用' if status.cli_available else '缺失'}")
        if status.command_path:
            console.print(f"  可执行文件: {status.command_path}")
        console.print(f"  MCP: {'已注册' if status.mcp_registered else '未注册'}")
        console.print(f"  Skill 根目录: {status.skill_root}")
        console.print(f"  已安装技能: {', '.join(status.installed_skills) or '—'}")
        for warning in status.warnings:
            console.print(f"  [yellow]![/yellow] {warning}")
        console.print()


@client_group.command("remove")
@click.argument("clients", nargs=-1, required=True)
def client_remove(clients: tuple[str, ...]) -> None:
    """Remove GRaDOS from one or more clients: claude, codex, or all."""
    try:
        statuses = remove_clients(clients)
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    console.print()
    console.print(f"[bold]GRaDOS Client Remove[/bold]  v{__version__}")
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("客户端", style="bold")
    table.add_column("MCP")
    table.add_column("剩余技能")
    for status in statuses:
        table.add_row(
            status.name,
            "[dim]已移除[/dim]",
            ", ".join(status.installed_skills) or "—",
        )
    console.print(table)
    console.print()


# ── grados import-pdfs ───────────────────────────────────────────────────────


@main.command("import-pdfs")
@click.option(
    "--from",
    "source",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="Path to a PDF file or a directory containing PDFs.",
)
@click.option("--recursive/--no-recursive", default=False, help="Recursively scan subdirectories.")
@click.option("--glob", "glob_pattern", default="*.pdf", show_default=True, help="Glob pattern for PDF discovery.")
@click.option(
    "--copy-to-library/--keep-in-place",
    "copy_to_library",
    default=True,
    help="Copy raw PDFs into the managed downloads archive or keep them in place.",
)
def import_pdfs(source: Path, recursive: bool, glob_pattern: str, copy_to_library: bool) -> None:
    """Import a local PDF library into the canonical paper store."""
    from grados.importing import import_local_pdf_library

    paths = GRaDOSPaths()

    console.print()
    console.print(f"[bold]GRaDOS Import PDFs[/bold]  v{__version__}")
    console.print(f"来源路径: [cyan]{source.expanduser().resolve()}[/cyan]")
    console.print(f"目标论文库: [cyan]{paths.papers}[/cyan]")
    console.print(f"Raw PDF 归档: {'开启' if copy_to_library else '关闭（keep in place）'}")
    console.print()

    result = asyncio.run(
        import_local_pdf_library(
            source_path=source,
            paths=paths,
            recursive=recursive,
            glob_pattern=glob_pattern,
            copy_to_library=copy_to_library,
        )
    )

    summary = Table(show_header=False, box=None, padding=(0, 2))
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("扫描文件", str(result.scanned))
    summary.add_row("导入成功", str(result.imported))
    summary.add_row("已跳过", str(result.skipped))
    summary.add_row("失败", str(result.failed))
    console.print(summary)

    if result.items:
        console.print()
        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column("状态", style="bold")
        table.add_column("标识")
        table.add_column("标题")
        table.add_column("来源文件", overflow="fold")
        table.add_column("说明", overflow="fold")
        for item in result.items[:20]:
            identifier = item.doi or item.safe_doi or "—"
            detail = "; ".join(item.warnings) or item.detail or item.copied_pdf_path or "—"
            table.add_row(item.status, identifier, item.title or "—", item.source_path, detail)
        console.print(table)
        remaining = len(result.items) - 20
        if remaining > 0:
            console.print(f"[dim]... 还有 {remaining} 条记录未展开[/dim]")

    if result.warnings:
        console.print()
        console.print("[bold]Warnings[/bold]")
        for warning in result.warnings:
            console.print(f"  - {warning}")

    console.print()
    console.print("[green bold]导入完成[/green bold]")
    console.print(
        "  运行 [cyan]search_saved_papers[/cyan] / [cyan]get_saved_paper_structure[/cyan] / "
        "[cyan]read_saved_paper[/cyan] 开始使用"
    )
    console.print()


# ── grados search ────────────────────────────────────────────────────────────


@main.command("search")
@click.argument("query", nargs=-1, required=True)
@click.option("--limit", default=15, show_default=True, type=click.IntRange(1, 50), help="Maximum metadata results.")
@click.option("--continuation-token", default=None, help="Token returned by a previous search.")
@click.option(
    "--indepth/--no-indepth",
    default=None,
    help=(
        "Override research.indepth.enabled for this request. "
        "Default config is off; --indepth materializes returned candidates with the same limit."
    ),
)
def search(query: tuple[str, ...], limit: int, continuation_token: str | None, indepth: bool | None) -> None:
    """Search academic metadata, optionally running indepth materialization."""
    from grados.server_tools.search_tools import search_academic_papers

    query_text = " ".join(query).strip()
    result = asyncio.run(
        search_academic_papers(
            query_text,
            limit=limit,
            continuation_token=continuation_token,
            indepth=indepth,
        )
    )
    console.print(result)


# ── grados status ────────────────────────────────────────────────────────────


@main.group("browser")
def browser_group() -> None:
    """Inspect the publisher PDF browser runtime."""


def _browser_status_payload() -> dict[str, object]:
    from grados.browser.lock import read_browser_profile_lock
    from grados.browser.manager import resolve_browser_executable
    from grados.browser.pdf.types import PDF_BROWSER_MODE_VERSION
    from grados.browser.profile import browser_profile_status

    paths = GRaDOSPaths()
    config = load_config(paths)
    resolution = resolve_browser_executable(config.extract.headless_browser, paths)
    lock = read_browser_profile_lock(paths.browser_profile)
    return {
        "protocol": PDF_BROWSER_MODE_VERSION,
        "config_file": str(paths.config_file),
        "config_exists": paths.config_file.is_file(),
        "browser_profile": str(paths.browser_profile),
        "browser_profile_status": browser_profile_status(paths.browser_profile),
        "browser_pdf_sessions": str(paths.browser_pdf_sessions),
        "browser_lock": lock,
        "browser_executable": {
            "found": resolution is not None,
            "browser": resolution.browser if resolution else "",
            "source": resolution.source if resolution else "",
            "executable_path": resolution.executable_path if resolution else "",
            "profile_directory": resolution.profile_directory if resolution else "",
        },
        "headless_browser": config.extract.headless_browser.model_dump(),
    }


@browser_group.command("status")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def browser_status(as_json: bool) -> None:
    """Show publisher PDF browser runtime state."""
    payload = _browser_status_payload()
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    executable = payload["browser_executable"]
    profile = payload["browser_profile_status"]
    assert isinstance(executable, dict)
    assert isinstance(profile, dict)
    console.print("[bold]GRaDOS browser runtime[/bold]")
    console.print(f"  Protocol: {payload['protocol']}")
    console.print(f"  Executable: {executable.get('executable_path') or 'not found'}")
    console.print(f"  Source: {executable.get('source') or 'n/a'}")
    console.print(f"  Publisher profile: {payload['browser_profile']}")
    console.print(f"  Profile initialized: {'yes' if profile.get('initialized') else 'no'}")
    console.print(f"  PDF sessions: {payload['browser_pdf_sessions']}")
    console.print(f"  Active lock: {'yes' if payload['browser_lock'] else 'no'}")


@browser_group.command("doctor")
@click.option("--live", is_flag=True, help="Run a live browser PDF acquisition probe.")
@click.option("--doi", default="", help="DOI to use for --live probe. Required when --live is set.")
def browser_doctor(live: bool, doi: str) -> None:
    """Check publisher PDF browser prerequisites and optional live capture."""
    payload = _browser_status_payload()
    executable = payload["browser_executable"]
    profile = payload["browser_profile_status"]
    assert isinstance(executable, dict)
    assert isinstance(profile, dict)

    console.print()
    console.print("[bold]Browser doctor[/bold]")
    console.print(f"  Browser executable: {executable.get('executable_path') or 'not found'}")
    console.print(f"  Browser source: {executable.get('source') or 'n/a'}")
    console.print(f"  Publisher profile: {payload['browser_profile']}")
    console.print(f"  Profile initialized: {'yes' if profile.get('initialized') else 'no'}")
    console.print(f"  PDF sessions: {payload['browser_pdf_sessions']}")
    console.print(f"  Active lock: {'yes' if payload['browser_lock'] else 'no'}")
    if not executable.get("found"):
        console.print("  [yellow]![/yellow] No compatible browser executable found. Run `grados setup`.")

    if live:
        if not doi.strip():
            raise click.ClickException("--doi is required with --live.")
        from grados.browser.generic import fetch_with_browser

        paths = GRaDOSPaths()
        config = load_config(paths)
        result = asyncio.run(
            fetch_with_browser(
                doi.strip(),
                config.extract.headless_browser,
                paths,
                max_capture_bytes=config.extract.security.max_browser_capture_bytes,
            )
        )
        console.print(f"  Live probe outcome: {result.outcome}")
        console.print(f"  State: {result.state}")
        console.print(f"  Session: {result.session_id}")
        console.print(f"  Record: {result.session_record_path}")
        if result.capture:
            console.print(f"  Capture: {result.capture}")
        if result.resume:
            console.print(f"  Resume: {result.resume}")
        for warning in result.warnings:
            console.print(f"  [yellow]![/yellow] {warning}")
    console.print()


@main.group("external-synthesis")
def external_synthesis_group() -> None:
    """Inspect the optional external synthesis protocol state."""


def _external_synthesis_status_payload() -> dict[str, object]:
    from grados.browser.chatgpt.profile import (
        chatgpt_profile_status,
        format_chatgpt_profile_setup_command,
    )

    paths = GRaDOSPaths()
    config = load_config(paths)
    enabled = bool(config.research.external_synthesis.enabled)
    profile_status = chatgpt_profile_status(paths.chatgpt_browser_profile)
    return {
        "enabled": enabled,
        "status": "enabled" if enabled else "disabled",
        "config_file": str(paths.config_file),
        "config_exists": paths.config_file.is_file(),
        "protocol": "external_synthesis_browser_v1",
        "browser_profile": str(paths.chatgpt_browser_profile),
        "browser_profile_initialized": bool(profile_status["initialized"]),
        "browser_profile_status": profile_status,
        "browser_sessions": str(paths.chatgpt_browser_sessions),
        "setup_command": format_chatgpt_profile_setup_command(paths.chatgpt_browser_profile),
    }


@external_synthesis_group.command("is-enabled")
@click.option("-q", "--quiet", is_flag=True, help="Suppress output and use exit status only.")
def external_synthesis_is_enabled(quiet: bool) -> None:
    """Return whether the external synthesis protocol is enabled."""
    payload = _external_synthesis_status_payload()
    enabled = bool(payload["enabled"])
    if not quiet:
        click.echo("true" if enabled else "false")
    if not enabled:
        raise click.exceptions.Exit(1)


@external_synthesis_group.command("status")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def external_synthesis_status(as_json: bool) -> None:
    """Show whether the external synthesis protocol is enabled."""
    payload = _external_synthesis_status_payload()

    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    enabled = bool(payload["enabled"])
    status_text = "[green]enabled[/green]" if enabled else "[dim]disabled[/dim]"
    console.print(f"External synthesis: {status_text}")
    console.print(f"Config file: {payload['config_file']}")
    console.print(f"ChatGPT profile: {payload['browser_profile']}")
    profile_status = (
        "[green]initialized[/green]"
        if payload["browser_profile_initialized"]
        else "[yellow]needs setup[/yellow]"
    )
    console.print(f"ChatGPT profile status: {profile_status}")
    if not payload["config_exists"]:
        console.print("[dim]No config file found; using default disabled state.[/dim]")
    if not payload["browser_profile_initialized"]:
        console.print(f"[dim]First-time setup: {payload['setup_command']}[/dim]")


@external_synthesis_group.command("doctor")
@click.option("--live", is_flag=True, help="Also verify the signed-in ChatGPT session.")
def external_synthesis_doctor(live: bool) -> None:
    """Check local external synthesis browser prerequisites."""
    from grados.browser.chatgpt.profile import chatgpt_profile_status
    from grados.browser.manager import resolve_browser_executable

    paths = GRaDOSPaths()
    config = load_config(paths)
    resolution = resolve_browser_executable(config.extract.headless_browser, paths)
    profile = chatgpt_profile_status(paths.chatgpt_browser_profile)

    console.print()
    console.print("[bold]External synthesis doctor[/bold]")
    console.print(f"  Enabled: {'yes' if config.research.external_synthesis.enabled else 'no'}")
    console.print(f"  Browser executable: {resolution.executable_path if resolution else 'not found'}")
    console.print(f"  ChatGPT profile: {paths.chatgpt_browser_profile}")
    console.print(f"  Profile initialized: {'yes' if profile['initialized'] else 'no'}")
    if not profile["initialized"]:
        console.print(f"  Setup: {profile['setup_command']}")
    if live:
        try:
            from grados.browser.chatgpt.runtime import check_chatgpt_login

            result = asyncio.run(check_chatgpt_login(paths, config.extract.headless_browser))
            console.print(f"  Live ChatGPT login: {'ok' if result.get('ok') else result.get('error', 'failed')}")
        except Exception as exc:
            console.print(f"  Live ChatGPT login: failed ({exc})")
    console.print()


@external_synthesis_group.command("setup-browser")
@click.option("--timeout", default=600.0, show_default=True, help="Seconds to wait for login.")
@click.option("--close-after-login", is_flag=True, help="Close the setup browser after login is detected.")
def external_synthesis_setup_browser(timeout: float, close_after_login: bool) -> None:
    """Open the private ChatGPT browser profile for first-time login."""
    from grados.browser.chatgpt.runtime import open_chatgpt_login_setup

    paths = GRaDOSPaths()
    config = load_config(paths)
    result = asyncio.run(
        open_chatgpt_login_setup(
            paths,
            config.extract.headless_browser,
            timeout_seconds=timeout,
            keep_open=not close_after_login,
        )
    )
    if result.get("ok"):
        console.print("[green]ChatGPT login detected in GRaDOS private profile.[/green]")
    else:
        console.print(f"[yellow]ChatGPT login setup incomplete:[/yellow] {result.get('error')}")
    console.print(f"Profile: {paths.chatgpt_browser_profile}")


@main.command()
def status() -> None:
    """Show GRaDOS health check: config, dependencies, assets, and index compatibility."""
    from grados.storage.embedding import inspect_embedding_runtime
    from grados.storage.vector import get_index_stats

    paths = GRaDOSPaths()
    config = load_config(paths)
    install_runtime_defaults(config)
    runtime = inspect_embedding_runtime(paths, config.indexing)
    stats = get_index_stats(paths.database_chroma, indexing_config=config.indexing)

    console.print()
    console.print(f"[bold]GRaDOS Status[/bold]  v{__version__}")
    console.print()

    # Config
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column(overflow="fold")

    config_status = "[green]✓[/green] 已加载" if paths.config_file.is_file() else "[yellow]未找到[/yellow]"
    table.add_row("配置文件", f"{config_status}  {paths.config_file}")
    table.add_row("数据根目录", str(paths.root))
    table.add_row("调试模式", "开启" if config.debug else "关闭")
    table.add_row("默认 embedding", config.indexing.model_id)
    table.add_row("索引 max_length", str(runtime["max_length"]))
    table.add_row("编码 batch", str(runtime["batch_size_hint"]))
    table.add_row("检索管线", "docs → chunks (two-stage)")
    console.print(table)
    console.print()

    # Dependencies
    console.print("[bold]核心依赖[/bold]")
    core_deps = [
        ("fastmcp", "fastmcp"),
        ("httpx", "httpx"),
        ("keyring", "keyring"),
        ("docling", "docling"),
        ("pymupdf4llm", "pymupdf4llm"),
        ("patchright", "patchright"),
        ("chromadb", "chromadb"),
        ("beautifulsoup4", "bs4"),
        ("lxml", "lxml"),
        ("sentence-transformers", "sentence_transformers"),
        ("transformers", "transformers"),
        ("torch", "torch"),
    ]
    for name, mod in core_deps:
        ok = _check_extra(mod)
        ver = ""
        if ok:
            try:
                ver = f" {pkg_version(name)}"
            except Exception:
                pass
        mark = f"[green]✓[/green]{ver}" if ok else "[red]✗ 缺失[/red]"
        console.print(f"  {mark}  {name}")

    console.print()
    console.print("[bold]可选依赖[/bold]")
    for display_name, module_name, extra in _EXTRAS:
        ok = _check_extra(module_name)
        mark = "[green]✓[/green]" if ok else "[dim]—[/dim]"
        console.print(f"  {mark}  {display_name}")

    # Runtime assets
    console.print()
    console.print("[bold]运行时资产[/bold]")
    browser_ok = (
        paths.browser_chromium.exists() and any(paths.browser_chromium.iterdir())
        if paths.browser_chromium.exists()
        else False
    )
    profile_ok = paths.browser_profile.exists()
    pdf_sessions_ok = paths.browser_pdf_sessions.exists()
    chroma_ok = paths.database_chroma.exists()
    remote_metadata_ok = paths.database_remote_metadata.exists()
    model_ok = (
        paths.models_embedding.exists() and any(paths.models_embedding.iterdir())
        if paths.models_embedding.exists()
        else False
    )

    console.print(f"  {'[green]✓[/green]' if browser_ok else '[dim]—[/dim]'}  浏览器 (Chrome for Testing)")
    console.print(f"  {'[green]✓[/green]' if profile_ok else '[dim]—[/dim]'}  浏览器配置 (persistent profile)")
    console.print(f"  {'[green]✓[/green]' if pdf_sessions_ok else '[dim]—[/dim]'}  PDF 浏览器会话")
    console.print(f"  {'[green]✓[/green]' if chroma_ok else '[dim]—[/dim]'}  ChromaDB")
    console.print(f"  {'[green]✓[/green]' if remote_metadata_ok else '[dim]—[/dim]'}  远程元数据缓存")
    console.print(f"  {'[green]✓[/green]' if model_ok else '[dim]—[/dim]'}  嵌入模型缓存")
    console.print(
        f"  {'[green]✓[/green]' if all(runtime['dependencies'].values()) else '[yellow]![/yellow]'}  "
        f"嵌入运行时 ({runtime['runtime']})"
    )
    compatibility_mark = "[green]✓[/green]" if not stats.reindex_required else "[yellow]![/yellow]"
    console.print(f"  {compatibility_mark}  索引兼容性")
    console.print(f"     provider: {runtime['provider']}")
    console.print(f"     model: {runtime['model_id']}")
    console.print(f"     query prompt: {runtime['query_prompt_mode']}")
    console.print(f"     max length: {runtime['max_length']}")
    console.print(f"     batch size: {runtime['batch_size_hint']}")
    console.print(f"     cache: {runtime['cache_dir']}")
    _print_embedding_runtime_warnings(runtime)
    if stats.embedding_dim:
        console.print(
            "     indexed dim: "
            f"{stats.embedding_dim}  |  papers: {stats.unique_papers}  chunks: {stats.total_chunks}"
        )
    if stats.reindex_required:
        console.print(f"     {stats.reindex_reason}")

    # API Keys
    console.print()
    console.print("[bold]API Keys[/bold]")
    summary = get_secret_summary(config)
    if summary is not None:
        console.print(
            f"  {_keychain_status_line(summary.keychain_available, summary.keychain_backend, summary.keychain_error)}"
        )
        for spec in iter_api_key_specs():
            entry = summary.entries[spec.field_name]
            console.print(f"  {_api_key_status(entry)}  {spec.display_name}")
            if entry.conflict:
                console.print("     [yellow]![/yellow] config.json value differs from keychain")
        if summary.warnings:
            console.print("  [dim]Use `grados auth status` for migration warnings and details.[/dim]")
    else:
        keys = config.api_keys
        for spec in iter_api_key_specs():
            value = getattr(keys, spec.field_name, "")
            entry = ApiKeyStatus(spec=spec, value=value, source="config" if value else "missing")
            console.print(f"  {_api_key_status(entry)}  {spec.display_name}")

    console.print()


# ── grados paths ─────────────────────────────────────────────────────────────


@main.command()
def paths() -> None:
    """Show all GRaDOS file paths and their status."""
    p = GRaDOSPaths()

    console.print()
    console.print("[bold]GRaDOS 文件路径[/bold]")
    console.print("─" * 60)

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold", min_width=14)
    table.add_column(min_width=30, overflow="fold")
    table.add_column(style="dim")

    for label, path in p.all_paths():
        stat = _path_stat(path)
        table.add_row(label, str(path), stat)

    console.print(table)

    # Show how to change root
    console.print()
    mode = "GRADOS_HOME" if "GRADOS_HOME" in __import__("os").environ else "默认"
    console.print(f"  [dim]模式: {mode}  |  设置 GRADOS_HOME 环境变量可自定义路径[/dim]")
    console.print()


# ── grados update-db ─────────────────────────────────────────────────────────


@main.command("update-db")
def update_db() -> None:
    """Batch-index papers/ into ChromaDB for semantic search."""
    from grados.storage.embedding import inspect_embedding_runtime
    from grados.storage.fts import ensure_fts_index
    from grados.storage.vector import get_index_stats, index_all_papers

    paths = GRaDOSPaths()
    config = load_config(paths)
    install_runtime_defaults(config)
    runtime = inspect_embedding_runtime(paths, config.indexing)

    console.print()
    console.print("[bold]GRaDOS Update-DB[/bold]")
    console.print(f"论文目录: [cyan]{paths.papers}[/cyan]")
    console.print(f"ChromaDB: [cyan]{paths.database_chroma}[/cyan]")
    console.print(f"默认 embedding: [cyan]{config.indexing.model_id}[/cyan]")
    console.print(
        f"max_length: [cyan]{runtime['max_length']}[/cyan]  |  batch: [cyan]{runtime['batch_size_hint']}[/cyan]"
    )
    _print_embedding_runtime_warnings(runtime)
    console.print()

    if not paths.papers.is_dir():
        console.print("[yellow]论文目录不存在，无需索引。[/yellow]")
        return

    md_files = list(paths.papers.glob("*.md"))
    if not md_files:
        console.print("[yellow]论文目录为空，无需索引。[/yellow]")
        return

    existing_stats = get_index_stats(paths.database_chroma, indexing_config=config.indexing)
    if existing_stats.reindex_required:
        console.print(f"[yellow]{existing_stats.reindex_reason}[/yellow]")
        console.print("请先运行 [cyan]grados reindex[/cyan] 以重建整个语义索引。")
        console.print()
        return

    console.print(f"发现 {len(md_files)} 篇论文，正在索引...", end=" ")
    try:
        papers_indexed, total_chunks = index_all_papers(
            paths.database_chroma,
            paths.papers,
            indexing_config=config.indexing,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    console.print("[green]✓[/green]")
    console.print(f"  已索引 [bold]{papers_indexed}[/bold] 篇论文，共 [bold]{total_chunks}[/bold] 个文本块")

    stats = get_index_stats(paths.database_chroma, indexing_config=config.indexing)
    console.print(f"  数据库总计: {stats.unique_papers} 篇 / {stats.total_chunks} 块")
    fts_stats = ensure_fts_index(papers_dir=paths.papers, chroma_dir=paths.database_chroma, force=True)
    console.print(f"  FTS/BM25: {fts_stats.paper_count} 篇 / {fts_stats.block_count} 块")
    console.print()


@main.command("reindex")
def reindex() -> None:
    """Rebuild the entire semantic index from scratch for the active embedding config."""
    from grados.storage.embedding import inspect_embedding_runtime
    from grados.storage.fts import ensure_fts_index
    from grados.storage.remote_metadata import migrate_remote_metadata_store
    from grados.storage.vector import get_index_stats, index_all_papers

    paths = GRaDOSPaths()
    config = load_config(paths)
    install_runtime_defaults(config)
    runtime = inspect_embedding_runtime(paths, config.indexing)

    console.print()
    console.print("[bold]GRaDOS Reindex[/bold]")
    console.print(f"论文目录: [cyan]{paths.papers}[/cyan]")
    console.print(f"ChromaDB: [cyan]{paths.database_chroma}[/cyan]")
    console.print(f"目标 embedding: [cyan]{config.indexing.model_id}[/cyan]")
    console.print(
        f"max_length: [cyan]{runtime['max_length']}[/cyan]  |  batch: [cyan]{runtime['batch_size_hint']}[/cyan]"
    )
    _print_embedding_runtime_warnings(runtime)
    console.print()

    if paths.database_chroma.exists():
        try:
            migrated = migrate_remote_metadata_store(
                paths.database_chroma,
                paths.database_remote_metadata,
                indexing_config=config.indexing,
            )
        except Exception as exc:
            raise click.ClickException(
                "Failed to migrate legacy remote_metadata before clearing the Chroma index. "
                f"Preserved the existing index directory. Error: {exc}"
            ) from exc
        if migrated:
            console.print(f"已迁移 [bold]{migrated}[/bold] 条 remote_metadata 到独立目录。")
        shutil.rmtree(paths.database_chroma, ignore_errors=True)
        console.print("已清空旧索引目录。")

    if not paths.papers.is_dir():
        console.print("[yellow]论文目录不存在，索引已重置但没有可重建的论文。[/yellow]")
        console.print()
        return

    md_files = list(paths.papers.glob("*.md"))
    if not md_files:
        console.print("[yellow]论文目录为空，索引已重置。[/yellow]")
        console.print()
        return

    console.print(f"发现 {len(md_files)} 篇论文，正在全量重建...", end=" ")
    try:
        papers_indexed, total_chunks = index_all_papers(
            paths.database_chroma,
            paths.papers,
            indexing_config=config.indexing,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    console.print("[green]✓[/green]")

    stats = get_index_stats(paths.database_chroma, indexing_config=config.indexing)
    console.print(f"  已重建 [bold]{papers_indexed}[/bold] 篇论文，共 [bold]{total_chunks}[/bold] 个文本块")
    console.print(f"  当前索引: {stats.unique_papers} 篇 / {stats.total_chunks} 块")
    fts_stats = ensure_fts_index(papers_dir=paths.papers, chroma_dir=paths.database_chroma, force=True)
    console.print(f"  FTS/BM25: {fts_stats.paper_count} 篇 / {fts_stats.block_count} 块")
    console.print()


@main.command("eval-retrieval")
@click.option(
    "--fixture",
    "fixture_path",
    required=True,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="JSONL retrieval eval fixture.",
)
@click.option("--k", default=5, show_default=True, type=click.IntRange(1, 50), help="Metric cutoff.")
@click.option("--limit", default=10, show_default=True, type=click.IntRange(1, 50), help="Search limit per case.")
@click.option("--dense-only", is_flag=True, help="Use dense_only mode instead of hybrid/RRF.")
@click.option("--json-output", is_flag=True, help="Print machine-readable JSON.")
def eval_retrieval(fixture_path: Path, k: int, limit: int, dense_only: bool, json_output: bool) -> None:
    """Evaluate saved-paper retrieval against a JSONL fixture."""
    from grados.storage.papers import read_paper
    from grados.storage.search_pipeline import search_saved_library

    paths = GRaDOSPaths()
    config = load_config(paths)
    install_runtime_defaults(config)
    cases = _load_retrieval_fixture(fixture_path)
    if not cases:
        raise click.ClickException(f"No retrieval cases found in {fixture_path}")

    per_case: list[dict[str, object]] = []
    recall_hits = 0
    reciprocal_rank_total = 0.0
    answerable_cases = 0
    block_hit_cases = 0
    block_eval_cases = 0
    no_answer_cases = 0
    no_answer_false_positives = 0
    verify_checks = 0
    verify_passes = 0
    started = time.perf_counter()

    for case in cases:
        question = str(case.get("question", "")).strip()
        if not question:
            continue
        case_started = time.perf_counter()
        pipeline_result = search_saved_library(
            chroma_dir=paths.database_chroma,
            papers_dir=paths.papers,
            query=question,
            limit=limit,
            use_reranking=not dense_only,
            indexing_config=config.indexing,
        )
        results = pipeline_result.results[:k]
        elapsed_ms = (time.perf_counter() - case_started) * 1000
        answerable = _case_answerable(case)
        first_paper_rank = _first_paper_hit_rank(results, case.get("gold_papers", []))
        block_hit = _case_block_hit(results, case)
        has_block_gold = bool(case.get("gold_blocks") or case.get("acceptable_windows"))
        verified = _verify_result_windows(results, paths.papers, read_paper)

        if answerable:
            answerable_cases += 1
            if first_paper_rank is not None:
                recall_hits += 1
                reciprocal_rank_total += 1.0 / first_paper_rank
            if has_block_gold:
                block_eval_cases += 1
                if block_hit:
                    block_hit_cases += 1
        else:
            no_answer_cases += 1
            if results:
                no_answer_false_positives += 1

        if results:
            verify_checks += 1
            if verified:
                verify_passes += 1

        per_case.append(
            {
                "question": question,
                "answerable": answerable,
                "mode": pipeline_result.mode,
                "retrievers": pipeline_result.retrievers,
                "recall_hit": first_paper_rank is not None,
                "first_paper_rank": first_paper_rank,
                "block_hit": block_hit if has_block_gold else None,
                "verify_window_readable": verified if results else None,
                "latency_ms": round(elapsed_ms, 2),
                "top_results": [
                    {
                        "rank": result.rank or index,
                        "doi": result.doi,
                        "safe_doi": result.safe_doi,
                        "block_id": result.block_id,
                        "paragraph_start": result.paragraph_start,
                        "paragraph_count": result.paragraph_count,
                        "score": round(float(result.score or 0.0), 8),
                        "mode": result.mode,
                        "retriever": result.retriever,
                    }
                    for index, result in enumerate(results, 1)
                ],
                "warnings": pipeline_result.warnings,
            }
        )

    total_elapsed_ms = (time.perf_counter() - started) * 1000
    latency_values = [_float_metric(case.get("latency_ms", 0.0)) for case in per_case]
    summary = {
        "cases": len(per_case),
        "answerable_cases": answerable_cases,
        "recall_at_k": _safe_ratio(recall_hits, answerable_cases),
        "mrr_at_k": _safe_ratio(reciprocal_rank_total, answerable_cases),
        "block_hit_rate": _safe_ratio(block_hit_cases, block_eval_cases),
        "no_answer_cases": no_answer_cases,
        "no_answer_false_positive_rate": _safe_ratio(no_answer_false_positives, no_answer_cases),
        "verify_window_readable_rate": _safe_ratio(verify_passes, verify_checks),
        "latency_ms_avg": _safe_ratio(sum(latency_values), len(per_case)),
        "latency_ms_total": round(total_elapsed_ms, 2),
        "mode": "dense_only" if dense_only else "hybrid_rrf",
        "k": k,
    }
    payload = {"summary": summary, "cases": per_case}

    if json_output:
        console.print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    console.print()
    console.print("[bold]GRaDOS Retrieval Eval[/bold]")
    console.print(f"fixture: [cyan]{fixture_path}[/cyan]")
    console.print(f"k: [cyan]{k}[/cyan]  |  mode: [cyan]{summary['mode']}[/cyan]")
    table = Table(show_header=True)
    table.add_column("metric")
    table.add_column("value", justify="right")
    for key in (
        "cases",
        "answerable_cases",
        "recall_at_k",
        "mrr_at_k",
        "block_hit_rate",
        "no_answer_false_positive_rate",
        "verify_window_readable_rate",
        "latency_ms_avg",
    ):
        value = summary[key]
        table.add_row(key, f"{value:.4f}" if isinstance(value, float) else str(value))
    console.print(table)
    console.print()


def _load_retrieval_fixture(path: Path) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise click.ClickException(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise click.ClickException(f"Invalid JSONL at {path}:{line_number}: expected object")
        cases.append(loaded)
    return cases


def _case_answerable(case: dict[str, object]) -> bool:
    value = case.get("answerability", True)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "no", "no-answer", "unanswerable", "insufficient"}


def _first_paper_hit_rank(results: Sequence[object], gold_papers: object) -> int | None:
    gold = _gold_strings(gold_papers)
    if not gold:
        return None
    for index, result in enumerate(results, 1):
        identifiers = {
            str(getattr(result, "doi", "") or "").lower(),
            str(getattr(result, "safe_doi", "") or "").lower(),
            f"grados://papers/{str(getattr(result, 'safe_doi', '') or '').lower()}",
        }
        if identifiers & gold:
            return index
    return None


def _case_block_hit(results: Sequence[object], case: dict[str, object]) -> bool:
    gold_blocks = _gold_strings(case.get("gold_blocks", []))
    windows = case.get("acceptable_windows", [])
    for result in results:
        block_id = str(getattr(result, "block_id", "") or "").lower()
        if block_id and block_id in gold_blocks:
            return True
        if _window_matches(result, windows):
            return True
    return False


def _window_matches(result: object, windows: object) -> bool:
    if not isinstance(windows, list):
        return False
    safe_doi = str(getattr(result, "safe_doi", "") or "").lower()
    start = int(getattr(result, "paragraph_start", 0) or 0)
    count = int(getattr(result, "paragraph_count", 0) or 0)
    end = start + max(0, count)
    for window in windows:
        if not isinstance(window, dict):
            continue
        window_safe = str(window.get("safe_doi") or window.get("paper_id") or "").lower()
        if window_safe and window_safe != safe_doi:
            continue
        window_start = int(window.get("paragraph_start", 0) or 0)
        window_count = int(window.get("paragraph_count", 1) or 1)
        window_end = window_start + max(0, window_count)
        if start < window_end and window_start < end:
            return True
    return False


def _verify_result_windows(
    results: Sequence[object],
    papers_dir: Path,
    read_paper_func: Callable[..., Any],
) -> bool:
    for result in results:
        safe_doi = str(getattr(result, "safe_doi", "") or "")
        paragraph_count = int(getattr(result, "paragraph_count", 0) or 0)
        if not safe_doi or paragraph_count <= 0:
            continue
        window = read_paper_func(
            papers_dir=papers_dir,
            safe_doi=safe_doi,
            start_paragraph=int(getattr(result, "paragraph_start", 0) or 0),
            max_paragraphs=paragraph_count,
        )
        return bool(window and window.text.strip())
    return False


def _gold_strings(values: object) -> set[str]:
    if isinstance(values, str):
        return {values.lower()}
    if not isinstance(values, list):
        return set()
    output: set[str] = set()
    for value in values:
        if isinstance(value, str):
            output.add(value.lower())
        elif isinstance(value, dict):
            for key in ("doi", "safe_doi", "paper_id", "canonical_uri", "block_id"):
                if value.get(key):
                    output.add(str(value[key]).lower())
    return output


def _safe_ratio(numerator: float, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / denominator, 6)


def _float_metric(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return 0.0


if __name__ == "__main__":
    main()
