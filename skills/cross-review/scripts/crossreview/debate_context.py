"""What the debate prompts say about the run: the state of the working tree and the context files the sides
read."""

from __future__ import annotations

import subprocess
from pathlib import Path

from crossreview.errors import ConfigError
from crossreview.runs import locate

CONVENTIONS = "project conventions; where they disagree with your taste, they win"




def tree_state(workdir: str, ref: str | None) -> str:
    """One paragraph on where the working tree stands relative to the commit under review."""
    base = _tree_base(workdir)
    if not ref:
        return f"{base} Cite lines as the working tree has them."
    short = _git(workdir, "rev-parse", "--short", ref)
    if not short:
        return f"{base} The ref `{ref}` does not resolve here; read the code the scope's commands show."
    argv = ["git", "-C", workdir, "merge-base", "--is-ancestor", ref, "HEAD"]
    if subprocess.run(argv, capture_output=True).returncode == 0:
        return (
            f"{base} It contains `{short}`, the commit under review, so files on disk are at or after it; "
            f"cite lines of `{short}` (`git show {short}:<path>`) when the two differ."
        )
    return (
        f"{base} It does **not** contain `{short}`, the commit under review: files on disk are an older or "
        f"unrelated version. Read every file through `git show {short}:<path>` (and `git diff`/`git log` on the "
        f"range), never from disk, and cite lines of `{short}`."
    )


def default_context(round_dir: Path) -> list[tuple[str, str]]:
    """The run's own input as context: scope.md, goal.md, context/* and the profile that calibrated the run."""
    inputs = round_dir / "input"
    context = []
    if (inputs / "scope.md").exists():
        context.append((str(inputs / "scope.md"), "what was reviewed and the commands that show the change"))
    if _has_text(inputs / "goal.md"):
        context.append((str(inputs / "goal.md"), "what the change is for and when it counts as correct"))
    context.extend(_material(inputs / "context"))
    profiles = (inputs / "profile.md", round_dir / "prompts" / "input-profile.md")
    profile = next((path for path in profiles if _has_text(path)), None)
    if profile is not None:
        context.append((str(profile), CONVENTIONS))
    return context


def own_run(round_dir: Path) -> Path | None:
    """The repository of a cross-review run; None for an external run or any other directory."""
    try:
        return locate(round_dir).repo
    except ConfigError:
        return None






def _tree_base(workdir: str) -> str:
    head = _git(workdir, "rev-parse", "--short", "HEAD")
    branch = _git(workdir, "branch", "--show-current") or "detached HEAD"
    commit = f" at `{head}`" if head else ", with no commit yet"
    dirty = ", with uncommitted changes" if _git(workdir, "status", "--porcelain") else ""
    return f"The working tree is `{branch}`{commit}{dirty}."


def _git(workdir: str, *args: str) -> str:
    argv = ["git", "-C", workdir, *args]
    return subprocess.run(argv, capture_output=True, text=True).stdout.strip()


def _has_text(path: Path) -> bool:
    return path.exists() and bool(path.read_text().strip())


def _material(folder: Path) -> list[tuple[str, str]]:
    listing = sorted(folder.iterdir()) if folder.is_dir() else []
    files = [path for path in listing if path.is_file()]
    return [(str(path), f"supporting material: {path.name}") for path in files]
