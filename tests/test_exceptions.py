"""Tests for the exception hierarchy."""

import pytest

from exceptions import (
    APIError,
    AuthenticationError,
    CoziException,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
    ResourceNotFoundError,
    ValidationError,
)


def test_base_stores_message_and_metadata():
    exc = CoziException("boom", status_code=500, response_data={"err": "x"})
    assert str(exc) == "boom"
    assert exc.status_code == 500
    assert exc.response_data == {"err": "x"}


def test_response_data_defaults_to_empty_dict():
    exc = CoziException("nope")
    assert exc.response_data == {}
    assert exc.status_code is None


def test_does_not_shadow_builtin_permission_error():
    """The renamed PermissionDeniedError must not collide with the builtin."""
    assert PermissionDeniedError is not PermissionError


@pytest.mark.parametrize(
    "subclass",
    [
        AuthenticationError,
        ValidationError,
        RateLimitError,
        APIError,
        NetworkError,
        ResourceNotFoundError,
        PermissionDeniedError,
    ],
)
def test_subclasses_inherit_from_cozi_exception(subclass):
    assert issubclass(subclass, CoziException)


def test_resource_not_found_is_api_error():
    assert issubclass(ResourceNotFoundError, APIError)


def test_permission_denied_is_api_error():
    assert issubclass(PermissionDeniedError, APIError)


def test_subclasses_carry_metadata():
    exc = ResourceNotFoundError("missing", status_code=404, response_data={"id": "x"})
    assert exc.status_code == 404
    assert exc.response_data == {"id": "x"}
    assert isinstance(exc, APIError)
    assert isinstance(exc, CoziException)
