import unittest
from unittest.mock import Mock, patch

from webapp.npc_item_exchange import (
    adjudicate_item_exchange_with_dm_llm,
    container_interaction_range_ft,
    container_item_qty,
    find_container_with_item,
    format_container_contents,
    format_container_list_summary,
    give_from_container_to_entity,
    nearby_ground_items,
    open_item_transfer_dialog,
    pickup_ground_item,
    pop_npc_gift_transfer_session,
    register_npc_gift_transfer_session,
    retrieve_from_container,
    store_in_container,
    summarize_player_gift_transfer,
    _PENDING_NPC_GIFT_TRANSFERS,
)


class TestNpcItemExchange(unittest.TestCase):
    def setUp(self):
        _PENDING_NPC_GIFT_TRANSFERS.clear()

    def test_summarize_player_gift_transfer_formats_items(self):
        summary = summarize_player_gift_transfer({
            'from': {'items': ['healing_potion', 'gold_coin'], 'qty': ['2', '5']},
            'to': {'items': [], 'qty': []},
        })
        self.assertIn('healing potion x2', summary)
        self.assertIn('gold coin x5', summary)

    def test_register_and_pop_npc_gift_transfer_session(self):
        session_id = register_npc_gift_transfer_session('npc-1', 'pc-1', item_hint='letter')
        session = pop_npc_gift_transfer_session(session_id)
        self.assertEqual(session['npc_uid'], 'npc-1')
        self.assertEqual(session['player_uid'], 'pc-1')
        self.assertEqual(session['item_hint'], 'letter')
        self.assertIsNone(pop_npc_gift_transfer_session(session_id))

    def test_has_active_npc_gift_transfer(self):
        from webapp.npc_item_exchange import has_active_npc_gift_transfer

        session_id = register_npc_gift_transfer_session('npc-1', 'pc-1')
        self.assertTrue(has_active_npc_gift_transfer('npc-1', 'pc-1'))
        self.assertFalse(has_active_npc_gift_transfer('npc-2', 'pc-1'))
        pop_npc_gift_transfer_session(session_id)
        self.assertFalse(has_active_npc_gift_transfer('npc-1', 'pc-1'))

    def test_open_item_transfer_dialog_includes_transfer_session_id(self):
        operator = Mock(entity_uid='pc-1')
        counterparty = Mock(entity_uid='npc-1')
        current_game = Mock()
        current_game.entity_owners.return_value = ['player1']
        current_game.username_to_sid = {'player1': ['sid-1']}
        current_game.socketio = Mock()

        delivered = open_item_transfer_dialog(
            current_game,
            operator_entity=operator,
            counterparty_entity=counterparty,
            action_type='give',
            prompt='Give items',
            transfer_session_id='session-abc',
        )

        self.assertTrue(delivered)
        emit_args = current_game.socketio.emit.call_args[0]
        self.assertEqual(
            emit_args[1]['message']['transfer_session_id'],
            'session-abc',
        )

    def test_pickup_ground_item_transfers_from_ground(self):
        actor = Mock()
        actor.label.return_value = 'Goblin'
        ground = Mock()
        ground.inventory = {'gold_coin': {'qty': 3}}
        ground.transfer = Mock()

        current_game = Mock()
        current_game.get_map_for_entity.return_value = Mock(
            items_on_the_ground=lambda ent: [(ground, ['gold_coin'])],
        )
        current_game.get_current_battle.return_value = None
        current_game.socketio = Mock()

        ok, message = pickup_ground_item(current_game, actor, 'gold_coin', qty=2)

        self.assertTrue(ok)
        ground.transfer.assert_called_once()
        self.assertIn('gold coin', message.lower())

    def test_open_item_transfer_dialog_targets_controller(self):
        operator = Mock(entity_uid='pc-1')
        counterparty = Mock(entity_uid='npc-1')
        current_game = Mock()
        current_game.entity_owners.return_value = ['player1']
        current_game.username_to_sid = {'player1': ['sid-1']}
        current_game.socketio = Mock()

        delivered = open_item_transfer_dialog(
            current_game,
            operator_entity=operator,
            counterparty_entity=counterparty,
            action_type='loot',
            prompt='Trade now',
        )

        self.assertTrue(delivered)
        current_game.socketio.emit.assert_called()

    def test_dm_adjudication_request_builds_transfer(self):
        actor = Mock()
        actor.label.return_value = 'Merchant'
        target = Mock()
        target.label.return_value = 'Guard'
        target.transfer = Mock()
        llm = Mock()
        llm.send_message.return_value = (
            '{"accepted": true, "reason": "The guard hands over the potion.", '
            '"transfer": {"from": {"items": ["healing_potion"], "qty": ["1"]}, "to": {"items": [], "qty": []}}}'
        )

        verdict = adjudicate_item_exchange_with_dm_llm(
            llm,
            actor,
            target,
            intent='request',
            item_slug='healing_potion',
        )

        self.assertTrue(verdict['accepted'])
        self.assertEqual(verdict['transfer']['from']['items'], ['healing_potion'])

    def test_retrieve_from_container_transfers_to_actor(self):
        actor = Mock()
        actor.label.return_value = 'Mara'
        container = Mock()
        container.inventory = {'ale_mug': {'qty': 5}}
        container.transfer = Mock()
        container.locked = Mock(return_value=False)
        container.opened = Mock(return_value=True)

        current_game = Mock()
        battle_map = Mock()
        battle_map.distance.return_value = 1
        battle_map.feet_per_grid = 5
        battle_map.can_see.return_value = True
        current_game.get_map_for_entity.return_value = battle_map
        current_game.get_current_battle.return_value = None
        current_game.socketio = Mock()

        ok, message = retrieve_from_container(current_game, actor, container, 'ale_mug', qty=2)

        self.assertTrue(ok)
        container.transfer.assert_called_once()
        self.assertIn('ale mug', message.lower())

    def test_store_in_container_moves_from_actor(self):
        actor = Mock()
        actor.inventory = {'bread_loaf': {'qty': 3}}
        container = Mock()
        container.inventory = {}
        container.transfer = Mock()
        container.locked = Mock(return_value=False)
        container.opened = Mock(return_value=True)

        current_game = Mock()
        battle_map = Mock()
        battle_map.distance.return_value = 1
        battle_map.feet_per_grid = 5
        battle_map.can_see.return_value = True
        current_game.get_map_for_entity.return_value = battle_map
        current_game.get_current_battle.return_value = None
        current_game.socketio = Mock()

        ok, message = store_in_container(current_game, actor, container, 'bread_loaf', qty=2)

        self.assertTrue(ok)
        container.transfer.assert_called_once()
        self.assertIn('bread loaf', message.lower())

    def test_give_from_container_to_entity_transfers_directly(self):
        recipient = Mock()
        container = Mock()
        container.inventory = {'ale_mug': {'qty': 4}}
        container.transfer = Mock()

        current_game = Mock()
        current_game.get_current_battle.return_value = None
        current_game.socketio = Mock()

        ok, message = give_from_container_to_entity(
            current_game,
            container,
            recipient,
            'ale_mug',
            qty=1,
        )

        self.assertTrue(ok)
        container.transfer.assert_called_once()
        self.assertIn('ale mug', message.lower())

    def test_find_container_with_item_prefers_stocked_container(self):
        actor = Mock()
        empty = Mock()
        empty.inventory = {}
        empty.transfer = Mock()
        empty.locked = Mock(return_value=False)
        empty.opened = Mock(return_value=True)

        stocked = Mock()
        stocked.inventory = {'ale_mug': {'qty': 2}}
        stocked.transfer = Mock()
        stocked.locked = Mock(return_value=False)
        stocked.opened = Mock(return_value=True)

        current_game = Mock()
        battle_map = Mock()
        battle_map.interactable_objects = {empty: True, stocked: True}
        battle_map.distance.return_value = 1
        battle_map.feet_per_grid = 5
        battle_map.can_see.return_value = True
        current_game.get_map_for_entity.return_value = battle_map

        found = find_container_with_item(current_game, actor, 'ale_mug')
        self.assertIs(found, stocked)

    def test_find_container_with_item_uses_staff_extended_range(self):
        actor = Mock()
        actor.entity_uid = 'pip_barmaid'

        stocked = Mock()
        stocked.inventory = {'ale_mug': {'qty': 2}}
        stocked.transfer = Mock()
        stocked.locked = Mock(return_value=False)
        stocked.opened = Mock(return_value=True)
        stocked.properties = {'staff_viewers': ['pip_barmaid']}

        current_game = Mock()
        battle_map = Mock()
        battle_map.interactable_objects = {stocked: True}
        battle_map.distance.return_value = 4
        battle_map.feet_per_grid = 5
        battle_map.can_see.return_value = True
        current_game.get_map_for_entity.return_value = battle_map

        found = find_container_with_item(current_game, actor, 'ale_mug')
        self.assertIs(found, stocked)

    def test_nearby_map_containers_supports_entities_uid_map(self):
        from natural20.session import Session
        from natural20.uid_containers import EntitiesUIDMap
        from webapp.npc_item_exchange import nearby_map_containers

        session = Session('user_levels/wild_sheep_chase')
        pip = session.entity_by_uid('pip_barmaid')
        bar = session.entity_by_uid('tavern_bar')
        battle_map = session.maps['town_market']

        current_game = Mock()
        current_game.get_map_for_entity.return_value = battle_map

        containers = nearby_map_containers(current_game, pip, range_ft=30)
        self.assertIn(bar, containers)

        found = find_container_with_item(current_game, pip, 'bread_loaf')
        self.assertIs(found, bar)

    def test_container_interaction_range_ft_staff_viewers(self):
        actor = Mock()
        actor.entity_uid = 'pip_barmaid'
        container = Mock()
        container.properties = {'staff_viewers': ['pip_barmaid', 'mara_bartender']}
        self.assertEqual(container_interaction_range_ft(actor, container), 30)
        actor.entity_uid = 'stranger'
        self.assertEqual(container_interaction_range_ft(actor, container), 15)

    def test_format_container_list_summary(self):
        actor = Mock()
        container = Mock()
        container.inventory = {'ale_mug': {'qty': 3}}
        container.entity_uid = 'tavern_bar'
        container.label.return_value = 'Bar Counter'
        container.transfer = Mock()
        container.locked = Mock(return_value=False)
        container.opened = Mock(return_value=True)

        current_game = Mock()
        battle_map = Mock()
        battle_map.distance.return_value = 1
        battle_map.feet_per_grid = 5
        battle_map.can_see.return_value = True
        current_game.get_map_for_entity.return_value = battle_map

        summary = format_container_list_summary(current_game, actor, container=container)
        self.assertIn('[LIST_CONTAINER]', summary)
        self.assertIn('ale mug x3', summary)

    def test_format_container_list_summary_locked(self):
        actor = Mock()
        container = Mock()
        container.inventory = {'gold_piece': {'qty': 40}}
        container.entity_uid = 'tavern_safe'
        container.label.return_value = 'Tavern Till Safe'
        container.transfer = Mock()
        container.locked = Mock(return_value=True)
        container.opened = Mock(return_value=False)

        current_game = Mock()
        battle_map = Mock()
        battle_map.distance.return_value = 1
        battle_map.feet_per_grid = 5
        battle_map.can_see.return_value = True
        current_game.get_map_for_entity.return_value = battle_map

        summary = format_container_list_summary(current_game, actor, container=container)
        self.assertIn('locked', summary.lower())

    def test_format_container_contents(self):
        container = Mock()
        container.inventory = {'bread_loaf': {'qty': 2}, 'ale_mug': {'qty': 0}}
        self.assertEqual(format_container_contents(container), 'bread loaf x2')
        self.assertEqual(container_item_qty(container, 'bread_loaf'), 2)


if __name__ == '__main__':
    unittest.main()
