# IDA run export

Export a runner directory as a static, multi-page website. The output contains
HTML, CSS, JavaScript and JSON and can be served from any URL prefix, including
GitHub project Pages. There is no backend, CDN dependency, or npm build.

From the repository root, with Python 3.11+ and uv installed:

```bash
uv sync
uv run ida-run-export runs/<run-id>
cd sites
python -m http.server
```

Open `http://localhost:8000` to browse the searchable list of converted runs.
Each export goes into `sites/<run-id>/` and updates `sites/index.html`. Original
inputs stay in `runs/`; `sites/` is ignored by Git and the Docker build. Paths
are relative to your current directory, so run export commands from the
repository root. You can also serve it from there with
`python -m http.server 8000 --directory sites`.

The package now lives in `src/run_site/`, with `pyproject.toml` and `uv.lock`
at the repository root. If you previously ran uv inside the old `run-site/`
folder, switch to the root and run `uv sync` to create its environment. Older
exports in `run-site/sites/` are preserved; use `--sites-dir run-site/sites` to
continue updating that library, or export into the default root `sites/`.
If migrating another checkout with an existing `run-site/.pagecast/` directory,
move it to the root as well to preserve Pagecast's workspace identity and
existing publication links.

Use `--sites-dir PATH` for another library location, or `--output PATH` for a
standalone export without a library index. To rebuild a previously converted run:

```bash
uv run ida-run-export runs/<run-id> --overwrite

# Reuse the previous IDA snapshot when only presentation/artifacts changed.
uv run ida-run-export runs/<run-id> --overwrite --ida-data sites/<run-id>/data
```

Serve the output over HTTP: opening `index.html`
with `file://` cannot load the IDA JSON chunks in most browsers.

