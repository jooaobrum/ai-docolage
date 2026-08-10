from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def tmp_output_volume(tmp_path) -> Path:
    output_dir = tmp_path / "output-volume"
    output_dir.mkdir()
    return output_dir
