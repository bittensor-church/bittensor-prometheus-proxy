from collections.abc import Generator

import pytest

from project.core.tests.mock_contact import MockSubtensorContact


@pytest.fixture
def some() -> Generator[int, None, None]:
    yield 1


@pytest.fixture
def mock_contact():
    return MockSubtensorContact()
