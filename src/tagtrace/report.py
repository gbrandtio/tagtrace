"""Turns a finished report into Markdown for people to read."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tagtrace.analyze import AffectedArea, ComponentAssessment, Gap, Report, Risk, Testing


def _bullets(items: list[str]) -> list[str]:
    """Render a list of strings, or nothing when it is empty."""
    return [*(f"- {item}" for item in items), ""] if items else []


def _citations(commits: list[str], tickets: list[str]) -> str:
    """Render evidence as short SHAs and ticket keys."""
    cited = [*tickets, *(sha[:10] for sha in commits)]
    return f" ({', '.join(cited)})" if cited else ""


def _risk(risk: "Risk") -> list[str]:
    """Render the risk section."""
    return [f"**Risk: {risk.level}.** {risk.rationale}", "", *_bullets(risk.factors)]


def _testing(testing: "Testing") -> list[str]:
    """Render the testing section."""
    lines = [f"**Testing effort: {testing.effort}.** {testing.rationale}", ""]
    if testing.recommended_tests:
        lines += ["Recommended tests:", "", *_bullets(testing.recommended_tests)]
    if testing.regression_areas:
        lines += ["Regression areas:", "", *_bullets(testing.regression_areas)]
    return lines


def _areas(areas: "list[AffectedArea]") -> list[str]:
    """Render the affected areas."""
    if not areas:
        return []
    rows = [
        f"**{area.area}** ({area.confidence} confidence): {area.why}{_citations(area.commits, area.tickets)}"
        for area in areas
    ]
    return ["Affected areas:", "", *_bullets(rows)]


def _gaps(gaps: "list[Gap]") -> list[str]:
    """Render the gaps."""
    if not gaps:
        return []
    rows = [f"{gap.kind.replace('_', ' ')}: {gap.detail}{_citations(gap.commits, gap.tickets)}" for gap in gaps]
    return ["Gaps:", "", *_bullets(rows)]


def _assessment(assessment: "ComponentAssessment") -> list[str]:
    """Render one component assessment."""
    return [
        assessment.summary,
        "",
        *_risk(assessment.risk),
        *_areas(assessment.affected_areas),
        *_testing(assessment.testing),
        *_gaps(assessment.gaps),
    ]


def _neutralise(markdown: str) -> str:
    """Stop text from commits or the model making a previewer fetch a URL through an image or HTML tag."""
    return markdown.replace("<", "&lt;").replace("![", "!\\[")


def write_markdown(report: "Report") -> str:
    """Render the report. Anything incomplete comes first, so nobody mistakes a partial result for a full one."""
    lines = ["# Release assessment", "", f"Comparing `{report.old_file}` to `{report.new_file}`.", ""]
    lines += [f"Generated {report.generated_at} by {report.provider} / {report.model}.", ""]

    if report.incomplete:
        lines += ["## Incomplete: this assessment is missing data", "", *_bullets(report.incomplete)]
    if report.invented_citations_removed:
        lines += [
            f"**Note:** {report.invented_citations_removed} citation(s) from the model did not exist in the input "
            "and were removed. Read its conclusions with extra care.",
            "",
        ]

    if report.fallback_models:
        lines += [
            f"**Note:** {report.model} declined at least one request, so {', '.join(report.fallback_models)} "
            "answered instead.",
            "",
        ]

    release = report.release
    if release is not None:
        lines += ["## Overall", "", release.summary, "", *_risk(release.risk), *_areas(release.affected_areas)]
        lines += [*_testing(release.testing)]
        if release.cross_component_risks:
            lines += ["Cross-component risks:", "", *_bullets(release.cross_component_risks)]
        lines += _gaps(release.gaps)
    elif not report.incomplete:
        lines += ["## Overall", "", "The two files have no differences to assess.", ""]

    if report.added or report.removed or report.repo_changed:
        lines += ["## Components without a commit range", ""]
        lines += [f"- Added: {item}" for item in report.added]
        lines += [f"- Removed: {item}" for item in report.removed]
        lines += [f"- Moved to another repository: {item}" for item in report.repo_changed]
        lines += [""]

    if report.tickets_not_found:
        lines += [
            "## Tickets Jira did not return",
            "",
            "These keys appear in commits, but the key is wrong, or you lack permission to see the ticket.",
            "",
            *_bullets(report.tickets_not_found),
        ]

    if report.components:
        lines += ["## Components", ""]
    for component in report.components:
        lines += [f"### {component.name}: {component.old_tag} -> {component.new_tag}", ""]
        lines += [
            f"{component.commits_with_jira} commit(s) with a Jira key, {component.commits_without_jira} without.",
            "",
        ]
        if component.status == "no_commits":
            lines += ["The two tags point to the same code.", ""]
        if component.error:
            lines += [f"**Problem:** {component.error}", ""]
        for number, assessment in enumerate(component.assessments, start=1):
            if len(component.assessments) > 1:
                lines += [f"#### Part {number} of {len(component.assessments)}", ""]
            lines += _assessment(assessment)
    return _neutralise("\n".join(lines).rstrip() + "\n")
