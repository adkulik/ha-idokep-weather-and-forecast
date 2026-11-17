"""Unit tests for weather alert parsing."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from custom_components.idokep_weather_and_forecast.api import AlertParser


class TestAlertParser:
    """Test alert parsing functionality."""

    @pytest.mark.parametrize(
        ("text", "level"),
        [
            ("Sárga riasztás ónos esőre", "yellow"),
            ("Sárga figyelmeztetés zivatarra", "yellow"),
            ("Narancs riasztás zivatar", "orange"),
            ("Piros riasztás vihar", "red"),
        ],
    )
    def test_banner_sets_the_warning_level(self, text: str, level: str) -> None:
        """The banner, an alert or a warning, sets the warning level, not alerts."""
        html = f"""
        <div class="background selectiveYellow color-darkblue p-2 mt-2 mb-2 rounded inlineWeatherAlert">
            <i class="fas fa-fw fa-exclamation-triangle"></i> {text}
        </div>
        """
        result = AlertParser().parse(BeautifulSoup(html, "html.parser"))

        assert result["warning_level"] == level
        assert result["alerts"] == []

    def test_parse_hourly_alert(self) -> None:
        """Test parsing hourly forecast alert."""
        html = """
        <div class="ik genericHourlyAlert">
            <a tabindex="0" class="ik d-block w-100 hover-over"
               data-bs-toggle="popover" data-bs-placement="bottom"
               data-bs-content="Sárga riasztás ónos esőre"
               data-bs-original-title="" title="">
                <img class="ik forecast-alert-icon"
                     alt="Sárga riasztás ónos esőre"
                     src="/images/elore3/figyikonok2/onoseso.png"
                     width="40" height="40">
            </a>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        parser = AlertParser()
        result = parser.parse(soup)

        assert result["alerts"] == [{"type": "freezing_rain", "level": "yellow"}]

    def test_parse_multiple_hourly_alerts(self) -> None:
        """Test parsing multiple different hourly alerts."""
        html = """
        <div class="ik genericHourlyAlert">
            <a class="ik d-block w-100 hover-over"
               data-bs-content="Sárga riasztás szélre">
                <img class="ik forecast-alert-icon" src="/images/alerts/wind.png">
            </a>
        </div>
        <div class="ik genericHourlyAlert">
            <a class="ik d-block w-100 hover-over"
               data-bs-content="Narancs riasztás zivatar">
                <img class="ik forecast-alert-icon" src="/images/alerts/thunder.png">
            </a>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        parser = AlertParser()
        result = parser.parse(soup)

        # In idokep.hu's type order: thunderstorm before wind
        assert result["alerts"] == [
            {"type": "thunderstorm", "level": "orange"},
            {"type": "wind", "level": "yellow"},
        ]

    def test_alerts_and_warnings_are_separate(self) -> None:
        """The banner is a warning; the hourly cards' icons are alerts."""
        html = """
        <div class="background selectiveYellow color-darkblue p-2 mt-2 mb-2 rounded inlineWeatherAlert">
            <a href="/radar#riasztas">Sárga riasztás szélre</a>
        </div>
        <div class="ik genericHourlyAlert">
            <a class="ik d-block w-100 hover-over"
               data-bs-content="Narancs riasztás zivatar">
                <img class="ik forecast-alert-icon" src="/images/alerts/thunder.png">
            </a>
        </div>
        <div class="ik genericHourlyAlert">
            <a class="ik d-block w-100 hover-over"
               data-bs-content="Piros riasztás vihar">
                <img class="ik forecast-alert-icon" src="/images/alerts/storm.png">
            </a>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        parser = AlertParser()
        result = parser.parse(soup)

        assert result["alerts"] == [
            {"type": "thunderstorm", "level": "orange"},
            {"type": "wind", "level": "red"},
        ]
        assert result["warnings"] == [{"type": "wind", "level": "yellow"}]
        assert result["warning_level"] == "yellow"

    def test_no_alerts(self) -> None:
        """Test parsing when no alerts are present."""
        html = """
        <div class="some-other-content">
            <p>No alerts today</p>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        parser = AlertParser()
        result = parser.parse(soup)

        assert result["alerts"] == []
        assert result["warnings"] == []

    def test_alert_type_extraction_heat(self) -> None:
        """Test alert type extraction for heat warning."""
        html = """
        <div class="ik genericHourlyAlert">
            <a class="hover-over" data-bs-content="Narancs riasztás hőségre"></a>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        parser = AlertParser()
        result = parser.parse(soup)

        assert result["alerts"] == [{"type": "heat", "level": "orange"}]

    def test_alert_type_extraction_fog(self) -> None:
        """Test alert type extraction for fog warning."""
        html = """
        <div class="ik genericHourlyAlert">
            <a class="hover-over" data-bs-content="Sárga riasztás ködre"></a>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        parser = AlertParser()
        result = parser.parse(soup)

        assert result["alerts"] == [{"type": "fog", "level": "yellow"}]

    def test_duplicate_alerts_removed(self) -> None:
        """Test that duplicate alerts are not added multiple times."""
        html = """
        <div class="ik genericHourlyAlert">
            <a class="ik d-block w-100 hover-over"
               data-bs-content="Sárga riasztás szélre">
                <img class="ik forecast-alert-icon" src="/images/alerts/wind.png">
            </a>
        </div>
        <div class="ik genericHourlyAlert">
            <a class="ik d-block w-100 hover-over"
               data-bs-content="Sárga riasztás szélre">
                <img class="ik forecast-alert-icon" src="/images/alerts/wind.png">
            </a>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        parser = AlertParser()
        result = parser.parse(soup)

        # Should only have 1 alert, not 2
        assert len(result["alerts"]) == 1
