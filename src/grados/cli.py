"""GRaDOS CLI entry point."""

from __future__ import annotations

import asyncio
import json
import sys
from importlib.metadata import version as pkg_version
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from grados import __version__
from grados.config import GRaDOSPaths, generate_default_config, load_config

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
@click.option("--all", "install_all", is_flag=True, help="Install all runtime assets (browser + models).")
@click.option("--with", "components", type=str, default="", help="Comma-separated components: browser,models")
def setup(install_all: bool, components: str) -> None:
    """Initialize GRaDOS: create directories, generate config, download runtime assets."""
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
    requested = set()
    if install_all:
        requested = {"browser", "models"}
    elif components:
        requested = {c.strip().lower() for c in components.split(",")}

    console.print("[bold]4/4[/bold] 运行时资产...")

    if "browser" in requested:
        _setup_browser(paths)
    else:
        browser_exists = paths.browser_chromium.exists() and any(paths.browser_chromium.iterdir())
        if browser_exists:
            console.print("  [green]✓[/green] 浏览器已安装")
        else:
            console.print('  [dim]—[/dim] 浏览器未安装  [dim]grados setup --with browser[/dim]')

    if "models" in requested:
        _setup_models(paths)
    else:
        model_exists = paths.models_embedding.exists() and any(paths.models_embedding.iterdir())
        if model_exists:
            console.print("  [green]✓[/green] 嵌入模型已就绪")
        else:
            console.print('  [dim]—[/dim] 嵌入模型未预热  [dim]grados setup --with models[/dim]')

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
    """Pre-download the default embedding model for ChromaDB."""
    console.print("  预热嵌入模型 (all-MiniLM-L6-v2)...", end=" ")
    paths.models_embedding.mkdir(parents=True, exist_ok=True)
    try:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        ef = DefaultEmbeddingFunction()
        # Trigger model download by running a dummy embedding
        ef(["warmup"])
        console.print("[green]✓[/green]")
    except Exception as e:
        console.print(f"[yellow]跳过: {e}[/yellow]")


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
    """Show GRaDOS health check: version, config, dependencies, API keys."""
    paths = GRaDOSPaths()
    config = load_config(paths)

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
    console.print(f"  {'[green]✓[/green]' if model_ok else '[dim]—[/dim]'}  嵌入模型")

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

    console.print()
    console.print("[bold]GRaDOS Update-DB[/bold]")
    console.print(f"论文目录: [cyan]{paths.papers}[/cyan]")
    console.print(f"ChromaDB: [cyan]{paths.database_chroma}[/cyan]")
    console.print()

    if not paths.papers.is_dir():
        console.print("[yellow]论文目录不存在，无需索引。[/yellow]")
        return

    md_files = list(paths.papers.glob("*.md"))
    if not md_files:
        console.print("[yellow]论文目录为空，无需索引。[/yellow]")
        return

    console.print(f"发现 {len(md_files)} 篇论文，正在索引...", end=" ")
    papers_indexed, total_chunks = index_all_papers(paths.database_chroma, paths.papers)
    console.print("[green]✓[/green]")
    console.print(f"  已索引 [bold]{papers_indexed}[/bold] 篇论文，共 [bold]{total_chunks}[/bold] 个文本块")

    stats = get_index_stats(paths.database_chroma)
    console.print(f"  数据库总计: {stats['unique_papers']} 篇 / {stats['total_chunks']} 块")
    console.print()


if __name__ == "__main__":
    main()
