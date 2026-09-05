from __future__ import annotations

import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name, get_lexer_for_filename
from pygments.util import ClassNotFound

from .ida_export import digest, write_json
from .metrics import session_metrics
from .stages import build_stages, session_metadata

ASSETS = Path(__file__).with_name("assets")
DATABASE_SUFFIXES = {".i64", ".idb"}
BINARY_SUFFIXES = {
    ".exe",
    ".dll",
    ".sys",
    ".bin",
    ".so",
    ".dylib",
    ".elf",
    ".zip",
    ".png",
    ".jpg",
    ".pdf",
}
SIDECARS = {".id0", ".id1", ".id2", ".nam", ".til"}


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def url(value: str) -> str:
    return quote(value, safe="/")


def relative(target: str, page: str) -> str:
    return url(posixpath.relpath(target, posixpath.dirname(page) or "."))


def db_key(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]


def page_for(path: str) -> str:
    return f"artifacts/{path}.html"


def text_code(content: str, filename: str = "", language: str = "") -> str:
    try:
        lexer = (
            get_lexer_by_name(language)
            if language
            else get_lexer_for_filename(filename)
        )
    except ClassNotFound:
        lexer = TextLexer()
    return highlight(content, lexer, HtmlFormatter(cssclass="highlight", wrapcode=True))


def file_tree(artifacts: list[dict]) -> str:
    tree = {"folders": {}, "files": []}
    for item in artifacts:
        branch = tree
        parts = item["path"].split("/")
        for folder in parts[:-1]:
            branch = branch["folders"].setdefault(folder, {"folders": {}, "files": []})
        branch["files"].append((parts[-1], item))

    def render(branch: dict, depth: int = 0) -> str:
        rows = []
        for name, child in sorted(branch["folders"].items()):
            rows.append(
                f'<li class="folder-node"><details class="file-folder" {"open" if depth == 0 else ""}>'
                f'<summary>{escape(name)}<span class="muted">/</span></summary>'
                f"{render(child, depth + 1)}</details></li>"
            )
        for name, item in sorted(branch["files"], key=lambda pair: pair[0].lower()):
            glyph = {
                "database": "DB",
                "sample": "↪",
                "markdown": "MD",
                "code": "{}",
            }.get(item["kind"], "·")
            size = (
                f"{item['bytes'] / 1024:.1f} KB"
                if item["bytes"] >= 1024
                else f"{item['bytes']} B"
            )
            rows.append(
                f'<li class="file-node" data-search="{escape(item["path"].lower())}">'
                f'<a class="file-row" data-kind="{escape(item["kind"])}" href="{url(item["page"])}" title="{escape(item["path"])}">'
                f'<span class="file-glyph" aria-label="{escape(item["kind"])}">{glyph}</span>'
                f'<span class="file-name">{escape(name)}</span><span class="file-size">{size}</span></a></li>'
            )
        return '<ul class="file-tree">' + "".join(rows) + "</ul>"

    return render(tree)


