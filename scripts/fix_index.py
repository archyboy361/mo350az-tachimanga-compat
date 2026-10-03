#!/usr/bin/env python3
import json
from pathlib import Path

SOURCE = Path("index.json")
OUTPUT = Path("index.min.json")

data = json.loads(SOURCE.read_text(encoding="utf-8"))

if not isinstance(data, list):
    raise SystemExit(
        f"MO350AZ index.json is no longer a legacy extension array "
        f"(got {type(data).__name__}). Refusing to publish a broken Tachimanga index."
    )

required_ext = {"name", "pkg", "apk", "lang", "code", "version", "sources"}
required_src = {"name", "lang", "id", "baseUrl"}

errors = []
for i, ext in enumerate(data):
    if not isinstance(ext, dict):
        errors.append(f"extension[{i}] is {type(ext).__name__}, not object")
        continue

    missing = required_ext - ext.keys()
    if missing:
        errors.append(f"extension[{i}] {ext.get('name', '<unnamed>')} missing {sorted(missing)}")
        continue

    sources = ext.get("sources")
    if not isinstance(sources, list):
        errors.append(f"extension[{i}] {ext.get('name')} sources is not an array")
        continue

    for j, src in enumerate(sources):
        if not isinstance(src, dict):
            errors.append(
                f"extension[{i}] {ext.get('name')} source[{j}] is "
                f"{type(src).__name__}, not object"
            )
            continue
        missing_src = required_src - src.keys()
        if missing_src:
            errors.append(
                f"extension[{i}] {ext.get('name')} source[{j}] "
                f"{src.get('name', '<unnamed>')} missing {sorted(missing_src)}"
            )

if errors:
    preview = "\n".join(errors[:25])
    extra = "" if len(errors) <= 25 else f"\n... and {len(errors)-25} more"
    raise SystemExit(
        "index.json is not valid for Tachimanga legacy parsing:\n"
        + preview + extra
    )

OUTPUT.write_text(
    json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n",
    encoding="utf-8",
)

# Final guard: Tachimanga expects the root token to be '['.
with OUTPUT.open("r", encoding="utf-8") as f:
    first = f.read(1)
if first != "[":
    raise SystemExit(f"Generated index starts with {first!r}, expected '['")

print(f"Wrote Tachimanga-compatible legacy index with {len(data)} extensions.")
