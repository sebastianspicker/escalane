"""Domain error hierarchy tests."""

from __future__ import annotations

import pytest

from escalane.config.errors import (
    ConfigurationError,
    ConflictError,
    EscalaneError,
    NotFoundError,
    ValidationError,
)
from tests.support.assertions import expect

pytestmark = [pytest.mark.unit]


class TestEscalaneError:
    def test_basic_message(self):
        err = EscalaneError("something failed")
        expect(str(err) == "something failed")
        expect(err.message == "something failed")
        expect(err.details == {})

    def test_with_details(self):
        err = EscalaneError("oops", details={"key": "val"})
        expect(err.details == {"key": "val"})

    def test_to_dict_no_details(self):
        result = EscalaneError("msg").to_dict()
        expect(result == {"error": "msg"})

    def test_to_dict_with_details(self):
        result = EscalaneError("msg", details={"x": 1}).to_dict()
        expect(result == {"error": "msg", "details": {"x": 1}})


class TestValidationError:
    def test_without_field(self):
        err = ValidationError("bad input")
        expect(err.field is None)
        d = err.to_dict()
        expect(d == {"error": "bad input"})

    def test_with_field(self):
        err = ValidationError("too long", field="title")
        expect(err.field == "title")
        d = err.to_dict()
        expect(d["field"] == "title")
        expect(d["error"] == "too long")

    def test_with_field_and_details(self):
        err = ValidationError("bad", field="name", details={"max": 500})
        d = err.to_dict()
        expect(d["field"] == "name")
        expect(d["details"] == {"max": 500})


class TestNotFoundError:
    def test_without_resource_id(self):
        err = NotFoundError("alarm")
        expect("alarm not found" in str(err))
        expect(err.resource_type == "alarm")
        expect(err.resource_id is None)

    def test_with_resource_id(self):
        err = NotFoundError("alarm", resource_id="abc-123")
        expect("abc-123" in str(err))
        expect(err.resource_id == "abc-123")


# ── ConflictError / ConfigurationError / simple subclasses ────────────


class TestSimpleSubclasses:
    def test_conflict_error(self):
        err = ConflictError("duplicate")
        expect(err.message == "duplicate")

    def test_configuration_error(self):
        err = ConfigurationError("missing env")
        expect(err.message == "missing env")
