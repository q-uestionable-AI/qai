"""IPI CLI subcommand modules.

Each module in this package registers one ``@app.command`` with the
shared Typer ``app`` exposed by :mod:`q_ai.ipi.commands._shared`. The
legacy entry point :mod:`q_ai.ipi.cli` imports each module here purely
for its registration side-effect and re-exports ``app``.
"""
