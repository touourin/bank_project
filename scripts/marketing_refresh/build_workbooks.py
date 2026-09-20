"""Bounded-memory Artifact Tool authoring, two independent chunks at a time."""

import concurrent.futures
import gzip
import hashlib
import subprocess
import time
from pathlib import Path

from .common import OUTPUT, TABLES, dump, load
from .excel_parts import assemble

NODE = "/Users/ourin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
BUILDER = Path(__file__).with_name("excel_chunk.mjs")


def author(label, index, lines):
    table = TABLES[label]
    folder = OUTPUT / "chunks" / table
    folder.mkdir(parents=True, exist_ok=True)
    basename = f"{index:06d}"
    part = folder / (basename + ".xlsx")
    marker = part.with_suffix(".done.json")
    content = "".join(lines)
    digest = hashlib.sha256(content.encode()).hexdigest()
    if marker.exists() and part.exists() and load(marker).get("digest") == digest:
        return len(lines)
    input_path = folder / (basename + ".jsonl")
    input_path.write_text(content, encoding="utf-8")
    start = time.monotonic()
    run = subprocess.run(
        [
            NODE,
            str(BUILDER),
            "chunk",
            str(input_path),
            str(part),
            str(OUTPUT / "excel_metadata.json"),
            label,
        ],
        capture_output=True,
        text=True,
        timeout=240,
    )
    if run.returncode:
        raise RuntimeError(label + " chunk failed: " + run.stderr[-3500:] + run.stdout[-1500:])
    if not part.exists():
        raise RuntimeError("Export missing")
    dump(
        marker,
        {"digest": digest, "rows": len(lines), "seconds": round(time.monotonic() - start, 2)},
    )
    input_path.unlink()
    return len(lines)


def main(only=None):
    metadata = load(OUTPUT / "excel_metadata.json")
    if load(OUTPUT / "validation.json")["error_count"]:
        raise ValueError("Dataset has not passed reconciliation")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for label, table in TABLES.items():
            if only and label != only:
                continue
            chunk_size = min(5000, max(500, 1000000 // len(metadata[label]["columns"])))
            pending = set()
            written = 0
            index = 0
            lines = []

            def collect(label=label):
                nonlocal pending, written
                done, pending = concurrent.futures.wait(
                    pending, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for f in done:
                    written += f.result()
                print(
                    f"Authored Excel {label}: {written:,}/{metadata[label]['rows']:,}", flush=True
                )

            with gzip.open(OUTPUT / (table + ".jsonl.gz"), "rt", encoding="utf-8") as f:
                for line in f:
                    lines.append(line)
                    if len(lines) == chunk_size:
                        pending.add(pool.submit(author, label, index, lines))
                        index += 1
                        lines = []
                        if len(pending) >= 2:
                            collect()
                if lines:
                    pending.add(pool.submit(author, label, index, lines))
            while pending:
                collect()
            assemble(label)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--label", choices=list(TABLES))
    main(parser.parse_args().label)
