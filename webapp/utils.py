from collections import deque
import time
import os
import yaml
from natural20.map import Map
from natural20.entity import Entity
from natural20.battle import Battle, action_animator
from natural20.generic_controller import GenericController
from natural20.llm_controller import LlmMcpController
from natural20.web.web_controller import WebController, ManualControl
from natural20.player_character import PlayerCharacter
from natural20.actions.attack_action import AttackAction
from natural20.actions.spell_action import SpellAction, spell_action_provokes_hostility
from natural20.actions.shove_action import ShoveAction
from natural20.actions.grapple_action import GrappleAction
import uuid
import pdb
from itertools import combinations
import logging
from mutagen.mp3 import MP3
from natural20.utils.serialization import Serialization
import gzip
import shutil
import threading
import queue
from natural20.action import Action, AsyncReactionHandler, BardicInspirationPrompt


def _coalesce_animation_log(animation_log):
    """Merge consecutive move entries for the same entity into a single walk.

    The combat AI emits one MoveAction per tile (single-step paths from the
    autobuilder), producing many ``[uid, [a, b], action_meta]`` entries in a
    row. The client renders each entry by cloning the entity sprite and
    animating along its path; cloned sprites only get cleaned up at the very
    end of the batch, so consecutive single-tile entries cause stacked ghost
    sprites and a janky stop-and-go animation.

    Coalescing them server-side gives the client one smooth multi-tile walk
    per logical move, which removes the ghosting and reduces the number of
    transitions the browser has to schedule.
    """
    if not animation_log:
        return animation_log

    coalesced = []
    for entry in animation_log:
        # Only merge plain move tuples: [entity_uid, path, action_meta].
        if not (isinstance(entry, list) and len(entry) == 3):
            coalesced.append(entry)
            continue
        uid, path, action_meta = entry
        if not isinstance(path, list) or len(path) < 2:
            coalesced.append(entry)
            continue

        if coalesced and isinstance(coalesced[-1], list) and len(coalesced[-1]) == 3:
            prev_uid, prev_path, prev_meta = coalesced[-1]
            if (prev_uid == uid
                    and isinstance(prev_path, list) and len(prev_path) >= 1
                    and list(prev_path[-1]) == list(path[0])):
                merged_path = list(prev_path) + [list(p) for p in path[1:]]
                # Prefer the latest action metadata (it reflects the final hop)
                coalesced[-1] = [prev_uid, merged_path, action_meta or prev_meta]
                continue
        coalesced.append(entry)
    return coalesced


def build_move_animation_log_entries(action, entity_uid=None, fallback_path=None):
    """Build client move animation tuples from a resolved MoveAction."""
    entries = []
    source = getattr(action, 'source', None)
    uid = entity_uid or getattr(source, 'entity_uid', None)
    if not uid:
        return entries
    for item in list(getattr(action, 'result', None) or []):
        if not isinstance(item, dict) or item.get('type') != 'move':
            continue
        if source is not None and item.get('source') is not source:
            continue
        path = item.get('path')
        if isinstance(path, list) and len(path) >= 2:
            entries.append((uid, path, None))
    if not entries and fallback_path and len(fallback_path) >= 2:
        entries.append((uid, list(fallback_path), None))
    return entries


def resolved_move_animation_path(action, fallback=None):
    """Return the path the engine actually committed for a MoveAction."""
    entries = build_move_animation_log_entries(action, fallback_path=fallback)
    if entries:
        return entries[-1][1]
    return list(fallback or getattr(action, 'move_path', None) or [])


def _json_safe_uid(uid):
    """Normalize ids for SocketIO payloads (uuid.UUID is not JSON-serializable)."""
    if uid is None:
        return None
    return str(uid)


def emit_message_spell_link(socketio, result_item, *, suppress_ui=False):
    """Broadcast Message cantrip link UI (toast + local chat prompt)."""
    if suppress_ui or socketio is None or not result_item:
        return
    source = result_item.get('source')
    target = result_item.get('target')
    target_name = getattr(target, 'name', None)
    if target_name is None and target is not None and callable(getattr(target, 'label', None)):
        try:
            target_name = target.label()
        except Exception:
            target_name = None
    caster_name = getattr(source, 'name', None)
    if caster_name is None and source is not None and callable(getattr(source, 'label', None)):
        try:
            caster_name = source.label()
        except Exception:
            caster_name = None
    label = target_name or 'the target'
    socketio.emit('message', {
        'type': 'message_spell_link',
        'link_id': result_item.get('link_id'),
        'source': _json_safe_uid(getattr(source, 'entity_uid', None)),
        'target_uid': _json_safe_uid(getattr(target, 'entity_uid', None)),
        'target_name': target_name,
        'caster_name': caster_name,
        'message': (
            f'Message spell link — magical whisper only. Whisper to {label} in Local Chat '
            '(not normal speech).'
        ),
    })


from natural20.utils.conversation import audible_entities
from typing import Optional, Dict, Any
from natural20.session import Session
from natural20.campaign_log_db import CampaignLogDB
from webapp.game_management_components import GameControllerRegistry, GameEntityRegistry, ShortTermGoalManager

