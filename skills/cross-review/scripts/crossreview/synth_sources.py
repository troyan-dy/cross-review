"""{{SOURCES}} of the synthesis prompt: what actually ran, one line per source.

A degraded source is loud here, since it is what tells the model corroboration was rarer than it looks.
"""

from __future__ import annotations

from crossreview.keys import AGENTS, FINDINGS, ID, LENSES, NAME, SOURCES
from crossreview.stage_run import degraded_names


def sources_block(found: dict) -> str:
    """The head line with the counts, then one line per source with the findings it reported."""
    agents = found[SOURCES][AGENTS]
    degraded = degraded_names(found)
    ids = _ids_by_source(found[FINDINGS])
    lines = [_source_line(agent, ids, degraded) for agent in agents]
    return "\n".join([_head(agents, degraded), *lines])


def _ids_by_source(findings: list[dict]) -> dict[str, list[str]]:
    ids: dict[str, list[str]] = {}
    for finding in findings:
        for source in finding[SOURCES]:
            ids.setdefault(source, []).append(finding[ID])
    return ids


def _head(agents: list[dict], degraded: list[str]) -> str:
    reported = sum(agent[NAME] not in degraded for agent in agents)
    head = f"{len(agents)} sources ran, {reported} reported."
    if degraded:
        names = ", ".join(degraded)
        head = f"{head} This run is DEGRADED: {names} reported nothing."
    return head


def _source_line(agent: dict, ids: dict, degraded: list[str]) -> str:
    lenses = ", ".join(agent[LENSES])
    entry = f"- {agent[NAME]} (lenses: {lenses})"
    own = ids.get(agent[NAME], [])
    if agent[NAME] in degraded:
        return f"{entry} DEGRADED, reported nothing"
    if not own:
        return f"{entry} reported no findings"
    listed = ", ".join(own)
    return f"{entry} reported {len(own)} findings: {listed}"
