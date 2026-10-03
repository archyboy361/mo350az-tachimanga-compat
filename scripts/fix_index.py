#!/usr/bin/env python3
import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

SOURCE = Path("index.json")
OUTPUT = Path("index.min.json")

REQUIRED_EXTENSION_FIELDS = {
    "name", "pkg", "apk", "lang", "code", "version", "nsfw", "sources"
}
REQUIRED_SOURCE_FIELDS = {"name", "lang", "id", "baseUrl"}


def fail(message):
    raise SystemExit(message)


def basename_from_url(url):
    name = unquote(Path(urlparse(url).path).name)
    if not name or name in {".", ".."}:
        fail(f"Could not derive filename from URL: {url!r}")
    return name


def load_modern_extensions():
    data = json.loads(SOURCE.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        fail(
            "Expected MO350AZ's modern repository object at the top level, "
            f"got {type(data).__name__}"
        )

    extension_list = data.get("extensionList")
    if not isinstance(extension_list, dict):
        fail("Missing object field: extensionList")

    extensions = extension_list.get("extensions")
    if not isinstance(extensions, list) or not extensions:
        fail("Missing/non-empty array field: extensionList.extensions")

    return extensions


def convert_extension(ext, index):
    if not isinstance(ext, dict):
        fail(f"extension[{index}] is not an object")

    name = ext.get("name")
    package_name = ext.get("packageName")
    resources = ext.get("resources")
    version_name = ext.get("versionName")
    version_code = ext.get("versionCode")
    warning = ext.get("contentWarning")
    sources = ext.get("sources")

    if not isinstance(name, str) or not name:
        fail(f"extension[{index}] has invalid name")
    if not isinstance(package_name, str) or not package_name:
        fail(f"extension[{index}] {name!r} has invalid packageName")
    if not isinstance(resources, dict):
        fail(f"extension[{index}] {name!r} has invalid resources")
    if not isinstance(version_name, str) or not version_name:
        fail(f"extension[{index}] {name!r} has invalid versionName")

    try:
        code = int(version_code)
    except (TypeError, ValueError):
        fail(
            f"extension[{index}] {name!r} has invalid versionCode "
            f"{version_code!r}"
        )

    if not isinstance(sources, list) or not sources:
        fail(f"extension[{index}] {name!r} has no sources")

    apk_url = resources.get("apkUrl")
    icon_url = resources.get("iconUrl")

    if not isinstance(apk_url, str) or not apk_url.startswith(("https://", "http://")):
        fail(f"extension[{index}] {name!r} has invalid resources.apkUrl")
    if not isinstance(icon_url, str) or not icon_url.startswith(("https://", "http://")):
        fail(f"extension[{index}] {name!r} has invalid resources.iconUrl")

    legacy_sources = []
    languages = []

    for source_index, source in enumerate(sources):
        if not isinstance(source, dict):
            fail(
                f"extension[{index}] {name!r} source[{source_index}] "
                "is not an object"
            )

        source_name = source.get("name")
        language = source.get("language")
        source_id = source.get("id")
        home_url = source.get("homeUrl", "")

        if not isinstance(source_name, str) or not source_name:
            fail(
                f"extension[{index}] {name!r} source[{source_index}] "
                "has invalid name"
            )
        if not isinstance(language, str) or not language:
            fail(
                f"extension[{index}] {name!r} source[{source_index}] "
                "has invalid language"
            )
        if source_id is None:
            fail(
                f"extension[{index}] {name!r} source[{source_index}] "
                "is missing id"
            )
        if home_url is None:
            home_url = ""
        if not isinstance(home_url, str):
            fail(
                f"extension[{index}] {name!r} source[{source_index}] "
                "has invalid homeUrl"
            )

        legacy_sources.append(
            {
                "name": source_name,
                "lang": language,
                # Existing Tachimanga legacy repositories commonly serialize IDs
                # as decimal strings, and Tachimanga accepts this form.
                "id": str(source_id),
                "baseUrl": home_url,
            }
        )
        languages.append(language)

    unique_languages = set(languages)
    extension_language = languages[0] if len(unique_languages) == 1 else "all"

    # Legacy format only has a 0/1 flag. Suwayomi interprets 1 as MIXED.
    # MO350AZ's filtered repo should contain SAFE/MIXED only.
    nsfw = 1 if warning in {"CONTENT_WARNING_MIXED", "CONTENT_WARNING_NSFW"} else 0

    apk_name = basename_from_url(apk_url)

    legacy = {
        "name": name if name.startswith("Tachiyomi: ") else f"Tachiyomi: {name}",
        "pkg": package_name,
        "apk": apk_name,
        "lang": extension_language,
        "code": code,
        "version": version_name,
        "nsfw": nsfw,
        "sources": legacy_sources,
    }

    assets = {
        "apk_url": apk_url,
        "apk_path": str(Path("apk") / apk_name),
        "icon_url": icon_url,
        "icon_path": str(Path("icon") / f"{package_name}.png"),
    }

    return legacy, assets


def download_one(url, path_string):
    path = Path(path_string)

    if path.is_file() and path.stat().st_size > 0:
        return "cached", path_string

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    last_error = None

    for attempt in range(1, 4):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "mo350az-tachimanga-compat/3",
                    "Accept": "*/*",
                },
            )
            with urlopen(request, timeout=120) as response, temp.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)

            if temp.stat().st_size <= 0:
                raise RuntimeError("downloaded file is empty")

            os.replace(temp, path)
            return "downloaded", path_string

        except Exception as exc:
            last_error = exc
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

            if attempt < 3:
                time.sleep(attempt * 2)

    raise RuntimeError(
        f"failed to download {url} -> {path_string}: {last_error}"
    )


