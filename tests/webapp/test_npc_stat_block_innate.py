"""NPC stat block: innate spellcasting groups and recharge labels."""
from natural20.event_manager import EventManager
from natural20.npc import Npc
from natural20.session import Session
from webapp.blueprints.helpers.npc_stat_block import build_npc_stat_block, format_npc_action


def test_innate_spellcasting_groups_and_uses_left():
    session = Session(root_path='templates', event_manager=EventManager())
    npc = Npc(session, 'goblin', {'name': 'caster', 'overrides': {'innate_spellcasting': {
        'ability': 'charisma', 'dc': 13, 'attack': 5, 'no_material': True,
        'at_will': ['mage_hand'], 'per_day': {2: ['misty_step', 'web']},
    }}})
    npc.resources['innate:web'].current = 1
    block = build_npc_stat_block(npc)['spellcasting']
    assert block['innate'] and block['no_material']
    assert block['save_dc'] == 13 and block['attack_bonus_signed'] == '+5'
    labels = [group['label'] for group in block['lists']]
    assert labels == ['At will', '2/day each']
    assert any('1 left' in name for name in block['lists'][1]['spells'])


def test_recharge_is_shown_in_the_action_name():
    entry = format_npc_action({'name': 'fire_breath', 'type': 'area_save', 'recharge': '5-6',
                               'description': 'Exhales fire.'})
    assert entry['name'].endswith('(Recharge 5-6)')
    assert entry['text'] == 'Exhales fire.'
