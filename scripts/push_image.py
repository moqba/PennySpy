import subprocess
import sys
from pathlib import Path


def run(cmd):
    print(f"> {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(result.returncode)


def get_git_hash():
    result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def docker_build():
    git_hash = get_git_hash()
    tag_hash = f"moqba/pennyspy:{git_hash}"
    tag_latest = "moqba/pennyspy:latest"
    root_dir = Path(__file__).parent.parent

    run(["docker", "build", "-t", tag_hash, str(root_dir)])
    run(["docker", "tag", tag_hash, tag_latest])


def docker_push():
    git_hash = get_git_hash()
    tag_hash = f"moqba/pennyspy:{git_hash}"
    tag_latest = "moqba/pennyspy:latest"

    print(f"Pushing {tag_hash} and latest...")
    run(["docker", "push", tag_hash])
    run(["docker", "push", tag_latest])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "push", "all"])
    args = parser.parse_args()

    if args.action == "build":
        docker_build()
    elif args.action == "push":
        docker_push()
    elif args.action == "all":
        docker_build()
        docker_push()
