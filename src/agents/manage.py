"""Discover, validate and build Agent runtime folders."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RUNTIME_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
IMAGE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")
AGENTS_ROOT = Path(__file__).resolve().parent


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeManifest:
    id: str
    image: str
    description: str
    directory: Path


def load_manifest(directory: Path) -> RuntimeManifest:
    manifest_path = directory / "runtime.json"
    dockerfile_path = directory / "Dockerfile"
    if not manifest_path.is_file():
        raise ManifestError(f"{directory.name}: missing runtime.json")
    if not dockerfile_path.is_file():
        raise ManifestError(f"{directory.name}: missing Dockerfile")
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{directory.name}: runtime.json is invalid JSON") from exc
    if not isinstance(document, dict):
        raise ManifestError(f"{directory.name}: runtime.json must be an object")
    if document.get("schema_version") != 1:
        raise ManifestError(f"{directory.name}: schema_version must be 1")
    runtime_id = document.get("id")
    image = document.get("image")
    description = document.get("description", "")
    if not isinstance(runtime_id, str) or not RUNTIME_ID_PATTERN.fullmatch(runtime_id):
        raise ManifestError(f"{directory.name}: id must be a valid runtime slug")
    if runtime_id != directory.name:
        raise ManifestError(
            f"{directory.name}: id must match the runtime directory name"
        )
    if not isinstance(image, str) or not IMAGE_REFERENCE_PATTERN.fullmatch(image):
        raise ManifestError(f"{directory.name}: image reference is invalid")
    if not isinstance(description, str):
        raise ManifestError(f"{directory.name}: description must be a string")
    return RuntimeManifest(runtime_id, image, description, directory)


def discover_runtimes(root: Path = AGENTS_ROOT) -> tuple[RuntimeManifest, ...]:
    manifests: list[RuntimeManifest] = []
    errors: list[str] = []
    for directory in sorted(root.iterdir(), key=lambda path: path.name):
        if not directory.is_dir() or directory.name.startswith((".", "__")):
            continue
        try:
            manifests.append(load_manifest(directory))
        except ManifestError as exc:
            errors.append(str(exc))
    if errors:
        raise ManifestError("\n".join(errors))
    if not manifests:
        raise ManifestError("No Agent runtime manifests were found")
    return tuple(manifests)


def build_runtime(manifest: RuntimeManifest, docker_binary: str) -> None:
    subprocess.run(
        [
            docker_binary,
            "build",
            "--file",
            str(manifest.directory / "Dockerfile"),
            "--tag",
            manifest.image,
            str(manifest.directory),
        ],
        check=True,
    )


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--docker-binary", default=os.getenv("DOCKER_BINARY", "docker")
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List discovered runtimes")
    subparsers.add_parser("validate", help="Validate all runtime manifests")
    build_parser = subparsers.add_parser("build", help="Build runtime images")
    build_parser.add_argument("runtime_id", nargs="?")
    build_parser.add_argument("--all", action="store_true", dest="build_all")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    try:
        manifests = discover_runtimes()
        if args.command == "list":
            for manifest in manifests:
                suffix = f" - {manifest.description}" if manifest.description else ""
                print(f"{manifest.id}\t{manifest.image}{suffix}")
            return 0
        if args.command == "validate":
            print(f"Validated {len(manifests)} Agent runtime(s)")
            return 0
        if args.build_all == bool(args.runtime_id):
            raise ManifestError("build requires one runtime id or --all")
        selected = (
            manifests
            if args.build_all
            else tuple(item for item in manifests if item.id == args.runtime_id)
        )
        if not selected:
            raise ManifestError(f"Unknown Agent runtime: {args.runtime_id}")
        for manifest in selected:
            print(f"Building {manifest.id} as {manifest.image}")
            build_runtime(manifest, args.docker_binary)
        return 0
    except (ManifestError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
