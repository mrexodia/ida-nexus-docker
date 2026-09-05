"""Static landing page for independently exported runs in one directory."""

from __future__ import annotations

import html
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

MARKER = ".run-site-library"


def validate_library(root: Path) -> None:
    if root.exists() and not root.is_dir():
        raise ValueError(f"sites directory is not a directory: {root}")
    if (root / "catalog.json").exists():
        raise ValueError(
            "--sites-dir must be a library directory, not an individual exported run"
        )
    if (root / "index.html").exists() and not (root / MARKER).is_file():
        raise ValueError(
            "sites directory already has an index not managed by ida-run-export; choose another --sites-dir"
        )


def write_library_index(root: Path) -> None:
    validate_library(root)
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for folder in sorted(root.iterdir(), key=lambda path: path.name, reverse=True):
        if not folder.is_dir() or folder.is_symlink() or folder.name.startswith("."):
            continue
        try:
            catalog = json.loads((folder / "catalog.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            not isinstance(catalog, dict)
            or catalog.get("schema") != 1
            or not (folder / "index.html").is_file()
        ):
            continue
        title = html.escape(str(catalog.get("title", folder.name)))
        name = html.escape(folder.name)
        model = html.escape(str(catalog.get("model") or ""))
        href = quote(folder.name, safe="") + "/index.html"
        rows.append(
            f'<li data-search="{html.escape((str(catalog.get("title", "")) + " " + folder.name + " " + str(catalog.get("model", ""))).lower(), quote=True)}">'
            f'<a href="{href}"><strong>{title}</strong><span>{name}</span></a>'
            f"<small>{len(catalog.get('stages', []))} stages · {len(catalog.get('artifacts', []))} artifacts</small><small>{model}</small></li>"
        )
    document = (
        """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Analysis runs</title>
<style>:root{color-scheme:dark;font:15px/1.6 system-ui;background:#111417;color:#e3e8eb}*{box-sizing:border-box}body{max-width:1100px;margin:auto;padding:40px 24px}h1{font-size:2rem;margin:0}p,small,span{color:#97a5af}input{font:inherit;background:#191e23;color:inherit;border:1px solid #303942;border-radius:5px;padding:8px 12px;width:100%;margin:12px 0 22px}ul{list-style:none;margin:0;padding:0}li{display:flex;align-items:center;gap:24px;padding:17px 10px;border-bottom:1px solid #303942}a{flex:1;min-width:0;color:#76d6b2;text-decoration:none}a:hover strong{text-decoration:underline}strong,span{display:block}span{font:12px/1.8 Consolas,monospace;overflow-wrap:anywhere}small{font-size:12px}a:focus-visible,input:focus-visible{outline:2px solid #76d6b2;outline-offset:3px}[hidden]{display:none!important}@media(max-width:650px){body{padding:24px 16px}li{flex-wrap:wrap;gap:4px 18px}a{flex-basis:100%}}</style></head>
<body><h1>Analysis runs</h1><p>Browse your converted runs.</p><label for="search">Find a run</label><input id="search" type="search" placeholder="Run name or model…">
<ul>"""
        + "".join(rows)
        + """</ul><p id="empty" hidden>No matching runs.</p>
<script>const search=document.querySelector('#search');function filter(){let count=0;for(const row of document.querySelectorAll('[data-search]')){row.hidden=!row.dataset.search.includes(search.value.trim().toLowerCase());if(!row.hidden)count++;}document.querySelector('#empty').hidden=count!==0;}search.addEventListener('input',filter);filter();</script></body></html>"""
    )
    (root / MARKER).write_text("1\n", encoding="utf-8")
    (root / ".nojekyll").touch()
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=".index-",
        suffix=".tmp",
        dir=root,
        delete=False,
    ) as stream:
        stream.write(document)
        temporary = Path(stream.name)
    try:
        os.replace(temporary, root / "index.html")
    finally:
        temporary.unlink(missing_ok=True)
