import argparse
import fnmatch
import subprocess
import sys
import tomllib
from pathlib import Path

from packaging.version import InvalidVersion, Version

DEFAULT_BASE_REF = "origin/main"
PACKAGE_RELEVANT_PATTERNS = (
    "pennyspy/**",
    "scripts/**",
    "pyproject.toml",
    "uv.lock",
    "Dockerfile",
)


def run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {message}")
    return result.stdout


def load_project_version(data: str, source: str) -> Version:
    try:
        raw_version = tomllib.loads(data)["project"]["version"]
    except KeyError as exc:
        raise ValueError(f"{source} does not contain [project].version") from exc

    try:
        return Version(raw_version)
    except InvalidVersion as exc:
        raise ValueError(f"{source} has an invalid version: {raw_version!r}") from exc


def current_version() -> Version:
    return load_project_version(Path("pyproject.toml").read_text(), "pyproject.toml")


def base_version(base_ref: str) -> Version:
    return load_project_version(run_git(["show", f"{base_ref}:pyproject.toml"]), f"{base_ref}:pyproject.toml")


def changed_files(base_ref: str) -> list[str]:
    merge_base = run_git(["merge-base", base_ref, "HEAD"]).strip()
    output = run_git(["diff", "--name-only", f"{merge_base}...HEAD"])
    return [line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()]


def is_package_relevant(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in PACKAGE_RELEVANT_PATTERNS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Require a pyproject.toml version bump for package changes.")
    parser.add_argument(
        "--base-ref",
        default=DEFAULT_BASE_REF,
        help=f"Base ref to compare against. Defaults to {DEFAULT_BASE_REF}.",
    )
    args = parser.parse_args()

    files = changed_files(args.base_ref)
    relevant_files = [path for path in files if is_package_relevant(path)]
    if not relevant_files:
        print("No package-relevant changes detected; version bump is not required.")
        return 0

    old_version = base_version(args.base_ref)
    new_version = current_version()
    if new_version > old_version:
        print(f"Version bump detected: {old_version} -> {new_version}.")
        return 0

    print(
        "Package-relevant changes require increasing [project].version in pyproject.toml.\n"
        f"Base version: {old_version}\n"
        f"Current version: {new_version}\n"
        "Relevant changed files:\n"
        + "\n".join(f"- {path}" for path in relevant_files),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
