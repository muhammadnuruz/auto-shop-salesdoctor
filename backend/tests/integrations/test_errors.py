import pytest

from app.integrations.salesdoctor.errors import (
    SDAuthError,
    SDError,
    SDMethodError,
    SDTimeWindowError,
    SDValidationError,
    raise_for_payload,
)

VALIDATION = {
    "status": False,
    "result": {"completed": 0, "error": 1, "data": {}},
    "error": {
        "code": 400,
        "message": "Error",
        "data": {
            "order": [
                {
                    "SD_id": "",
                    "code_1C": "WEB-1",
                    "name": None,
                    "index": 0,
                    "errors": ["Agent is not found", "Client is not found"],
                }
            ]
        },
    },
}


def test_success_passes() -> None:
    raise_for_payload("x", {"status": True, "result": []})


def test_validation_rows_parsed() -> None:
    with pytest.raises(SDValidationError) as ei:
        raise_for_payload("setOrder", VALIDATION)
    (row,) = ei.value.items
    assert row.entity == "order"
    assert row.index == 0
    assert row.ids["code_1C"] == "WEB-1"
    assert row.messages == ["Agent is not found", "Client is not found"]
    assert "Agent is not found" in str(ei.value)


def test_time_window_string_error() -> None:
    payload = {"status": False, "error": "this request allowed time is from 20:00 to 08:00"}
    with pytest.raises(SDTimeWindowError):
        raise_for_payload("getStockForDate", payload)


def test_other_string_error_is_base() -> None:
    with pytest.raises(SDError) as ei:
        raise_for_payload("m", {"status": False, "error": "boom"})
    assert type(ei.value) is SDError


def test_402_and_401() -> None:
    with pytest.raises(SDMethodError):
        raise_for_payload(
            "m", {"status": False, "error": {"code": 402, "message": "Invalid method"}}
        )
    with pytest.raises(SDAuthError):
        raise_for_payload(
            "m", {"status": False, "error": {"code": 401, "message": "Invalid token"}}
        )


def test_missing_error_object() -> None:
    with pytest.raises(SDError):
        raise_for_payload("m", {"status": False})
