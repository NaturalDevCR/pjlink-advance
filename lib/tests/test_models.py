"""Tests for pjlink_advance.models."""

import pytest

from pjlink_advance.models import (
    ErrorLevel,
    ErrorStatus,
    InputSource,
    InputType,
    Resolution,
)


def test_input_source_from_code_class1() -> None:
    src = InputSource.from_code("31")
    assert src == InputSource(InputType.DIGITAL, "1")
    assert src.code == "31"
    assert str(src) == "DIGITAL 1"


def test_input_source_from_code_class2_extended() -> None:
    src = InputSource.from_code("3A")
    assert src.type is InputType.DIGITAL
    assert src.index == "A"
    assert src.code == "3A"


def test_input_source_internal() -> None:
    assert InputSource.from_code("61").type is InputType.INTERNAL


@pytest.mark.parametrize("code", ["", "3", "71", "30", "3a", "311", "x1"])
def test_input_source_invalid(code: str) -> None:
    with pytest.raises(ValueError):
        InputSource.from_code(code)


def test_input_source_requires_class2() -> None:
    assert not InputSource.from_code("31").requires_class2
    assert InputSource.from_code("3A").requires_class2
    assert InputSource.from_code("61").requires_class2


def test_error_status_has_problem() -> None:
    assert not ErrorStatus.ok().has_problem
    status = ErrorStatus.ok().replace(filter=ErrorLevel.WARNING)
    assert status.has_problem
    assert status.filter is ErrorLevel.WARNING


def test_resolution_str() -> None:
    assert str(Resolution(1920, 1200)) == "1920x1200"
