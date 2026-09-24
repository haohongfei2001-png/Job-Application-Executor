from executor.adapters.generic_web import GenericWebAdapter
from executor.forms import FormObservationError
import pytest


class SnapshotPage:
    def __init__(self):
        self.url = "https://example.test/form"
        self.evaluate_calls = 0

    def evaluate(self, _script):
        self.evaluate_calls += 1
        return [
            {
                "fieldId": "name",
                "selector": "#name",
                "label": "姓名",
                "inputType": "text",
                "required": True,
                "options": [],
                "current": "Example User",
                "index": 0,
                "tag": "input",
                "section": "个人信息",
                "selectedText": "",
            },
            {
                "fieldId": "city",
                "selector": "#city",
                "label": "期望城市",
                "inputType": "select",
                "required": False,
                "options": ["上海市", "北京市"],
                "current": "3100",
                "index": 1,
                "tag": "select",
                "section": "个人信息",
                "selectedText": "上海市",
            },
        ]


def test_discover_fields_uses_single_dom_snapshot():
    adapter = GenericWebAdapter("https://example.test/form")
    page = SnapshotPage()
    adapter.page = page

    fields = adapter.discover_fields()

    assert page.evaluate_calls == 1
    assert [field.field_id for field in fields] == ["name", "city"]
    assert fields[0].current_value == "Example User"
    assert fields[1].options == ["上海市", "北京市"]
    assert fields[1].metadata["selected_text"] == "上海市"


def test_failed_observation_is_not_reported_as_an_empty_form():
    adapter = GenericWebAdapter("https://example.test/form")

    class BrokenPage:
        def evaluate(self, _script):
            raise RuntimeError("synthetic private page detail")

    adapter.page = BrokenPage()
    with pytest.raises(FormObservationError, match="observation unavailable") as caught:
        adapter.discover_fields()
    assert "private page detail" not in str(caught.value)
