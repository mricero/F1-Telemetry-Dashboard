"""Display units (UX-12): metric (km/h, degrees C) or imperial (mph, degrees F).

Data stays metric everywhere - FastF1 and the live feed are metric, and so is
the records store. Only what is drawn is converted, at the last moment. The
choice is per viewer: ``st.session_state["units"]``, seeded from the URL
(``?units=imperial``) so a shared link keeps it, and set on the Settings page.
"""

from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger(__name__)

UNITS_KEY = "units"
METRIC = "metric"
IMPERIAL = "imperial"
SYSTEMS = (METRIC, IMPERIAL)
KMH_PER_MPH = 1.609344


def preference() -> str:
    """The viewer's unit system; metric outside a script run or when unset."""
    try:
        chosen = st.session_state.get(UNITS_KEY) or st.query_params.get(UNITS_KEY)
    except Exception as exc:  # no script-run context (unit tests, bare mode): metric
        logger.debug("No viewer preference for units: %s", exc)
        return METRIC
    return chosen if chosen in SYSTEMS else METRIC


def speed_unit(system: str | None = None) -> str:
    return "mph" if (system or preference()) == IMPERIAL else "km/h"


def speed(kmh, system: str | None = None):
    """A km/h value (scalar, array or Series) in the viewer's unit."""
    if kmh is None:
        return None
    return kmh / KMH_PER_MPH if (system or preference()) == IMPERIAL else kmh


def temperature_unit(system: str | None = None) -> str:
    return "°F" if (system or preference()) == IMPERIAL else "°C"


def temperature(celsius, system: str | None = None):
    """A degrees-C value (scalar, array or Series) in the viewer's unit."""
    if celsius is None:
        return None
    return celsius * 9.0 / 5.0 + 32.0 if (system or preference()) == IMPERIAL else celsius


def set_preference(system: str) -> None:
    """Store the choice and put it in the URL, so a shared link keeps it."""
    system = system if system in SYSTEMS else METRIC
    st.session_state[UNITS_KEY] = system
    if system == METRIC:
        st.query_params.pop(UNITS_KEY, None)
    else:
        st.query_params[UNITS_KEY] = system