def hydrate_assets(assets, workers):
    # De-duplicate URLs/paths in case multiple records happen to reference
    # the same file.
    jobs = {}
    for asset in assets:
        jobs[asset["apk_path"]] = asset["apk_url"]
        jobs[asset["icon_path"]] = asset["icon_url"]

    counts = {"cached": 0, "downloaded": 0}
    failures = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(download_one, url, path): (url, path)
            for path, url in jobs.items()
        }

        for future in as_completed(futures):
            url, path = futures[future]
            try:
                status, _ = future.result()
                counts[status] += 1
            except Exception as exc:
                failures.append(str(exc))

    if failures:
        preview = "\n".join(failures[:20])
        extra = (
            ""
            if len(failures) <= 20
            else f"\n... and {len(failures) - 20} more"
        )
        fail("Asset hydration failed:\n" + preview + extra)

    print(
        f"Assets: {counts['downloaded']} downloaded, "
        f"{counts['cached']} already present."
    )


def prune_assets(assets):
    wanted_apks = {Path(a["apk_path"]).resolve() for a in assets}
    wanted_icons = {Path(a["icon_path"]).resolve() for a in assets}

    removed = 0

    for directory, pattern, wanted in [
        (Path("apk"), "*.apk", wanted_apks),
        (Path("icon"), "*.png", wanted_icons),
    ]:
        if not directory.exists():
            continue

        for path in directory.glob(pattern):
            if path.resolve() not in wanted:
                path.unlink()
                removed += 1

    print(f"Pruned {removed} stale APK/icon files.")


def validate_legacy(legacy, assets, require_assets):
    if not isinstance(legacy, list) or not legacy:
        fail("Generated legacy index is not a non-empty array")

    for i, ext in enumerate(legacy):
        missing = REQUIRED_EXTENSION_FIELDS - ext.keys()
        if missing:
            fail(f"generated extension[{i}] missing {sorted(missing)}")

        if not isinstance(ext["code"], int):
            fail(f"generated extension[{i}] code is not an integer")

        if ext["nsfw"] not in (0, 1):
            fail(f"generated extension[{i}] nsfw is not 0 or 1")

        if not isinstance(ext["sources"], list) or not ext["sources"]:
            fail(f"generated extension[{i}] has no sources")

        for j, source in enumerate(ext["sources"]):
            missing_source = REQUIRED_SOURCE_FIELDS - source.keys()
            if missing_source:
                fail(
                    f"generated extension[{i}] source[{j}] "
                    f"missing {sorted(missing_source)}"
                )

    if require_assets:
        missing_apks = [
            a["apk_path"]
            for a in assets
            if not Path(a["apk_path"]).is_file()
            or Path(a["apk_path"]).stat().st_size <= 0
        ]
        missing_icons = [
            a["icon_path"]
            for a in assets
            if not Path(a["icon_path"]).is_file()
            or Path(a["icon_path"]).stat().st_size <= 0
        ]

        if missing_apks or missing_icons:
            fail(
                f"Missing hydrated assets: {len(missing_apks)} APKs, "
                f"{len(missing_icons)} icons"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hydrate", action="store_true")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    modern = load_modern_extensions()

    legacy = []
    assets = []

    for index, ext in enumerate(modern):
        converted, ext_assets = convert_extension(ext, index)
        legacy.append(converted)
        assets.append(ext_assets)

    if args.hydrate:
        hydrate_assets(assets, max(1, min(args.workers, 24)))

    if args.prune:
        prune_assets(assets)

    validate_legacy(legacy, assets, require_assets=args.hydrate)

    OUTPUT.write_text(
        json.dumps(legacy, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    raw = OUTPUT.read_bytes()

    if not raw.startswith(b"["):
        fail("Generated index.min.json does not begin with '['")

    parsed = json.loads(raw)

    if len(parsed) != len(modern):
        fail("Generated index extension count differs from modern index")

    print(
        f"Wrote Tachimanga legacy index with {len(legacy)} extensions and "
        f"{sum(len(e['sources']) for e in legacy)} sources."
    )


if __name__ == "__main__":
    main()
