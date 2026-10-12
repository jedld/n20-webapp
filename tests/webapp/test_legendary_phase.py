"""Manual legendary action phase: GameManagement pause/advance + turn panel."""
import os
import random
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import jinja2

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from natural20.battle import Battle  # noqa: E402
from natural20.event_manager import EventManager  # noqa: E402
from natural20.map import Map  # noqa: E402
from natural20.player_character import PlayerCharacter  # noqa: E402
from natural20.session import Session  # noqa: E402
from natural20.web.web_controller import WebController  # noqa: E402
from webapp.utils import GameManagement  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
FIXTURES = os.path.join(REPO_ROOT, 'tests', 'fixtures')
TEMPLATES = os.path.abspath(os.path.join(WEBAPP_DIR, 'templates'))


def _setup_battle():
    random.seed(1234)
    session = Session(root_path=FIXTURES, event_manager=EventManager())
    battle_map = Map(session, os.path.join(FIXTURES, 'jev_arena.yml'))
    session.register_map('arena', battle_map)
    battle = Battle(session, battle_map)
    pc = PlayerCharacter.load(session, 'high_elf_fighter.yml')
    battle.add(pc, 'a', position=[5, 4], add_to_initiative=True)
    brute = session.npc('legendary_brute')
    battle_map.add(brute, 5, 5)
    battle.add(brute, 'b', controller=WebController(session, 'dm'), add_to_initiative=True)
    battle.start()
    battle.set_current_turn(pc)
    return battle, pc, brute


def _fake_game(battle):
    game = SimpleNamespace(
        socketio=MagicMock(),
        get_current_battle=lambda: battle,
        emit_current_turn_state=MagicMock(),
        end_current_battle=MagicMock(),
        schedule_continue_game_loop=MagicMock(),
    )
    game.begin_legendary_phase = GameManagement.begin_legendary_phase.__get__(game)
    game.advance_legendary_phase = GameManagement.advance_legendary_phase.__get__(game)
    return game


def _emitted_types(game):
    return [c.args[1].get('type') for c in game.socketio.emit.call_args_list]


def test_end_of_turn_pauses_for_manual_legendary_creature():
    battle, pc, brute = _setup_battle()
    game = _fake_game(battle)
    battle.end_turn()

    assert game.begin_legendary_phase(battle) is True
    assert battle.legendary_phase == brute.entity_uid
    assert 'legendary_phase' in _emitted_types(game)
    game.emit_current_turn_state.assert_called()


def test_advance_ends_phase_and_moves_to_next_turn():
    battle, pc, brute = _setup_battle()
    game = _fake_game(battle)
    battle.end_turn()
    game.begin_legendary_phase(battle)
    turn_before = battle.current_turn_index

    game.advance_legendary_phase(battle)

    assert battle.legendary_phase is None
    assert 'legendary_phase_end' in _emitted_types(game)
    assert battle.current_turn_index != turn_before
    game.schedule_continue_game_loop.assert_called_once()


def test_turn_panel_shows_end_legendary_action_button():
    battle, pc, brute = _setup_battle()
    battle.end_turn()
    battle.begin_legendary_phase()
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES))
    template = env.get_template('turn.jinja')

    controller_html = template.render(battle=battle, username='dm', controller_of=lambda uid, user: True)
    assert 'End Legendary Action' in controller_html
    assert 'Legendary Action' in controller_html

    observer_html = template.render(battle=battle, username='player', controller_of=lambda uid, user: False)
    assert 'taking a legendary action' in observer_html
    assert 'End Turn' not in observer_html
