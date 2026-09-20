# red-rail task runner. Every target is what CI runs, nothing more.

.PHONY: sync lint test check ci audit skills-install contracts-check

RAIL_FLAGS ?=
DATE ?= $(shell date +%F)
# The drift snapshots name every ReD project: they live at the ReD root, outside this
# public repository (red-watcher, 2026-09-20).
AUDIT_DIR ?= ../../docs/audits

## Install the project with every extra (dev, brain, reviewer)
sync:
	uv sync --all-extras

## Lint and format check
lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

## Run the test suite
test:
	uv run pytest -q

## Run the rail gates against this repository (dogfooding; RAIL_FLAGS=--ci in CI)
check:
	uv run rail check $(RAIL_FLAGS)

## What CI runs, in order
ci: lint test check

## Audit every ReD project and keep the dated snapshot (the drift table, versioned)
audit:
	mkdir -p $(AUDIT_DIR)
	uv run rail audit .. --json > $(AUDIT_DIR)/$(DATE)-projects.json
	uv run rail audit .. > $(AUDIT_DIR)/$(DATE)-projects.md
	uv run rail audit ..

## Symlink the facade skills into ~/.claude/skills (operator's workstation)
skills-install:
	mkdir -p $(HOME)/.claude/skills
	for d in skills/*/; do ln -sfn "$(CURDIR)/$$d" "$(HOME)/.claude/skills/$$(basename $$d)"; done

## Compare the vendored brain-v42 contracts with the pinned ref of the sibling checkout
contracts-check:
	uv run pytest -q tests/test_boundary.py -k vendored_files_equal_the_pinned_ref -rs
