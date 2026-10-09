from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def load_fixture():
    def _load(name: str) -> Any:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture(scope="session")
def load_page() -> str:
    return (FIXTURES / "page.html").read_text(encoding="utf-8")
