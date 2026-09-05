"""One database per process. Always inspect a temporary copy, never save the source."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, separators=(",", ":")), encoding="utf-8"
    )


def digest(path: Path) -> str:
    with path.open("rb") as source:
        sha = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def export(
    database: Path,
    output: Path,
    chunk_size: int = 512,
    max_functions: int | None = None,
    max_heads: int | None = None,
) -> None:
    # Import order matters: idapro bootstraps the IDAPython modules outside IDA.
    import idapro  # noqa: F401

    # isort: split
    import ida_bytes
    import ida_funcs
    import ida_ida
    import ida_idaapi
    import ida_lines
    import ida_nalt
    import ida_range
    import ida_segment
    import ida_typeinf
    import ida_xref
    import idautils
    from ida_domain import Database
    from ida_domain.database import IdaCommandOptions
    from ida_domain.types import TypeKind

    source_hash = digest(database)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ida-run-export-") as temporary:
        copied = Path(temporary) / database.name
        shutil.copy2(database, copied)
        with Database.open(
            str(copied), IdaCommandOptions(auto_analysis=False), save_on_close=False
        ) as db:
            metadata = {
                "schema": 1,
                "source_sha256": source_hash,
                "database": database.name,
                "input_file": ida_nalt.get_input_file_path(),
                "input_sha256": (ida_nalt.retrieve_input_file_sha256() or b"").hex(),
                "image_base": hex(ida_nalt.get_imagebase()),
                "entry_points": [],
                "functions": [],
                "chunks": [],
                "types": [],
                "segments": [],
                "names": [[hex(ea), name] for ea, name in db.names.get_all()],
                "warnings": [],
            }
            entries = [
                entry
                for entry in db.entries
                if entry.address != ida_idaapi.BADADDR
                and not entry.has_forwarder()
                and ida_bytes.is_mapped(entry.address)
            ]
            start_ea = ida_ida.inf_get_start_ea()
            # The loader's execution entry comes before exported symbols (e.g. a DLL's exports).
            entries.sort(key=lambda entry: entry.address != start_ea)
            metadata["entry_points"] = [
                {"address": hex(entry.address), "name": entry.name} for entry in entries
            ]
            if (
                start_ea != ida_idaapi.BADADDR
                and ida_bytes.is_mapped(start_ea)
                and not any(entry.address == start_ea for entry in entries)
            ):
                metadata["entry_points"].insert(
                    0,
                    {
                        "address": hex(start_ea),
                        "name": db.names.get_at(start_ea) or "Entry point",
                    },
                )
            # Domain's iterators still call deprecated getnseg/getn_func in IDA 9.4.
            for index in range(ida_segment.get_segm_qty()):
                segment = ida_segment.segment_info_t()
                if not ida_segment.get_segment_info_by_num(
                    segment, index, ida_segment.GSI_NAME
                ):
                    raise RuntimeError(f"Cannot read segment {index}")
                metadata["segments"].append(
                    {
                        "start": hex(segment.start_ea),
                        "end": hex(segment.end_ea),
                        "name": segment.get_name(),
                    }
                )

            def xrefs(ea: int, incoming: bool) -> list[dict]:
                refs = (
                    idautils.XrefsTo(ea, 0) if incoming else idautils.XrefsFrom(ea, 0)
                )
                return [
                    {
                        "address": hex(ref.frm if incoming else ref.to),
                        "kind": idautils.XrefTypeName(ref.type),
                    }
                    for ref in refs
                    if ref.type != ida_xref.fl_F
                ]

            failed = 0
            for index in range(ida_funcs.get_func_qty()):
                function = ida_funcs.func_entry_info_t()
                if not ida_funcs.get_func_entry_info_by_num(function, index):
                    raise RuntimeError(f"Cannot read function {index}")
                ea = function.start_ea
                ranges = ida_range.rangeset_t()
                if ida_funcs.get_func_ranges_ea(ranges, ea) == ida_idaapi.BADADDR:
                    raise RuntimeError(f"Cannot read function ranges at {ea:#x}")
                item = {
                    "address": hex(ea),
                    "end": hex(function.end_ea),
                    "name": db.names.get_at(ea) or "",
                    "ranges": [
                        [hex(chunk.start_ea), hex(chunk.end_ea)] for chunk in ranges
                    ],
                    "file": f"functions/{ea:x}.json",
                }
                details = {
                    **item,
                    "signature": ida_typeinf.idc_get_type(ea),
                    "comment": ida_funcs.get_func_cmt_ea(ea, False) or "",
                    "repeatable_comment": ida_funcs.get_func_cmt_ea(ea, True) or "",
                    "incoming": xrefs(ea, True),
                    "pseudocode": None,
                    "error": None,
                }
                if max_functions is not None and index >= max_functions:
                    details["error"] = "Pseudocode omitted by --max-functions."
                else:
                    try:
                        pseudo = db.pseudocode.decompile(ea)
                        # Older Domain releases returned lines; newer releases wrap cfunc_t.
                        lines = (
                            pseudo.to_text() if hasattr(pseudo, "to_text") else pseudo
                        )
                        if isinstance(lines, str):
                            lines = lines.splitlines()
                        details["pseudocode"] = [
                            ida_lines.tag_remove(str(line)) for line in lines
                        ]
                    except Exception as error:  # noqa: BLE001 - SDK/decompiler errors belong to this function's export.
                        failed += 1
                        details["error"] = f"Decompilation unavailable: {error}"
                write_json(output / item["file"], details)
                metadata["functions"].append(item)
                if index % 100 == 0:
                    print(f"Exported {index + 1} functions", flush=True)
            if failed:
                metadata["warnings"].append(
                    f"Decompilation unavailable for {failed} functions; see individual function errors."
                )
            if max_functions is not None and len(metadata["functions"]) > max_functions:
                metadata["warnings"].append(
                    f"Pseudocode limited to the first {max_functions} functions."
                )

            # Both code and data, in address order, including items outside functions.
            rows: list[dict] = []
            count = 0

            def flush() -> None:
                if not rows:
                    return
                filename = f"listing/{len(metadata['chunks']):06d}.json"
                metadata["chunks"].append(
                    {
                        "start": rows[0]["address"],
                        "end": rows[-1]["end"],
                        "count": len(rows),
                        "file": filename,
                    }
                )
                write_json(output / filename, rows)
                rows.clear()

            for ea in db.heads:
                if max_heads is not None and count >= max_heads:
                    metadata["warnings"].append(
                        f"Linear listing limited to {max_heads} defined items."
                    )
                    break
                size = max(1, ida_bytes.get_item_size(ea))
                raw = ida_bytes.get_bytes(ea, min(size, 16)) or b""
                rows.append(
                    {
                        "address": hex(ea),
                        "end": hex(ea + size),
                        "bytes": raw.hex(" ") + (" …" if size > 16 else ""),
                        "text": ida_lines.tag_remove(
                            ida_lines.generate_disasm_line(ea, 0) or ""
                        ),
                        "name": db.names.get_at(ea) or "",
                        "comment": ida_bytes.get_cmt(ea, False) or "",
                        "repeatable_comment": ida_bytes.get_cmt(ea, True) or "",
                        "incoming": xrefs(ea, True),
                        "outgoing": xrefs(ea, False),
                    }
                )
                count += 1
                if len(rows) >= chunk_size:
                    flush()
            flush()
            metadata["head_count"] = count

            # Local numbered types include user-defined structs, unions, enums and aliases.
            seen = set()
            for kind in (TypeKind.NUMBERED, TypeKind.NAMED):
                for type_info in db.types.get_all(type_kind=kind):
                    name = type_info.get_type_name() or str(type_info)
                    if name in seen:
                        continue
                    seen.add(name)
                    ordinal = len(metadata["types"])
                    declaration = type_info._print(
                        name,
                        ida_typeinf.PRTYPE_MULTI
                        | ida_typeinf.PRTYPE_TYPE
                        | ida_typeinf.PRTYPE_DEF,
                    ) or str(type_info)
                    item = {
                        "id": str(ordinal),
                        "name": name,
                        "file": f"types/{ordinal}.json",
                    }
                    write_json(
                        output / item["file"],
                        {
                            **item,
                            "declaration": declaration,
                            "comment": db.types.get_comment(type_info),
                        },
                    )
                    metadata["types"].append(item)
            # Written last: its presence means that the snapshot completed successfully.
            write_json(output / "index.json", metadata)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--chunk-size", type=int, default=512)
    parser.add_argument("--max-functions", type=int)
    parser.add_argument("--max-heads", type=int)
    options = parser.parse_args()
    export(**vars(options))
