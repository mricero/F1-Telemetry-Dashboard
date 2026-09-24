"""Why a panel is empty (IMPROVEMENTS.md UI-06, guideline 5.9).

A panel with nothing to draw says what is missing and why, in the same box
the data would fill, instead of a generic "no data". ``DataStatus`` carries
that reason from where it is known (the session, the live feed) to the
panel that shows it.
"""

from dataclasses import dataclass

import streamlit as st

OK = "ok"
EMPTY = "empty"
UNAVAILABLE = "unavailable"
AUTH_REQUIRED = "auth_required"
ERROR = "error"


@dataclass(frozen=True)
class DataStatus:
    """``ok``, ``empty``, ``unavailable``, ``auth_required`` or ``error``, and the sentence."""

    kind: str
    message: str = ""

    @classmethod
    def ok(cls) -> "DataStatus":
        return cls(OK)

    @classmethod
    def empty(cls, what: str) -> "DataStatus":
        return cls(EMPTY, f"No {what} for this session.")

    @classmethod
    def unavailable(cls, reason: str) -> "DataStatus":
        return cls(UNAVAILABLE, reason.rstrip(".") + ".")

    @classmethod
    def auth_required(cls, what: str) -> "DataStatus":
        return cls(AUTH_REQUIRED, f"{what} need an F1TV subscription token.")

    @classmethod
    def error(cls, message: str) -> "DataStatus":
        return cls(ERROR, message.rstrip(".") + ".")

    @property
    def is_ok(self) -> bool:
        return self.kind == OK


def show(status: DataStatus) -> None:
    """Draw the one line that explains an empty panel."""
    if status.is_ok:
        return
    if status.kind == ERROR:
        st.error(status.message)
    elif status.kind == AUTH_REQUIRED:
        st.warning(status.message)
    else:
        st.info(status.message)


def frame_status(frame, what: str, reason: str | None = None) -> DataStatus:
    """``ok`` for a frame with rows, else why it has none."""
    if frame is not None and not getattr(frame, "empty", True):
        return DataStatus.ok()
    return DataStatus.unavailable(reason) if reason else DataStatus.empty(what)
