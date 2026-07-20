"""Shared TGI resolver for split official Sims 4 FullBuild packages.

Official game/DLC content is often split over ClientFullBuild0, 1, 2, ...:
one file can hold MODL/MLOD resources while another holds the texture payloads.
The game resolves those links by TGI (type, group, instance), not by filenames.
This module gives the exporter the same lookup model without hard-coding any
EP/GP/SP numbers or a per-DLC map.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import os
from pathlib import Path
import re

from .dbpf import DBPFIndex, DBPFStream, IndexEntry
from . import resource_types as rt


# Prefix contains FullBuild or DeltaBuild; trailing number selects a sibling.
_NUMBERED_BUILD_RE = re.compile(r"^(.*(?:fullbuild|deltabuild))(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class ResourceLocation:
    """One resource and the package file in which it is stored."""

    path: str
    entry: IndexEntry
    scope: str  # source-family | game-fallback
    sequence: int

    @property
    def tgi(self) -> tuple[int, int, int]:
        return (self.entry.type_id, self.entry.group_id, self.entry.instance)

    @property
    def package_name(self) -> str:
        return os.path.basename(self.path)


class LinkedResourceLibrary:
    """Resolve resources across a FullBuild family and, when available, game data.

    Indexing intentionally reads only DBPF headers/index tables. Actual resource
    bytes are read lazily and seek-based from the one package that owns the
    required texture. This keeps a linked export from loading all FullBuild
    files into memory simultaneously.
    """

    def __init__(self, primary_path: str, family_paths: list[str], game_root: str | None):
        self.primary_path = os.path.abspath(primary_path)
        self.family_paths = [os.path.abspath(p) for p in family_paths]
        self.game_root = os.path.abspath(game_root) if game_root else None
        self._by_tgi: dict[tuple[int, int, int], list[ResourceLocation]] = defaultdict(list)
        self._streams: dict[str, DBPFStream] = {}
        self._indexed_paths: set[str] = set()
        self._warnings: list[str] = []
        self._game_indexed = False
        self._read_counts: Counter[str] = Counter()

        for sequence, path in enumerate(self.family_paths):
            self._index_package(path, "source-family", sequence)

    # ------------------------------------------------------------------
    # Construction / package discovery
    # ------------------------------------------------------------------
    @classmethod
    def for_package(cls, package_path: str) -> "LinkedResourceLibrary | None":
        """Create a resolver when *package_path* is a numbered Build package.

        A normal CC .package deliberately returns ``None`` so its legacy export
        behavior is unchanged.
        """
        p = Path(package_path).resolve()
        match = _NUMBERED_BUILD_RE.match(p.stem)
        if not match or p.suffix.lower() != ".package":
            return None

        prefix = match.group(1)
        sibling_re = re.compile(r"^" + re.escape(prefix) + r"\d+$", re.IGNORECASE)
        siblings = [
            child for child in p.parent.iterdir()
            if child.is_file() and child.suffix.lower() == ".package"
            and sibling_re.match(child.stem)
        ]
        if not siblings:
            siblings = [p]

        def build_number(child: Path) -> int:
            found = re.search(r"(\d+)$", child.stem)
            return int(found.group(1)) if found else 0

        family_paths = [str(child) for child in sorted(siblings, key=build_number)]
        return cls(str(p), family_paths, _infer_game_root(str(p)))

    @property
    def is_multi_package(self) -> bool:
        return len(self.family_paths) > 1

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

    def model_package_paths(self) -> list[str]:
        """Return family packages containing at least one MODL or MLOD."""
        result = []
        for path in self.family_paths:
            has_model = any(
                loc.entry.type_id in (rt.MODL, rt.MLOD)
                for locations in self._by_tgi.values()
                for loc in locations
                if loc.path == path
            )
            if has_model:
                result.append(path)
        return result or [self.primary_path]

    # ------------------------------------------------------------------
    # Index / resolve / read
    # ------------------------------------------------------------------
    def _index_package(self, path: str, scope: str, sequence: int) -> None:
        path = os.path.abspath(path)
        if path in self._indexed_paths:
            return
        try:
            index = DBPFIndex.from_file(path)
        except Exception as exc:
            self._warnings.append(f"index {path}: {exc}")
            return
        self._indexed_paths.add(path)
        for entry in index.entries:
            location = ResourceLocation(path, entry, scope, sequence)
            self._by_tgi[location.tgi].append(location)

    @property
    def game_fallback_ready(self) -> bool:
        return self._game_indexed

    def prepare_game_fallback(self, progress_callback=None) -> None:
        """Index installed Build/DeltaBuild packages once, if a game root exists.

        The method only reads each package's index table, not its full data. It
        is safe to call for every object; after the first call it is a no-op.
        """
        if self._game_indexed:
            return
        self._game_indexed = True
        if not self.game_root or not os.path.isdir(self.game_root):
            return

        root = Path(self.game_root)
        try:
            candidates = [
                child for child in root.rglob("*.package")
                if _NUMBERED_BUILD_RE.match(child.stem)
            ]
        except OSError as exc:
            self._warnings.append(f"scan game root {self.game_root}: {exc}")
            return

        # Delta packages are assigned a later sequence. Resolution prefers a
        # DeltaBuild copy over a FullBuild copy when a game update replaces a
        # resource with the same TGI.
        candidates.sort(key=lambda p: (_is_delta_path(str(p)), str(p).lower()))
        next_sequence = len(self._indexed_paths)
        total = len(candidates)
        for current, child in enumerate(candidates, 1):
            self._index_package(str(child), "game-fallback", next_sequence)
            next_sequence += 1
            if progress_callback is not None:
                try:
                    progress_callback(current, total, str(child))
                except Exception:
                    pass

    def resolve(self, tgi: tuple[int, int, int], preferred_path: str | None = None,
                search_game: bool = True) -> ResourceLocation | None:
        """Return the highest-priority location for a resource TGI.

        Once requested, all installed Build/DeltaBuild indexes are searched so
        shared base-game textures work too. No DLC-specific rules are used.
        """
        if search_game:
            self.prepare_game_fallback()
        locations = self._by_tgi.get(tgi)
        if not locations:
            return None
        preferred = os.path.abspath(preferred_path) if preferred_path else None
        return min(locations, key=lambda loc: self._priority(loc, preferred))

    def _priority(self, loc: ResourceLocation, preferred_path: str | None) -> tuple:
        # Sims game patches in DeltaBuild normally override FullBuild data.
        # For all other equal candidates favor the package currently exporting,
        # then its sibling family, then stable discovery order.
        delta_rank = 0 if _is_delta_path(loc.path) else 1
        preferred_rank = 0 if preferred_path and loc.path == preferred_path else 1
        family_rank = 0 if loc.scope == "source-family" else 1
        return (delta_rank, preferred_rank, family_rank, loc.sequence, loc.path.lower())

    def read_resource(self, location: ResourceLocation, loaded_package=None,
                      loaded_path: str | None = None) -> bytes:
        """Read one location lazily; reuse the currently-open source DBPF."""
        if loaded_package is not None and loaded_path:
            if os.path.abspath(loaded_path) == location.path:
                self._read_counts[location.package_name] += 1
                return loaded_package.read_resource(location.entry)

        stream = self._streams.get(location.path)
        if stream is None:
            # Entries in the index have the exact same IndexEntry layout as a
            # normal DBPF object, so the stream can use them directly.
            stream = DBPFStream(location.path)
            self._streams[location.path] = stream
        self._read_counts[location.package_name] += 1
        return stream.read_resource(location.entry)

    def describe(self) -> dict:
        duplicates = sum(1 for locations in self._by_tgi.values() if len(locations) > 1)
        return {
            "family_packages": [os.path.basename(p) for p in self.family_paths],
            "game_root": self.game_root or "",
            "game_indexed": self._game_indexed,
            "indexed_packages": len(self._indexed_paths),
            "indexed_tgis": len(self._by_tgi),
            "duplicate_tgis": duplicates,
            "resources_read": dict(self._read_counts),
        }

    def close(self) -> None:
        for stream in self._streams.values():
            stream.close()
        self._streams.clear()


def _is_delta_path(path: str) -> bool:
    return "deltabuild" in os.path.basename(path).lower()


def _infer_game_root(package_path: str) -> str | None:
    """Locate ``The Sims 4`` install root from a FullBuild package path."""
    current = Path(package_path).resolve().parent
    # EP04/ClientFullBuild0 -> parent is game root; Data/Client -> two parents.
    # Do not slice Path.parents here: Python 3.9 supports indexing it but not
    # every build supports slicing, while the launcher still targets Python 3.9.
    candidate = current
    for _ in range(6):
        if (candidate / "Data").is_dir():
            return str(candidate)
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return None
