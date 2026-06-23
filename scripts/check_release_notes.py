import argparse
import re
import sys
import tomllib
from pathlib import Path

VERSION_HEADING_PATTERN = re.compile(r"^## \[(?P<version>[^\]]+)](?:\s+-\s+.*)?\s*$", re.MULTILINE)


def load_project_version(pyproject_path: Path) -> str:
    try:
        raw_version = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]["version"]
    except KeyError as exc:
        raise ValueError(f"{pyproject_path} does not contain [project].version") from exc

    if not isinstance(raw_version, str) or not raw_version.strip():
        raise ValueError(f"{pyproject_path} has an invalid version: {raw_version!r}")
    return raw_version.strip()


def find_release_section(changelog_text: str, version: str) -> str:
    matches = list(VERSION_HEADING_PATTERN.finditer(changelog_text))

    for index, match in enumerate(matches):
        if match.group("version").strip() != version:
            continue

        section_start = match.end()
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(changelog_text)
        section = changelog_text[section_start:section_end].strip()
        if not section:
            raise ValueError(f"CHANGELOG.md release section for {version} is empty")
        return section

    raise ValueError(f"CHANGELOG.md must contain a non-empty '## [{version}]' release section")


def main() -> int:
    parser = argparse.ArgumentParser(description="Require release notes for the current pyproject version.")
    parser.add_argument("--pyproject", default="pyproject.toml", help="Path to pyproject.toml.")
    parser.add_argument("--changelog", default="CHANGELOG.md", help="Path to CHANGELOG.md.")
    parser.add_argument("--output", help="Write the current version release notes to this file.")
    args = parser.parse_args()

    try:
        version = load_project_version(Path(args.pyproject))
        section = find_release_section(Path(args.changelog).read_text(encoding="utf-8"), version)
    except (OSError, ValueError) as exc:
        print(f"Release notes check failed: {exc}", file=sys.stderr)
        return 1

    if args.output:
        Path(args.output).write_text(section + "\n", encoding="utf-8")

    print(f"Release notes found for {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
