"""Log a failing download once, and its recovery once."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logging import Logger


class FailureLog:
    """
    Warn at the first failure of a download and when it works again.

    The failures in between are not logged.
    """

    def __init__(self, logger: Logger, name: str) -> None:
        """Initialize; name says what is downloaded."""
        self._logger = logger
        self._name = name
        self._failing = False

    def failed(self, reason: object, exc_info: BaseException | None = None) -> None:
        """Log the reason, unless the download was failing already."""
        if not self._failing:
            self._failing = True
            self._logger.warning(
                "%s failed: %s", self._name, reason, exc_info=exc_info
            )

    def succeeded(self) -> None:
        """Log the recovery of a download that was failing."""
        if self._failing:
            self._failing = False
            self._logger.warning("%s works again", self._name)
