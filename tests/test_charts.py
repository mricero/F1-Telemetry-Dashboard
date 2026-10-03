"""Chart construction in ``ui.layout``: the shared style and per-chart settings.

The charts are built by the render functions and handed to
``st.plotly_chart``; the tests capture the figure there instead of drawing it.
"""

import pandas as pd
import plotly.graph_objects as go
import pytest

import f1dash.ui.layout as layout
from f1dash.ui.layout import styled_figure


@pytest.fixture
def drawn(monkeypatch):
    """Figures passed to ``st.plotly_chart``, in order."""
    figures: list[go.Figure] = []
    monkeypatch.setattr(layout.st, "plotly_chart", lambda fig, *a, **k: figures.append(fig))
    return figures


def _laps(drivers=("VER", "HAM", "LEC", "NOR"), laps=5) -> pd.DataFrame:
    rows = []
    for index, code in enumerate(drivers):
        for lap in range(1, laps + 1):
            rows.append(
                {
                    "Driver": code,
                    "LapNumber": lap,
                    "Position": index + 1,
                    "LapTime": pd.Timedelta(90 + index + lap * 0.1, unit="s"),
                    "IsPitOutLap": lap == 3,
                }
            )
    return pd.DataFrame(rows)


class TestSharedStyleGoesUnderTheChart:
    """UI-12: the template no longer overrides a chart's own layout."""

    def test_own_hovermode_and_legend_survive(self):
        fig = go.Figure(go.Scatter(x=[1, 2], y=[1, 2]))
        fig.update_layout(hovermode="closest", legend=dict(orientation="v", x=1.01))

        styled_figure(fig)

        assert fig.layout.hovermode == "closest"
        assert fig.layout.legend.orientation == "v"
        assert fig.layout.legend.x == 1.01

    def test_the_template_fills_what_the_chart_left_unset(self):
        fig = go.Figure(go.Scatter(x=[1], y=[1]))

        styled_figure(fig)

        assert fig.layout.hovermode == "x"
        assert fig.layout.paper_bgcolor == "rgba(0,0,0,0)"
        assert fig.layout.showlegend is False  # one series: no legend

    def test_the_positions_chart_keeps_closest_hover_and_side_legend(self, drawn):
        layout.render_position_changes(_laps(), {})

        fig = drawn[-1]
        assert fig.layout.hovermode == "closest"
        assert fig.layout.legend.orientation == "v"

    def test_uirevision_is_set_when_given(self):
        fig = styled_figure(go.Figure(), uirevision="2024_R_monza")

        assert fig.layout.uirevision == "2024_R_monza"
