from __future__ import annotations

import importlib.util
import pathlib
import types
import uuid

def load_plugin_file(path: str) -> types.ModuleType:
    """
    Dynamically import a plugin file by path.
    The plugin's top-level code runs on import, typically registering rules.
    """
    p = pathlib.Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.lower() != ".py":
        raise ValueError(f"Plugin must be a .py file: {p}")

    module_name = f"shuffle_plugin_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, str(p))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load plugin: {p}")

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # plugin code runs here
    return mod