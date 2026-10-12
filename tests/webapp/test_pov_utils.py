"""Tests for DM NPC POV focus helpers."""

import os
import sys
from unittest.mock import Mock, patch

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from natural20.player_character import PlayerCharacter
from webapp.pov_utils import (
    build_portrait_render_context,
    dm_pov_portrait_entities,
    dm_pov_portrait_groups,
    sidekick_follow_state,
    user_may_set_pov_entity,
)


def test_dm_pov_portrait_entities_includes_pcs_and_map_npcs():
    pc = Mock(spec=PlayerCharacter)
    pc.entity_uid = 'pc-1'
    npc = Mock()
    npc.entity_uid = 'mara-1'
    npc.is_npc = Mock(return_value=True)

    other_npc = Mock()
    other_npc.entity_uid = 'pip-1'
    other_npc.is_npc = Mock(return_value=True)

    tavern = Mock()
    tavern.entities = [pc, npc]
    upstairs = Mock()
    upstairs.entities = [other_npc]

    current_game = Mock()
    current_game.maps = {'tavern': tavern, 'upstairs': upstairs}

    portraits = dm_pov_portrait_entities(current_game, tavern)
    uids = {entity.entity_uid for entity in portraits}
    assert uids == {'pc-1', 'mara-1'}


def test_dm_pov_portrait_groups_splits_party_and_npcs():
    pc = Mock(spec=PlayerCharacter)
    pc.entity_uid = 'pc-1'
    npc = Mock()
    npc.entity_uid = 'mara-1'
    npc.is_npc = Mock(return_value=True)
    npc.name = 'Mara'
    npc.label = Mock(return_value='Mara')

    tavern = Mock()
    tavern.entities = [pc, npc]

    current_game = Mock()
    current_game.maps = {'tavern': tavern}

    groups = dm_pov_portrait_groups(current_game, tavern)
    assert [e.entity_uid for e in groups['party']] == ['pc-1']
    assert [e.entity_uid for e in groups['npcs']] == ['mara-1']


def test_user_may_set_pov_entity_dm_vs_player():
    current_game = Mock()
    entity = Mock(entity_uid='mara-1')
    battle_map = Mock()

    assert user_may_set_pov_entity(current_game, 'dm', ['dm'], entity, battle_map=battle_map)

    current_game.get_map_for_user.return_value = battle_map
    with patch(
        'webapp.blueprints.helpers.template_globals.entities_controlled_by',
        return_value=[],
    ):
        assert not user_may_set_pov_entity(current_game, 'player', ['player'], entity, battle_map=battle_map)


def test_user_may_set_pov_entity_player_controlled_across_maps():
    current_game = Mock()
    entity = Mock(entity_uid='pc-2')
    battle_map = Mock()

    with patch(
        'webapp.blueprints.helpers.template_globals.entities_controlled_by',
        return_value=[entity],
    ):
        assert user_may_set_pov_entity(current_game, 'player', ['player'], entity, battle_map=battle_map)


def test_user_may_set_pov_entity_player_cannot_switch_uncontrolled_entity():
    current_game = Mock()
    controlled_entity = Mock(entity_uid='pc-1')
    target_entity = Mock(entity_uid='npc-7')

    with patch(
        'webapp.blueprints.helpers.template_globals.entities_controlled_by',
        return_value=[controlled_entity],
    ):
        assert not user_may_set_pov_entity(current_game, 'player', ['player'], target_entity)


def test_sidekick_follow_state_dm_can_control():
    sidekick = Mock(entity_uid='squire-1')
    session = Mock()
    current_game = Mock()
    current_game.game_session = session

    with patch('natural20.sidekick.iter_in_party_sidekicks', return_value=[sidekick]), \
         patch('natural20.sidekick.sidekick_owners', return_value=['gomerin']), \
         patch('natural20.sidekick.is_auto_follow_sidekick', return_value=True):
        state = sidekick_follow_state(current_game, 'dm', ['dm'])

    assert state == {'squire-1': {'auto_follow': True, 'can_control': True}}


def test_sidekick_follow_state_owner_can_control():
    sidekick = Mock(entity_uid='squire-1')
    session = Mock()
    current_game = Mock()
    current_game.game_session = session

    with patch('natural20.sidekick.iter_in_party_sidekicks', return_value=[sidekick]), \
         patch('natural20.sidekick.sidekick_owners', return_value=['gomerin']), \
         patch('natural20.sidekick.is_auto_follow_sidekick', return_value=False):
        state = sidekick_follow_state(current_game, 'Gomerin', ['player'])

    assert state == {'squire-1': {'auto_follow': False, 'can_control': True}}


def test_sidekick_follow_state_non_owner_cannot_control():
    sidekick = Mock(entity_uid='squire-1')
    session = Mock()
    current_game = Mock()
    current_game.game_session = session

    with patch('natural20.sidekick.iter_in_party_sidekicks', return_value=[sidekick]), \
         patch('natural20.sidekick.sidekick_owners', return_value=['gomerin']), \
         patch('natural20.sidekick.is_auto_follow_sidekick', return_value=False):
        state = sidekick_follow_state(current_game, 'stranger', ['player'])

    assert state == {'squire-1': {'auto_follow': False, 'can_control': False}}


def test_sidekick_follow_state_empty_without_session():
    current_game = Mock()
    current_game.game_session = None

    assert sidekick_follow_state(current_game, 'dm', ['dm']) == {}


def test_build_portrait_render_context_includes_sidekick_follow():
    sidekick = Mock(entity_uid='squire-1')
    pc = Mock(spec=PlayerCharacter)
    pc.entity_uid = 'pc-1'
    pc.is_npc = Mock(return_value=False)

    battle_map = Mock()
    battle_map.entities = [pc]

    current_game = Mock()
    current_game.game_session = Mock()
    current_game.get_pov_entity_for_user.return_value = pc
    current_game.get_map_for_user.return_value = battle_map
    current_game.maps = {'tavern': battle_map}

    with patch('webapp.blueprints.helpers.template_globals.entities_controlled_by', return_value=[]), \
         patch('natural20.sidekick.iter_in_party_sidekicks', return_value=[sidekick]), \
         patch('natural20.sidekick.sidekick_owners', return_value=[]), \
         patch('natural20.sidekick.is_auto_follow_sidekick', return_value=True):
        dm_ctx = build_portrait_render_context(current_game, 'dm', ['dm'])
        player_ctx = build_portrait_render_context(current_game, 'gomerin', ['player'])

    assert dm_ctx['sidekick_follow'] == {'squire-1': {'auto_follow': True, 'can_control': True}}
    assert player_ctx['sidekick_follow'] == {'squire-1': {'auto_follow': True, 'can_control': False}}
