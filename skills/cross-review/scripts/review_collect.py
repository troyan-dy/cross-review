#!/usr/bin/env python3
"""Collect the finder answers of a run into stages/1-found.json.

Reads the roster from the run's manifest.json and every agent's answer from agents/<agent>.json: the JSON
object the finder schema describes, optionally wrapped in prose or a ```json fence. Each finding gets the id
<agent>-<n> and `sources: [<agent>]`; lenses the agent was not given are dropped, and a missing `fix` or
`end_line` becomes empty. A finding that breaks the schema is left out with a `finding_rejected` event.

An agent is degraded when its answer is missing, is not JSON or does not match the schema, or when its
agents/<agent>.exit holds a non-zero code; a usage limit named in the tail of its .log is added to the
reason. `{"findings": []}` is a healthy agent that found nothing. Degraded agents and what each healthy one
raised go to events.jsonl.

Writes stages/1-found.json in the run format and prints one JSON line: the counts, the degraded
agents with their reasons and how many agents of each side reported. Exit 1 when the run cannot be read.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crossreview import cli  # noqa: E402
from crossreview.collect import collect  # noqa: E402
from crossreview.errors import ConfigError  # noqa: E402
from crossreview.jsonio import emit, fail  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Collect a run and print the counts; a run that cannot be read is {"error": ...} and exit 1."""
    root = cli.parser(__doc__)
    root.add_argument("run_dir", help="the run directory, after its agents answered")
    args = root.parse_args(argv)
    try:
        payload = collect(Path(args.run_dir).resolve())
    except ConfigError as error:
        return fail(str(error))
    emit(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
