import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("SHORTTALE_ROOT", str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from shorttale.campaign import load_campaign  # noqa: E402


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture
def tailmailer():
    return load_campaign(ROOT / "config" / "campaigns" / "tailmailer.yml")


@pytest.fixture
def demo():
    return load_campaign(ROOT / "config" / "campaigns" / "demo.yml")