class SocketIOOutputLogger:
    """
    A simple logger that logs to stdout
    """
    def __init__(self, socketio):
        self.logging_queue = deque(maxlen=1000)
        self.socketio = socketio
        self._game_getter = None
        self._role_lookup = None
        self._controlled_entities_lookup = None
        self._event_context = None
        self._campaign_log_db_getter = None

    def configure_visibility(self, game_getter=None, role_lookup=None, controlled_entities_lookup=None):
        self._game_getter = game_getter
        self._role_lookup = role_lookup
        self._controlled_entities_lookup = controlled_entities_lookup

    def configure_persistence(self, campaign_log_db_getter=None):
        self._campaign_log_db_getter = campaign_log_db_getter

    def get_all_logs(self, username=None, roles=None):
        return [entry['message'] for entry in self.get_visible_entries(username=username, roles=roles)]

    def get_visible_entries(self, username=None, roles=None):
        if username is None and roles is None:
            return list(self.logging_queue)

        visible_entries = []
        for entry in self.logging_queue:
            if self._entry_visible_to_user(entry, username=username, roles=roles):
                visible_entries.append(entry)
        return visible_entries

    def get_visible_entries_for_entity(self, entity):
        return [entry for entry in self.logging_queue if self._entry_visible_to_entity(entry, entity)]

    def get_logs_for_entity(self, entity):
        return [entry['message'] for entry in self.get_visible_entries_for_entity(entity)]

    def clear_logs(self):
        self.logging_queue.clear()

    def get_log_snapshot(self):
        """Return a serializable list of all log entries for saving."""
        return list(self.logging_queue)

    def restore_log_snapshot(self, entries):
        """Restore log entries from a saved snapshot."""
        self.logging_queue.clear()
        for entry in (entries or []):
            self.logging_queue.append(entry)

    def set_event_context(self, event):
        self._event_context = event

    def clear_event_context(self):
        self._event_context = None

    def update(self):
        game = self._current_game()
        if not game or not getattr(game, 'username_to_sid', None):
            self.socketio.emit('message', {'type': 'console', 'messages': self.get_all_logs()})
            return

        for username, sids in game.username_to_sid.items():
            if not sids:
                continue
            messages = self.get_all_logs(username=username, roles=self._roles_for_username(username))
            for sid in sids:
                self.socketio.emit('message', {'type': 'console', 'messages': messages}, to=sid)

    def log(self, event_msg, event=None, visibility=None):
        # add time to the message
        current_time_str = time.strftime("%Y:%m:%d.%H:%M:%S", time.localtime())
        rendered_message = f"{current_time_str}: {event_msg}"
        effective_event = event if event is not None else self._event_context

        entry = {
            'timestamp': current_time_str,
            'message': rendered_message,
            'visibility': self._snapshot_visibility(event=effective_event, visibility=visibility),
        }

        self.logging_queue.append(entry)
        self._persist_combat_entry(entry)
        self._emit_entry(entry)

    def _persist_combat_entry(self, entry):
        getter = self._campaign_log_db_getter
        if not getter:
            return
        try:
            db = getter()
            if db is None:
                return
            db.append(
                'combat',
                entry.get('message', ''),
                created_at=entry.get('timestamp'),
                visibility=entry.get('visibility'),
            )
        except Exception:
            pass

    def _emit_entry(self, entry):
        game = self._current_game()
        if not game or not getattr(game, 'username_to_sid', None):
            self.socketio.emit('message', {'type': 'console', 'message': entry['message']})
            return

        emitted = False
        for username, sids in game.username_to_sid.items():
            if not sids:
                continue
            if not self._entry_visible_to_user(entry, username=username):
                continue
            emitted = True
            for sid in sids:
                self.socketio.emit('message', {'type': 'console', 'message': entry['message']}, to=sid)

        if not emitted and entry['visibility'].get('public'):
            self.socketio.emit('message', {'type': 'console', 'message': entry['message']})

    def _snapshot_visibility(self, event=None, visibility=None):
        if visibility is None and event is None:
            return self._public_visibility()

        if visibility == 'public':
            return self._public_visibility()

        if visibility in ('dm', 'dm_only'):
            return self._dm_only_visibility()

        if isinstance(visibility, dict):
            kind = visibility.get('kind')
            if kind == 'combat':
                return self._scoped_visibility(entity_uids=self._combat_visible_entity_uids(visibility))
            if kind == 'conversation':
                return self._scoped_visibility(entity_uids=self._conversation_visible_entity_uids(visibility))
            if kind in ('entity_only', 'entities'):
                return self._scoped_visibility(
                    entity_uids=self._entity_uids_from_values(
                        visibility.get('entity_uids') or visibility.get('entities') or []
                    ),
                    usernames=visibility.get('usernames') or [],
                )
            if visibility.get('public'):
                return self._public_visibility()
            if visibility.get('dm_only'):
                return self._dm_only_visibility()
            if visibility.get('entity_uids') or visibility.get('usernames'):
                return self._scoped_visibility(
                    entity_uids=self._entity_uids_from_values(visibility.get('entity_uids') or []),
                    usernames=visibility.get('usernames') or [],
                )

        if event is not None:
            event_name = event.get('event')
            if event_name == 'conversation':
                return self._scoped_visibility(entity_uids=self._conversation_visible_entity_uids(event))
            if event_name == 'console':
                return self._public_visibility()
            return self._scoped_visibility(entity_uids=self._combat_visible_entity_uids(event))

        return self._public_visibility()

    def _public_visibility(self):
        return {
            'public': True,
            'dm_only': False,
            'entity_uids': [],
            'usernames': [],
        }

    def _dm_only_visibility(self):
        return {
            'public': False,
            'dm_only': True,
            'entity_uids': [],
            'usernames': [],
        }

    def _scoped_visibility(self, entity_uids=None, usernames=None):
        # Coerce to str so sorted() never mixes uuid.UUID with str (e.g. DM
        # scratch entities vs slug uids like "rumblebelly").
        entity_uids = sorted(
            {str(uid) for uid in (entity_uids or []) if uid is not None and uid != ''}
        )
        usernames = sorted(set(name for name in (usernames or []) if name))
        if not entity_uids and not usernames:
            return self._public_visibility()
        return {
            'public': False,
            'dm_only': False,
            'entity_uids': entity_uids,
            'usernames': usernames,
        }

    def _entry_visible_to_user(self, entry, username=None, roles=None):
        if roles is None:
            roles = self._roles_for_username(username)

        if roles and 'dm' in roles:
            return True

        visibility = entry.get('visibility') or self._public_visibility()
        if visibility.get('public'):
            return True
        if visibility.get('dm_only'):
            return False
        if username and username in set(visibility.get('usernames') or []):
            return True

        visible_uids = {str(u) for u in (visibility.get('entity_uids') or [])}
        if not visible_uids:
            return False

        controlled_uids = {str(u) for u in self._controlled_entity_uids_for_username(username)}
        return bool(controlled_uids.intersection(visible_uids))

    def _entry_visible_to_entity(self, entry, entity):
        if entity is None:
            return False

        try:
            if getattr(entity, 'is_admin', False):
                return True
        except Exception:
            pass

        visibility = entry.get('visibility') or self._public_visibility()
        if visibility.get('public'):
            return True
        if visibility.get('dm_only'):
            return False

        entity_uid = self._entity_uid(entity)
        if not entity_uid:
            return False

        visible_uids = {str(u) for u in (visibility.get('entity_uids') or [])}
        return str(entity_uid) in visible_uids

    def _combat_visible_entity_uids(self, payload):
        source = payload.get('source')
        targets = self._collect_entities(payload)
        visible_entities = set(self._entity_uids_from_values(targets))
        if source is not None:
            source_uid = self._entity_uid(source)
            if source_uid:
                visible_entities.add(source_uid)

        if payload.get('players'):
            visible_entities.update(self._entity_uids_from_values(payload['players'].keys()))

        battle_map = self._map_for_payload(payload)
        if battle_map is None:
            return visible_entities

        seeds = []
        if source is not None:
            seeds.append(source)
        seeds.extend(targets)

        if not seeds:
            return visible_entities

        for viewer in self._map_entities(battle_map):
            viewer_uid = self._entity_uid(viewer)
            if not viewer_uid:
                continue
            for seed in seeds:
                try:
                    if battle_map.can_see(viewer, seed):
                        visible_entities.add(viewer_uid)
                        break
                except Exception:
                    continue

        return visible_entities

    def _conversation_visible_entity_uids(self, payload):
        source = payload.get('source')
        targets = self._collect_entities(payload)
        visible_entities = set(self._entity_uids_from_values(targets))

        if source is None:
            return visible_entities

        source_uid = self._entity_uid(source)
        if source_uid:
            visible_entities.add(source_uid)

        battle_map = self._map_for_payload(payload)
        if battle_map is None:
            return visible_entities

        try:
            listeners = audible_entities(
                source,
                battle_map,
                distance_ft=payload.get('distance_ft', 30),
                mode=payload.get('volume'),
            )
        except Exception:
            listeners = []

        for listener_entry in listeners:
            listener_uid = self._entity_uid(listener_entry.get('entity'))
            if not listener_uid:
                continue
            visible_entities.add(listener_uid)

        return visible_entities

    def _collect_entities(self, payload):
        entities = []
        if payload.get('target') is not None:
            entities.append(payload.get('target'))
        entities.extend(payload.get('targets') or [])
        return entities

    def _entity_uids_from_values(self, values):
        entity_uids = []
        for value in values:
            uid = self._entity_uid(value)
            if uid:
                entity_uids.append(uid)
        return entity_uids

    def _entity_uid(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return getattr(value, 'entity_uid', None)

    def _map_for_payload(self, payload):
        source = payload.get('source')
        candidate_entities = [source] + self._collect_entities(payload)
        for entity in candidate_entities:
            battle_map = self._map_for_entity(entity)
            if battle_map is not None:
                return battle_map
        return None

    def _map_for_entity(self, entity):
        if entity is None:
            return None

        game = self._current_game()
        if game is not None:
            try:
                battle_map = game.get_map_for_entity(entity)
                if battle_map is not None:
                    return battle_map
            except Exception:
                pass

        session = getattr(entity, 'session', None)
        if session is not None:
            try:
                return session.map_for_entity(entity)
            except Exception:
                return None
        return None

    def _map_entities(self, battle_map):
        entities = getattr(battle_map, 'entities', None)
        if entities is None:
            return []
        try:
            return list(entities)
        except Exception:
            return []

    def _current_game(self):
        if self._game_getter is None:
            return None
        try:
            return self._game_getter()
        except Exception:
            return None

    def _roles_for_username(self, username):
        if not username or self._role_lookup is None:
            return []
        try:
            return self._role_lookup(username) or []
        except Exception:
            return []

    def _controlled_entity_uids_for_username(self, username):
        if not username or self._controlled_entities_lookup is None:
            return []
        try:
            entities = self._controlled_entities_lookup(username) or []
        except Exception:
            return []

        entity_uids = []
        for entity in entities:
            uid = self._entity_uid(entity)
            if uid:
                entity_uids.append(uid)
        return entity_uids

# Defines a class for high level game management
class GameManagement:
    dm_turn_hold = None

    def __init__(self, game_session: Session, map_location, other_maps, socketio, output_logger, tile_px, controllers,
                 npc_controller = None,
                 force_llm_npc_combat = False,
                 autosave = False,
                 auto_battle=True,
                 system_logger=None,  soundtrack=None,
                 defer_player_spawn=False,
                 reset_saves=False):
        """
        Initialize the game management

        :param game_session: the game session
        :param map_location: the map location
        :param socketio: the socketio instance
        :param output_logger: the output logger
        :param tile_px: the tile pixel size
        :param controllers: the controllers
        :param auto_battle: whether to auto battle
        :param reset_saves: when True, wipe persisted save state at boot
            (save files, campaign logs, notebook, NPC memories) so the
            campaign starts fresh (``--reset`` startup flag).
        """
        self.map_location = map_location
        self.other_maps = other_maps
        self.game_session = game_session
        self.socketio = socketio
        self.output_logger = output_logger
        self.tile_px = tile_px
        self.waiting_for_user = False
        self.waiting_for_reaction = False
        self.waiting_for_bardic_inspiration = False
        self.end_turn_state = False
        self.controllers = controllers
        self.npc_controller = npc_controller
        self.force_llm_npc_combat = force_llm_npc_combat
        self.auto_battle = auto_battle
        self.auto_battle_distance_ft = int(os.environ.get('N20_AUTO_BATTLE_DISTANCE_FT', '30'))
        # When False (default, non-PvP maps), auto-start combat only on an
        # actually aggressive action (attack/spell/shove/grapple targeting an
        # opposing entity). When True (PvP maps), proximity (within
        # ``auto_battle_distance_ft``) and line-of-sight between opposing
        # groups will also auto-start combat. This prevents incidental
        # movement or self/ally-targeted spells from triggering combat in
        # non-PvP campaigns.
        self.pvp_enabled = False
        self.web_controllers = {}
        self.maps = {}
        self.max_save_states = 10
        self.pov_entity_for_user = {}
        self.current_map_for_user = {}
        self.username_to_sid = {}
        # entity_uid of an AI combatant whose turn is done but waits for the
        # DM to end it (battle pacing: ``dm_ends_ai_turns``).
        self.dm_turn_hold = None
        self.save_states = []
        self.defer_player_spawn = defer_player_spawn
        self.deferred_players = {}  # entity_uid -> {entity, map_name, position}
        self.entity_registry = GameEntityRegistry(self)
        self.controller_registry = GameControllerRegistry(self)
        self.soundtracks = soundtrack
        self.current_soundtrack = None
        self.autosave = autosave
        self.gzip = False
        self.read_notes = set()
        self.game_state_lock = threading.Lock()
        self._game_loop_task_guard = threading.Lock()
        self._game_loop_task_running = False
        # Monotonic marker for map-render-affecting state changes.
        # /update responses can be safely reused while this epoch is unchanged.
        self._render_epoch = 0
        self._render_epoch_by_user = {}
        # Per-map locks for non-battle actions to reduce contention across maps
        self.map_state_locks = {}
        self._map_state_locks_guard = threading.Lock()
        # Initialize logger as early as possible so subsequent setup can log safely
        if not system_logger:
            self.logger = logging.getLogger(__name__)
            self.logger.setLevel(logging.INFO)
        else:
            self.logger = system_logger
        # Initialize async save worker
        self._save_queue = queue.Queue(maxsize=50)
        # Sentinel object to signal orderly shutdown of the save worker
        self._SAVE_SENTINEL = object()
        self._save_thread = threading.Thread(target=self._save_worker, name="save-worker", daemon=True)
        self._save_thread.start()
        # Centralize save directory (writable in Docker). Can be absolute or relative.
        base_save_dir = os.environ.get('SAVE_DIR', os.path.join(os.getcwd(), 'saves'))

        # Namespace saves by game to avoid collisions when multiple games use the same SAVE_DIR.
        try:
            game_props = getattr(self.game_session, 'game_properties', {}) or {}
        except Exception:
            game_props = {}

        # Prefer an explicit game name/id from game properties, else fall back to the root_path folder name
        game_name_raw = game_props.get('name') or game_props.get('id') or os.path.basename(os.path.abspath(self.game_session.root_path)) or 'game'
        import re, tempfile
        game_ns = re.sub(r'[^a-zA-Z0-9_-]+', '-', str(game_name_raw)).strip('-_ ').lower() or 'game'

        self.save_dir = os.path.join(base_save_dir, game_ns)
        try:
            os.makedirs(self.save_dir, exist_ok=True)
        except Exception:
            # Fallback to a local namespaced saves dir inside CWD
            try:
                self.save_dir = os.path.join(os.getcwd(), 'saves', game_ns)
                os.makedirs(self.save_dir, exist_ok=True)
            except Exception:
                # Last resort: a tmp dir unique per process and game
                tmpdir = os.path.join(tempfile.gettempdir(), f"natural20_saves_{os.getpid()}_{game_ns}")
                os.makedirs(tmpdir, exist_ok=True)
                self.save_dir = tmpdir

        # If directory exists but is not writable, fallback to a tmp dir unique per process + game
        try:
            test_path = os.path.join(self.save_dir, '.write_test')
            with open(test_path, 'w') as _f:
                _f.write('ok')
            os.remove(test_path)
        except Exception:
            tmpdir = os.path.join(tempfile.gettempdir(), f"natural20_saves_{os.getpid()}_{game_ns}")
            os.makedirs(tmpdir, exist_ok=True)
            self.save_dir = tmpdir
            self.logger.warning(f"SAVE_DIR not writable. Falling back to {self.save_dir}")

        # --reset startup flag: wipe persisted state BEFORE the sqlite DBs
        # are opened and before any save autoload so the boot starts fresh.
        if reset_saves:
            try:
                self._wipe_save_state()
            except Exception:
                self.logger.exception("--reset: failed to clear persisted save state")

        self.campaign_log_db = CampaignLogDB(
            os.path.join(self.save_dir, 'campaign_logs.sqlite')
        )
        from natural20.notebook_db import NotebookDB
        self.notebook_db = NotebookDB(
            os.path.join(self.save_dir, 'notebook.sqlite'),
            files_dir=os.path.join(self.save_dir, 'notebook_files'),
        )
        from natural20.npc_memory_store import NpcMemoryStore
        self.npc_memory_store = NpcMemoryStore(getattr(self.game_session, 'root_path', '.'))
        if reset_saves:
            try:
                self._clear_npc_memory_files()
            except Exception:
                self.logger.exception("--reset: failed to clear NPC memory files")
        if self.output_logger:
            try:
                snapshot = self.campaign_log_db.combat_log_snapshot(1000)
                if snapshot:
                    self.output_logger.restore_log_snapshot(snapshot)
            except Exception as exc:
                self.logger.debug(f"Combat log restore skipped: {exc}")

        self.logger.info(f"Loading map from {self.map_location}")
        self.battle_map = self._starting_battle_map(game_session)

        self.maps = self.game_session.maps
        try:
            from webapp.blueprints.helpers.time_of_day import apply_time_of_day_to_maps
            apply_time_of_day_to_maps(self)
        except Exception:
            self.logger.exception('Failed to initialize time-of-day lighting')
        self.battle = None
        self.trigger_handlers = {}
        self.callbacks = {}
        self.current_save_index = 0
        self.previous_time = 0
        self.player_character_game_times = {}
        self.short_term_goals: Dict[str, Dict[str, Any]] = {}
        self.npc_movements: Dict[str, Dict[str, Any]] = {}
        self.goal_turn_seconds = max(1, int(os.environ.get('NPC_GOAL_TURN_SECONDS', '6')))
        self.goal_poll_interval = max(0.25, float(os.environ.get('NPC_GOAL_POLL_INTERVAL', '1.0')))
        self.goal_manager = ShortTermGoalManager(self)
        from webapp.npc_movement import NpcMovementManager
        self.movement_manager = NpcMovementManager(self)
        self._goal_thread_stop = threading.Event()
        self._movement_thread_stop = threading.Event()
        self._goal_thread = threading.Thread(target=self.goal_manager.goal_worker, name='npc-goal-worker', daemon=True)
        self._movement_thread = threading.Thread(
            target=self.movement_manager.movement_worker,
            name='npc-movement-worker',
            daemon=True,
        )
        self._goal_thread.start()
        self._movement_thread.start()
        try:
            from natural20.map_stack_movement import on_entity_relocation
            on_entity_relocation(self.movement_manager.on_entity_moved)
        except Exception:
            pass

        if self.soundtracks:
            loaded_soundtracks = []
            for track in self.soundtracks:
                track['duration'] = 0
                track['name'] = track['name'].strip()
                track['start_time'] = int(time.time())
                if 'volume' not in track or track['volume'] is None:
                    track['volume'] = 0
                audio_path = os.path.join(
                    self.game_session.root_path, 'assets', track['file'].lstrip('/')
                )
                if not os.path.isfile(audio_path):
                    self.logger.warning(
                        f"Soundtrack {track['name']} not found at {audio_path}; skipping"
                    )
                    continue
                try:
                    audio = MP3(audio_path)
                    track['duration'] = max(1, int(audio.info.length or 0))
                except Exception as exc:
                    self.logger.warning(
                        f"Soundtrack {track['name']} could not be loaded from {audio_path}: {exc}"
                    )
                    continue
                self.logger.info(
                    f"Loaded soundtrack {track['name']} with duration {track['duration']}"
                )
                loaded_soundtracks.append(track)
                if 'background' in track['name']:
                    self.current_soundtrack = track
            self.soundtracks = loaded_soundtracks
        self._setup_controllers()
        if autosave:
            # Build an initial save list from files present
            available_files = []
            try:
                for file in os.listdir(self.save_dir):
                    if file.startswith('save_') and (file.endswith('.yml') or file.endswith('.yml.gz')):
                        try:
                            index = int(file.split('_')[1].split('.')[0])
                            available_files.append((index, file))
                        except Exception:
                            # Named saves without numeric index
                            available_files.append((10**9, file))
            except FileNotFoundError:
                pass

            self.save_states = [file for index, file in sorted(available_files, key=lambda x: x[0])]
            # Attempt to autoload the last save if recorded
            last_save_path = os.path.join(self.save_dir, 'last_save.txt')
            if os.path.exists(last_save_path):
                with open(last_save_path, 'r') as f:
                    last_save = f.read().strip()
                    if last_save:
                        save_file, index = last_save.split(',')
                        try:
                            self.current_save_index = int(index)
                        except Exception:
                            self.current_save_index = 0
                        print(f"Loading save {save_file} {index}")
                        # Prefer loading by filename to avoid index/position ambiguity
                        self.load_save(filename=save_file)
        # Defer after any autosave restore so players respawn at their saved
        # positions instead of stale copies from the campaign YAML.
        if self.defer_player_spawn:
            self._defer_all_players()

    def _setup_controllers(self):
        self.controller_registry.setup_controllers()

    def _defer_all_players(self):
        self.entity_registry.defer_all_players()

    def spawn_player_for_user(self, username):
        return self.entity_registry.spawn_player_for_user(username)

    def get_pov_entity_for_user(self, username):
        return self.entity_registry.get_pov_entity_for_user(username)

    def _pov_entity_for_commit(self, username, pov_entities):
        """Resolve the POV entity for map-switch tracking during commit_and_update."""
        pov_entity = self.get_pov_entity_for_user(username)
        if pov_entity is not None:
            return pov_entity
        normalized = str(username or '').strip().lower()
        if not normalized:
            return None
        try:
            from webapp.blueprints.helpers.template_globals import entity_owners
        except Exception:
            entity_owners = None
        for candidate in pov_entities or []:
            if candidate is None:
                continue
            if entity_owners is None:
                continue
            owners = [str(owner).strip().lower() for owner in (entity_owners(candidate) or [])]
            if normalized in owners:
                return candidate
        return None

    def bump_render_epoch(self, username=None):
        """Advance the render epoch after game state mutations."""
        self._render_epoch += 1
        if username is not None:
            self._render_epoch_by_user[str(username)] = self._render_epoch
        return self._render_epoch

    def render_epoch_for_user(self, username=None):
        if username is None:
            return self._render_epoch
        return max(self._render_epoch, self._render_epoch_by_user.get(str(username), 0))

    def set_pov_entity_for_user(self, username, entity):
        self.entity_registry.set_pov_entity_for_user(username, entity)
        self.bump_render_epoch(username=username)
        self._sync_sidekick_auto_follows()

    def _sync_sidekick_auto_follows(self):
        """Realign sidekick auto-follow tasks after a POV (focus) change."""
        try:
            from webapp.sidekick_follow import sync_sidekick_auto_follows
            sync_sidekick_auto_follows(self)
        except Exception as exc:
            self.logger.debug("Sidekick auto-follow sync skipped: %s", exc)

    def switch_map_for_user(self, username, map_name):
        self.entity_registry.switch_map_for_user(username, map_name)
        self.bump_render_epoch(username=username)
        try:
            pov_entity = self.get_pov_entity_for_user(username)
            if pov_entity is not None:
                self.update_ambient_soundtrack(entity=pov_entity)
        except Exception:
            pass

    def recalc_lights_for_user(self, username):
        """Recompute cached light grids and bump the render epoch.

        Called after mutations that change entity-borne lighting outside the
        normal action commit path (e.g. equipping/unequipping a torch from the
        character sheet, or DM-side item swaps).  Without this, the server's
        cached per-tile light grid stays stale and the /update render cache
        keeps serving pre-equip HTML, so the map only picks up the new light
        source after a full page reload.
        """
        try:
            maps = self.maps or {}
        except Exception:
            maps = {}
        for map_obj in maps.values():
            compute = getattr(map_obj, '_compute_lights', None)
            if compute is not None:
                try:
                    compute()
                except Exception:
                    self.logger.debug('recalc_lights_for_user: failed to recompute lights for %r', getattr(map_obj, 'name', map_obj), exc_info=True)
        self.bump_render_epoch(username=username)

    def campaign_default_soundtrack_name(self):
        for soundtrack in (self.soundtracks or []):
            if 'background' in soundtrack['name'].lower():
                return soundtrack['name']
        if self.soundtracks:
            return self.soundtracks[0]['name']
        return None

    def resolve_ambient_soundtrack(self, battle_map, pos_x, pos_y):
        if battle_map is None:
            return self.campaign_default_soundtrack_name()
        zone_track = battle_map.soundtrack_for_position(pos_x, pos_y)
        if zone_track:
            return zone_track
        return self.campaign_default_soundtrack_name()

    def update_ambient_soundtrack(self, entity=None, battle_map=None, position=None):
        """Switch ambient music based on map position. Skipped during combat."""
        if self.get_current_battle():
            return False
        if entity is not None and battle_map is None:
            battle_map = self.get_map_for_entity(entity)
        if battle_map is None:
            return False
        if position is None:
            if entity is None:
                return False
            try:
                position = battle_map.position_of(entity)
            except Exception:
                return False
        track_name = self.resolve_ambient_soundtrack(battle_map, position[0], position[1])
        if not track_name:
            return False
        available = {soundtrack['name'].lower(): soundtrack['name'] for soundtrack in (self.soundtracks or [])}
        canonical = available.get(str(track_name).lower())
        if not canonical:
            self.logger.warning("Music zone requested unknown soundtrack %r", track_name)
            return False
        if getattr(self, '_last_ambient_soundtrack', None) == canonical:
            if self.current_soundtrack and self.current_soundtrack.get('name') == canonical:
                return False
        self._last_ambient_soundtrack = canonical
        self.play_soundtrack(canonical)
        return True

    def restore_ambient_soundtrack_after_battle(self):
        """Resume zone-appropriate ambient music when combat ends."""
        if self.get_current_battle():
            return
        from natural20.player_character import PlayerCharacter

        for battle_map in self.maps.values():
            for entity in battle_map.entities:
                if not isinstance(entity, PlayerCharacter):
                    continue
                try:
                    position = battle_map.position_of(entity)
                except Exception:
                    continue
                if self.update_ambient_soundtrack(entity=entity, battle_map=battle_map, position=position):
                    return
        default_track = self.campaign_default_soundtrack_name()
        if default_track:
            self.play_soundtrack(default_track)

    def get_map_for_user(self, username) -> Map:
        return self.entity_registry.get_map_for_user(username)

    def get_map_for_entity(self, entity) -> Map:
        return self.entity_registry.get_map_for_entity(entity)

    def get_entity_by_uid(self, entity_uid) -> Entity:
        return self.entity_registry.get_entity_by_uid(entity_uid)

    def canonical_map_entity(self, entity):
        """Return the map's live token for this entity UID, if any."""
        if entity is None:
            return None
        uid = getattr(entity, 'entity_uid', None)
        if uid is None:
            return entity
        resolved = self.get_entity_by_uid(uid)
        return resolved if resolved is not None else entity

    def play_battle_music(self, battle=None):
        """Start the configured combat soundtrack, if present."""
        battle_music = 'battle'
        battle = battle or self.get_current_battle()
        if battle is not None:
            for entity in battle.entities:
                custom = getattr(entity, 'battle_music', None)
                if custom:
                    battle_music = custom
                    self.logger.info(
                        "Using battle music %s for %s",
                        battle_music,
                        getattr(entity, 'name', entity),
                    )
                    break
        for soundtrack in (self.soundtracks or []):
            if battle_music.lower() == soundtrack['name'].lower():
                self.play_soundtrack(soundtrack['name'])
                return True
        self.logger.debug("No soundtrack named %r in campaign config", battle_music)
        return False

    def get_background_image_for_user(self, username):
        try:
            from webapp.blueprints.helpers.time_of_day import get_time_of_day_state
            state = get_time_of_day_state(self)
        except Exception:
            state = None
        return self.entity_registry.get_background_image_for_user(username, time_of_day_state=state)

    def waiting_for_user_input(self):
        return self.waiting_for_user

    def waiting_for_reaction_input(self):
        return self.waiting_for_reaction

    def clear_reaction_input(self):
        self.waiting_for_reaction = False

    def set_waiting_for_reaction_input(self, waiting):
        self.waiting_for_reaction = waiting

    def set_waiting_for_user_input(self, waiting):
        self.waiting_for_user = waiting

    def reset(self):
        self.battle_map = self._starting_battle_map(self.game_session)
        self.battle = None
        self.socketio.emit('message', {'type': 'reset', 'message': {}})

    def _starting_battle_map(self, game_session):
        """Return the campaign starting map object, keyed by its game.yml name.

        Older campaigns used the registry key ``index`` for the starting map.
        Do not reuse that name when ``index`` is a different map (for example
        a later dungeon while ``starting_map`` is a prologue hub).
        """
        getter = getattr(game_session, 'default_map_key', None)
        start_key = getter(self.map_location) if callable(getter) else None
        maps = getattr(game_session, 'maps', None) or {}
        if start_key and start_key in maps:
            return maps[start_key]
        if start_key:
            return Map(game_session, self.map_location, name=start_key)
        if 'index' in maps:
            return maps['index']
        return Map(game_session, self.map_location, name='index')

    def _map_file_for(self, map_name: str) -> str:
        """Resolve campaign YAML path for a map registry key."""
        if map_name in self.other_maps:
            return self.other_maps[map_name]
        game_maps = (getattr(self.game_session, 'game_properties', None) or {}).get('maps') or {}
        if map_name in game_maps:
            return game_maps[map_name]
        live = (self.maps or {}).get(map_name)
        live_path = getattr(live, 'map_file_path', None) if live is not None else None
        if live_path:
            return live_path
        if map_name in ('index', 'battle') and self.map_location:
            return self.map_location
        raise KeyError(f"Map file reference not found for {map_name!r}")

    def reload_map_by_name(self, map_name: str):
        """Reload a single map from campaign YAML (e.g. after edit-mode changes)."""
        self.entity_registry.defer_entities_on_map(map_name)
        map_file = self._map_file_for(map_name)
        new_map = Map(self.game_session, map_file, name=map_name)
        self.maps[map_name] = new_map
        if getattr(self.battle_map, 'name', None) == map_name:
            self.battle_map = new_map
        for username, (current_name, _current_map) in list(self.current_map_for_user.items()):
            if current_name == map_name:
                self.current_map_for_user[username] = (map_name, new_map)
        self.entity_registry.respawn_deferred_on_map(map_name)
        # /update caches rendered tiles per epoch; edits must invalidate them.
        self.bump_render_epoch()
        return new_map

    def ensure_user_entities_spawned(self, username):
        return self.entity_registry.ensure_user_entities_spawned(username)

    def reload_map_for_user(self,  username):
        map_name, _ = self.entity_registry._default_map_for_user(username)
        self.reload_map_by_name(map_name)

    def set_current_battle_map(self, battle_map):
        for k,v in self.current_map_for_user.items():
            self.current_map_for_user[k] = (battle_map.name, battle_map)

    def set_current_battle(self, battle):
        self.battle = battle

    def get_current_battle(self):
        return self.battle

    def get_current_battle_map(self) -> Map:
        return self.battle_map

    def register_event_handler(self, event, handler):
        """
        Register an event handler
        """
        if event not in self.trigger_handlers:
            self.trigger_handlers[event] = []
        self.trigger_handlers[event].append(handler)

    def trigger_event(self, event):
        """
        Trigger an event
        """
        results = []
        if event in self.trigger_handlers:
            for handlers in self.trigger_handlers[event]:
                results.append(handlers(self, self.game_session))
        if len(results) == 0:
            return False
        return any(results)

    def prompt(self, message, callback=None, options=None, usernames=None, title=None):
        callback_id = uuid.uuid4().hex
        self.callbacks[callback_id] = callback
        payload = {'type': 'prompt', 'message': message, 'callback': callback_id}
        if options:
            payload['options'] = options
        if title:
            payload['title'] = title

        if usernames:
            delivered = False
            for username in usernames:
                for sid in self.username_to_sid.get(str(username).lower(), []):
                    self.socketio.emit('message', payload, to=sid)
                    delivered = True
            if delivered:
                return

        self.socketio.emit('message', payload)

    def push_animation(self):
        self.socketio.emit('message', {'type': 'move', 'message': {'animation_log': _coalesce_animation_log(self.battle.get_animation_logs())}})
        self.battle.clear_animation_logs()

    def push_end_of_turn_animations(self, battle=None):
        """Emit animations queued during ``Battle.end_turn`` (legendary
        actions resolve there) so they play before the next turn starts."""
        battle = battle or self.get_current_battle()
        if battle is None:
            return
        # Legendary actions and end-of-turn effects mutate the board outside
        # commit_and_update(); without a new epoch /update serves stale tiles.
        self.bump_render_epoch()
        if not battle.get_animation_logs():
            return
        # No trailing refresh_map: the client refreshes tiles once the walk
        # finishes, and an early refresh removes the in-flight sprite.
        self.socketio.emit('message', {'type': 'move', 'message': {'animation_log': _coalesce_animation_log(battle.get_animation_logs())}})
        battle.clear_animation_logs()

    def begin_legendary_phase(self, battle=None) -> bool:
        """Open the next manually controlled legendary action window queued
        by ``Battle.end_turn``. Returns True when the battle should pause for
        the player instead of advancing to the next turn."""
        battle = battle or self.get_current_battle()
        if battle is None or battle.battle_ends():
            return False
        entity = battle.begin_legendary_phase()
        if entity is None:
            return False
        self.socketio.emit('message', {
            'type': 'legendary_phase',
            'message': {
                'id': entity.entity_uid,
                'label': entity.label(),
                'remaining': entity.total_legendary_actions(battle),
            },
        })
        self.emit_current_turn_state()
        return True

    def advance_legendary_phase(self, battle=None) -> None:
        """Close the open legendary window and move on: the next queued
        legendary creature, else the next turn in initiative order."""
        battle = battle or self.get_current_battle()
        if battle is None:
            return
        ended_uid = battle.legendary_phase
        battle.end_legendary_phase()
        if ended_uid:
            self.socketio.emit('message', {'type': 'legendary_phase_end', 'message': {'id': ended_uid}})
        if self.begin_legendary_phase(battle):
            return
        if battle.battle_ends():
            self.end_current_battle()
            return
        battle.next_turn()
        self.schedule_continue_game_loop()

    def emit_current_turn_state(self):
        battle = self.get_current_battle()
        if not battle:
            return
        # Start-of-turn effects (regeneration, death saves) change HP markers.
        self.bump_render_epoch()
        self.socketio.emit('message', {
            'type': 'initiative',
            'message': {'index': battle.current_turn_index}
        })
        self.socketio.emit('message', {'type': 'turn', 'message': {}})

    # -- Battle pacing: DM ends AI turns ------------------------------------ #

    def dm_ends_ai_turns(self) -> bool:
        """Whether AI combatants wait for the DM to end their turn.

        The DM's runtime toggle (``session_state['battle_pacing']``, saved with
        the game) overrides the campaign default ``game.yml`` →
        ``battle_pacing.dm_ends_ai_turns``.
        """
        session = getattr(self, 'game_session', None)
        state = (getattr(session, 'session_state', None) or {}).get('battle_pacing') or {}
        if 'dm_ends_ai_turns' in state:
            return bool(state['dm_ends_ai_turns'])
        props = getattr(session, 'game_properties', None) or {}
        block = props.get('battle_pacing') or {}
        return bool(block.get('dm_ends_ai_turns', False)) if isinstance(block, dict) else False

    def set_dm_ends_ai_turns(self, enabled: bool) -> bool:
        enabled = bool(enabled)
        self.game_session.session_state.setdefault('battle_pacing', {})['dm_ends_ai_turns'] = enabled
        self.socketio.emit('message', {'type': 'battle_pacing', 'message': self.battle_pacing_state()})
        if not enabled and self.dm_turn_hold:
            self.release_dm_turn_hold()
        return enabled

    def battle_pacing_state(self) -> dict:
        return {
            'dm_ends_ai_turns': self.dm_ends_ai_turns(),
            'dm_connected': self.dm_connected(),
            'held_uid': self.dm_turn_hold,
        }

    def dm_connected(self) -> bool:
        sids = self.username_to_sid or {}
        return any(sids.get(name) for name in self._dm_usernames())

    def _hold_for_dm(self, battle, entity) -> bool:
        """Pause after an AI combatant acted so the DM can narrate; returns True when held.

        Holds even with no DM online: the turn waits until a DM logs in and ends it.
        """
        if entity is None or battle is None or battle.battle_ends():
            return False
        if not self.dm_ends_ai_turns():
            return False
        if isinstance(battle.controller_for(entity), WebController):
            return False
        self.dm_turn_hold = entity.entity_uid
        self.logger.info("Holding %s's turn for the DM to end.", entity.name)
        self.socketio.emit('message', {
            'type': 'dm_turn_hold',
            'message': {'id': entity.entity_uid, 'label': entity.label()},
        })
        self.emit_current_turn_state()
        return True

    def release_dm_turn_hold(self) -> bool:
        """End the held AI turn and continue combat. Returns False when nothing was held."""
        held = self.dm_turn_hold
        if not held:
            return False
        self.dm_turn_hold = None
        self.socketio.emit('message', {'type': 'dm_turn_hold_end', 'message': {'id': held}})
        self.socketio.start_background_task(self._after_dm_hold_background)
        return True

    def _after_dm_hold_background(self) -> None:
        # The loop task that paused for the hold may still be emitting updates.
        for _ in range(200):
            if self._try_begin_game_loop_task():
                break
            self.socketio.sleep(0.05)
        else:
            self.logger.error("could not resume combat after DM-ended turn: game loop busy")
            return
        try:
            with self.game_state_lock:
                battle = self.get_current_battle()
                if battle is not None:
                    try:
                        if not self._finish_turn(battle):
                            self._run_turns()
                    except ManualControl:
                        self.logger.info("waiting for user to end turn.")
                    except AsyncReactionHandler as e:
                        self._await_reaction(e)
            self.emit_post_game_loop_updates(battle_start=False)
        except Exception:
            self.logger.exception("game loop after DM-ended turn failed")
        finally:
            self._end_game_loop_task()

    def _try_begin_game_loop_task(self) -> bool:
        with self._game_loop_task_guard:
            if self._game_loop_task_running:
                return False
            self._game_loop_task_running = True
            return True

    def _end_game_loop_task(self) -> None:
        with self._game_loop_task_guard:
            self._game_loop_task_running = False

    def game_loop_task_running(self) -> bool:
        with self._game_loop_task_guard:
            return self._game_loop_task_running

    def emit_post_game_loop_updates(self, *, battle_start: bool = False) -> None:
        battle = self.get_current_battle()
        if not battle:
            return

        if battle_start:
            self.socketio.emit('message', {'type': 'initiative', 'message': {}})
        else:
            self.socketio.emit('message', {
                'type': 'initiative',
                'message': {'index': battle.current_turn_index},
            })

        logs = battle.get_animation_logs()
        if logs:
            move_message = {'animation_log': _coalesce_animation_log(logs)}
            if not battle_start:
                current_turn = battle.current_turn()
                if current_turn is not None:
                    move_message['id'] = current_turn.entity_uid
            self.socketio.emit('message', {'type': 'move', 'message': move_message})
        battle.clear_animation_logs()
        self.socketio.emit('message', {'type': 'turn', 'message': {}})

    def _run_game_loop_once(self, *, battle_start: bool = False) -> None:
        if battle_start:
            self.output_logger.log("Battle started.", visibility='public')
        with self.game_state_lock:
            self.game_loop()
        self.emit_post_game_loop_updates(battle_start=battle_start)

    def _game_loop_background(self, *, battle_start: bool = False) -> None:
        try:
            self._run_game_loop_once(battle_start=battle_start)
        except Exception:
            self.logger.exception("background game loop failed")
        finally:
            self._end_game_loop_task()

    def schedule_game_loop(self, *, battle_start: bool = False) -> bool:
        """Run ``game_loop`` off the HTTP greenlet; returns False if one is already running."""
        if not self._try_begin_game_loop_task():
            self.logger.debug("skipped scheduling game loop: task already running")
            return False
        self.socketio.start_background_task(self._game_loop_background, battle_start=battle_start)
        return True

    def execute_game_loop(self, *, blocking: bool = False) -> bool:
        """Start advancing combat turns until manual control or battle end.

        By default runs asynchronously so HTTP/WebSocket handlers stay responsive.
        Pass ``blocking=True`` for gym/tests that need a synchronous run.
        """
        if blocking:
            self._run_game_loop_once(battle_start=True)
            return True
        return self.schedule_game_loop(battle_start=True)

    def schedule_continue_game_loop(self) -> bool:
        return self.schedule_game_loop(battle_start=False)

    def schedule_after_reaction(self) -> bool:
        """Continue AI turns after a reaction resolves (runs ai_loop then game_loop)."""
        if not self._try_begin_game_loop_task():
            self.logger.debug("skipped scheduling post-reaction loop: task already running")
            return False
        self.socketio.start_background_task(self._after_reaction_background)
        return True

    def _after_reaction_background(self) -> None:
        try:
            with self.game_state_lock:
                self.resume_after_reaction()
            self.emit_post_game_loop_updates(battle_start=False)
        except Exception:
            self.logger.exception("post-reaction game loop failed")
        finally:
            self._end_game_loop_task()

    def refresh_client_map(self):
        width, height = self.battle_map.size
        tiles_dimension_width = width * self.tile_px
        tiles_dimension_height = height * self.tile_px
        map_image_url = f"assets/{ self.battle_map.name + '.png'}"

        self.socketio.emit('message', {'type': 'map',
                                  'width': tiles_dimension_width,
                                  'height': tiles_dimension_height,
                                  'image_offset_px': self.battle_map.image_offset_px,
                                  'message': map_image_url})
        self.socketio.emit('message', {'type': 'initiative', 'message': {}})
        self.socketio.emit('message', {'type': 'turn', 'message': {}})

    def get_available_maps(self):
        return list(self.maps.keys())

    def effective_npc_combat_controller(self, entity=None):
        return self.controller_registry.effective_npc_combat_controller(entity)

    def build_combat_controller_for_entity(self, entity, controller_kind=None):
        return self.controller_registry.build_combat_controller_for_entity(
            entity,
            controller_kind=controller_kind,
        )
    
    def update_group(self, entity, group):
        entity.group = group
        self.loop_environment()

    def _entity_group(self, entity):
        group = getattr(entity, 'group', None)
        if group is None and isinstance(getattr(entity, 'properties', None), dict):
            group = entity.properties.get('group')
        if group is None:
            battle_map = None
            try:
                battle_map = self.get_map_for_entity(entity)
            except Exception:
                battle_map = None
            if battle_map is not None:
                for entry in getattr(battle_map, 'unaware_npcs', []) or []:
                    if entry.get('entity') is entity:
                        group = entry.get('group')
                        break
        return group

    def _faction_is_neutral(self, group):
        """Return True when a campaign group has no declared enemies (townsfolk)."""
        if not group:
            return True
        info = (self.game_session.groups() or {}).get(group) or {}
        return not info.get('enemies')

    def _is_manually_controlled(self, entity):
        """Best-effort check for whether ``entity`` is being driven by a
        human player (WebController) rather than AI/LLM. Used by
        ``loop_environment`` so manual PCs can lazy-join an active battle
        on proximity alone."""
        try:
            controller = self.controller_registry.get_controller_for_entity(entity)
        except Exception:
            controller = None
        if isinstance(controller, WebController):
            return True
        # Fall back to inspecting how a combat controller would be built
        # for this entity. PCs without a registered web controller default
        # to manual in webapp setup.
        try:
            built = self.controller_registry.build_combat_controller_for_entity(entity)
        except Exception:
            built = None
        return isinstance(built, WebController)

    def _hostile_targets_for_action(self, action):
        if not isinstance(action, (AttackAction, SpellAction, ShoveAction, GrappleAction)):
            return set()
        if isinstance(action, SpellAction) and not spell_action_provokes_hostility(action):
            return set()

        source = getattr(action, 'source', None)
        source_group = self._entity_group(source) if source else None
        if source is None or source_group is None:
            return set()

        candidate_targets = []
        target = getattr(action, 'target', None)
        if isinstance(target, list):
            candidate_targets.extend([t for t in target if isinstance(t, Entity)])
        elif isinstance(target, Entity):
            candidate_targets.append(target)

        for item in getattr(action, 'result', []) or []:
            target = item.get('target')
            if isinstance(target, Entity):
                candidate_targets.append(target)

        hostile_targets = set()
        for target in candidate_targets:
            target_group = self._entity_group(target)
            if target_group is not None and self.game_session.opposing(source_group, target_group):
                hostile_targets.add(target)
        return hostile_targets

    def _npc_witnesses_fight(self, witness, participants, battle_map):
        """True when an NPC can see or hear any combatant in a fight starting nearby."""
        participants = [p for p in (participants or []) if p is not None and p is not witness]
        if not participants:
            return False

        from natural20.utils.conversation import (
            SPEECH_BASE_RANGES,
            acoustic_profile,
            effective_hearing_distance,
        )
        combat_noise_ft = SPEECH_BASE_RANGES['shout']

        for participant in participants:
            try:
                if battle_map.can_see(witness, participant):
                    return True
            except Exception:
                pass
            try:
                raw_distance_ft = battle_map.distance(witness, participant) * battle_map.feet_per_grid
                acoustic = acoustic_profile(participant, witness, battle_map)
                adjusted_distance_ft = raw_distance_ft + float(acoustic.get('penalty_ft', 0) or 0)
                if adjusted_distance_ft <= effective_hearing_distance(witness, combat_noise_ft):
                    return True
            except Exception:
                continue
        return False

    def _add_entities_to_initiative(
        self,
        add_to_initiative_set,
        entity_by_groups,
        battle_map,
        anchor,
        group,
        include_allies=False,
        fight_participants=None,
    ):
        add_to_initiative_set.add((anchor, group))
        participants = set(fight_participants or [anchor])
        for ally in entity_by_groups.get(group, set()):
            if ally == anchor or not ally.conscious():
                continue
            ally_group = self._entity_group(ally)
            if ally_group and self._faction_is_neutral(ally_group):
                continue
            if group == 'a':
                # Party-wide: every conscious PC joins when any party member enters combat.
                should_add = True
            else:
                if self.get_map_for_entity(ally) != battle_map:
                    continue
                if include_allies:
                    should_add = self._npc_witnesses_fight(ally, participants, battle_map)
                else:
                    should_add = battle_map.can_see(ally, anchor)
            if should_add:
                add_to_initiative_set.add((ally, ally_group or group))

    def loop_environment(self, aggressive_pairs=None):
        if not self.auto_battle:
            return

        # check all entities in the map if it would set off a battle
        entity_by_groups = {}
        start_battle = False
        add_to_initiative_set = set()
        pc_groups = ['a']
        enemy_groups = ['b']
        aggressive_pairs = {
            frozenset((source, target))
            for source, target in (aggressive_pairs or [])
            if source is not None and target is not None
        }

        for battle_map in self.maps.values():
            for entity in battle_map.entities:
                entity_group = self._entity_group(entity)
                if entity_group not in entity_by_groups:
                    entity_by_groups[entity_group] = set()
                entity_by_groups[entity_group].add(entity)

        for battle_map in self.maps.values():
            for group1 in pc_groups:
                for group2 in enemy_groups:
                    if self.game_session.opposing(group1, group2):

                        if group1 not in entity_by_groups:
                            continue

                        for entity1 in entity_by_groups[group1]:
                            if not entity1.conscious():
                                continue
                            if group2 not in entity_by_groups:
                                continue
                            for entity2 in entity_by_groups[group2]:
                                if not entity2.conscious():
                                    continue

                                # Ignore if both entities already belong to an ongoing battle
                                if self.battle and (entity1 in self.battle.entities and entity2 in self.battle.entities):
                                    continue

                                if self.get_map_for_entity(entity1) != self.get_map_for_entity(entity2):
                                    continue

                                if entity2.passive() and frozenset((entity1, entity2)) not in aggressive_pairs:
                                    continue

                                distance_ft = None
                                try:
                                    distance_ft = battle_map.distance(entity1, entity2) * battle_map.feet_per_grid
                                except Exception:
                                    distance_ft = None

                                aggressive_trigger = frozenset((entity1, entity2)) in aggressive_pairs
                                # PvP maps trigger on proximity OR mutual
                                # visibility between opposing player teams.
                                # In non-PvP (adventure) mode, hostile NPCs
                                # spotting a PC must still auto-start combat
                                # (e.g. a trap-spawned monster appearing in
                                # line of sight) — but a PC merely seeing a
                                # passive NPC must not. We restrict the
                                # non-PvP visibility trigger to the case
                                # where the hostile NPC (entity2) is a true
                                # NPC, not another PlayerCharacter, and can
                                # see the PC.
                                if self.pvp_enabled:
                                    proximity_trigger = distance_ft is not None and distance_ft <= self.auto_battle_distance_ft
                                    visibility_trigger = battle_map.can_see(entity2, entity1)
                                else:
                                    proximity_trigger = False
                                    visibility_trigger = (
                                        not isinstance(entity2, PlayerCharacter)
                                        and battle_map.can_see(entity2, entity1)
                                    )

                                # Lazy-add for manual PCs while a battle is
                                # already in progress: a human-controlled
                                # PC who isn't yet in the initiative should
                                # be pulled in by mere proximity to any
                                # battle participant, even without line of
                                # sight (request from the user). NPC AI
                                # controllers keep the stricter visibility
                                # rule above and will not lazy-join on
                                # proximity alone.
                                if (
                                    not proximity_trigger
                                    and self.battle is not None
                                    and isinstance(entity1, PlayerCharacter)
                                    and entity1 not in self.battle.entities
                                    and entity2 in self.battle.entities
                                    and self._is_manually_controlled(entity1)
                                ):
                                    if distance_ft is not None and distance_ft <= self.auto_battle_distance_ft:
                                        proximity_trigger = True

                                if aggressive_trigger or proximity_trigger or visibility_trigger:
                                    include_allies = aggressive_trigger or proximity_trigger
                                    fight_participants = {entity1, entity2}
                                    self._add_entities_to_initiative(
                                        add_to_initiative_set,
                                        entity_by_groups,
                                        battle_map,
                                        entity1,
                                        group1,
                                        include_allies=include_allies,
                                        fight_participants=fight_participants,
                                    )
                                    self._add_entities_to_initiative(
                                        add_to_initiative_set,
                                        entity_by_groups,
                                        battle_map,
                                        entity2,
                                        group2,
                                        include_allies=include_allies,
                                        fight_participants=fight_participants,
                                    )
                                    start_battle = True
        add_to_initiative = list(add_to_initiative_set)

        if add_to_initiative:
            if start_battle:
                if not self.battle:
                    self.battle = Battle(self.game_session, self.maps, animation_log_enabled=True)
                    for entity, group in add_to_initiative:
                        controller = self.build_combat_controller_for_entity(entity)
                        if not controller:
                            self.logger.error(f"Controller not found for {entity}")
                            controller = self._default_combat_controller_for_entity(entity)

                        controller.register_handlers_on(entity)
                        self.logger.info(f"Adding {entity.name} to battle with group {group}")
                        self.battle.add(entity, group, controller=controller)

                    self.play_battle_music(self.battle)
                    self.battle.start()
                    forced_manager = getattr(self, 'forced_conversation_manager', None)
                    if forced_manager is not None:
                        try:
                            forced_manager.on_battle_started()
                        except Exception as exc:
                            self.logger.debug('Forced conversation battle hook skipped: %s', exc)
                    self.schedule_game_loop(battle_start=True)
                else:
                    for entity, group in add_to_initiative:
                        controller = self.build_combat_controller_for_entity(entity)
                        if controller is None:
                            controller = self._default_combat_controller_for_entity(entity)
                        controller.register_handlers_on(entity)
                        self.battle.add(entity, group, add_to_initiative=True, controller=controller)

                self.socketio.emit('message', { 'type': 'initiative','message': {'index': self.battle.current_turn_index if self.battle else None}})
                self.socketio.emit('message', { 'type': 'turn', 'message': {}})


    def get_controller_for_entity(self, entity):
        return self.controller_registry.get_controller_for_entity(entity)

    def get_web_controllers_for_user(self, username, default_controller = None):
        return self.controller_registry.get_web_controllers_for_user(username, default_controller=default_controller)

    def entity_owners(self, entity):
        return self.controller_registry.entity_owners(entity)

    def entities_owned_by(self, entity):
        return self.controller_registry.entities_owned_by(entity)

    def game_loop(self):
        """Run turns from the start of the current combatant's turn until a
        human must act, a reaction is pending, or the battle ends."""
        if self.dm_turn_hold:
            # The held combatant already acted; only the DM ends that turn.
            return
        try:
            self._run_turns()
        except ManualControl:
            self.logger.info("waiting for user to end turn.")
        except AsyncReactionHandler as e:
            self._await_reaction(e)

    def _run_turns(self):
        battle = self.get_current_battle()
        while True:
            if battle.battle_ends():
                break
            battle.skip_dead_turns()

            self.end_turn_state = False
            battle.start_turn()
            current_turn = battle.current_turn()
            current_turn.reset_turn(battle)
            self.emit_current_turn_state()

            if battle.battle_ends():
                break

            # Unconscious creatures only roll their death save (start_turn).
            if not (current_turn.dead() or current_turn.unconscious()):
                self.ai_loop()
                if battle.battle_ends():
                    break
                if self._hold_for_dm(battle, current_turn):
                    return

            if self._finish_turn(battle):
                return
        self.end_current_battle()

    def _finish_turn(self, battle) -> bool:
        """End the current turn (legendary actions resolve here) and advance.

        Returns True when play must pause for a manual legendary action phase.
        A reaction raised while the turn is ending leaves ``end_turn_state``
        set so the resumed flow skips straight to the next turn.
        """
        self.end_turn_state = True
        battle.end_turn()
        self.push_end_of_turn_animations(battle)
        self.loop_environment()
        return self._advance_after_end_of_turn(battle)

    def _advance_after_end_of_turn(self, battle) -> bool:
        self.end_turn_state = False
        if battle.battle_ends():
            return False
        if self.begin_legendary_phase(battle):
            return True
        battle.next_turn()
        return False

    def _await_reaction(self, e):
        """Hand a pending async reaction (Shield, opportunity attack, ...) to
        the player; play resumes from ``resume_after_reaction``."""
        entity = None
        for _, ent, valid_actions in e.resolve():
            entity = ent
            valid_actions_str = [
                [str(action.uid), str(action), action] for action in valid_actions
            ]
            self.waiting_for_reaction = [ent, e, e.resolve(), valid_actions_str]
        if entity is not None:
            self.socketio.emit('message', {
                'type': 'reaction',
                'message': {'id': entity.entity_uid, 'reaction': e.reaction_type},
            })

    def resume_after_reaction(self):
        """Continue combat after a reaction resolved, from where it paused.

        Mid-turn: the active combatant finishes its turn (AI keeps acting, a
        human keeps control). End of turn: the turn already ended, so go
        straight to the next combatant instead of replaying the turn.
        """
        battle = self.get_current_battle()
        if battle is None or self.dm_turn_hold:
            return
        try:
            if battle.legendary_phase:
                return
            if battle.battle_ends():
                self.end_turn_state = False
                self.end_current_battle()
                return
            if not self.end_turn_state:
                current = battle.current_turn()
                if not (current.dead() or current.unconscious()):
                    self.ai_loop()
                    if battle.battle_ends():
                        self.end_current_battle()
                        return
                    if self._hold_for_dm(battle, current):
                        return
                if self._finish_turn(battle):
                    return
            elif self._advance_after_end_of_turn(battle):
                return
            self._run_turns()
        except ManualControl:
            self.logger.info("waiting for user to end turn.")
        except AsyncReactionHandler as e:
            self._await_reaction(e)

    def _default_combat_controller_for_entity(self, entity):
        """Pick a safe default when no explicit controller kind was requested."""
        if isinstance(entity, PlayerCharacter):
            return self.controller_registry.ensure_web_controller_for_entity(entity)
        return GenericController(self.game_session)

    def ai_loop(self):
        battle = self.get_current_battle()
        entity = battle.current_turn()
        cycles = 0
        while True:
            cycles += 1
            action = battle.move_for(entity)
            if not action:
                print(f"{entity.name}: End turn.")
                break
            battle.action(action)
            battle.commit(action)
            # AI turns mutate board state (movement, status, visibility) outside
            # commit_and_update(); advance render epoch so /update never serves
            # stale cached HTML after NPC animations.
            self.bump_render_epoch()
            if not action or entity.unconscious() or entity.dead():
                break

    def set_volume(self, volume):
        self.current_soundtrack['volume'] = volume
        self.socketio.emit('message', {'type': 'volume', 'message': { 'volume': volume } })
        self.logger.info(f"Setting volume to {volume}")
    """
    Play a soundtrack

    :param track_id: the track id

    :return: None
    """
    def play_soundtrack(self, track_id):
        if track_id == "-1":
            # Clear current soundtrack and notify clients to stop
            self.current_soundtrack = None
            self.socketio.emit('message', {'type': 'stoptrack', 'message': {}})
        else:
            for soundtrack in (self.soundtracks or []):
                url = soundtrack['file']

                if track_id != soundtrack['name']:
                    continue

                if self.current_soundtrack:
                    if self.current_soundtrack['name'] != soundtrack['name']:
                        current_time_in_seconds = int(time.time())
                        current_soundtrack = {
                            'url': url,
                            'id': track_id,
                            'track_id': track_id,
                            'volume': soundtrack.get('volume'),
                            'start_time': current_time_in_seconds,
                            'duration': soundtrack.get('duration', 0),
                        }
                        soundtrack['time'] = 0
                        self.logger.info(f"Playing soundtrack {current_soundtrack}")
                        self.socketio.emit('message', { 'type': 'track', 'message': current_soundtrack })
                        self.current_soundtrack = soundtrack
                        break
                    else:
                        duration = max(1, int(self.current_soundtrack.get('duration') or 1))
                        time_s = (time.time() - self.current_soundtrack['start_time']) % duration
                        self.current_soundtrack['time'] = time_s
                        self.logger.info(f"Playing soundtrack {self.current_soundtrack}")
                        self.socketio.emit('message', { 'type': 'track', 'message': self.current_soundtrack})
                else:
                    current_soundtrack = {
                        'url': url,
                        'id': track_id,
                        'track_id': track_id,
                        'volume': soundtrack.get('volume'),
                        'start_time': time.time(),
                        'duration': soundtrack.get('duration', 0),
                    }
                    self.logger.info(f"Playing soundtrack {current_soundtrack}")
                    self.socketio.emit('message', {'type': 'track', 'message': current_soundtrack})
                    self.current_soundtrack = soundtrack
                    break

    """
    Againt track_id, seek to a specific time
    :param track_id: the track id
    :param time_s: the time in seconds
    """
    def seek_soundtrack(self, time_s):
        if self.current_soundtrack:
            self.current_soundtrack['start_time'] = int(time.time()) - time_s
            self.current_soundtrack['time'] = time_s
            self.socketio.emit('message', {'type': 'track', 'message': self.current_soundtrack})
            self.logger.info(f"Seeking soundtrack {self.current_soundtrack['name']} to {time_s}")

    def list_states(self):
        return self.save_states

    def _get_map_lock(self, map_obj):
        """Return an RLock dedicated to the provided map object.
        This allows actions on different maps to proceed concurrently when safe (outside battle).
        """
        if map_obj is None:
            # Fallback to global lock if map is unknown
            return self.game_state_lock
        try:
            map_key = getattr(map_obj, 'name', None) or id(map_obj)
        except Exception:
            map_key = id(map_obj)
        with self._map_state_locks_guard:
            lock = self.map_state_locks.get(map_key)
            if lock is None:
                lock = threading.RLock()
                self.map_state_locks[map_key] = lock
            return lock

    def _wipe_save_state(self) -> list:
        """Delete all persisted save state under ``save_dir`` (for ``--reset``).

        Removes save files (``save_*.yml[.gz]``), ``last_save.txt``, per-save
        log/read-notes snapshots (``*.log.json`` / ``*.read_notes.json``), the
        campaign-logs and notebook sqlite databases, and the notebook file
        attachments directory. Must run before the sqlite DB objects are
        opened so they start empty. Returns the list of removed paths.
        """
        removed = []
        if not self.save_dir or not os.path.isdir(self.save_dir):
            return removed
        for fname in list(os.listdir(self.save_dir)):
            path = os.path.join(self.save_dir, fname)
            delete = False
            if os.path.isfile(path):
                if fname.startswith('save_') and (fname.endswith('.yml') or fname.endswith('.yml.gz')):
                    delete = True
                elif fname in ('last_save.txt',):
                    delete = True
                elif fname.endswith('.log.json') or fname.endswith('.read_notes.json'):
                    delete = True
                elif fname in ('campaign_logs.sqlite', 'notebook.sqlite'):
                    delete = True
                elif fname == '.write_test':
                    delete = True
            elif os.path.isdir(path) and fname == 'notebook_files':
                delete = True
            if delete:
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                    removed.append(fname)
                except OSError as exc:
                    self.logger.warning(f"--reset: could not delete {path}: {exc}")
        # Reset in-memory save bookkeeping so nothing stale is autoloaded.
        self.save_states = []
        self.current_save_index = 0
        if removed:
            self.logger.info(
                f"--reset: cleared persisted save state in {self.save_dir} "
                f"({len(removed)} item(s): {', '.join(sorted(removed))})"
            )
        else:
            self.logger.info(f"--reset: no persisted save state found in {self.save_dir}")
        return removed

    def reset_campaign_logs(self, *, clear_live_conversation_buffers: bool = False) -> Dict[str, int]:
        """Wipe persisted campaign logs (combat, chat, DM assistant, journals)."""
        before = {}
        try:
            before = self.campaign_log_db.counts_by_category()
        except Exception:
            before = {}
        self.campaign_log_db.clear_all()
        if self.output_logger:
            self.output_logger.clear_logs()
        if clear_live_conversation_buffers:
            try:
                for battle_map in (self.maps or {}).values():
                    for ent in list(getattr(battle_map, 'entities', []) or []):
                        if hasattr(ent, 'clear_conversation_buffer'):
                            ent.clear_conversation_buffer()
            except Exception as exc:
                self.logger.debug(f"Conversation buffer clear skipped: {exc}")
        return before

    def clear_save_files(self) -> list:
        """Delete game save files under ``save_dir`` (not campaign_logs.sqlite or notebook.sqlite)."""
        removed = []
        if not self.save_dir or not os.path.isdir(self.save_dir):
            return removed
        for fname in list(os.listdir(self.save_dir)):
            path = os.path.join(self.save_dir, fname)
            if not os.path.isfile(path):
                continue
            delete = False
            if fname.startswith('save_') and (fname.endswith('.yml') or fname.endswith('.yml.gz')):
                delete = True
            elif fname == 'last_save.txt':
                delete = True
            elif fname.endswith('.log.json') or fname.endswith('.read_notes.json'):
                delete = True
            if delete:
                try:
                    os.remove(path)
                    removed.append(fname)
                except OSError as exc:
                    self.logger.warning(f"Could not delete save file {path}: {exc}")
        self.save_states = []
        self.current_save_index = 0
        return removed

    def _clear_npc_memory_files(self) -> int:
        store = getattr(self, 'npc_memory_store', None)
        memories_dir = getattr(store, 'memories_dir', None)
        if not memories_dir or not os.path.isdir(memories_dir):
            return 0
        removed = 0
        for fname in list(os.listdir(memories_dir)):
            if not fname.endswith('.json'):
                continue
            try:
                os.remove(os.path.join(memories_dir, fname))
                removed += 1
            except OSError as exc:
                self.logger.warning(f"Could not delete NPC memory file {fname}: {exc}")
        return removed

    def reset_to_fresh_campaign(
        self,
        *,
        clear_logs: bool = False,
        clear_live_conversation_buffers: bool = False,
        clear_npc_memories: bool = False,
    ) -> dict:
        """Delete saves and reload the campaign from YAML templates."""
        from natural20.session import Session

        if self.battle is not None:
            try:
                self.end_current_battle()
            except Exception:
                self.battle = None

        removed_saves = self.clear_save_files()
        removed_npc_memories = 0
        if clear_npc_memories:
            removed_npc_memories = self._clear_npc_memory_files()

        removed_log_counts = {}
        if clear_logs:
            removed_log_counts = self.reset_campaign_logs(
                clear_live_conversation_buffers=clear_live_conversation_buffers,
            )

        root_path = self.game_session.root_path
        event_manager = self.game_session.event_manager
        conversation_handlers = getattr(self.game_session, 'conversation_handlers', None) or {}

        new_session = Session(
            root_path,
            event_manager=event_manager,
            conversation_handlers=conversation_handlers,
        )
        new_session.render_for_text = False

        self.game_session = new_session
        self.battle = None
        self.battle_map = self._starting_battle_map(new_session)
        self.maps = new_session.maps
        self.read_notes = set()
        self.player_character_game_times = {}
        self.short_term_goals = {}
        self.npc_movements = {}
        self.deferred_players = {}
        self.pov_entity_for_user = {}
        self.current_map_for_user = {}
        self.web_controllers = {}
        self.waiting_for_user = False
        self.waiting_for_reaction = False
        self.end_turn_state = False

        self._setup_controllers()
        if self.defer_player_spawn:
            self._defer_all_players()
        self.restore_ambient_soundtrack_after_battle()

        return {
            'removed_saves': removed_saves,
            'removed_npc_memories': removed_npc_memories,
            'removed_log_counts': removed_log_counts,
        }

    def save_game(self, name: Optional[str] = None):
        # Snapshot state and determine filename under lock, then write outside the lock
        yaml_str, target_file, index_for_record, log_snapshot, read_notes_snapshot = self._prepare_save(name)
        self._write_save_file(yaml_str, target_file, index_for_record, log_snapshot, read_notes_snapshot)

    def save_game_async(self, name: Optional[str] = None):
        """Queue a save request to be processed by the background save worker."""
        try:
            # Coalesce: if queue is full, drop older requests
            if self._save_queue.full():
                try:
                    _ = self._save_queue.get_nowait()
                except Exception:
                    pass
            self._save_queue.put_nowait(name)
        except Exception as e:
            # Fallback to synchronous save if queueing fails
            self.logger.warning(f"Falling back to sync save due to queue error: {e}")
            try:
                self.save_game(name=name)
            except Exception as save_exc:
                # Autosave is best-effort: surface the failure to the DM (toaster)
                # and keep the original exception propagating to the caller.
                try:
                    self.logger.exception(f"Sync autosave fallback failed: {save_exc}")
                except Exception:
                    pass
                self._emit_autosave_error(save_exc, name=name)
                raise

    def _save_worker(self):
        """Background worker that processes save requests, coalescing bursts."""
        while True:
            name = None  # default so the except handler below is always safe
            stop_after = False
            try:
                name = self._save_queue.get()  # block
                if name is self._SAVE_SENTINEL:
                    # Nothing pending before sentinel, exit immediately
                    break
                # Drain to last request to coalesce rapid bursts. If we encounter sentinel while
                # draining, process the last non-sentinel request and then stop.
                while not self._save_queue.empty():
                    try:
                        next_item = self._save_queue.get_nowait()
                        if next_item is self._SAVE_SENTINEL:
                            stop_after = True
                            break
                        name = next_item
                    except Exception:
                        break
                yaml_str, target_file, index_for_record, log_snapshot, read_notes_snapshot = self._prepare_save(name)
                self._write_save_file(yaml_str, target_file, index_for_record, log_snapshot, read_notes_snapshot)
                if stop_after:
                    break
            except Exception as e:
                try:
                    self.logger.exception(f"Async save failed: {e}")
                except Exception:
                    pass
                # Surface autosave failures to the DM with full error details so a
                # broken autosave is visible in the VTT instead of only in logs.
                try:
                    self._emit_autosave_error(e, name=name)
                except Exception:
                    try:
                        self.logger.exception("Failed to emit autosave_error to DM")
                    except Exception:
                        pass
                if stop_after:
                    # The sentinel was consumed while draining: stop the worker
                    # even though this save failed (e.g. shutdown flush).
                    break

    def _dm_usernames(self):
        """Return usernames of logged-in accounts holding the DM role."""
        dm_names = []
        try:
            from webapp.blueprints.helpers.runtime_state import get_logins
            for login in get_logins() or []:
                try:
                    if 'dm' in (login.get('role') or []):
                        name = login.get('name')
                        if name:
                            dm_names.append(str(name))
                except Exception:
                    continue
        except Exception:
            # runtime_state not registered (e.g., standalone/tests): fall back to
            # the conventional 'dm' username prefix used elsewhere in the webapp.
            try:
                for username in (self.username_to_sid or {}).keys():
                    if username and str(username).lower().startswith('dm'):
                        dm_names.append(str(username))
            except Exception:
                pass
        return dm_names

    def _emit_autosave_error(self, exc: Exception, name: Optional[str] = None):
        """Emit an ``autosave_error`` SocketIO message to logged-in DMs.

        The payload carries the full error details (exception type, message,
        traceback, target save file, save directory) so the client can render
        a dismissible error toaster for the DM.
        """
        socketio = getattr(self, 'socketio', None)
        if socketio is None:
            return
        try:
            dm_names = self._dm_usernames()
            if not dm_names:
                return
            sids = []
            for username in dm_names:
                sids.extend((self.username_to_sid or {}).get(str(username), []))
            if not sids:
                return
            import traceback as _traceback
            tb = ''.join(_traceback.format_exception(type(exc), exc, exc.__traceback__))
            payload = {
                'type': 'autosave_error',
                'message': {
                    'title': 'Autosave failed',
                    'error_type': type(exc).__name__,
                    'error': str(exc) or repr(exc),
                    'traceback': tb[-4000:],
                    'target_file': name if name else 'autosave',
                    'save_dir': getattr(self, 'save_dir', None),
                    'autosave': bool(getattr(self, 'autosave', False)),
                },
            }
            socketio.emit('message', payload, to=sids)
        except Exception:
            # Emitting must never break the save worker itself.
            try:
                self.logger.exception("Failed to emit autosave_error to DM")
            except Exception:
                pass

    def _prepare_save(self, name: Optional[str]):
        """Prepare YAML string and decide target filename/index under lock."""
        with self.game_state_lock:
            serializer = Serialization()
            yaml_str = serializer.serialize(self.game_session, self.battle, self.maps)
            log_snapshot = self.output_logger.get_log_snapshot()
            read_notes_snapshot = list(self.read_notes)

            # Determine filename
            if name:
                import re
                slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-_ ")
                ts = time.strftime('%Y%m%d-%H%M%S')
                base_name = f"save_{ts}_{slug}.yml" if slug else f"save_{ts}.yml"
                index_for_record = -1
            else:
                index = self.current_save_index % self.max_save_states
                base_name = f"save_{index}.yml"
                index_for_record = index
                # Advance index to avoid race while writing
                self.current_save_index += 1

            target_file = base_name + ('.gz' if self.gzip and not base_name.endswith('.gz') else '')
            return yaml_str, target_file, index_for_record, log_snapshot, read_notes_snapshot

    def _write_save_file(self, yaml_str: str, target_file: str, index_for_record: int, log_snapshot=None, read_notes_snapshot=None):
        """Write the given YAML string to disk atomically and update indices/state."""
        abs_target = os.path.join(self.save_dir, target_file)
        import tempfile
        tmp_dir = self.save_dir
        try:
            if abs_target.endswith('.gz'):
                fd, tmp_path = tempfile.mkstemp(prefix='.save_', suffix='.yml.gz', dir=tmp_dir)
                os.close(fd)
                with gzip.open(tmp_path, 'wb') as f:
                    f.write(yaml_str.encode('utf-8'))
            else:
                fd, tmp_path = tempfile.mkstemp(prefix='.save_', suffix='.yml', dir=tmp_dir)
                with os.fdopen(fd, 'w') as f:
                    f.write(yaml_str)
            os.replace(tmp_path, abs_target)
        except PermissionError:
            import tempfile as _tempfile
            fallback_dir = os.path.join(_tempfile.gettempdir(), f"natural20_saves_{os.getpid()}")
            os.makedirs(fallback_dir, exist_ok=True)
            self.logger.warning(f"Permission denied writing to {self.save_dir}. Falling back to {fallback_dir}")
            self.save_dir = fallback_dir
            abs_target = os.path.join(self.save_dir, target_file)
            if abs_target.endswith('.gz'):
                with gzip.open(abs_target, 'wb') as f:
                    f.write(yaml_str.encode('utf-8'))
            else:
                with open(abs_target, 'w') as f:
                    f.write(yaml_str)

        # Rebuild save list
        available_files = []
        try:
            for file in os.listdir(self.save_dir):
                if file.startswith('save_') and (file.endswith('.yml') or file.endswith('.yml.gz')):
                    try:
                        idx = int(file.split('_')[1].split('.')[0])
                        available_files.append((idx, file))
                    except Exception:
                        available_files.append((10**9, file))
        except FileNotFoundError:
            pass
        self.save_states = [file for idx, file in sorted(available_files, key=lambda x: x[0])]
        try:
            with open(os.path.join(self.save_dir, 'last_save.txt'), 'w') as f:
                f.write(f"{target_file},{index_for_record}")
        except Exception:
            # Non-fatal if we can't write last_save.txt
            pass

        # Save combat log alongside the game state
        if log_snapshot is not None:
            import json
            log_file = os.path.splitext(target_file.replace('.gz', ''))[0] + '.log.json'
            try:
                abs_log = os.path.join(self.save_dir, log_file)
                with open(abs_log, 'w') as f:
                    json.dump(log_snapshot, f)
            except Exception:
                pass

        # Save read notes alongside the game state
        if read_notes_snapshot is not None:
            import json
            notes_file = os.path.splitext(target_file.replace('.gz', ''))[0] + '.read_notes.json'
            try:
                abs_notes = os.path.join(self.save_dir, notes_file)
                with open(abs_notes, 'w') as f:
                    json.dump(read_notes_snapshot, f)
            except Exception:
                pass

    def load_save(self, index=None, filename=None):
        """
        Load a save by filename or identifier.
        - If filename is provided, load that file directly.
        - If index is provided, first try save_{index}.yml(.gz); otherwise treat as positional index into self.save_states.
        - If neither provided, load the latest (last in sorted list).
        """
        target_file = None
        if filename:
            # Trust provided filename as-is
            target_file = filename
        elif index is not None:
            # Try id-based file first
            candidate_yml = f"save_{index}.yml"
            candidate_gz = f"save_{index}.yml.gz"
            if os.path.exists(os.path.join(self.save_dir, candidate_yml)):
                target_file = candidate_yml
            elif os.path.exists(os.path.join(self.save_dir, candidate_gz)):
                target_file = candidate_gz
            else:
                # Fallback to positional within current list
                try:
                    target_file = self.save_states[index]
                except Exception:
                    return
        else:
            # Default to latest known
            if not self.save_states:
                return
            target_file = self.save_states[-1]

        # Read the state content
        # Resolve to absolute path under save_dir if not absolute
        abs_path = target_file
        if not os.path.isabs(abs_path):
            abs_path = os.path.join(self.save_dir, target_file)
        if abs_path.endswith('.gz'):
            with gzip.open(abs_path, 'rb') as f:
                state = f.read().decode('utf-8')
        else:
            with open(abs_path, 'r') as f:
                state = f.read()
        serializer = Serialization()
        new_session, new_battle, new_maps = serializer.deserialize(state)
        self.dm_turn_hold = None
        self.game_session = new_session
        self.battle = new_battle

        new_maps = new_maps or {}
        getter = getattr(new_session, 'default_map_key', None)
        start_key = getter(self.map_location) if callable(getter) else None
        self.battle_map = (
            (new_maps.get(start_key) if start_key else None)
            or new_maps.get('index')
            or next(iter(new_maps.values()), None)
        )
        if self.battle_map is None:
            self.battle_map = self._starting_battle_map(new_session)
        self.maps = new_maps
        # Do not mutate the save list on load; keep history intact

        # Restore combat log if a log file exists alongside the save
        import json
        log_file = os.path.splitext(target_file.replace('.gz', ''))[0] + '.log.json'
        abs_log = os.path.join(self.save_dir, log_file)
        try:
            if os.path.exists(abs_log):
                with open(abs_log, 'r') as f:
                    log_snapshot = json.load(f)
                self.output_logger.restore_log_snapshot(log_snapshot)
        except Exception:
            pass

        # Restore read notes if a file exists alongside the save
        notes_file = os.path.splitext(target_file.replace('.gz', ''))[0] + '.read_notes.json'
        abs_notes = os.path.join(self.save_dir, notes_file)
        try:
            if os.path.exists(abs_notes):
                with open(abs_notes, 'r') as f:
                    self.read_notes = set(json.load(f))
            else:
                self.read_notes = set()
        except Exception:
            pass

        try:
            self._setup_controllers()
        except Exception:
            pass


    def end_current_battle(self):
        battle = self.get_current_battle()
        if battle is not None:
            try:
                from natural20.environment_zones import demote_battle_zones_to_environment
                demote_battle_zones_to_environment(battle)
            except Exception:
                self.logger.exception('Failed to demote battle zones to environment ticking')
        self.trigger_event('on_battle_end')
        self.dm_turn_hold = None
        self.set_current_battle(None)
        self.restore_ambient_soundtrack_after_battle()
        self.socketio.emit('message', {'type': 'console', 'message': 'Battle has ended.'})
        self.socketio.emit('message', {'type': 'stop', 'message': {}})

    def execute_command(self, command):
        """Execute a command string and return the result."""
        try:
            # Split the command into parts
            parts = command.strip().split()
            if not parts:
                return "Empty command"
                
            cmd = parts[0].lower()
            args = parts[1:]
            
            # Process different commands
            if cmd == "help":
                return "Available commands: help, status, list, move, attack, cast, use, give, take, say, whisper, shout"
            elif cmd == "status":
                return f"Current map: {self.current_map.name}, Battle in progress: {self.battle is not None}"
            elif cmd == "list":
                if len(args) > 0 and args[0] == "entities":
                    entities = self.current_map.get_entities()
                    return "\n".join([f"{e.name} ({e.entity_uid})" for e in entities])
                else:
                    return "Usage: list entities"
            else:
                return f"Unknown command: {cmd}. Type 'help' for available commands."
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def increment_game_time(self, player_character: PlayerCharacter, seconds: int = 6):
        """Advance a PC's exploration watermark and, if it leads, world time.

        Each player character tracks personal exploration time. Global
        ``Session.game_time`` advances by the delta whenever the highest
        watermark increases (so lagged PCs do not rewind the world clock).
        """
        try:
            seconds = int(seconds or 0)
        except (TypeError, ValueError):
            seconds = 0
        if seconds <= 0 or player_character is None:
            return

        if player_character not in self.player_character_game_times:
            self.player_character_game_times[player_character] = 0
        self.player_character_game_times[player_character] += seconds

        max_player_time = max(self.player_character_game_times.values(), default=0)

        if max_player_time > self.previous_time:
            delta = max_player_time - self.previous_time
            self.previous_time = max_player_time
            self.game_session.increment_game_time(delta)
            if not self.get_current_battle():
                self._tick_exploration_environment()

    @staticmethod
    def movement_travel_time_seconds(entity, distance_ft) -> int:
        """Return in-game seconds for traveling ``distance_ft`` at entity speed.

        Uses ``(distance / speed) * 6`` (one combat round = 6 seconds for a
        full speed budget). Returns 0 when distance or speed is non-positive.
        """
        try:
            distance = float(distance_ft or 0)
        except (TypeError, ValueError):
            distance = 0.0
        try:
            speed = float(entity.speed() if entity is not None else 0)
        except Exception:
            speed = 0.0
        if distance <= 0 or speed <= 0:
            return 0
        return max(0, int(round((distance / speed) * 6.0)))

    @staticmethod
    def move_action_distance_ft(action) -> float:
        """Estimate feet traveled by a resolved ``MoveAction``."""
        total_squares = 0.0
        feet_per_grid = 5.0
        for item in list(getattr(action, 'result', None) or []):
            if not isinstance(item, dict) or item.get('type') != 'move':
                continue
            map_obj = item.get('map')
            if map_obj is not None:
                try:
                    feet_per_grid = float(getattr(map_obj, 'feet_per_grid', 5) or 5)
                except (TypeError, ValueError):
                    feet_per_grid = 5.0
            cost = item.get('move_cost')
            if cost is not None:
                try:
                    total_squares += max(0.0, float(cost))
                    continue
                except (TypeError, ValueError):
                    pass
            path = item.get('path') or []
            if isinstance(path, (list, tuple)) and len(path) > 1:
                total_squares += float(len(path) - 1)
        return total_squares * feet_per_grid

    def _tick_exploration_environment(self):
        if self.get_current_battle() is not None:
            return
        try:
            from natural20.environment_zones import tick_environment_zones
            tick_environment_zones(self.maps, event_manager=self.game_session.event_manager)
        except Exception:
            self.logger.exception('Exploration environment zone tick failed')

    def realtime_ui_suppressed(self) -> bool:
        try:
            from webapp.long_rest_npc_simulation import RealtimeUiSuppressor
            return RealtimeUiSuppressor.suppressed(self)
        except Exception:
            return False

    def advance_world_time(self, seconds: int = 6, trigger_environment: bool = True):
        with self.game_state_lock:
            old_time = int(getattr(self.game_session, 'game_time', 0) or 0)
            self.game_session.increment_game_time(seconds)
            if trigger_environment and not self.get_current_battle():
                self.loop_environment()
                self._tick_exploration_environment()
            new_time = int(self.game_session.game_time)
        if trigger_environment and not self.get_current_battle():
            try:
                from webapp.npc_environment_ticks import schedule_npc_environment_ticks
                schedule_npc_environment_ticks(self)
            except Exception:
                self.logger.exception('Failed to schedule NPC environment ticks')
        try:
            from webapp.blueprints.helpers.time_of_day import on_game_time_changed
            on_game_time_changed(self, old_time=old_time, new_time=new_time)
        except Exception:
            self.logger.exception('time-of-day update failed')
        if self.realtime_ui_suppressed():
            return
        try:
            from webapp.blueprints.helpers.time_of_day import time_of_day_turn_payload
            turn_message = {
                'game_time': self.game_session.game_time,
                'time_of_day': time_of_day_turn_payload(self),
            }
        except Exception:
            turn_message = {'game_time': self.game_session.game_time}
        self.socketio.emit('message', {'type': 'turn', 'message': turn_message})
        if self.autosave:
            self.save_game_async()

    def schedule_short_term_goal(self, entity, goal_text, speaker=None):
        return self.goal_manager.schedule_short_term_goal(entity, goal_text, speaker=speaker)

    def get_short_term_goal(self, entity):
        return self.goal_manager.get_short_term_goal(entity)

    def record_short_term_goal_history(self, entity, entry):
        return self.goal_manager.record_short_term_goal_history(entity, entry)

    def complete_short_term_goal(self, entity, status='completed', reason=None):
        return self.goal_manager.complete_short_term_goal(entity, status=status, reason=reason)

    def schedule_npc_move_to(
        self,
        entity,
        target_info,
        distance_ft=5,
        speaker=None,
        *,
        return_to_post=False,
        trigger_arrival_debrief=False,
        on_arrival_event=None,
        on_arrival_payload=None,
    ):
        return self.movement_manager.schedule_move_to(
            entity,
            target_info,
            distance_ft=distance_ft,
            speaker=speaker,
            return_to_post=return_to_post,
            trigger_arrival_debrief=trigger_arrival_debrief,
            on_arrival_event=on_arrival_event,
            on_arrival_payload=on_arrival_payload,
        )

    def schedule_npc_follow(self, entity, target, distance_ft=5, speaker=None):
        return self.movement_manager.schedule_follow(entity, target, distance_ft=distance_ft, speaker=speaker)

    def get_npc_movement_task(self, entity):
        return self.movement_manager.get_task(entity)

    def cancel_npc_movement(self, entity, reason=None, mode=None):
        return self.movement_manager.cancel(entity, reason=reason, mode=mode)

    def complete_npc_movement(self, entity, status='completed', reason=None):
        return self.movement_manager.complete(entity, status=status, reason=reason)

    def record_npc_movement_history(self, entity, entry):
        return self.movement_manager.record_history(entity, entry)

    def _goal_worker(self):
        self.goal_manager.goal_worker()

    def commit_and_update(self, username, action, pov_entities):
        """Commit an action and update clients.

        Locking policy:
        - During an active battle, use the global game_state_lock to preserve strict ordering.
        - Outside battle, use a per-map lock for the map containing the acting entity
          so actions on different maps don't block each other.
        """
        # Snapshot basic references without holding locks for long
        battle = self.get_current_battle()
        acting_source = getattr(action, 'source', None)
        pov_entity = self._pov_entity_for_commit(username, pov_entities)
        pov_map = self.get_map_for_entity(pov_entity) if pov_entity is not None else None
        track_map_switch = (
            acting_source is not None
            and pov_entity is not None
            and getattr(acting_source, 'entity_uid', None) == getattr(pov_entity, 'entity_uid', None)
        )

        # An entity may be acting on the field while a battle is in progress
        # without itself being a participant of that battle (e.g. a player
        # whose PC was missed when initiative was rolled, or a DM-spawned
        # token that hasn't been added yet). Treat such non-participants as
        # out-of-battle so they can still move and act; a follow-up
        # ``loop_environment`` pass will lazy-add them on contact.
        from webapp.blueprints.helpers.action_utils import canonicalize_action_entities
        canonicalize_action_entities(self, action, battle=battle)
        treat_as_battle = bool(battle and action.source in battle.entities)
        suppress_ui = self.realtime_ui_suppressed()

        forced_manager = getattr(self, 'forced_conversation_manager', None)
        if forced_manager is not None and not treat_as_battle:
            blocked = forced_manager.block_action(action)
            if blocked:
                try:
                    self.socketio.emit('message', {
                        'type': 'message_toaster',
                        'message': blocked,
                    })
                except Exception:
                    pass
                return False

        legendary_turn = bool(treat_as_battle and battle.in_legendary_phase(action.source))
        if legendary_turn:
            from natural20.utils.legendary_actions import prepare_legendary_phase_action
            error = prepare_legendary_phase_action(action.source, battle, action)
            if error:
                if not suppress_ui:
                    self.socketio.emit('message', {'type': 'message_toaster', 'message': error})
                return {'status': 'error', 'message': error}

        if treat_as_battle:
            # Strict global locking while in battle
            with self.game_state_lock:
                try:
                    battle.action(action)
                    battle.commit(action)
                except BardicInspirationPrompt as exc:
                    from webapp.bardic_inspiration_prompt import emit_bardic_inspiration_prompt
                    self.waiting_for_bardic_inspiration = [exc, action]
                    emit_bardic_inspiration_prompt(self.socketio, exc)
                    return {'status': 'bardic_inspiration_prompt'}
                if battle.battle_ends():
                    self.end_current_battle()
            try:
                from webapp.pickpocket_reaction import process_pickpocket_reactions

                process_pickpocket_reactions(
                    self, action, in_combat=True, username=username,
                )
            except Exception as exc:
                self.logger.debug("Pickpocket reaction processing skipped: %s", exc)
        else:
            # More forgiving: lock only the acting entity's map
            action_battle_map = self.get_map_for_entity(action.source)
            map_lock = self._get_map_lock(action_battle_map)
            # Try a short timeout first to avoid long waits; then block if needed
            acquired = False
            try:
                acquired = map_lock.acquire(timeout=0.2)
            except Exception:
                # Fallback to blocking acquire if timeout not supported
                acquired = False
            if not acquired:
                map_lock.acquire()
            try:
                action.resolve(self.game_session, action_battle_map)
                # Use cached subclass list to avoid repeated runtime introspection.
                from natural20.action import get_action_subclasses
                _action_subclasses = get_action_subclasses()
                for item in list(action.result):
                    for klass in _action_subclasses:
                        other_results = klass.apply(None, item, session=self.game_session)
                        if isinstance(other_results, list):
                            action.result.extend(other_results)
                # handle game time for out of battle actions
                if isinstance(action.source, PlayerCharacter):
                    from natural20.actions.move_action import MoveAction
                    if isinstance(action, MoveAction):
                        distance_ft = self.move_action_distance_ft(action)
                        seconds = self.movement_travel_time_seconds(action.source, distance_ft)
                        self.increment_game_time(action.source, seconds=seconds)
                    else:
                        self.increment_game_time(action.source)
                # Fast-path: skip imports and reaction processing for irrelevant action types.
                # Each reaction module checks the action type internally, but the import
                # overhead and function entry cost adds up.  Guard with a quick name check.
                _action_name = type(action).__name__
                # NPC damage reactions only matter for attack/spell actions
                if _action_name not in ('MoveAction', 'InteractAction', 'PickpocketAction'):
                    try:
                        from webapp.npc_damage_reaction import process_out_of_combat_damage_reactions

                        process_out_of_combat_damage_reactions(self, action, in_combat=False)
                    except Exception as exc:
                        self.logger.debug("NPC out-of-combat hit reaction skipped: %s", exc)
                # Steal reactions only matter for InteractAction
                if _action_name == 'InteractAction':
                    try:
                        from webapp.steal_reaction import process_steal_reactions

                        process_steal_reactions(
                            self, action, in_combat=False, username=username,
                        )
                    except Exception as exc:
                        self.logger.debug("Steal reaction processing skipped: %s", exc)
                # Pickpocket reactions only matter for PickpocketAction
                if _action_name == 'PickpocketAction':
                    try:
                        from webapp.pickpocket_reaction import process_pickpocket_reactions

                        process_pickpocket_reactions(
                            self, action, in_combat=False, username=username,
                        )
                    except Exception as exc:
                        self.logger.debug("Pickpocket reaction processing skipped: %s", exc)
            finally:
                try:
                    map_lock.release()
                except Exception:
                    pass
            # If a battle is in progress but this entity is acting outside
            # of it, give the environment a chance to pull them in (e.g.
            # they walked into combat range).
            if battle:
                try:
                    self.loop_environment()
                except Exception:
                    pass
        # did the map change for the current pov?
        # NOTE: We intentionally defer emitting the `switch_map` notification
        # until *after* the move animation event below. The client uses an
        # EventQueue that processes events sequentially (awaiting each), so
        # emitting `switch_map` first would cause the destination map to be
        # rendered before the entity's movement animation plays — producing
        # a visible glitch when the move included a teleporter / stairs.
        # We still update the user's current map server-side immediately so
        # subsequent server-side queries reflect the new map.
        deferred_map_switch = None
        if track_map_switch:
            try:
                with self.game_state_lock:
                    new_battle_map = self.get_map_for_entity(pov_entity)
                    if pov_map is not None and new_battle_map is not None and new_battle_map != pov_map:
                        self.switch_map_for_user(username, new_battle_map.name)
                        entity_uid = getattr(pov_entity, 'entity_uid', None)
                        deferred_map_switch = {
                            'sids': list(self.username_to_sid.get(username, [])),
                            'payload': {'map': new_battle_map.name, 'entity_uid': _json_safe_uid(entity_uid)},
                        }
            except Exception:
                deferred_map_switch = None

        if treat_as_battle:
            if not suppress_ui:
                self.socketio.emit('message', {'type': 'move', 'message': { 'animation_log': _coalesce_animation_log(battle.get_animation_logs())}})
            battle.clear_animation_logs()
            # Emit deferred map switch after the move event so the client
            # finishes the movement animation before swapping the background.
            if deferred_map_switch and not suppress_ui:
                for sid in deferred_map_switch['sids']:
                    self.socketio.emit('message', {'type': 'switch_map', 'message': deferred_map_switch['payload']}, to=sid)
            # Lighting or visibility may have changed (e.g. fire damage lighting a fireplace)
            if not suppress_ui:
                self.socketio.emit('message', {'type': 'refresh_map'})
        else:
            # For out-of-battle movement, the route emits the real path-based
            # move event. Emitting an empty move here causes duplicate move
            # processing on the client and can race visual reconciliation.
            if action.action_type != 'move':
                if not suppress_ui:
                    self.socketio.emit('message', {'type': 'move', 'message': {'animation_log': []}})
            # check if spell and send animation log
            # Note: skip 'move' payloads here. action_animator returns a
            # {'type': 'move', 'token_image': ...} envelope (without a
            # 'message.animation_log') for move actions. The client's
            # processMoveEvent treats that as a legacy/no-animation move and
            # immediately refreshes tiles, which paints the entity at its
            # destination before the upcoming animated 'move' event runs —
            # producing a brief teleport-to-destination flash before the path
            # animation. Only emit non-move animator payloads here.
            try:
                _animator_payload = action_animator(action)
            except Exception:
                _animator_payload = None
            if _animator_payload and _animator_payload.get('type') != 'move' and not suppress_ui:
                self.socketio.emit('message', _animator_payload)
            # For non-battle moves, the *real* move animation event (with the
            # animation_log path) is emitted by the route handler (app.py)
            # AFTER this function returns. Stash any pending map switch so
            # the route can flush it once the animation event has been sent;
            # this preserves correct ordering on the client EventQueue and
            # avoids the destination map being rendered before the entity
            # finishes animating into the teleporter / stairs tile.
            if deferred_map_switch:
                try:
                    if not hasattr(self, '_pending_map_switches') or self._pending_map_switches is None:
                        self._pending_map_switches = {}
                    self._pending_map_switches[username] = deferred_map_switch
                except Exception:
                    pass
            # Check if the action affects visibility (doors, lighting, etc.) and emit refresh_map
            request_map_refresh = False
            if hasattr(action, 'result') and action.result:
                for result_item in action.result:
                    if (result_item.get('type') == 'interact' and
                        result_item.get('action') in ['open', 'close']):
                        # Door open/close affects line of sight, refresh the map
                        request_map_refresh = True
                    elif (result_item.get('type') == 'interact' and
                          isinstance(result_item.get('action'), str) and
                          result_item.get('action').endswith('_check')):
                        # Ability check interactions can reveal gated notes
                        # (e.g. notes with investigation_dc / perception_dc)
                        # — refresh the map so the new content is rendered.
                        request_map_refresh = True
                    elif result_item.get('type') == 'look':
                        # Look action can reveal notes and concealed entities, refresh the map
                        request_map_refresh = True
                    elif result_item.get("type") == "message":
                        if not suppress_ui:
                            _src = result_item.get("source")
                            _src_uid = getattr(_src, "entity_uid", None) if _src is not None else None
                            self.socketio.emit('message', {"type": "message_toaster", "source": _json_safe_uid(_src_uid), "message": result_item["message"], "position": result_item["position"]})
                    elif result_item.get('type') == 'message_spell':
                        if not suppress_ui:
                            from webapp.utils import emit_message_spell_link
                            emit_message_spell_link(self.socketio, result_item, suppress_ui=suppress_ui)
                    elif result_item.get("type") == "contextual_sound":
                        if not suppress_ui:
                            from webapp.blueprints.helpers.contextual_sound_ui import emit_contextual_sound_payload
                            emit_contextual_sound_payload(self.socketio, result_item, suppress_ui=suppress_ui)
                    elif result_item.get('type') == 'use_item':
                        request_map_refresh = True
                    elif result_item.get('refresh_map'):
                        request_map_refresh = True
            if request_map_refresh and not suppress_ui:
                self.socketio.emit('message', {'type': 'refresh_map'})
            aggressive_pairs = [
                (action.source, target)
                for target in self._hostile_targets_for_action(action)
            ]
            self.loop_environment(aggressive_pairs=aggressive_pairs)
            if (
                not treat_as_battle
                and getattr(action, 'action_type', None) == 'move'
                and getattr(action, 'source', None) is not None
            ):
                self._schedule_npc_follow_after_move(action.source)
                if forced_manager is not None:
                    try:
                        forced_manager.check_after_move(action.source)
                    except Exception as exc:
                        self.logger.debug("Forced conversation proximity check skipped: %s", exc)
        if not suppress_ui:
            self.socketio.emit('message', {'type': 'turn', 'message': { 'game_time': self.game_session.game_time }})

        # Any committed action can alter rendered state (tokens, visibility,
        # doors, effects, HP markers), so bump epoch once per successful commit.
        self.bump_render_epoch(username=username)

        if (
            legendary_turn
            and self.get_current_battle() is battle
            and battle.in_legendary_phase(action.source)
        ):
            # One legendary action per window (SRD); play it, then move on.
            self.push_end_of_turn_animations(battle)
            self.advance_legendary_phase(battle)

        if self.autosave:
            self.save_game_async()
        return True

    def _schedule_npc_follow_after_move(self, entity) -> None:
        """Run NPC follow hooks after the player move animation is queued."""
        movement_manager = getattr(self, 'movement_manager', None)
        if movement_manager is None or entity is None:
            return

        def _run() -> None:
            try:
                movement_manager.on_entity_moved(entity)
            except Exception as exc:
                self.logger.debug("NPC follow hook skipped: %s", exc)

        socketio = getattr(self, 'socketio', None)
        start_background = getattr(socketio, 'start_background_task', None) if socketio is not None else None
        if callable(start_background):
            try:
                start_background(_run)
                return
            except Exception:
                pass
        _run()

    def emit_out_of_combat_move_animation(self, action, username=None):
        """Broadcast a path-based move animation for exploration-mode moves."""
        if self.realtime_ui_suppressed():
            return False
        if getattr(action, 'action_type', None) != 'move':
            return False
        entries = build_move_animation_log_entries(
            action,
            fallback_path=getattr(action, 'move_path', None),
        )
        if not entries:
            return False
        entity = getattr(action, 'source', None)
        entity_uid = getattr(entity, 'entity_uid', None)
        if not entity_uid:
            return False
        first_path = entries[0][1]
        last_path = entries[-1][1]
        animation_log = _coalesce_animation_log(entries)
        self.socketio.emit('message', {
            'type': 'move',
            'message': {
                'from': first_path[0],
                'to': last_path[-1],
                'animation_log': animation_log,
            },
        })
        if username:
            try:
                self.flush_pending_map_switch(username)
            except Exception:
                pass
        return True

    def shutdown_save_worker(self, timeout: float = 5.0):
        """Request the background save worker to flush any pending saves and stop.
        Blocks up to `timeout` seconds waiting for the thread to exit.
        Safe to call multiple times.
        """
        try:
            # If thread already not alive, nothing to do
            if not getattr(self, "_save_thread", None) or not self._save_thread.is_alive():
                return
            # Enqueue sentinel; block if needed to preserve ordering
            self._save_queue.put(self._SAVE_SENTINEL)
            self._save_thread.join(timeout)
            if self._save_thread.is_alive():
                try:
                    self.logger.warning("Save worker did not stop within timeout; proceeding with shutdown.")
                except Exception:
                    pass
        except Exception:
            # Best-effort shutdown; never raise during app teardown
            pass
        try:
            self._goal_thread_stop.set()
            if getattr(self, '_goal_thread', None) and self._goal_thread.is_alive():
                self._goal_thread.join(timeout)
        except Exception:
            pass

    def flush_pending_map_switch(self, username):
        """Emit any deferred `switch_map` notification for the given user.

        Called by route handlers after they have emitted the actual move
        animation event, so the client EventQueue plays the animation before
        switching to the destination map.
        """
        try:
            pending = getattr(self, '_pending_map_switches', None)
            if not pending:
                return
            entry = pending.pop(username, None)
            if not entry:
                return
            for sid in entry.get('sids', []) or []:
                try:
                    self.socketio.emit('message', {'type': 'switch_map', 'message': entry['payload']}, to=sid)
                except Exception:
                    pass
        except Exception:
            pass

    def check_and_notify_map_change(self, pov_map, pov_entity, username):
        # Use the lock to make the operation atomic
        with self.game_state_lock:
            new_battle_map = self.get_map_for_entity(pov_entity)
            if pov_map is None:
                return
            if new_battle_map is None:
                return
            self.logger.info(f"pov_map: {pov_map.name} new_battle_map: {new_battle_map.name}")

            if new_battle_map != pov_map:
                self.switch_map_for_user(username, new_battle_map.name)
                sids = self.username_to_sid.get(username, [])
                # Include the entity_uid that triggered the switch so the
                # client can auto-center on it once the new map renders
                # (handles teleporters / stairs / ladders).
                entity_uid = getattr(pov_entity, 'entity_uid', None)
                payload = {'map': new_battle_map.name, 'entity_uid': _json_safe_uid(entity_uid)}
                for sid in sids:
                    self.socketio.emit('message', {'type': 'switch_map', 'message': payload}, to=sid)
