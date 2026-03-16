"""RXP subcommands — RAG retrieval poisoning optimizer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from q_ai.rxp.models import CorpusDocument, ValidationResult
from q_ai.rxp.profiles import get_profile, list_profiles, load_corpus, load_poison
from q_ai.rxp.registry import list_models, resolve_model

app = typer.Typer(no_args_is_help=True)
console = Console()


def _error(message: str) -> NoReturn:
    """Print error and exit."""
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def _resolve_model_ids(model: str) -> list[str]:
    """Resolve model option to list of model IDs."""
    if model == "all":
        return [m.id for m in list_models()]
    return [model]


def _load_corpus_docs_from_dir(corpus_dir: Path) -> list[CorpusDocument]:
    """Load corpus documents from a directory of .txt files.

    Args:
        corpus_dir: Path to directory containing .txt corpus files.

    Returns:
        List of CorpusDocument objects loaded from the directory.
    """
    if not corpus_dir.is_dir():
        _error(f"Corpus directory not found: {corpus_dir}")
    docs = []
    for txt_file in sorted(corpus_dir.glob("*.txt")):
        text = txt_file.read_text(encoding="utf-8").strip()
        docs.append(CorpusDocument(id=txt_file.stem, text=text, source=str(txt_file)))
    if not docs:
        _error(f"No .txt files found in {corpus_dir}")
    return docs


def _load_poison_doc(poison_file: Path) -> list[CorpusDocument]:
    """Load a single poison document from a file path.

    Args:
        poison_file: Path to the poison document file.

    Returns:
        List containing one CorpusDocument marked as poison.
    """
    if not poison_file.exists():
        _error(f"Poison file not found: {poison_file}")
    text = poison_file.read_text(encoding="utf-8").strip()
    return [CorpusDocument(id=poison_file.stem, text=text, source=str(poison_file), is_poison=True)]


def _resolve_corpus(
    profile: str | None,
    corpus_dir: Path | None,
    poison_file: Path | None,
    queries_override: list[str] | None = None,
) -> tuple[list[CorpusDocument], list[CorpusDocument], list[str]]:
    """Resolve corpus docs, poison docs, and queries from CLI args.

    Args:
        profile: Domain profile ID.
        corpus_dir: Path to custom corpus directory.
        poison_file: Path to poison document.
        queries_override: Explicit queries from --query flags.

    Returns:
        Tuple of (corpus_docs, poison_docs, queries).
    """
    corpus_docs: list[CorpusDocument] = []
    poison_docs: list[CorpusDocument] = []
    queries: list[str] = []

    if profile is not None:
        prof = get_profile(profile)
        if prof is None:
            _error(f"Unknown profile: {profile}")
        corpus_docs = load_corpus(prof)
        poison_docs = load_poison(prof)
        queries = prof.queries
    elif corpus_dir is not None:
        corpus_docs = _load_corpus_docs_from_dir(corpus_dir)

    if poison_file is not None:
        poison_docs = _load_poison_doc(poison_file)

    if queries_override:
        queries = queries_override

    if not poison_docs:
        _error("No poison documents found. Use --poison-file or a profile with poison docs.")
    if not queries:
        _error("No queries found. Use --profile or provide --query flags with --corpus-dir.")

    return corpus_docs, poison_docs, queries


def _print_result(result: ValidationResult, verbose: bool) -> None:
    """Print validation result for a single model."""
    typer.echo(f"Results for {result.model_id}:")
    typer.echo(
        f"  Retrieval rate: {result.poison_retrievals}/{result.total_queries} "
        f"({result.retrieval_rate:.1%})"
    )
    if result.mean_poison_rank is not None:
        typer.echo(f"  Mean poison rank: {result.mean_poison_rank:.1f} (when retrieved)")
    typer.echo()

    if verbose:
        for qr in result.query_results:
            typer.echo(f'  Query: "{qr.query}"')
            if qr.poison_retrieved:
                typer.echo(f"    Poison retrieved: YES (rank {qr.poison_rank})")
            else:
                typer.echo("    Poison retrieved: NO")
        typer.echo()


def _print_comparison(results: list[ValidationResult]) -> None:
    """Print comparison summary for multi-model runs."""
    typer.echo("Comparison Summary:")
    typer.echo(f"{'Model':<15} {'Rate':<15} {'Mean Rank':<12}")
    typer.echo("-" * 42)
    for r in results:
        rate = f"{r.poison_retrievals}/{r.total_queries} ({r.retrieval_rate:.1%})"
        rank = f"{r.mean_poison_rank:.1f}" if r.mean_poison_rank is not None else "N/A"
        typer.echo(f"{r.model_id:<15} {rate:<15} {rank:<12}")


@app.command("list-models")
def list_models_cmd() -> None:
    """Show registered embedding models."""
    models = list_models()
    table = Table()
    table.add_column("ID", style="cyan")
    table.add_column("Model")
    table.add_column("Dimensions", justify="right")
    table.add_column("Description")
    for m in models:
        dims = str(m.dimensions) if m.dimensions is not None else "\u2014"
        table.add_row(m.id, m.name, dims, m.description)
    console.print(table)


@app.command("list-profiles")
def list_profiles_cmd() -> None:
    """Show built-in domain profiles."""
    profiles = list_profiles()
    if not profiles:
        typer.echo("No profiles found.")
        return
    table = Table()
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Queries", justify="right")
    table.add_column("Corpus Docs", justify="right")
    for p in profiles:
        corpus = load_corpus(p)
        table.add_row(p.id, p.name, str(len(p.queries)), str(len(corpus)))
    console.print(table)


@app.command()
def validate(
    profile: Annotated[
        str | None,
        typer.Option(help="Domain profile ID."),
    ] = None,
    corpus_dir: Annotated[
        Path | None,
        typer.Option(help="Path to custom corpus directory."),
    ] = None,
    poison_file: Annotated[
        Path | None,
        typer.Option(help="Path to poison document."),
    ] = None,
    model: Annotated[
        str,
        typer.Option(help="Embedding model: registry shortcut, 'all', or HuggingFace model name."),
    ] = "minilm-l6",
    top_k: Annotated[
        int,
        typer.Option(help="Number of retrieval results per query."),
    ] = 5,
    output: Annotated[
        Path | None,
        typer.Option(help="Write JSON results to file."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show per-query hit details."),
    ] = False,
    save: Annotated[
        bool,
        typer.Option("--save", help="Persist results to the q-ai database."),
    ] = False,
    query: Annotated[
        list[str] | None,
        typer.Option("--query", help="Query to test (repeatable). Required with --corpus-dir."),
    ] = None,
) -> None:
    """Run retrieval validation against a corpus."""
    if profile is None and corpus_dir is None:
        _error("Either --profile or --corpus-dir is required.")

    model_ids = _resolve_model_ids(model)
    corpus_docs, poison_docs, queries = _resolve_corpus(
        profile, corpus_dir, poison_file, queries_override=query
    )

    from q_ai.rxp._deps import require_rxp_deps

    require_rxp_deps()

    from q_ai.rxp.validator import validate_retrieval

    all_results: list[ValidationResult] = []
    for model_id in model_ids:
        model_config = resolve_model(model_id)
        typer.echo(f"Loading model: {model_config.name}...")
        typer.echo(
            f"Ingesting corpus: {len(corpus_docs)} documents + "
            f"{len(poison_docs)} poison document(s)"
        )
        typer.echo(f"Running {len(queries)} queries (top-{top_k})...")
        typer.echo()

        result = validate_retrieval(
            corpus_docs=corpus_docs,
            poison_docs=poison_docs,
            queries=queries,
            model_id=model_id,
            top_k=top_k,
        )
        all_results.append(result)
        _print_result(result, verbose)

    if len(all_results) > 1:
        _print_comparison(all_results)

    if save:
        from q_ai.rxp.mapper import persist_validation

        for result in all_results:
            run_id = persist_validation(
                result=result,
                profile_id=profile,
                top_k=top_k,
            )
            typer.echo(f"Saved to database (run {run_id})")

    if output is not None:
        json_data = [r.to_dict() for r in all_results]
        output.write_text(json.dumps(json_data, indent=2), encoding="utf-8")
        typer.echo(f"\nResults written to {output}")
