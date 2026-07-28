import os
import sys
import unittest
from unittest.mock import Mock, patch

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

from webapp.app import app  # noqa: E402
from webapp.merchant_service import parse_llm_pricing_response, resolve_merchant_discount  # noqa: E402
from natural20.player_character import PlayerCharacter  # noqa: E402


class TestMerchantService(unittest.TestCase):
    def test_parse_llm_pricing_response_json(self):
        discount, reason = parse_llm_pricing_response(
            '{"discount_percent": 12.5, "reason": "You saved my stall once."}'
        )
        self.assertEqual(discount, 12.5)
        self.assertIn('stall', reason)

    def test_resolve_merchant_discount_disabled(self):
        merchant = Mock(properties={'merchant': {'llm_pricing': False, 'wares': []}})
        customer = Mock(entity_uid='pc-1')
        customer.label.return_value = 'Hero'
        result = resolve_merchant_discount(merchant, customer, memory_store=None, llm_handler=None)
        self.assertEqual(result['source'], 'disabled')
        self.assertEqual(result['discount_percent'], 0.0)

    def test_resolve_merchant_discount_from_llm(self):
        merchant = Mock(properties={'merchant': {'llm_pricing': True, 'wares': []}})
        merchant.label.return_value = 'Bram'
        merchant.entity_uid = 'bram-1'
        customer = Mock(entity_uid='pc-1')
        customer.label.return_value = 'Hero'

        provider = Mock()
        provider.send_message.return_value = '{"discount_percent": 15, "reason": "Welcome back."}'
        llm_handler = Mock(npc_provider=provider)

        result = resolve_merchant_discount(
            merchant,
            customer,
            memory_store=Mock(list_summaries=lambda *_a, **_k: []),
            llm_handler=llm_handler,
        )
        self.assertEqual(result['discount_percent'], 15.0)
        self.assertEqual(result['source'], 'llm')


class TestMerchantRoutes(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.merchant = Mock()
        self.merchant.entity_uid = 'merchant-1'
        self.merchant.label.return_value = 'Bram'
        self.merchant.properties = {
            'merchant': {
                'shop_name': "Bram's Armory",
                'wares': [{'type': 'dagger', 'qty': 2, 'price': 2}],
            },
            'merchant_stock': {'dagger': 2},
        }
        self.merchant.inventory = {}
        self.merchant.add_item = Mock()

        self.buyer = Mock()
        self.buyer.entity_uid = 'buyer-1'
        self.buyer.label.return_value = 'Hero'
        self.buyer.inventory = {'gold_piece': {'qty': 10}}
        self.buyer.inventory_items.return_value = [
            {'name': 'gold_piece', 'label': 'Gold Piece', 'qty': 10, 'image': 'gold_piece'},
        ]
        self.buyer.deduct_item = Mock(side_effect=self._deduct)
        self.buyer.add_item = Mock()

    def _deduct(self, item, qty):
        self.buyer.inventory[item]['qty'] -= qty

    @patch('webapp.blueprints.merchant.get_socketio')
    @patch('webapp.blueprints.merchant.get_game_session')
    @patch('webapp.blueprints.merchant.resolve_merchant_discount')
    @patch('webapp.blueprints.merchant.get_current_game')
    @patch('webapp.blueprints.merchant._buyer_allowed', return_value=True)
    def test_merchant_trade_route(self, allowed_mock, game_mock, pricing_mock, session_mock, socketio_mock):
        pricing_mock.return_value = {'discount_percent': 0, 'reason': ''}
        game = Mock()
        game.get_entity_by_uid.side_effect = lambda uid: {
            'merchant-1': self.merchant,
            'buyer-1': self.buyer,
        }[uid]
        game_mock.return_value = game
        session_mock.return_value = Mock(load_thing=lambda slug: {
            'dagger': {'name': 'Dagger', 'cost': 2, 'type': 'melee_attack', 'subtype': 'weapon'},
            'gold_piece': {'name': 'Gold Piece', 'cost': 1, 'type': 'currency'},
        }.get(slug))

        with self.client.session_transaction() as sess:
            sess['username'] = 'player1'

        response = self.client.post(
                '/merchant/trade',
                json={
                    'merchant_uid': 'merchant-1',
                    'buyer_uid': 'buyer-1',
                    'purchase': {'items': ['dagger'], 'qty': ['1']},
                    'payment': {'items': ['gold_piece'], 'qty': ['2']},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.buyer.add_item.assert_any_call('dagger', 1)
        socketio_mock.return_value.emit.assert_called()

    @patch('webapp.blueprints.merchant.get_game_session')
    @patch('webapp.blueprints.merchant.resolve_merchant_discount')
    @patch('webapp.blueprints.merchant.get_current_game')
    @patch('webapp.blueprints.merchant.entities_controlled_by')
    def test_merchant_ui_route(self, controlled_mock, game_mock, pricing_mock, session_mock):
        pricing_mock.return_value = {'discount_percent': 5, 'reason': 'Loyal customer.'}
        self.buyer = Mock(spec=PlayerCharacter)
        self.buyer.entity_uid = 'buyer-1'
        self.buyer.label.return_value = 'Hero'
        self.buyer.inventory = {'gold_piece': {'qty': 10}}
        self.buyer.inventory_items.return_value = [
            {'name': 'gold_piece', 'label': 'Gold Piece', 'qty': 10, 'image': 'gold_piece'},
        ]
        controlled_mock.return_value = [self.buyer]
        game = Mock()
        game.get_entity_by_uid.side_effect = lambda uid: {
            'merchant-1': self.merchant,
            'buyer-1': self.buyer,
        }[uid]
        game.npc_memory_store = None
        game_mock.return_value = game
        session_mock.return_value = Mock(load_thing=lambda slug: {
            'dagger': {'name': 'Dagger', 'cost': 2, 'type': 'melee_attack', 'subtype': 'weapon'},
            'gold_piece': {'name': 'Gold Piece', 'cost': 1, 'type': 'currency'},
        }.get(slug))

        with self.client.session_transaction() as sess:
            sess['username'] = 'player1'

        response = self.client.get(
            '/merchant?merchant_uid=merchant-1&buyer_uid=buyer-1',
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('Bram', body)
        self.assertIn('Armory', body)
        self.assertIn('merchant-trade-panel', body)


    def test_open_merchant_shop_dialog_emits_socket_event(self):
        merchant = Mock(entity_uid='merchant-1')
        merchant.label.return_value = 'Bram'
        merchant.properties = {'merchant': {'wares': [{'type': 'dagger', 'qty': 1}]}}
        buyer = Mock(entity_uid='buyer-1')
        current_game = Mock()
        current_game.entity_owners.return_value = ['player1']
        current_game.username_to_sid = {'player1': ['sid-1']}
        current_game.socketio = Mock()

        from webapp.merchant_service import open_merchant_shop_dialog

        delivered = open_merchant_shop_dialog(
            current_game,
            merchant=merchant,
            buyer=buyer,
            prompt='Welcome to my shop.',
        )

        self.assertTrue(delivered)
        emit_args = current_game.socketio.emit.call_args[0]
        self.assertEqual(emit_args[1]['type'], 'open_merchant_shop')
        self.assertEqual(emit_args[1]['message']['merchant_uid'], 'merchant-1')


if __name__ == '__main__':
    unittest.main()
