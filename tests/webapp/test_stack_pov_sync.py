"""Stack POV must not relocate player tokens when the viewed floor changes."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def test_sync_stack_pov_to_view_map_is_noop():
  from webapp.blueprints.navigation import _sync_stack_pov_to_view_map

  current_game = MagicMock()
  battle_map = MagicMock()
  pov_entity = SimpleNamespace(entity_uid='pc-1', map=MagicMock(name='town_market'))
  pov_entities = [pov_entity]

  _sync_stack_pov_to_view_map(current_game, battle_map, pov_entities)

  current_game.transfer_entity.assert_not_called()
  current_game.switch_map_for_user.assert_not_called()


def test_commit_and_update_does_not_track_map_switch_for_npc_acting():
  from webapp.utils import GameManagement

  gm = GameManagement.__new__(GameManagement)
  gm.username_to_sid = {'player': ['sid-1']}
  gm.game_state_lock = MagicMock()
  gm.get_pov_entity_for_user = MagicMock(
      return_value=SimpleNamespace(entity_uid='pc-1')
  )
  gm.get_map_for_entity = MagicMock(return_value=MagicMock(name='town_market'))
  gm.switch_map_for_user = MagicMock()

  pov_entities = [SimpleNamespace(entity_uid='pc-1')]
  acting_npc = SimpleNamespace(entity_uid='npc-mara')

  pov_entity = gm._pov_entity_for_commit('player', pov_entities)
  track_map_switch = (
      acting_npc is not None
      and pov_entity is not None
      and getattr(acting_npc, 'entity_uid', None) == getattr(pov_entity, 'entity_uid', None)
  )

  assert pov_entity.entity_uid == 'pc-1'
  assert track_map_switch is False
