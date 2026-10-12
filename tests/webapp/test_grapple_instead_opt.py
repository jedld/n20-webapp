from types import SimpleNamespace

from webapp.blueprints.helpers.action_utils import apply_knock_unconscious_opt


def _action(npc_action):
    return SimpleNamespace(npc_action=npc_action, grapple_instead=False, knock_unconscious=False)


def test_grapple_instead_opt_applies_to_npc_attack_with_option():
    action = _action({'name': 'unarmed_strike', 'grapple_instead': {'escape_dc': 18}})
    apply_knock_unconscious_opt(action, {'grapple_instead': True})
    assert action.grapple_instead is True
    assert action.knock_unconscious is False


def test_grapple_instead_opt_ignored_without_yaml_option():
    action = _action({'name': 'claw'})
    apply_knock_unconscious_opt(action, {'grapple_instead': True})
    assert action.grapple_instead is False
