from __future__ import annotations
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

@dataclass(frozen=True)
class TargetFile:
    path: Path
    fab: str
    env: str
    relative_path: str


def discover(root: str | Path, fab_starts_with: list[str] | None = None,
             env_include: list[str] | None = None,
             includes: list[str] | None = None,
             excludes: list[str] | None = None) -> list[TargetFile]:
    root = Path(root)
    fab_starts_with = fab_starts_with or []
    env_include = env_include or []
    includes = includes or ["**/*.yaml", "**/*.yml"]
    excludes = excludes or []
    result: list[TargetFile] = []
    for fab_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if fab_starts_with and not any(fab_dir.name.startswith(x) for x in fab_starts_with): continue
        for env_dir in sorted(p for p in fab_dir.iterdir() if p.is_dir()):
            if env_include and env_dir.name not in env_include: continue
            for p in env_dir.rglob("*"):
                if not p.is_file(): continue
                rel = p.relative_to(env_dir).as_posix()
                if not any(fnmatch(rel, pat) or fnmatch(p.name, pat) for pat in includes): continue
                if any(fnmatch(rel, pat) for pat in excludes): continue
                result.append(TargetFile(p, fab_dir.name, env_dir.name, rel))
    return result
