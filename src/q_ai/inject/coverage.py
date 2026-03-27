"""Coverage analysis for findings-informed inject campaigns.

Compares audit finding categories against inject template coverage
to identify which findings were exercised vs. untested.
"""

from __future__ import annotations

from q_ai.inject.mapper import _OUTCOME_SEVERITY
from q_ai.inject.models import Campaign, CoverageReport, PayloadTemplate


def build_coverage_report(
    audit_categories: set[str],
    campaign: Campaign,
    templates: list[PayloadTemplate],
    *,
    native_categories: set[str] | None = None,
    imported_categories: set[str] | None = None,
) -> CoverageReport:
    """Build a coverage report comparing audit findings to inject results.

    Args:
        audit_categories: Combined set of finding categories (native + imported).
        campaign: Completed inject campaign with results.
        templates: Templates that were used in the campaign.
        native_categories: Categories from native audit findings within this
            workflow run. Defaults to ``audit_categories`` when not provided.
        imported_categories: Categories from imported external findings.
            Defaults to empty set when not provided.

    Returns:
        CoverageReport with coverage metrics and template match details.
    """
    resolved_native = native_categories if native_categories is not None else audit_categories
    resolved_imported = imported_categories if imported_categories is not None else set()

    if not audit_categories:
        return CoverageReport(
            audit_categories=set(),
            tested_categories=set(),
            untested_categories=set(),
            coverage_ratio=0.0,
            template_matches=[],
            native_categories=resolved_native,
            imported_categories=resolved_imported,
        )

    # Build a lookup from template name to its relevant_categories
    template_cats: dict[str, list[str]] = {t.name: t.relevant_categories for t in templates}

    # Find which templates produced security-relevant results
    tested_categories: set[str] = set()
    template_matches: list[dict[str, object]] = []

    for result in campaign.results:
        if result.outcome not in _OUTCOME_SEVERITY:
            continue

        cats = template_cats.get(result.payload_name, [])
        matched = set(cats) & audit_categories
        if matched:
            tested_categories.update(matched)
            template_matches.append(
                {
                    "template": result.payload_name,
                    "categories": sorted(matched),
                }
            )

    untested_categories = audit_categories - tested_categories
    coverage_ratio = len(tested_categories) / len(audit_categories)

    return CoverageReport(
        audit_categories=audit_categories,
        tested_categories=tested_categories,
        untested_categories=untested_categories,
        coverage_ratio=coverage_ratio,
        template_matches=template_matches,
        native_categories=resolved_native,
        imported_categories=resolved_imported,
    )
