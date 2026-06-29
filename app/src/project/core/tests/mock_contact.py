from collections import defaultdict
from typing import Any

from project.core.contact import AbstractSubtensorContact


class Behave:
    """
    A reusable behavior mocker that 'behaves' in the configured way when called.
    It can be used to create mock implementations from abstract classes for testing.
    The behavior can be verified through recorded calls.

    Example usage:
        class MockConcreteClass(AbstractClass):
            def __init__(self):
                self.behave = Behave()

            def method_to_mock(self, arg1, arg2):
                self.behave.track("method_to_mock", arg1, arg2)
                return self.behave.execute("method_to_mock", arg1, arg2)

        # In the test:
        mock_instance = MockConcreteClass()
        mock_instance.behave.add_behavior("method_to_mock", 1)
        mock_instance.behave.add_behavior("method_to_mock", Exception("Error"))

        assert mock_instance.method_to_mock("A", "B") == 1
        with pytest.raises(Exception, match="Error"):
            mock_instance.method_to_mock("C", "D")

        assert mock_instance.behave.calls["method_to_mock"] == [("A", "B"), ("C", "D")]
    """

    def __init__(self) -> None:
        self._behaviors: dict[str, list] = defaultdict(list)
        self.calls: dict[str, list] = defaultdict(list)

    def execute(self, method_name: str, *args, **kwargs) -> Any:
        if not self._behaviors[method_name]:
            raise NotImplementedError(
                f"No mock behavior configured for {method_name}. Use add_behavior() to configure it."
            )
        behavior = self._behaviors[method_name].pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        if callable(behavior):
            return behavior(*args, **kwargs)
        return behavior

    def track(self, method_name: str, *args, **kwargs) -> None:
        if kwargs:
            self.calls[method_name].append((args, kwargs))
        else:
            self.calls[method_name].append(args)

    def add_behavior(self, method_name: str, behavior: Any) -> None:
        self._behaviors[method_name].append(behavior)

    def reset(self) -> None:
        self.calls.clear()
        self._behaviors.clear()


class MockSubtensorContact(AbstractSubtensorContact):
    def __init__(self) -> None:
        self._behave = Behave()

    @property
    def calls(self) -> dict[str, list]:
        return self._behave.calls

    def add_behavior(self, method_name: str, behavior: Any) -> None:
        self._behave.add_behavior(method_name, behavior)

    def reset(self) -> None:
        self._behave.reset()

    def get_validator_hotkeys(self, netuid: int) -> list[str]:
        self._behave.track("get_validator_hotkeys", netuid)
        return self._behave.execute("get_validator_hotkeys", netuid)
