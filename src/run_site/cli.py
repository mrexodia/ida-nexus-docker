from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .library import validate_library, write_library_index
from .publish import publish_site
from .site import build_site


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a run directory as a static website."
    )
    parser.add_argument(
        "run", type=Path, help="run directory containing workspace/ and state/"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="standalone output directory (default: sites/<run-directory-name>)",
    )
    parser.add_argument(
        "--sites-dir",
        type=Path,
        default=Path("sites"),
        help="converted-run library (default: ./sites); ignored with --output",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace a site previously generated from this run",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="publish the completed run using npx pagecast (requires one-time setup)",
    )
    parser.add_argument("--title", help="report title (defaults to run name)")
    parser.add_argument(
        "--pi", default="pi", help="Pi executable for sessions missing an HTML export"
    )
    parser.add_argument(
        "--ida-python",
        default=sys.executable,
        help="Python with licensed idapro/ida-domain installed",
    )
    parser.add_argument(
        "--ida-data",
        type=Path,
        help="reuse a previous site's data/ directory; no IDA needed",
    )
    parser.add_argument(
        "--skip-ida", action="store_true", help="explicitly omit database contents"
    )
    parser.add_argument(
        "--chunk-size",
        type=positive,
        default=512,
        help="listing items per JSON chunk (default: 512)",
    )
    parser.add_argument(
        "--max-functions",
        type=positive,
        help="export pseudocode only for the first N functions",
    )
    parser.add_argument(
        "--max-heads",
        type=positive,
        help="explicitly limit the linear listing to N items",
    )
    parser.add_argument(
        "--ida-timeout",
        type=positive,
        default=3600,
        help="seconds allowed per database (default: 3600)",
    )
    args = parser.parse_args(argv)
    library = None if args.output else args.sites_dir.resolve()
    if library:
        args.output = library / args.run.resolve().name
    try:
        if library:
            validate_library(library)
        build_site(args)
        if library:
            write_library_index(library)
        print(f"Static site: {args.output.resolve() / 'index.html'}", flush=True)
        if library:
            print(f"Run library: {library / 'index.html'}", flush=True)
        if args.publish:
            print("Publishing this run with Pagecast…", flush=True)
            result = publish_site(args.output.resolve(), args.run.resolve().name)
            print(f"Published: {result['url']}")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0
