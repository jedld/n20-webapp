"""Tests for map reload path resolution."""

from __future__ import annotations

from types import SimpleNamespace

from webapp.utils import GameManagement


def test_map_file_for_uses_game_yml_maps_when_not_in_other_maps():
    session = SimpleNamespace(
        game_properties={
            "maps": {
                "town_market": "maps/town_market",
                "woodland_path": "maps/woodland_path",
            }
        }
    )
    gm = GameManagement.__new__(GameManagement)
    gm.other_maps = {"woodland_path": "maps/woodland_path"}
    gm.map_location = "maps/town_market"
    gm.game_session = session

    assert gm._map_file_for("town_market") == "maps/town_market"
    assert gm._map_file_for("woodland_path") == "maps/woodland_path"