The export machine needs a licensed IDA 9.4 installation with idalib configured for
Python. This project uses `idapro` and the
[IDA Domain API](https://ida-domain.docs.hex-rays.com/), plus IDAPython for
listing lines, xrefs and type declarations. Tested with IDA 9.4 and
`ida-domain` 0.5.1. Decompilation needs the appropriate Hex-Rays decompiler.
Readers of the generated site need only a browser.

If IDA is configured in another Python environment:

```bash
uv run ida-run-export runs/<run-id> -o site --ida-python /path/to/ida-venv/bin/python
```

Without uv, install from the repository root with `python -m pip install -e .`.
Then use `ida-run-export` or `python -m run_site` in that Python environment.

## Included content

- An ordered stage timeline with the actual mounted prompt text, corresponding
  Pi session links, and stage result files. Sessions are matched by the runner's
  recorded session names, falling back to a unique exact prompt-text match.
  Retries stay together; missing or ambiguous matches remain explicitly unassigned.
- Input, output, cache and total tokens, with recorded USD costs for each,
  in the run header and every stage. Expand Cache read / write for its breakdown.
  Stage totals include all matched attempts; run totals also include unassigned
  sessions. Values come from assistant-message usage records, with decimal cost
  aggregation. Missing values appear as `—`, and incomplete usage is labeled.
- Stage duration from the first matched session's start to its last message,
  including retries and intervening gaps. Total duration uses the runner's
  recorded elapsed seconds, then its start/completion timestamps, then the span
  of all sessions. The duration tooltip identifies the source. Session spans
  exclude runner setup outside those sessions; later session renames do not
  extend them. Missing timing remains unavailable.
- A searchable, collapsible workspace file tree, plus manifest and console links.
  IDA Nexus semantic/worker logs are omitted from the site.
- Every workspace Markdown file rendered with tables, heading anchors and
  highlighted code fences. Python, JSON and other UTF-8 text artifacts get
  syntax highlighting. Nested directories are preserved in the page paths.
- Pi's **native HTML exports**, embedded in a sandboxed frame. JSONL downloads
  use Pi's built-in control; no separate JSONL copy is included in the site.
  Existing sibling `.html` files are reused. If one is missing,
  the build runs `pi --export SESSION.jsonl OUTPUT.html`; use `--pi PATH` to
  select the executable. There is no alternate transcript renderer. You can
  also create the sibling HTML once before exporting a run:

  ```bash
  pi --export runs/<run-id>/state/pi-sessions/<session>.jsonl \
    runs/<run-id>/state/pi-sessions/<session>.html
  ```

- An interactive browser for each `.i64` or `.idb`: searchable function list,
  linear disassembly and data items, decompilation, incoming/outgoing code and
  data xrefs, local type declarations, and segment navigation.
- Sample pages redirect to their matching database, including extensionless
  samples and renamed databases. Matching uses IDA's recorded input SHA-256,
  with an adjacent filename fallback when no hash match is available. Multiple
  matching databases produce a choice page.

Markdown links and inline-code paths such as `/workspace/unpacked.exe` link to
the corresponding rendered pages. Pi's dynamically rendered workspace links
are redirected to the same pages. Raw HTML inside Markdown is escaped. Other
HTML artifacts are shown as highlighted source; only Pi's native export runs
inside its sandbox. Binary files without a matching database have a size/hash
page. Executable sample bytes and raw IDBs are not copied into the site.

## IDA navigation

Opening a sample or database starts in **Decompilation**, at its first entry
point. The loader's execution entry takes priority over exported symbols; when
no execution entry is recorded, the first mapped IDA entry is used. Databases
without entry points fall back to the first function (then the first listing
item). Explicit address/view bookmarks always override these defaults. Entry
points are also accessible from the sidebar. Snapshots made before entry-point
metadata was added need one export without `--ida-data` to pick up this behavior.

Database pages fill the viewport. A compact toolbar and status bar surround
independently scrolling symbol and data panes. The Symbols button collapses the
sidebar; on narrow screens it opens as an overlay. Function signatures and
comments are available under Function info.

Click addresses, symbol names, operand xref arrows or type names to navigate.
The selected address and view live in the URL fragment, so links can be shared
and browser Back/Forward works:

```text
artifacts/workspace/unpacked.exe.i64.html#addr=0x401000&view=pseudocode
artifacts/workspace/unpacked.exe.i64.html#addr=0x401000&view=xrefs
artifacts/workspace/unpacked.exe.i64.html#type=1
```

The jump field accepts hexadecimal addresses (with or without `0x`), exact
symbol names and local type names. Keyboard shortcuts: **G** focuses Jump,
**X** opens xrefs, **F5** switches between disassembly and decompilation.
Function-list entries, function links and xref targets preserve the active
disassembly/decompilation view. Xref URLs record the originating view with
`&return=pseudocode` or `&return=disassembly`, including after reload. Back/Forward
restores the previous pane scroll position; **Escape** also navigates back.
64-bit addresses are serialized as hex strings and handled with `BigInt`.

The linear listing covers defined code/data items across all segments,
including items outside functions; undefined gaps are not synthesized. Each
item includes the first 16 bytes, its disassembly line, comments and non-flow
xrefs. Ordinary fall-through edges are excluded. Function tail ranges are
included in address-to-function lookup. Pseudocode/type text links resolve
known global symbol and local type names; this is not a full interactive ctree
or local-variable editor. Explicit xref links retain their IDA target addresses.

## Large databases and reproducibility

The default exports all functions and defined listing items. The browser loads
listing chunks (512 items by default), function details and type declarations
on demand and keeps a bounded data cache. The function/name/type indexes are
loaded once; the sidebar initially renders 200 entries with a Show more button.

```bash
uv run ida-run-export runs/<run-id> -o site --chunk-size 256 --ida-timeout 7200

# Explicit partial export; limitations are displayed in the report.
uv run ida-run-export runs/<run-id> -o partial-site --max-functions 100 --max-heads 50000

# Artifacts/logs only, with an explicit omission notice for databases.
uv run ida-run-export runs/<run-id> -o artifacts-site --skip-ida

# Rebuild presentation without rerunning IDA; also works on a machine without IDA.
uv run ida-run-export runs/<run-id> -o rebuilt-site --ida-data site/data
```

`--max-functions` limits decompilation; all function names and ranges are still
indexed. Decompiler failures appear on the affected function and in export
notes, while its disassembly remains available. `--ida-data` verifies the source
database hash before reuse and retains the snapshot's original limits. Database
paths within the run must also match, since they determine snapshot identifiers.

Every database is opened in a separate process from a temporary copy with
automatic analysis disabled and saving disabled. Original run files are not
modified. The output must be new or empty unless `--overwrite` is supplied.
Overwriting requires this exporter's ownership marker for the same source run;
unrelated folders and sites made before that marker was introduced are refused.
Choose a new output folder for those older exports. A rebuild is prepared in a
temporary directory before replacing the previous site, so a failed build keeps
the previous export. Missing Pi exports or a failed IDA worker fail the build.
The site includes the run's artifact/log contents; it does not redact them.

Library layout (each run is independently hostable):

```text
sites/
  index.html                    searchable run list
  <run-id>/                     complete static site
  <another-run-id>/
```

Inside each run's generated directory:

```text
<run-id>/
  .nojekyll
  index.html
  catalog.json
  assets/
  artifacts/workspace/<original-path>.html
  artifacts/prompts/...
  artifacts/state/...
  logs/<session-id>.html          wrapper
  logs/<session-id>.native.html   Pi export
  data/<database-id>/
    index.json
    functions/<hex-address>.json
    listing/<chunk-number>.json
    types/<type-id>.json
```

## GitHub Pages

Publish the **contents of the generated directory**, including `.nojekyll`.
All application URLs are relative, so a project URL such as
`https://example.github.io/analysis/run-123/` works without rebuilding.

For branch-based Pages, copy the output into the configured branch's root or
`docs/` directory. For an existing Pages Actions workflow, upload the generated
directory as the Pages artifact. IDA export can happen locally; no IDA license
is required on GitHub when deploying an already generated site. The CLI's
optional `--publish` uses Pagecast/Cloudflare; GitHub deployment is separate.

## Cloudflare Pages through Pagecast

[Pagecast](https://github.com/Amal-David/pagecast) publishes this output as a
multi-file static report. No sibling checkout or global npm installation is
needed. Its `publish` command copies the folder containing `index.html`, including
nested artifact pages, browser assets, Pi HTML and JSON data. Relative URLs work
under Pagecast's `/p/<slug>/` report URLs.

### One-time setup

1. Install **Node.js 20.19 or newer**, which includes npm/npx. Confirm with
   `node --version` and `npx --version` in a new terminal.
2. Connect Pagecast to a Cloudflare Pages project:

   ```bash
   npx pagecast pages setup --project my-analysis-runs
   ```

   Accept npm's package-install prompt if shown. Follow the browser sign-in and
   authorize Cloudflare access through Wrangler. Choose the Cloudflare account
   where your reports should live. For multiple accounts, supply
   `--account <account-id>`. This configures Pagecast's Home project; skip this
   step if it is already connected to the project you want.
3. Confirm the connected account and project:

   ```bash
   npx pagecast pages status --json
   ```

### Export and publish a run

From this repository root:

```bash
uv run ida-run-export runs/<run-id> --publish
```

This builds `sites/<run-id>/`, updates the local library, then prints the
published URL. Only that run is sent to Pagecast. Other converted runs remain
local until you publish them individually. The wrapper invokes
`npx --yes pagecast@0.7.0 publish … --non-interactive --json`, using the Home
project configured above; it does not launch account setup during an export.

For a run you have already converted, add `--overwrite`:

```bash
uv run ida-run-export runs/<run-id> --overwrite --publish

# Rebuild and publish using an unchanged database snapshot.
uv run ida-run-export runs/<run-id> --overwrite --ida-data sites/<run-id>/data --publish
```

Each run gets a stable context ID and item key, so repeating the command with
the same run directory name updates its existing Pagecast link in the same
Pagecast workspace. Reports use `--expires never`, overriding Pagecast's default
30-day expiry. If publishing fails, the completed local export and library stay
available, and the command returns a failure status.

To publish an existing export directly, including retrying after setup without
rebuilding it, use the equivalent command:

```bash
npx pagecast publish sites/<run-id>/index.html --context-id "ida-run:<run-id>" --item-key "<run-id>" --expires never --json
```

For a **dedicated Pages project containing just this run**, Pagecast also offers:

```bash
npx pagecast pages deploy sites/<run-id> --project my-dedicated-run --branch main --json
```

`pages deploy` replaces the contents of that whole Pages project. Use a separate
project from the managed Pagecast Home when choosing this option.

Point Pagecast at `sites/<run-id>/index.html` to publish one run. Pointing at
`sites/index.html` would include the whole library. It includes every non-hidden
regular file under the entry file's folder and
skips dotfiles, including `.nojekyll` (unnecessary on Cloudflare); any rendered
workspace dotfiles are also skipped. Pagecast links are unlisted by default;
use its password option if access should be restricted.

For large runs, Cloudflare currently limits each static asset to 25 MiB and Free
plan sites to 20,000 files. The exporter produces one file per function/type
plus listing chunks, so file count can matter even when the total size is small.
See [Cloudflare Pages limits](https://developers.cloudflare.com/pages/platform/limits/)
and [Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/).

## Tests

```bash
uv sync
uv run pytest

# Optional integration test against a real closed database (bash syntax):
IDA_TEST_DATABASE=/path/to/sample.i64 uv run pytest tests/test_ida_integration.py

# Optional browser smoke test; requires Playwright and an installed browser:
uv run --with playwright python tests/browser_smoke.py http://localhost:8000/<run-id>/ --channel msedge --library-url http://localhost:8000/
```

The integration test checks source preservation, absence of deprecation warnings,
and consistency of exported listing chunks, function details and types. The UI check covers navigation,
history, decompilation, xrefs, types, sample redirects, mobile layout and Pi logs.
Library/rebuild and publishing tests use temporary sites and a mocked Pagecast
process; the test suite does not publish anything to Cloudflare.
