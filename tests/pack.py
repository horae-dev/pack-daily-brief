# SPDX-License-Identifier: Apache-2.0
"""Paths and loaders shared by the tests: everything is read with the core's own code."""

from pathlib import Path
from typing import Any

import yaml
from horae.contracts import Pipeline
from horae.contracts.plugin import PluginManifest
from horae.evals import LoadedCase, evals_dir, load_cases
from horae.skills import Skill, load_skill

ROOT = Path(__file__).resolve().parent.parent
PIPELINES = ROOT / "pipelines"
SKILL = "daily-brief/rank-and-word"
PIPELINE = "daily-brief"


def read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def manifest() -> PluginManifest:
    return PluginManifest.model_validate(read_yaml(ROOT / "plugin.yaml"))


def pipeline(name: str = PIPELINE) -> Pipeline:
    return Pipeline.model_validate(read_yaml(PIPELINES / f"{name}.yaml"))


def skill(name: str = SKILL) -> Skill:
    return load_skill(ROOT, name, allowed_intents=manifest().permissions.intents)


def cases(name: str = SKILL) -> dict[str, LoadedCase]:
    return {loaded.name: loaded for loaded in load_cases(evals_dir(ROOT, name))}
