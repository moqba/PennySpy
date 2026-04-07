.PHONY: setup lint format typecheck check test docker_build docker_push clean

VENV := .venv
SENTINEL := $(VENV)/.installed

setup: $(SENTINEL)

$(VENV):
	uv venv

$(SENTINEL): $(VENV) pyproject.toml uv.lock
	uv sync --all-extras
	@touch $(SENTINEL)

lint: $(SENTINEL)
	uv run ruff check .

format: $(SENTINEL)
	uv run ruff format .
	uv run ruff check --fix .

typecheck: $(SENTINEL)
	uv run mypy pennyspy

check: lint typecheck

test: $(SENTINEL)
	uv run pytest

clean:
	rm -rf $(VENV)

docker_build:
	@git_hash=$$(git rev-parse --short HEAD); \
	docker build -t "moqba/pennyspy:$$git_hash" .; \
	docker tag "moqba/pennyspy:$$git_hash" moqba/pennyspy:latest

docker_push:
	@git_hash=$$(git rev-parse --short HEAD); \
	echo "Pushing moqba/pennyspy:$$git_hash and latest..."; \
	docker push "moqba/pennyspy:$$git_hash"; \
	docker push moqba/pennyspy:latest
