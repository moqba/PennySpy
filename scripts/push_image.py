import subprocess
import sys
from pathlib import Path


class CommandRunner:
    def run(self, cmd):
        print(f"> {' '.join(cmd)}")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            sys.exit(result.returncode)


class DockerImagePublisher(CommandRunner):
    image = "moqba/pennyspy"

    def __init__(self):
        self.root_dir = Path(__file__).parent.parent
        self.git_hash = self.get_git_hash()
        self.git_tag = self.get_git_tag()
        self.version = self.git_tag or self.git_hash
        self.version_tag = f"{self.image}:{self.version}"
        self.latest_tag = f"{self.image}:latest"

    def get_git_hash(self):
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def get_git_tag(self):
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None

        return result.stdout.strip() or None

    def docker_build(self):
        self.run(["docker", "build", "-t", self.version_tag, str(self.root_dir)])
        self.run(["docker", "tag", self.version_tag, self.latest_tag])

    def docker_push(self):
        print(f"Pushing {self.version_tag} and latest...")
        self.run(["docker", "push", self.version_tag])
        self.run(["docker", "push", self.latest_tag])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "push", "all"])
    args = parser.parse_args()

    publisher = DockerImagePublisher()

    if args.action == "build":
        publisher.docker_build()
    elif args.action == "push":
        publisher.docker_push()
    elif args.action == "all":
        publisher.docker_build()
        publisher.docker_push()