def usage_summary(metrics: dict) -> str:
    usage = metrics["usage"]

    def tokens(key):
        value = usage["tokens"][key]
        return f"{value:,}" if value is not None else "—"

    def cost(key):
        value = usage["cost"][key]
        return f"${Decimal(value):.6f}" if value is not None else "—"

    items = (
        f'<div class="usage-item usage-total"><span>Total cost</span>'
        f'<strong>{cost("total")}</strong><small>{tokens("total")} tokens</small></div>'
    )
    items += "".join(
        f'<div class="usage-item"><span>{label}</span><strong>{tokens(key)}</strong><small>{cost(key)}</small></div>'
        for key, label in (
            ("input", "Input"),
            ("output", "Output"),
            ("cache", "Cache"),
        )
    )
    seconds = metrics["duration_seconds"]
    duration = "—"
    if seconds is not None:
        hours, remaining = divmod(int(seconds + 0.5), 3600)
        minutes, seconds = divmod(remaining, 60)
        duration = (
            f"{hours}h {minutes:02d}m {seconds:02d}s"
            if hours
            else f"{minutes}m {seconds:02d}s"
            if minutes
            else f"{seconds}s"
        )
    explanation = {
        "runner": "Elapsed time recorded by the runner, including setup and gaps between stages.",
        "runner_timestamps": "Elapsed time between the runner's start and completion timestamps.",
        "session_span": "First session start to last message; includes retries and gaps. Excludes runner setup outside sessions.",
    }.get(metrics["duration_source"], "Duration was not recorded.")
    items += f'<div class="usage-item usage-duration" title="{escape(explanation)}"><span>Duration</span><strong>{duration}</strong><small>Elapsed</small></div>'
    note = (
        '<span class="usage-note">Incomplete usage; — means unavailable.</span>'
        if not usage["complete"]
        else ""
    )
    return (
        f'<div class="usage-summary" aria-label="Token usage, costs in USD, and duration">{items}</div>'
        '<div class="usage-footnote"><details class="usage-breakdown"><summary>Cache read / write</summary>'
        f"<div>Read: {tokens('cache_read')} tokens · {cost('cache_read')}<br>Write: {tokens('cache_write')} tokens · {cost('cache_write')}</div></details>"
        f"{note}</div>"
    )


def stage_timeline(stages: list[dict]) -> str:
    rows = []
    for stage in stages:
        links = []
        for index, session in enumerate(stage["sessions"], 1):
            label = (
                "Open Pi session"
                if len(stage["sessions"]) == 1
                else f"Pi session {index}"
            )
            links.append(
                f'<a class="stage-session" href="{url(session["page"])}">{label} ↗</a>'
            )
        sessions_html = (
            "".join(links) or '<span class="unmatched-note">No matched session</span>'
        )
        prompt_label = escape(stage["prompt_path"].removeprefix("prompts/"))
        prompt_link = (
            f'<a class="prompt-source" href="{url(stage["prompt_page"])}">{prompt_label}</a>'
            if stage["prompt_page"]
            else f'<span class="prompt-source">{prompt_label}</span>'
        )
        if stage["prompt"] is not None:
            origin = " · from session" if stage["prompt_source"] == "session" else ""
            prompt_html = f'<details class="stage-prompt" open><summary>Prompt{origin}</summary><div class="prompt-text">{escape(stage["prompt"])}</div></details>'
        else:
            prompt_html = '<p class="unmatched-note">The prompt text is missing from this run.</p>'
        results = "".join(
            f'<a href="{url(item["page"])}">{escape(item["path"].removeprefix("workspace/"))} ↗</a>'
            for item in stage["results"]
        )
        results_html = (
            f'<div class="stage-results"><span>Results</span>{results}</div>'
            if results
            else ""
        )
        rows.append(
            f'<li class="stage" id="{stage["id"]}"><span class="stage-number">{stage["number"]:02d}</span>'
            f'<div class="stage-body"><div class="stage-heading"><h3>{escape(stage["title"])}</h3><div class="stage-sessions">{sessions_html}</div></div>'
            f"{usage_summary(stage['metrics'])}{prompt_link}{prompt_html}{results_html}</div></li>"
        )
    return (
        '<ol class="stage-timeline">' + "".join(rows) + "</ol>"
        if rows
        else '<p class="muted">No prompts were retained in this run.</p>'
    )


