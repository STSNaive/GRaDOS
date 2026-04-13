"""GRaDOS CLI entry point."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from importlib.metadata import version as pkg_version
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from grados import __version__
from grados.config import GRaDOSPaths, generate_default_config, load_config
from grados.integrations import inspect_clients, install_clients, remove_clients

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
    ("marker-pdf", "marker", "marker"),
    ("docling", "docling", "docling"),
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


def _api_key_status(key: str) -> str:
    if not key:
        return "[dim]未设置[/dim]"
    return f"[green]✓[/green] ...{key[-4:]}"


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
        console.print("  [dim]请用编辑器打开配置文件填写 API keys[/dim]")

    # 3. Check installed extras
    console.print("[bold]3/4[/bold] 检测可选依赖...")
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
    console.print("  运行 [cyan]grados status[/cyan] 查看完整状态")
    console.print()


def _setup_browser(paths: GRaDOSPaths) -> None:
    """Download Chrome for Testing and create profile directory."""
    console.print("  下载 Chrome for Testing...", end=" ")
    paths.browser_chromium.mkdir(parents=True, exist_ok=True)
    paths.browser_profile.mkdir(parents=True, exist_ok=True)
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
    """Pre-download the configured embedding backend and verify query/doc encoding."""
    from grados.storage.embedding import load_embedding_backend

    config = load_config(paths)
    console.print(f"  预热嵌入模型 ({config.indexing.model_id})...", end=" ")
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


# ── grados migrate-config ────────────────────────────────────────────────────


@main.command("migrate-config")
@click.option(
    "--from",
    "source",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to legacy grados-config.json or its directory.",
)
@click.option("--dry-run", is_flag=True, help="Preview the migration without writing files.")
@click.option("--force", is_flag=True, help="Overwrite config and merge into non-empty destination directories.")
def migrate_config(source: Path | None, dry_run: bool, force: bool) -> None:
    """Migrate a legacy TypeScript GRaDOS install into the Python layout."""
    from grados.setup.migration import find_legacy_config, migrate_legacy_install

    target_paths = GRaDOSPaths()
    source_config = find_legacy_config(source, target_paths)
    if source_config is None:
        raise click.ClickException(
            "未找到旧版 grados-config.json。请使用 --from 指定旧配置文件或其所在目录。"
        )

    console.print()
    console.print(f"[bold]GRaDOS Migrate-Config[/bold]  v{__version__}")
    console.print(f"旧配置文件: [cyan]{source_config}[/cyan]")
    console.print(f"目标数据根: [cyan]{target_paths.root}[/cyan]")
    console.print()

    try:
        result = migrate_legacy_install(
            source_config,
            target_paths,
            force=force,
            dry_run=dry_run,
        )
    except FileExistsError as exc:
        raise click.ClickException(str(exc)) from exc

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("项目", style="bold")
    table.add_column("来源")
    table.add_column("目标")
    table.add_column("状态")

    for action in result.actions:
        detail = f" ({action.detail})" if action.detail else ""
        table.add_row(
            action.label,
            str(action.source),
            str(action.destination),
            f"{action.status}{detail}",
        )

    console.print(table)

    if result.warnings:
        console.print()
        console.print("[bold]说明[/bold]")
        for warning in result.warnings:
            console.print(f"  - {warning}")

    console.print()
    if dry_run:
        console.print("[yellow]Dry-run 完成[/yellow]，未写入任何文件。")
    else:
        console.print(f"[green bold]迁移完成[/green bold]，新配置已写入 {result.target_config}")
        console.print("  运行 [cyan]grados status[/cyan] 检查当前安装状态")
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
            detail = item.detail or item.copied_pdf_path or "—"
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


# ── grados status ────────────────────────────────────────────────────────────


@main.command()
def status() -> None:
    """Show GRaDOS health check: config, dependencies, assets, and index compatibility."""
    from grados.storage.embedding import inspect_embedding_runtime
    from grados.storage.vector import get_index_stats

    paths = GRaDOSPaths()
    config = load_config(paths)
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
    table.add_row("检索管线", "docs → chunks (two-stage)")
    console.print(table)
    console.print()

    # Dependencies
    console.print("[bold]核心依赖[/bold]")
    core_deps = [
        ("fastmcp", "fastmcp"),
        ("httpx", "httpx"),
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
    chroma_ok = paths.database_chroma.exists()
    model_ok = (
        paths.models_embedding.exists() and any(paths.models_embedding.iterdir())
        if paths.models_embedding.exists()
        else False
    )

    console.print(f"  {'[green]✓[/green]' if browser_ok else '[dim]—[/dim]'}  浏览器 (Chrome for Testing)")
    console.print(f"  {'[green]✓[/green]' if profile_ok else '[dim]—[/dim]'}  浏览器配置 (persistent profile)")
    console.print(f"  {'[green]✓[/green]' if chroma_ok else '[dim]—[/dim]'}  ChromaDB")
    console.print(f"  {'[green]✓[/green]' if model_ok else '[dim]—[/dim]'}  嵌入模型缓存")
    console.print(
        f"  {'[green]✓[/green]' if all(runtime['dependencies'].values()) else '[yellow]![/yellow]'}  "
        f"嵌入运行时 ({runtime['runtime']})"
    )
    compatibility_mark = "[green]✓[/green]" if not stats["reindex_required"] else "[yellow]![/yellow]"
    console.print(f"  {compatibility_mark}  索引兼容性")
    console.print(f"     provider: {runtime['provider']}")
    console.print(f"     model: {runtime['model_id']}")
    console.print(f"     query prompt: {runtime['query_prompt_mode']}")
    console.print(f"     cache: {runtime['cache_dir']}")
    if stats["embedding_dim"]:
        console.print(
            "     indexed dim: "
            f"{stats['embedding_dim']}  |  papers: {stats['unique_papers']}  chunks: {stats['total_chunks']}"
        )
    if stats["reindex_required"]:
        console.print(f"     {stats['reindex_reason']}")

    # API Keys
    console.print()
    console.print("[bold]API Keys[/bold]")
    keys = config.api_keys
    for field_name, display in [
        ("ELSEVIER_API_KEY", "Elsevier"),
        ("WOS_API_KEY", "Web of Science"),
        ("SPRINGER_meta_API_KEY", "Springer Meta"),
        ("SPRINGER_OA_API_KEY", "Springer OA"),
        ("LLAMAPARSE_API_KEY", "LlamaParse"),
        ("ZOTERO_API_KEY", "Zotero"),
    ]:
        val = getattr(keys, field_name, "")
        console.print(f"  {_api_key_status(val)}  {display}")

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
    from grados.storage.vector import get_index_stats, index_all_papers

    paths = GRaDOSPaths()
    config = load_config(paths)

    console.print()
    console.print("[bold]GRaDOS Update-DB[/bold]")
    console.print(f"论文目录: [cyan]{paths.papers}[/cyan]")
    console.print(f"ChromaDB: [cyan]{paths.database_chroma}[/cyan]")
    console.print(f"默认 embedding: [cyan]{config.indexing.model_id}[/cyan]")
    console.print()

    if not paths.papers.is_dir():
        console.print("[yellow]论文目录不存在，无需索引。[/yellow]")
        return

    md_files = list(paths.papers.glob("*.md"))
    if not md_files:
        console.print("[yellow]论文目录为空，无需索引。[/yellow]")
        return

    existing_stats = get_index_stats(paths.database_chroma, indexing_config=config.indexing)
    if existing_stats["reindex_required"]:
        console.print(f"[yellow]{existing_stats['reindex_reason']}[/yellow]")
        console.print("请先运行 [cyan]grados reindex[/cyan] 以重建整个语义索引。")
        console.print()
        return

    console.print(f"发现 {len(md_files)} 篇论文，正在索引...", end=" ")
    papers_indexed, total_chunks = index_all_papers(
        paths.database_chroma,
        paths.papers,
        indexing_config=config.indexing,
    )
    console.print("[green]✓[/green]")
    console.print(f"  已索引 [bold]{papers_indexed}[/bold] 篇论文，共 [bold]{total_chunks}[/bold] 个文本块")

    stats = get_index_stats(paths.database_chroma, indexing_config=config.indexing)
    console.print(f"  数据库总计: {stats['unique_papers']} 篇 / {stats['total_chunks']} 块")
    console.print()


@main.command("reindex")
def reindex() -> None:
    """Rebuild the entire semantic index from scratch for the active embedding config."""
    from grados.storage.vector import get_index_stats, index_all_papers

    paths = GRaDOSPaths()
    config = load_config(paths)

    console.print()
    console.print("[bold]GRaDOS Reindex[/bold]")
    console.print(f"论文目录: [cyan]{paths.papers}[/cyan]")
    console.print(f"ChromaDB: [cyan]{paths.database_chroma}[/cyan]")
    console.print(f"目标 embedding: [cyan]{config.indexing.model_id}[/cyan]")
    console.print()

    if paths.database_chroma.exists():
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
    papers_indexed, total_chunks = index_all_papers(
        paths.database_chroma,
        paths.papers,
        indexing_config=config.indexing,
    )
    console.print("[green]✓[/green]")

    stats = get_index_stats(paths.database_chroma, indexing_config=config.indexing)
    console.print(f"  已重建 [bold]{papers_indexed}[/bold] 篇论文，共 [bold]{total_chunks}[/bold] 个文本块")
    console.print(f"  当前索引: {stats['unique_papers']} 篇 / {stats['total_chunks']} 块")
    console.print()


if __name__ == "__main__":
    main()
