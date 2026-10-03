from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    path: Path
    data: dict[str, Any]

    def section(self, name: str) -> dict[str, Any]:
        value = self.data.get(name)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ConfigError(f"config section [{name}] must be a table")
        return value

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.section(section).get(key, default)

    @property
    def queries(self) -> list[dict[str, Any]]:
        return list(self.section("queries").get("items", []))

    @property
    def selectors(self) -> dict[str, str]:
        return self.section("selectors")

    @property
    def profile(self) -> dict[str, Any]:
        return self.section("profile")

    @property
    def scoring(self) -> dict[str, Any]:
        return self.section("scoring")

    @property
    def threshold(self) -> int:
        return int(self.scoring.get("threshold", 5))

    def path_for(self, key: str, default: str) -> Path:
        raw = self.get("paths", key, default)
        candidate = Path(raw)
        return candidate if candidate.is_absolute() else self.path.parent / candidate


EXAMPLE_NAME = "config.example.toml"


def load(path: str | Path | None = None) -> Config:
    config_path = Path(path) if path else Path(__file__).resolve().parent.parent / "config.toml"
    if not config_path.exists():
        root = config_path.parent
        if (root / EXAMPLE_NAME).exists() and path is None:
            raise ConfigError(
                f"{config_path.name} not found. Copy the template and edit [profile]:\n"
                f"    Copy-Item {EXAMPLE_NAME} config.toml    # PowerShell\n"
                f"    cp {EXAMPLE_NAME} config.toml            # bash"
            )
        raise ConfigError(f"config file not found: {config_path}")
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    return Config(path=config_path.resolve(), data=data)