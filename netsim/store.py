"""YAML-backed storage for profiles and labs (one file per object)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from netsim.config import chown_to_user
from netsim.models import NAME_RE, Lab, Profile

T = TypeVar("T", bound=BaseModel)


class StoreError(Exception):
    def __init__(self, message: str, details: list[dict] | None = None):
        super().__init__(message)
        self.details = details or []


def format_validation(err: ValidationError) -> list[dict]:
    out = []
    for e in err.errors():
        loc = ".".join(str(x) for x in e["loc"] if not str(x).startswith("function-"))
        msg = e["msg"].removeprefix("Value error, ")
        out.append({"loc": loc, "msg": msg})
    return out


def parse_yaml(text: str, model: type[T], name: str | None = None) -> T:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        raise StoreError(f"YAML syntax error{where}: {getattr(e, 'problem', e)}") from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise StoreError("top level must be a mapping")
    if name is not None:
        data.setdefault("name", name)
    try:
        return model.model_validate(data)
    except ValidationError as e:
        details = format_validation(e)
        raise StoreError("; ".join(f"{d['loc']}: {d['msg']}" if d["loc"] else d["msg"] for d in details),
                         details) from None


def dump_yaml(obj: BaseModel) -> str:
    data = obj.model_dump(exclude_none=True, exclude_defaults=True)
    data.pop("name", None)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


@dataclass
class Entry(Generic[T]):
    name: str
    text: str
    obj: T | None
    error: StoreError | None


class YamlStore(Generic[T]):
    def __init__(self, directory: Path, model: type[T]):
        self.dir = directory
        self.model = model
        self._mem: dict[str, str] = {}  # ephemeral objects, never written to disk

    def _path(self, name: str) -> Path:
        if not re.fullmatch(NAME_RE, name):
            raise StoreError(f"invalid name {name!r}")
        return self.dir / f"{name}.yaml"

    def names(self) -> list[str]:
        disk = {p.stem for p in self.dir.glob("*.yaml")} if self.dir.is_dir() else set()
        return sorted(disk | set(self._mem))

    def exists(self, name: str) -> bool:
        return name in self._mem or self._path(name).exists()

    def is_ephemeral(self, name: str) -> bool:
        return name in self._mem

    def text(self, name: str) -> str:
        if name in self._mem:
            return self._mem[name]
        path = self._path(name)
        if not path.exists():
            raise KeyError(name)
        return path.read_text()

    def get(self, name: str) -> T:
        return parse_yaml(self.text(name), self.model, name)

    def entry(self, name: str) -> Entry[T]:
        text = self.text(name)
        try:
            return Entry(name, text, parse_yaml(text, self.model, name), None)
        except StoreError as e:
            return Entry(name, text, None, e)

    def save(self, name: str, text: str, ephemeral: bool = False) -> T:
        obj = parse_yaml(text, self.model, name)
        if not text.endswith("\n"):
            text += "\n"
        if ephemeral:
            self._mem[name] = text
            return obj
        self._mem.pop(name, None)
        path = self._path(name)
        if not self.dir.exists():
            self.dir.mkdir(parents=True)
            chown_to_user(self.dir)
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(text)
        tmp.replace(path)
        chown_to_user(path)
        return obj

    def delete(self, name: str) -> None:
        if self._mem.pop(name, None) is not None:
            return
        path = self._path(name)
        if not path.exists():
            raise KeyError(name)
        path.unlink()

    def rename(self, old: str, new: str) -> None:
        if self.exists(new):
            raise StoreError(f"{new!r} already exists")
        text = self.text(old)
        self.save(new, text, ephemeral=self.is_ephemeral(old))
        self.delete(old)


class ProfileStore(YamlStore[Profile]):
    def __init__(self, directory: Path):
        super().__init__(directory, Profile)


class LabStore(YamlStore[Lab]):
    def __init__(self, directory: Path):
        super().__init__(directory, Lab)
