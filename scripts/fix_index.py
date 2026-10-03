#!/usr/bin/env python3
"""Unwrap MO350AZ's filtered catalogue without modifying entries."""
import json
import sys
from pathlib import Path

def unwrap(data):
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict) and isinstance(data.get("extensionList"), dict):
        entries = data["extensionList"].get("extensions")
    elif isinstance(data, dict):
        candidates = [data[k] for k in ("extensions", "data", "items", "packages", "results") if isinstance(data.get(k), list)]
        if len(candidates) != 1:
            raise ValueError("Unknown or ambiguous wrapper; refusing to publish")
        entries = candidates[0]
    else:
        raise ValueError("Unsupported index root")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Missing or empty catalogue")
    packages = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Entry is not an object")
        package = entry.get("pkg", entry.get("packageName"))
        if not isinstance(package, str) or not package:
            raise ValueError("Missing package name")
        packages.append(package)
    if len(set(packages)) != len(packages):
        raise ValueError("Duplicate packages")
    return entries

if __name__ == "__main__":
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "index.min.json")
    entries = unwrap(json.loads(path.read_text(encoding="utf-8-sig")))
    encoded = (json.dumps(entries, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    if not encoded.startswith(b"[") or json.loads(encoded) != entries:
        raise ValueError("Output validation failed")
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)
    print(f"Verified first byte [; preserved {len(entries)} entries unchanged")
