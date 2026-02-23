from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Hashable

# Registry to manage external user rules.

RuleFn = Callable[[datetime], Hashable]

@dataclass(frozen=True)
class RuleSpec:
    name: str
    fn: RuleFn
    description: str = ""

_RULES: Dict[str, RuleSpec] = {}

def register_rule(name: str, fn: RuleFn, description: str = "") -> None:
    """Register a new shuffle rule by name."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Rule name must be a non-empty string")
    if not callable(fn):
        raise TypeError(f"Rule '{name}' must be callable")
    if name in _RULES:
        raise ValueError(f"Rule '{name}' already exists (name collision)")
    _RULES[name] = RuleSpec(name=name, fn=fn, description=description)

def all_rules() -> Dict[str, RuleSpec]:
    """Returns a copy of registered rules {name: RuleSpec}."""
    return dict(_RULES)
