"""CLI commands for preset curricula (``deeptutor learning …``)."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from deeptutor.learning.presets import apply_preset, list_presets

console = Console()


def register(app: typer.Typer) -> None:
    @app.command("list")
    def learning_list() -> None:
        """List available preset curricula."""
        presets = list_presets()
        if not presets:
            console.print("[yellow]No presets available.[/yellow]")
            return
        table = Table(title="Available Presets", show_lines=True)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Name", style="white")
        table.add_column("Subject", style="green")
        table.add_column("Grade", style="yellow")
        table.add_column("Semester", style="blue")
        for p in presets:
            table.add_row(
                p["id"],
                p["name"],
                p["subject"],
                p["grade"],
                p["semester"],
            )
        console.print(table)

    @app.command("apply")
    def learning_apply(
        preset_id: str = typer.Argument(..., help="Preset id from `deeptutor learning list`."),
        path_id: str = typer.Option(
            "",
            "--path-id",
            help="Mastery path id (defaults to preset id).",
        ),
        mode: str = typer.Option("replace", "--mode", help="replace | append"),
    ) -> None:
        """Apply a preset curriculum to create/overwrite a mastery path."""
        pid = path_id or preset_id
        try:
            result = apply_preset(preset_id, pid, mode=mode)
        except FileNotFoundError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1)
        console.print(f"[green]Preset applied:[/green] {result['preset_name']}")
        console.print(f"  path_id:          {result['path_id']}")
        console.print(f"  modules:          {result['modules_count']}")
        console.print(f"  knowledge_points: {result['knowledge_points_count']}")
        console.print()
        console.print("[dim]Map snapshot:[/dim]")
        console.print(json.dumps(result["map"], ensure_ascii=False, indent=2))