class Renderer:
    def __init__(self, output: Path, title: str, routes: dict[str, str]):
        self.output, self.title, self.routes = output, title, routes

    def write(self, path: str, content: str) -> None:
        destination = self.output / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def page(
        self,
        path: str,
        title: str,
        body: str,
        *,
        wide: bool = False,
        script: str = "",
        log_page: bool = False,
    ) -> None:
        scripts = (
            f'<script type="module" src="{relative("assets/" + script, path)}"></script>'
            if script
            else ""
        )
        if script == "ida.js":
            scripts += (
                f'<link rel="stylesheet" href="{relative("assets/ida.css", path)}">'
            )
        if script == "report.js":
            scripts += f'<link rel="stylesheet" href="{relative("assets/overview.css", path)}">'
        if log_page:
            scripts += (
                f'<link rel="stylesheet" href="{relative("assets/log.css", path)}">'
            )
        self.write(
            path,
            f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light"><title>{escape(title)} · {escape(self.title)}</title>
<link rel="stylesheet" href="{relative("assets/site.css", path)}">
<link rel="stylesheet" href="{relative("assets/highlight.css", path)}">{scripts}</head>
<body class="{"log-page" if log_page else "ida-page" if script == "ida.js" else "report-page"}"><header class="site-header"><a class="brand" href="{relative("index.html", path)}">RUN / ARCHIVE</a>
<span>{escape(self.title)}</span><a href="{relative("index.html", path)}">All artifacts ↗</a></header>
<main class="{"wide" if wide else "document"}">{body}</main></body></html>''',
        )

    def resolve_link(self, href: str, source: str, page: str) -> str:
        parts = urlsplit(href)
        if parts.scheme or parts.netloc or not parts.path:
            return href
        path = unquote(parts.path).replace("\\", "/")
        if path.startswith("/workspace/"):
            path = "workspace/" + path[len("/workspace/") :]
        elif path.startswith("/prompts/"):
            path = path.lstrip("/")
        elif not path.startswith("/"):
            path = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
        target = self.routes.get(path)
        if target:
            return (
                relative(target, page)
                + ("?" + parts.query if parts.query else "")
                + ("#" + parts.fragment if parts.fragment else "")
            )
        return href

    def markdown(self, content: str, source: str, page: str) -> str:
        md = MarkdownIt("commonmark", {"html": False})
        md.renderer.rules["fence"] = lambda tokens, idx, options, env: text_code(
            tokens[idx].content, language=tokens[idx].info.strip().split(" ")[0]
        )
        md.enable("table")
        tokens = md.parse(content)
        headings: dict[str, int] = {}
        for index, token in enumerate(tokens):
            if token.type == "heading_open" and index + 1 < len(tokens):
                label = tokens[index + 1].content.lower()
                slug = re.sub(r"[^\w\- ]", "", label).replace(" ", "-") or "section"
                count = headings.get(slug, 0)
                headings[slug] = count + 1
                token.attrSet("id", slug + (f"-{count}" if count else ""))
            for child in token.children or []:
                if child.type == "link_open":
                    child.attrSet(
                        "href",
                        self.resolve_link(child.attrGet("href") or "", source, page),
                    )
                elif child.type == "code_inline":
                    target = self.resolve_link(child.content, source, page)
                    if target != child.content:
                        child.type = "html_inline"
                        child.content = f'<a href="{escape(target)}"><code>{escape(child.content)}</code></a>'
        return md.renderer.render(tokens, md.options, {})


def safe_files(root: Path) -> list[Path]:
    if root.is_symlink():
        raise ValueError(f"input directory is a symlink: {root}")
    result = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            name for name in dirs if not (Path(directory) / name).is_symlink()
        )
        result.extend(
            Path(directory) / name
            for name in sorted(files)
            if not (Path(directory) / name).is_symlink()
        )
    return result


def export_database(source: Path, destination: Path, args) -> dict:
    if args.ida_data:
        cached = args.ida_data.resolve() / destination.name
        index = json.loads((cached / "index.json").read_text(encoding="utf-8"))
        if index.get("schema") != 1 or index.get("source_sha256") != digest(source):
            raise ValueError(
                f"snapshot is incompatible or database hash changed: {source}"
            )
        shutil.copytree(cached, destination)
        return index
    command = [
        args.ida_python,
        str(Path(__file__).with_name("ida_export.py")),
        str(source),
        str(destination),
        "--chunk-size",
        str(args.chunk_size),
    ]
    for flag in ("max_functions", "max_heads"):
        if getattr(args, flag):
            command.extend(["--" + flag.replace("_", "-"), str(getattr(args, flag))])
    print(f"Exporting IDA database: {source.name}", flush=True)
    try:
        subprocess.run(command, check=True, timeout=args.ida_timeout)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"IDA export timed out for {source.name}; increase --ida-timeout"
        ) from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"IDA export failed for {source.name}. Use a licensed --ida-python with ida-domain installed."
        ) from error
    return json.loads((destination / "index.json").read_text(encoding="utf-8"))


def native_pi_html(source: Path, destination: Path, args) -> None:
    existing = source.with_suffix(".html")
    if existing.is_file() and not existing.is_symlink():
        shutil.copy2(existing, destination)
        return
    executable = shutil.which(args.pi)
    if not executable:
        raise RuntimeError(
            f"Pi HTML missing for {source.name}; run pi --export first or set --pi to the Pi executable."
        )
    print(f"Exporting Pi session: {source.name}", flush=True)
    try:
        subprocess.run(
            [executable, "--export", str(source), str(destination)],
            check=True,
            timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"pi --export failed for {source}") from error
    if not destination.is_file():
        raise RuntimeError(f"pi --export did not produce HTML for {source}")


def build_site(args) -> None:
    run, output = args.run.resolve(), args.output.resolve()
    if not (run / "workspace").is_dir():
        raise ValueError(f"run must contain workspace/: {run}")
    if (
        output == run
        or output in run.parents
        or any(
            output == run / name or run / name in output.parents
            for name in ("workspace", "state", "prompts")
        )
    ):
        raise ValueError("output must be separate from the run's input directories")
    source_token = hashlib.sha256(
        os.path.normcase(str(run)).encode("utf-8")
    ).hexdigest()
    replace_existing = output.is_dir() and any(output.iterdir())
    if output.exists() and (not output.is_dir() or replace_existing):
        if not getattr(args, "overwrite", False):
            raise ValueError(
                "output must be new or empty; use --overwrite to rebuild a site generated from this run"
            )
        try:
            marker = json.loads((output / ".run-site.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(
                "--overwrite requires a site generated by ida-run-export from this run"
            ) from error
        if marker != {"schema": 1, "source_token": source_token}:
            raise ValueError(
                "--overwrite cannot replace a site generated from a different run"
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".run-site-", dir=output.parent
    ) as temporary:
        staging = Path(temporary) / "site"
        staging.mkdir()
        assemble(run, staging, args)
        write_json(
            staging / ".run-site.json", {"schema": 1, "source_token": source_token}
        )
        backup = Path(temporary) / "previous"
        if replace_existing:
            output.replace(backup)
        elif output.exists():
            output.rmdir()  # Only the empty directory verified above; no recursive deletion.
        try:
            staging.replace(output)
        except OSError:
            if replace_existing:
                backup.replace(output)
            raise


def assemble(run: Path, output: Path, args) -> None:
    manifest_path = run / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    title = args.title or manifest.get("name") or run.name
    files = safe_files(run / "workspace") + safe_files(run / "prompts")
    files = [p for p in files if p.suffix.lower() not in SIDECARS]
    routes = {
        p.relative_to(run).as_posix(): page_for(p.relative_to(run).as_posix())
        for p in files
    }
    renderer = Renderer(output, title, routes)
    shutil.copytree(ASSETS, output / "assets")
    (output / "assets/highlight.css").write_text(
        HtmlFormatter(style="monokai").get_style_defs(".highlight"), encoding="utf-8"
    )
    (output / ".nojekyll").touch()
    snapshots: dict[str, dict] = {}
    aliases: dict[str, list[str]] = {}
    warnings: list[str] = []
    for source in files:
        if source.suffix.lower() not in DATABASE_SUFFIXES:
            continue
        path = source.relative_to(run).as_posix()
        if args.skip_ida:
            warnings.append(f"Database contents explicitly skipped: {path}")
            continue
        snapshot = export_database(source, output / "data" / db_key(path), args)
        snapshots[path] = snapshot
        warnings.extend(f"{source.name}: {warning}" for warning in snapshot["warnings"])
        # Prefer the recorded hash: this also resolves extensionless or renamed samples.
        sample_hash = snapshot.get("input_sha256")
        matches = [
            p
            for p in files
            if p.suffix.lower() not in DATABASE_SUFFIXES
            and p.relative_to(run).parts[0] == "workspace"
            and sample_hash
            and digest(p) == sample_hash
        ]
        if not matches:
            # IDBs sometimes lack a hash. Only use unambiguous adjacent filename matches.
            input_name = (
                snapshot.get("input_file", "").replace("\\", "/").rsplit("/", 1)[-1]
            )
            candidates = (
                {source.with_suffix(""), source.with_name(input_name)}
                if input_name
                else {source.with_suffix("")}
            )
            matches = [p for p in files if p in candidates and p != source]
        for sample in matches:
            aliases.setdefault(sample.relative_to(run).as_posix(), []).append(path)

    catalog = []
    for source in files:
        path = source.relative_to(run).as_posix()
        page = routes[path]
        heading = f'<div class="eyebrow">{escape(path.rsplit("/", 1)[0])}</div><h1>{escape(source.name)}</h1>'
        kind = "artifact"
        if path in snapshots:
            kind = "database"
            data_path = relative(f"data/{db_key(path)}/index.json", page)
            renderer.page(
                page,
                source.name,
                f'''<div id="ida-app" data-index="{data_path}">
<div class="ida-heading"><button id="toggle-symbols" aria-controls="symbol-sidebar" aria-expanded="true" title="Toggle symbol list">Symbols</button>
<button id="navigate-back" aria-label="Go back" title="Back (Escape)">←</button><button id="navigate-forward" aria-label="Go forward" title="Forward">→</button>
<h1 title="{escape(source.name)}">{escape(source.name)}</h1>
<form id="jump-form"><label class="sr-only" for="jump">Jump to address or symbol</label><div class="input-row"><input id="jump" placeholder="Jump to address or symbol (G)" autocomplete="off"><button>Go</button></div></form></div>
<div id="ida-warning"></div>
<div class="ida-layout"><aside class="ida-sidebar" id="symbol-sidebar"><div class="tab-row"><button id="functions-tab" aria-pressed="true">Functions</button><button id="types-tab" aria-pressed="false">Types</button></div>
<label class="sr-only" for="filter">Filter functions or types</label><input id="filter" placeholder="Filter by name or address…" type="search">
<div id="item-count" class="muted"></div><nav id="item-list" aria-label="Database symbols"></nav><button id="more-items" hidden>Show more</button>
<details><summary>Entry points</summary><nav id="entry-points" aria-label="Entry points"></nav></details>
<details><summary>Segments</summary><nav id="segments"></nav></details></aside>
<section class="ida-main"><div class="tab-row"><button id="pseudocode-tab" aria-pressed="true">Decompilation</button><button id="disassembly-tab" aria-pressed="false">Linear disassembly</button><button id="xrefs-tab" aria-pressed="false">Xrefs</button></div>
<div id="selection"></div><div id="ida-content"></div></section></div>
<div id="ida-status" role="status">Loading database index…</div></div>
<noscript>This database browser requires JavaScript. Its data is available as static JSON.</noscript>''',
                wide=True,
                script="ida.js",
            )
        elif path in aliases:
            kind = "sample"
            targets = aliases[path]
            if len(targets) == 1:
                target = relative(routes[targets[0]], page)
                renderer.page(
                    page,
                    source.name,
                    heading
                    + f'<p>Opening the <a id="redirect" href="{target}">IDA database</a>…</p>',
                    script="redirect.js",
                )
            else:
                renderer.page(
                    page,
                    source.name,
                    heading
                    + "<p>Choose a database for this sample:</p><ul>"
                    + "".join(
                        f'<li><a href="{relative(routes[t], page)}">{escape(t)}</a></li>'
                        for t in targets
                    )
                    + "</ul>",
                )
        elif source.suffix.lower() in DATABASE_SUFFIXES:
            kind = "database"
            renderer.page(
                page,
                source.name,
                heading + "<p>Database contents omitted by --skip-ida.</p>",
            )
        else:
            content = None
            if source.suffix.lower() not in BINARY_SUFFIXES:
                try:
                    content = source.read_text(encoding="utf-8-sig")
                    if "\0" in content:
                        content = None
                except UnicodeError:
                    pass
            if content is None:
                kind = "binary"
                body = f'<p>{source.stat().st_size:,} bytes</p><p class="muted">SHA-256</p><code class="break">{digest(source)}</code><p>No database matched this binary.</p>'
            elif source.suffix.lower() == ".md":
                kind = "markdown"
                body = f'<article class="prose">{renderer.markdown(content, path, page)}</article>'
            else:
                kind = "code"
                body = text_code(content, source.name)
            renderer.page(page, source.name, heading + body)
        catalog.append(
            {"path": path, "page": page, "kind": kind, "bytes": source.stat().st_size}
        )

    # Keep Pi's own transcript rendering; read only metadata to associate run stages.
    sessions = [
        p for p in safe_files(run / "state/pi-sessions") if p.suffix.lower() == ".jsonl"
    ]
    session_html = [
        p
        for p in safe_files(run / "state/pi-sessions")
        if p.suffix.lower() == ".html" and not p.with_suffix(".jsonl").is_file()
    ]
    pi_sessions = []
    for source in sessions + session_html:
        metadata = (
            session_metadata(source)
            if source.suffix.lower() == ".jsonl"
            else {"name": source.stem}
        )
        path = source.relative_to(run).as_posix()
        pi_sessions.append(
            {**metadata, "path": path, "page": f"logs/{db_key(path)}.html"}
        )
        if metadata.get("invalid_lines"):
            warnings.append(
                f"{source.name}: {metadata['invalid_lines']} malformed JSONL lines ignored while reading stage metadata."
            )
    stages, unmatched_sessions = build_stages(
        run,
        manifest,
        [p for p in files if p.relative_to(run).parts[0] == "prompts"],
        pi_sessions,
        catalog,
    )
    metrics = session_metrics(pi_sessions, manifest)
    logs = []
    for source, metadata in zip(sessions + session_html, pi_sessions):
        key = db_key(source.relative_to(run).as_posix())
        page, native = f"logs/{key}.html", f"logs/{key}.native.html"
        (output / "logs").mkdir(exist_ok=True)
        if source.suffix.lower() == ".jsonl":
            native_pi_html(source, output / native, args)
        else:
            shutil.copy2(source, output / native)
        # Handle links created dynamically by Pi's Markdown renderer, including /workspace paths.
        mapping = {p: relative(route, native) for p, route in routes.items()}
        link_script = (output / "assets/pi-links.js").read_text(encoding="utf-8")
        embedded = json.dumps(mapping, ensure_ascii=True).replace("<", "\\u003c")
        with (output / native).open("a", encoding="utf-8") as stream:
            stream.write(
                f"\n<script>const artifactRoutes = {embedded};\n{link_script}</script>"
            )
        stage_number = metadata.get("stage")
        stage = stages[stage_number - 1] if stage_number else None
        log_title = (
            f"Stage {stage_number:02d} · {stage['title']}" if stage else "Pi session"
        )
        stage_link = (
            f'<a class="stage-back" href="../index.html#{stage["id"]}">← Stage {stage_number:02d}</a>'
            if stage
            else ""
        )
        renderer.page(
            page,
            log_title,
            f'<div class="log-heading">{stage_link}<h1 title="{escape(log_title)} · {escape(source.stem)}">{escape(log_title)}</h1></div>'
            '<iframe class="pi-log" title="Pi session export" '
            f'sandbox="allow-scripts allow-downloads allow-top-navigation-by-user-activation" src="{key}.native.html"></iframe>',
            wide=True,
            log_page=True,
        )
        logs.append(
            {
                "path": metadata["path"],
                "page": page,
                "name": metadata["name"],
                "stage": stage_number,
                "metrics": session_metrics([metadata]),
            }
        )

    run_details = []
    for source in (manifest_path, run / "state/console.log"):
        if source.is_file():
            path = source.relative_to(run).as_posix()
            page = page_for(path)
            renderer.page(
                page,
                source.name,
                f"<h1>{escape(source.name)}</h1>"
                + text_code(
                    source.read_text(encoding="utf-8", errors="replace"), source.name
                ),
            )
            logs.append({"path": path, "page": page})
            run_details.append({"path": path, "page": page})

    write_json(
        output / "catalog.json",
        {
            "schema": 1,
            "title": title,
            "run_id": manifest.get("run_id", run.name),
            "model": manifest.get("model"),
            "created_at": manifest.get("created_at"),
            "artifacts": catalog,
            "logs": logs,
            "stages": stages,
            "metrics": metrics,
            "warnings": warnings,
        },
    )
    workspace_files = [
        item for item in catalog if item["path"].startswith("workspace/")
    ]
    other_sessions = (
        (
            '<section class="unassigned-sessions"><h2>Unassigned Pi sessions</h2>'
            '<p class="muted">These sessions could not be matched uniquely to a prompt.</p>'
            + "".join(
                f'<div class="unassigned-session"><a class="log-link" href="{url(item["page"])}">{escape(item["name"])} ↗</a>{usage_summary(session_metrics([item]))}</div>'
                for item in unmatched_sessions
            )
            + "</section>"
        )
        if unmatched_sessions
        else ""
    )
    details_links = "".join(
        f'<a href="{url(item["page"])}">{escape(Path(item["path"]).name)}</a>'
        for item in run_details
    )
    warning_html = (
        '<details class="warning"><summary>Export notes</summary><ul>'
        + "".join(f"<li>{escape(w)}</li>" for w in warnings)
        + "</ul></details>"
        if warnings
        else ""
    )
    renderer.page(
        "index.html",
        title,
        f"""<div class="run-overview"><section class="run-heading"><div class="eyebrow">ANALYSIS RUN</div><h1>{escape(title)}</h1>
<div class="run-meta"><span>{escape(manifest.get("status", "exported"))}</span><span>{escape(manifest.get("model", ""))}</span>
<span>{len(stages)} stages · {len(pi_sessions)} Pi sessions · {len(snapshots)} databases</span></div>
<p class="run-identifier">{escape(manifest.get("run_id", run.name))}</p>{usage_summary(metrics)}</section>
{warning_html}<div class="overview-columns"><section class="stages-section"><div class="section-heading"><h2>Stages</h2><span class="muted">Prompt → session → results</span></div>
{stage_timeline(stages)}{other_sessions}</section><aside class="workspace-panel"><div class="section-heading"><h2>Files</h2><span class="muted">{len(workspace_files)} files</span></div>
<label for="artifact-filter" class="sr-only">Find a file</label><input type="search" id="artifact-filter" placeholder="Find a file…">
<nav class="workspace-tree" aria-label="Workspace files">{file_tree(workspace_files)}</nav><p id="no-artifacts" hidden>No matching files.</p></aside></div>
<footer class="run-details"><span>Run details</span>{details_links}</footer></div>""",
        script="report.js",
    )
