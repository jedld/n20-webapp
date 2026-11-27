"""DM-only mutation tools: HP/status/property edits, spawning, teleport."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from natural20.player_character import PlayerCharacter
from natural20.progression import award_xp_to_character, split_xp

from .context import MCPContext
from .tool_registry import ToolRegistry, tool, tool_error


def register(registry: ToolRegistry) -> None:
    @tool(
        registry,
        name='dm.set_hp',
        description="Set an entity's current HP to an absolute value. "
                    "Clamped to 0..max_hp. Use this when you need a precise value.",
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'hp'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'hp': {'type': 'integer', 'minimum': 0},
            },
        },
        category='dm',
    )
    def set_hp(context: MCPContext, entity_uid: str, hp: int):
        ent = _resolve(context, entity_uid)
        max_hp = ent.max_hp() if hasattr(ent, 'max_hp') else None
        clamped = max(0, int(hp))
        if max_hp is not None:
            clamped = min(clamped, int(max_hp))
        try:
            ent.attributes['hp'] = clamped
        except Exception as exc:
            raise tool_error(f'Failed to set hp: {exc}')
        if clamped == 0 and 'unconscious' not in (ent.statuses or []):
            try:
                if hasattr(ent, 'make_unconscious'):
                    ent.make_unconscious()
            except Exception:
                pass
        context.log_dm(f"DM set {ent.label()} HP to {clamped}")
        context.emit_refresh()
        return {'entity_uid': entity_uid, 'hp': clamped, 'max_hp': max_hp}

    @tool(
        registry,
        name='dm.heal',
        description='Heal an entity for a given amount (uses the engine heal() flow).',
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'amount'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'amount': {'type': 'integer', 'minimum': 1},
            },
        },
        category='dm',
    )
    def heal(context: MCPContext, entity_uid: str, amount: int):
        ent = _resolve(context, entity_uid)
        try:
            ent.heal(int(amount))
        except Exception as exc:
            raise tool_error(f'Failed to heal: {exc}')
        context.log_dm(f"DM healed {ent.label()} for {amount}")
        context.emit_refresh()
        return {'entity_uid': entity_uid, 'hp': ent.hp(), 'max_hp': ent.max_hp()}

    @tool(
        registry,
        name='dm.damage',
        description='Apply damage to an entity using the engine take_damage() flow.',
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'amount'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'amount': {'type': 'integer', 'minimum': 1},
                'damage_type': {'type': 'string', 'default': 'piercing'},
            },
        },
        category='dm',
    )
    def damage(context: MCPContext, entity_uid: str, amount: int,
               damage_type: str = 'piercing'):
        ent = _resolve(context, entity_uid)
        battle = _safe_call(lambda: context.current_game.get_current_battle())
        try:
            ent.take_damage(int(amount), battle=battle,
                            damage_type=damage_type, session=context.game_session)
        except Exception as exc:
            raise tool_error(f'Failed to apply damage: {exc}')
        context.log_dm(f"DM dealt {amount} {damage_type} to {ent.label()}")
        context.emit_refresh()
        return {'entity_uid': entity_uid, 'hp': ent.hp(), 'max_hp': ent.max_hp()}

    @tool(
        registry,
        name='dm.add_status',
        description='Add a status condition (e.g. "prone", "poisoned", "invisible") to an entity.',
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'status'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'status': {'type': 'string'},
            },
        },
        category='dm',
    )
    def add_status(context: MCPContext, entity_uid: str, status: str):
        ent = _resolve(context, entity_uid)
        st = str(status).strip().lower()
        try:
            statuses = ent.statuses
            if st not in statuses:
                statuses.append(st)
        except Exception as exc:
            raise tool_error(f'Failed to add status: {exc}')
        context.log_dm(f"DM added status '{st}' to {ent.label()}")
        context.emit_refresh()
        return {'entity_uid': entity_uid, 'statuses': list(ent.statuses)}

    @tool(
        registry,
        name='dm.remove_status',
        description='Remove a status condition from an entity.',
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'status'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'status': {'type': 'string'},
            },
        },
        category='dm',
    )
    def remove_status(context: MCPContext, entity_uid: str, status: str):
        ent = _resolve(context, entity_uid)
        st = str(status).strip().lower()
        try:
            if st in ent.statuses:
                ent.statuses.remove(st)
        except Exception as exc:
            raise tool_error(f'Failed to remove status: {exc}')
        context.log_dm(f"DM removed status '{st}' from {ent.label()}")
        context.emit_refresh()
        return {'entity_uid': entity_uid, 'statuses': list(ent.statuses)}

    @tool(
        registry,
        name='dm.set_property',
        description="Set or update a key inside an entity's `properties` dict. "
                    "Use sparingly: only safe for primitive (str/int/float/bool/list/dict) values.",
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'key', 'value'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'key': {'type': 'string'},
                'value': {},
            },
        },
        category='dm',
    )
    def set_property(context: MCPContext, entity_uid: str, key: str, value: Any):
        ent = _resolve(context, entity_uid)
        try:
            ent.properties[key] = value
        except Exception as exc:
            raise tool_error(f'Failed to set property: {exc}')
        context.log_dm(f"DM set {ent.label()}.properties[{key!r}]={value!r}")
        context.emit_refresh()
        return {'entity_uid': entity_uid, 'key': key, 'value': value}

    @tool(
        registry,
        name='dm.award_xp',
        description='Award D&D 5e 2014 XP to one, many, or all player characters.',
        input_schema={
            'type': 'object',
            'required': ['amount'],
            'properties': {
                'amount': {'type': 'integer', 'minimum': 0},
                'entity_uids': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Player character UIDs. Omit to award all PCs on loaded maps.',
                },
                'split': {'type': 'boolean', 'default': False},
                'source': {'type': 'string', 'default': 'mcp'},
                'reason': {'type': 'string'},
            },
        },
        category='dm',
    )
    def award_xp(context: MCPContext, amount: int, entity_uids: Optional[List[str]] = None,
                 split: bool = False, source: str = 'mcp', reason: Optional[str] = None):
        amount = int(amount or 0)
        if amount < 0:
            raise tool_error('XP amount cannot be negative')
        players = _resolve_player_characters(context, entity_uids)
        if not players:
            raise tool_error('No player characters found')
        allocations = split_xp(amount, players, split=bool(split))
        awards = []
        lock = getattr(context.current_game, 'game_state_lock', None)

        def _apply():
            for player in players:
                award = award_xp_to_character(
                    player,
                    allocations[player.entity_uid],
                    source=source or 'mcp',
                    reason=reason,
                )
                context.log_dm(f"DM awarded {award.amount} XP to {player.label()}")
                awards.append({
                    'entity_uid': player.entity_uid,
                    'name': player.label(),
                    'amount': award.amount,
                    'old_xp': award.old_xp,
                    'new_xp': award.new_xp,
                    'old_level': award.old_level,
                    'eligible_level': player.eligible_level(),
                    'levels_available': player.pending_level_ups(),
                    'progress': player.xp_progress(),
                })

        if lock is not None:
            with lock:
                _apply()
        else:
            _apply()
        socketio = context.socketio
        if socketio is not None:
            for award in awards:
                try:
                    socketio.emit('message', {'type': 'xp_awarded', 'message': award})
                    if award.get('levels_available', 0) > 0:
                        socketio.emit('message', {'type': 'level_up_available', 'message': award})
                except Exception:
                    pass
        context.emit_refresh()
        try:
            context.current_game.save_game_async()
        except Exception:
            pass
        return {'awards': awards}

    @tool(
        registry,
        name='dm.grant_level_up',
        description='Grant level-up permissions for DM-gated or event-gated progression.',
        input_schema={
            'type': 'object',
            'properties': {
                'entity_uids': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Player character UIDs. Omit to grant all PCs on loaded maps.',
                },
                'levels': {'type': 'integer', 'minimum': 1, 'default': 1},
                'reason': {'type': 'string'},
                'event': {'type': 'string', 'description': 'Configured event key for event-gated progression.'},
            },
        },
        category='dm',
    )
    def grant_level_up(context: MCPContext, entity_uids: Optional[List[str]] = None,
                       levels: int = 1, reason: Optional[str] = None,
                       event: Optional[str] = None):
        players = _resolve_player_characters(context, entity_uids)
        if not players:
            raise tool_error('No player characters found')
        payloads = []
        lock = getattr(context.current_game, 'game_state_lock', None)

        def _apply():
            for player in players:
                if event:
                    grants = player.grant_event_level_up(event, reason=reason)
                else:
                    grants = player.grant_level_up(source='dm', levels=levels, reason=reason)
                payload = {
                    'entity_uid': player.entity_uid,
                    'name': player.label(),
                    'event': event,
                    'grants': grants,
                    'eligible_level': player.eligible_level(),
                    'levels_available': player.pending_level_ups(),
                    'progress': player.xp_progress(),
                }
                payloads.append(payload)
                context.log_dm(f"DM granted {len(grants)} level-up permission(s) to {player.label()}")

        try:
            if lock is not None:
                with lock:
                    _apply()
            else:
                _apply()
        except ValueError as exc:
            raise tool_error(str(exc))
        socketio = context.socketio
        if socketio is not None:
            for payload in payloads:
                if payload.get('levels_available', 0) > 0:
                    try:
                        socketio.emit('message', {'type': 'level_up_available', 'message': payload})
                    except Exception:
                        pass
        context.emit_refresh()
        try:
            context.current_game.save_game_async()
        except Exception:
            pass
        return {'grants': payloads}

    @tool(
        registry,
        name='dm.spawn_npc',
        description='Spawn an NPC of `npc_type` at (x, y) on the given map. '
                    'Unique NPCs already on the same map set are moved instead of duplicated. '
                    'Set duplicate=true to force a fresh independent copy even for unique '
                    'NPCs (x/y optional; the first free square is chosen). '
                    'Returns the entity uid and whether the token was moved.',
        input_schema={
            'type': 'object',
            'required': ['npc_type'],
            'properties': {
                'npc_type': {'type': 'string'},
                'x': {'type': 'integer', 'minimum': 0},
                'y': {'type': 'integer', 'minimum': 0},
                'map_name': {'type': 'string'},
                'group': {'type': 'string', 'default': 'b'},
                'rand_life': {'type': 'boolean', 'default': True},
                'duplicate': {'type': 'boolean', 'default': False},
            },
        },
        category='dm',
    )
    def spawn_npc(context: MCPContext, npc_type: str, x: Optional[int] = None, y: Optional[int] = None,
                  map_name: Optional[str] = None, group: str = 'b',
                  rand_life: bool = True, duplicate: bool = False):
        from natural20.map_set import spawn_or_move_npc

        if not duplicate and (x is None or y is None):
            raise tool_error('x and y are required unless duplicate=true')

        battle_map = context.resolve_map(map_name)
        if battle_map is None:
            raise tool_error(f'Map not found: {map_name}')
        try:
            result = spawn_or_move_npc(
                context.game_session, npc_type, battle_map, x, y,
                group=group, rand_life=bool(rand_life), duplicate=bool(duplicate),
            )
        except FileNotFoundError:
            raise tool_error(f'NPC type "{npc_type}" not found')
        except ValueError as exc:
            raise tool_error(str(exc))
        except Exception as exc:
            raise tool_error(f'Failed to create NPC: {exc}')
        verb = 'moved' if result.get('moved') else 'spawned'
        placed_at = result.get('position') or [x, y]
        context.log_dm(f"DM {verb} {npc_type} at ({placed_at[0]},{placed_at[1]}) on {battle_map.name}")
        context.emit_refresh()
        return result

    @tool(
        registry,
        name='dm.spawn_npc_near',
        description='Spawn an NPC near a target entity (by uid or name). '
                    'Finds the first placeable nearby tile and returns the new entity uid.',
        input_schema={
            'type': 'object',
            'required': ['npc_type'],
            'oneOf': [
                {'required': ['target_entity_uid']},
                {'required': ['target_name']},
            ],
            'properties': {
                'npc_type': {'type': 'string'},
                'target_entity_uid': {'type': 'string'},
                'target_name': {'type': 'string'},
                'group': {'type': 'string', 'default': 'b'},
                'rand_life': {'type': 'boolean', 'default': True},
                'max_distance': {'type': 'integer', 'minimum': 1, 'maximum': 5, 'default': 1},
                'include_diagonals': {'type': 'boolean', 'default': True},
            },
        },
        category='dm',
    )
    def spawn_npc_near(context: MCPContext, npc_type: str,
                       target_entity_uid: Optional[str] = None,
                       target_name: Optional[str] = None,
                       group: str = 'b', rand_life: bool = True,
                       max_distance: int = 1,
                       include_diagonals: bool = True):
        from natural20.map_set import spawn_or_move_npc, unique_npc_uid_for_type

        target, battle_map, target_pos = _resolve_target_on_map(
            context, target_entity_uid=target_entity_uid, target_name=target_name
        )
        uid = unique_npc_uid_for_type(context.game_session, npc_type)
        existing = context.game_session.entity_by_uid(uid) if uid else None
        probe = existing
        if probe is None:
            try:
                probe = context.game_session.npc(npc_type, {'rand_life': bool(rand_life)})
            except FileNotFoundError:
                raise tool_error(f'NPC type "{npc_type}" not found')
            except Exception as exc:
                raise tool_error(f'Failed to create NPC: {exc}')

        battle = _safe_call(lambda: context.current_game.get_current_battle())
        found = _find_nearby_placeable_position(
            battle_map,
            probe,
            int(target_pos[0]),
            int(target_pos[1]),
            max_distance=max(1, int(max_distance)),
            include_diagonals=bool(include_diagonals),
            battle=battle,
        )
        if found is None:
            raise tool_error('No free placeable tile found near target entity')

        x, y = found
        if existing is not None:
            try:
                result = spawn_or_move_npc(
                    context.game_session, npc_type, battle_map, x, y,
                    group=group, rand_life=bool(rand_life),
                )
            except ValueError as exc:
                raise tool_error(str(exc))
        else:
            battle_map.add(probe, x, y, group=group)
            result = {
                'entity_uid': probe.entity_uid,
                'npc_type': npc_type,
                'map': battle_map.name,
                'position': [x, y],
                'unique': bool(uid),
                'moved': False,
                'from_map': None,
            }
        verb = 'moved' if result.get('moved') else 'spawned'
        context.log_dm(
            f"DM {verb} {npc_type} near {target.label()} at ({x},{y}) on {battle_map.name}"
        )
        context.emit_refresh()
        result['near'] = {'entity_uid': target.entity_uid, 'name': target.label()}
        return result

    @tool(
        registry,
        name='dm.library',
        description='Campaign-level spawner library edits. op=create_npc '
                    '(name, optional base_type/unique/description/max_hp/default_ac/speed) '
                    'adds <campaign>/npcs/<slug>.yml; op=delete_npc (npc_type) removes a '
                    'campaign-authored NPC type (bundled types and placed ones are refused); '
                    'op=set_unique_npc (npc_type, unique) marks a NPC type unique — one '
                    'instance per map set — by writing an authored entity_uid at the campaign '
                    'level; op=create_object (name, optional description/category/color/'
                    'passable/interactable) adds a campaign items/objects.yml entry; '
                    'op=delete_object (object_id) removes a campaign entry or tombstones a '
                    'bundled one for this campaign.',
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {'type': 'string', 'enum': [
                    'create_npc', 'delete_npc', 'set_unique_npc',
                    'create_object', 'delete_object']},
                'name': {'type': 'string'},
                'npc_type': {'type': 'string'},
                'base_type': {'type': 'string'},
                'unique': {'type': 'boolean', 'default': True},
                'object_id': {'type': 'string'},
                'description': {'type': 'string'},
                'category': {'type': 'string'},
                'color': {'type': 'string'},
                'passable': {'type': 'boolean'},
                'interactable': {'type': 'boolean'},
                'max_hp': {'type': 'integer'},
                'default_ac': {'type': 'integer'},
                'speed': {'type': 'integer'},
            },
        },
        category='dm',
    )
    def library(context: MCPContext, op: str, **kwargs):
        from webapp.blueprints.helpers import campaign_library
        from webapp.blueprints.helpers.runtime_state import get_app

        session_obj = context.game_session
        try:
            if op == 'create_npc':
                result = campaign_library.create_npc_type(
                    session_obj,
                    name=kwargs.get('name', ''),
                    base_type=kwargs.get('base_type'),
                    unique=bool(kwargs.get('unique')),
                    fields={k: kwargs.get(k) for k in
                            ('description', 'max_hp', 'default_ac', 'speed')
                            if kwargs.get(k) is not None},
                )
            elif op == 'delete_npc':
                result = campaign_library.delete_npc_type(session_obj, kwargs.get('npc_type', ''))
            elif op == 'set_unique_npc':
                result = campaign_library.set_npc_unique(
                    session_obj, kwargs.get('npc_type', ''), bool(kwargs.get('unique', True)))
            elif op == 'create_object':
                result = campaign_library.create_object_type(
                    session_obj,
                    name=kwargs.get('name', ''),
                    fields={k: kwargs.get(k) for k in
                            ('id', 'description', 'category', 'color', 'passable',
                             'opaque', 'interactable', 'default_ac', 'max_hp',
                             'item_class', 'token')
                            if kwargs.get(k) is not None},
                )
            elif op == 'delete_object':
                result = campaign_library.delete_object_type(session_obj, kwargs.get('object_id', ''))
            else:
                raise tool_error(f'Unknown library op: {op}')
        except campaign_library.SpawnerLibraryError as exc:
            raise tool_error(str(exc))

        campaign_library.invalidate_spawner_caches(_safe_call(get_app), session_obj)
        context.log_dm(f"DM campaign library {op}: {result}")
        return result

    @tool(
        registry,
        name='dm.spawn_object',
        description='Spawn an interactable object (catalog name from items/objects.yml) at (x, y).',
        input_schema={
            'type': 'object',
            'required': ['object_type', 'x', 'y'],
            'properties': {
                'object_type': {'type': 'string'},
                'x': {'type': 'integer', 'minimum': 0},
                'y': {'type': 'integer', 'minimum': 0},
                'map_name': {'type': 'string'},
            },
        },
        category='dm',
    )
    def spawn_object(context: MCPContext, object_type: str, x: int, y: int,
                     map_name: Optional[str] = None):
        battle_map = context.resolve_map(map_name)
        if battle_map is None:
            raise tool_error(f'Map not found: {map_name}')
        x, y = int(x), int(y)
        if x < 0 or y < 0 or x >= battle_map.size[0] or y >= battle_map.size[1]:
            raise tool_error('Position is outside map bounds')
        try:
            object_info = context.game_session.load_object(object_type)
        except Exception as exc:
            raise tool_error(f'Unknown object: {object_type} ({exc})')
        try:
            battle_map.place_object(object_info, x, y, object_meta={'name': object_type})
        except Exception as exc:
            raise tool_error(f'Failed to place object: {exc}')
        context.log_dm(f"DM spawned object {object_type} at ({x},{y}) on {battle_map.name}")
        context.emit_refresh()
        return {'object_type': object_type, 'position': [x, y], 'map': battle_map.name}

    @tool(
        registry,
        name='dm.spawn_object_near',
        description='Spawn an interactable object near a target entity (by uid or name). '
                    'Finds the first nearby tile where placement succeeds.',
        input_schema={
            'type': 'object',
            'required': ['object_type'],
            'oneOf': [
                {'required': ['target_entity_uid']},
                {'required': ['target_name']},
            ],
            'properties': {
                'object_type': {'type': 'string'},
                'target_entity_uid': {'type': 'string'},
                'target_name': {'type': 'string'},
                'max_distance': {'type': 'integer', 'minimum': 1, 'maximum': 5, 'default': 1},
                'include_diagonals': {'type': 'boolean', 'default': True},
            },
        },
        category='dm',
    )
    def spawn_object_near(context: MCPContext, object_type: str,
                          target_entity_uid: Optional[str] = None,
                          target_name: Optional[str] = None,
                          max_distance: int = 1,
                          include_diagonals: bool = True):
        target, battle_map, target_pos = _resolve_target_on_map(
            context, target_entity_uid=target_entity_uid, target_name=target_name
        )
        try:
            object_info = context.game_session.load_object(object_type)
        except Exception as exc:
            raise tool_error(f'Unknown object: {object_type} ({exc})')

        candidates = _nearby_offsets(
            max_distance=max(1, int(max_distance)),
            include_diagonals=bool(include_diagonals),
        )
        tx, ty = int(target_pos[0]), int(target_pos[1])
        found = None
        for dx, dy in candidates:
            x, y = tx + dx, ty + dy
            if x < 0 or y < 0 or x >= battle_map.size[0] or y >= battle_map.size[1]:
                continue
            if battle_map.entity_at(x, y):
                continue
            try:
                battle_map.place_object(object_info, x, y, object_meta={'name': object_type})
                found = (x, y)
                break
            except Exception:
                continue

        if found is None:
            raise tool_error('No suitable tile found to place object near target entity')

        x, y = found
        context.log_dm(
            f"DM spawned object {object_type} near {target.label()} at ({x},{y}) on {battle_map.name}"
        )
        context.emit_refresh()
        return {
            'object_type': object_type,
            'map': battle_map.name,
            'position': [x, y],
            'near': {'entity_uid': target.entity_uid, 'name': target.label()},
        }

    @tool(
        registry,
        name='dm.remove_entity',
        description='Remove an entity from its current map (does not delete its registry record). '
                    'Removals are recorded so they can be restored with dm.undo_remove_entity.',
        input_schema={
            'type': 'object',
            'required': ['entity_uid'],
            'properties': {'entity_uid': {'type': 'string'}},
        },
        category='dm',
    )
    def remove_entity(context: MCPContext, entity_uid: str):
        ent = _resolve(context, entity_uid)
        battle_map = context.map_for_entity(ent)
        if battle_map is None:
            raise tool_error('Entity is not currently on any map')
        try:
            from ..blueprints.helpers.entity_delete_undo import remove_entity_with_undo
            entry = remove_entity_with_undo(context.current_game, ent, battle_map, 'dm')
        except Exception as exc:
            raise tool_error(f'Failed to remove entity: {exc}')
        context.log_dm(f"DM removed {ent.label()} from {battle_map.name}")
        context.emit_refresh()
        return {'entity_uid': entity_uid, 'map': battle_map.name}

    @tool(
        registry,
        name='dm.undo_remove_entity',
        description='Undo the most recent dm.remove_entity: re-places the token on its original '
                    'square (nearest free square if occupied), restores battle membership and '
                    'initiative when it was removed mid-battle, and restores the DM POV if needed. '
                    'Pass entity_uid to only undo when it matches the most recent removal.',
        input_schema={
            'type': 'object',
            'properties': {'entity_uid': {'type': 'string'}},
        },
        category='dm',
    )
    def undo_remove_entity(context: MCPContext, entity_uid: Optional[str] = None):
        from ..blueprints.helpers.entity_delete_undo import undo_entity_delete
        try:
            payload, status = undo_entity_delete(context.current_game, context.game_session, 'dm', entity_uid)
        except Exception as exc:
            raise tool_error(f'Failed to undo entity removal: {exc}')
        if status != 200 or payload.get('status') != 'ok':
            raise tool_error(payload.get('error') or 'Nothing to undo')
        context.log_dm(f"DM undid removal of {payload['name']} ({payload['entity_uid']})")
        context.emit_refresh()
        return payload

    @tool(
        registry,
        name='dm.teleport',
        description='Teleport an entity to (x, y) on its current map (or `map_name` if given). '
                    'Bypasses pathing/initiative; use `actions.move` for in-battle movement.',
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'x', 'y'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'x': {'type': 'integer', 'minimum': 0},
                'y': {'type': 'integer', 'minimum': 0},
                'map_name': {'type': 'string'},
            },
        },
        category='dm',
    )
    def teleport(context: MCPContext, entity_uid: str, x: int, y: int,
                 map_name: Optional[str] = None):
        ent = _resolve(context, entity_uid)
        battle_map = context.resolve_map(map_name) if map_name else context.map_for_entity(ent)
        if battle_map is None:
            raise tool_error('Map not found for entity')
        x, y = int(x), int(y)
        if x < 0 or y < 0 or x >= battle_map.size[0] or y >= battle_map.size[1]:
            raise tool_error('Coordinates out of bounds')
        battle = _safe_call(lambda: context.current_game.get_current_battle())
        if not battle_map.placeable(ent, x, y, battle):
            raise tool_error('Target position is not placeable for this entity')
        try:
            current_pos = battle_map.entity_or_object_pos(ent)
        except Exception:
            current_pos = None
        # If the entity isn't on this map yet (cross-map teleport), add it.
        if ent not in (battle_map.entities or {}):
            current_map = context.map_for_entity(ent)
            if current_map is not None and current_map is not battle_map:
                session = context.game_session
                if session is not None and not session.same_map_set(current_map.name, battle_map.name):
                    raise tool_error(
                        'Cannot teleport across map sets; use Place Party or Activate Map Set'
                    )
                try:
                    current_map.remove(ent)
                except Exception:
                    pass
            battle_map.add(ent, x, y, group='b')
        else:
            battle_map.move_to(ent, x, y, battle)
        context.log_dm(
            f"DM teleported {ent.label()} from {list(current_pos) if current_pos else None}"
            f" to ({x},{y}) on {battle_map.name}"
        )
        context.emit_refresh()
        return {
            'entity_uid': entity_uid,
            'from': list(current_pos) if current_pos else None,
            'to': [x, y],
            'map': battle_map.name,
        }

    @tool(
        registry,
        name='dm.add_item',
        description='Add an item (weapon/equipment/object name) to an entity\'s inventory.',
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'item_name'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'item_name': {'type': 'string'},
                'qty': {'type': 'integer', 'minimum': 1, 'default': 1},
            },
        },
        category='dm',
    )
    def add_item(context: MCPContext, entity_uid: str, item_name: str, qty: int = 1):
        ent = _resolve(context, entity_uid)
        try:
            source = context.game_session.load_thing(item_name)
        except Exception:
            source = None
        if source is None:
            raise tool_error(f'Unknown item: {item_name}')
        try:
            ent.add_item(item_name, amount=int(qty))
        except Exception as exc:
            raise tool_error(f'Failed to add item: {exc}')
        context.log_dm(f"DM gave {qty} x {item_name} to {ent.label()}")
        context.emit_refresh()
        return {
            'entity_uid': entity_uid,
            'item_name': item_name,
            'qty': int((ent.inventory.get(item_name) or {}).get('qty', 0)),
        }

    @tool(
        registry,
        name='dm.remove_item',
        description='Remove (or decrement) an item from an entity\'s inventory.',
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'item_name'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'item_name': {'type': 'string'},
                'qty': {'type': 'integer', 'minimum': 1, 'default': 1},
                'all': {'type': 'boolean', 'default': False},
            },
        },
        category='dm',
    )
    def remove_item(context: MCPContext, entity_uid: str, item_name: str,
                    qty: int = 1, all: bool = False):  # noqa: A002 - matches public name
        ent = _resolve(context, entity_uid)
        inventory = getattr(ent, 'inventory', {}) or {}
        if item_name not in inventory:
            raise tool_error('Entity does not have that item')
        current_qty = int((inventory.get(item_name) or {}).get('qty', 0))
        drop = current_qty if all else max(1, int(qty))
        try:
            ent.remove_item(item_name, amount=drop)
        except Exception as exc:
            raise tool_error(f'Failed to remove item: {exc}')
        context.log_dm(f"DM removed {drop} x {item_name} from {ent.label()}")
        context.emit_refresh()
        return {
            'entity_uid': entity_uid,
            'item_name': item_name,
            'qty': int((ent.inventory.get(item_name) or {}).get('qty', 0)),
        }

    # ------------------------------------------------------------------
    # Consolidated DM tools (one tool per concept, op-discriminated)
    # ------------------------------------------------------------------

    @tool(
        registry,
        name='dm.equipment',
        description='Equip or unequip an item the entity already owns, or manage magic item attunement. '
                    '`op`: equip | unequip | attune | end_attunement | cancel_attunement; `item_id` is the inventory key. '
                    'Attunement changes wait for the next rest unless `force` is true (immediate, waives prerequisites; '
                    'the 3-item limit still applies).',
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'op', 'item_id'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'op': {'type': 'string', 'enum': ['equip', 'unequip', 'attune', 'end_attunement', 'cancel_attunement']},
                'item_id': {'type': 'string'},
                'force': {'type': 'boolean'},
            },
        },
        category='dm',
    )
    def equipment(context: MCPContext, entity_uid: str, op: str, item_id: str, force: bool = False):
        ent = _resolve(context, entity_uid)
        op = (op or '').lower()
        if op in ('attune', 'end_attunement', 'cancel_attunement'):
            from natural20.utils.attunement import (
                AttunementError, attunement_status, cancel_pending, request_attunement, request_end,
            )
            try:
                if op == 'attune':
                    result = request_attunement(ent, item_id, force=bool(force))
                elif op == 'end_attunement':
                    result = request_end(ent, item_id, force=bool(force))
                else:
                    result = cancel_pending(ent, item_id)
            except AttunementError as exc:
                raise tool_error(str(exc))
            context.log_dm(f"DM {op.replace('_', ' ')} {item_id} on {ent.label()} ({result['status']})")
            context.emit_refresh()
            status = attunement_status(ent)
            return {
                'entity_uid': entity_uid,
                'op': op,
                'item_id': item_id,
                'status': result['status'],
                'attuned': [row['item'] for row in status['items'] if row['attuned']],
                'pending': status['pending'],
                'limit': status['limit'],
            }
        if op not in ('equip', 'unequip'):
            raise tool_error("op must be 'equip', 'unequip', 'attune', 'end_attunement' or 'cancel_attunement'")
        try:
            (ent.equip if op == 'equip' else ent.unequip)(item_id)
        except Exception as exc:
            raise tool_error(f'Failed to {op}: {exc}')
        context.log_dm(f"DM {op}ped {item_id} on {ent.label()}")
        # Invalidate the /update render cache so the client re-renders the map
        # with the new lighting state (e.g. equipping a torch in a dark room).
        _recalc_lights(context)
        context.emit_refresh()
        return {
            'entity_uid': entity_uid,
            'op': op,
            'item_id': item_id,
            'equipped': list(_safe_call(lambda: ent.properties.get('equipped', [])) or []),
        }

    @tool(
        registry,
        name='dm.set_resource',
        description='Mutate a numeric resource on an entity. '
                    'resource_type: action | bonus_action | reaction | spell_slot | temp_hp | resource_pool | inspiration. '
                    'op: set | add | subtract. For spell_slot, `character_class` and `level` are required. '
                    'For resource_pool, `resource_name` is required (e.g. "superiority_dice"). '
                    'inspiration is a 0/1 Heroic Inspiration (2024) or Inspiration (2014) token. '
                    'Battle-required for action/bonus_action/reaction.',
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'resource_type', 'value'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'resource_type': {
                    'type': 'string',
                    'enum': ['action', 'bonus_action', 'reaction', 'spell_slot', 'temp_hp', 'resource_pool', 'inspiration'],
                },
                'value': {'type': 'integer', 'minimum': 0},
                'op': {'type': 'string', 'enum': ['set', 'add', 'subtract'], 'default': 'set'},
                'character_class': {'type': 'string',
                                     'description': 'Required for spell_slot (e.g. "wizard").'},
                'level': {'type': 'integer', 'minimum': 1, 'maximum': 9,
                           'description': 'Required for spell_slot.'},
                'resource_name': {'type': 'string',
                                  'description': 'Required for resource_pool.'},
            },
        },
        category='dm',
    )
    def set_resource(context: MCPContext, entity_uid: str, resource_type: str,
                     value: int, op: str = 'set',
                     character_class: Optional[str] = None,
                     level: Optional[int] = None,
                     resource_name: Optional[str] = None):
        ent = _resolve(context, entity_uid)
        op = (op or 'set').lower()
        if op not in ('set', 'add', 'subtract'):
            raise tool_error("op must be 'set', 'add' or 'subtract'")
        rt = (resource_type or '').lower()
        value = int(value)
        if value < 0:
            raise tool_error('value cannot be negative')

        def _apply(current: int, cap: Optional[int]) -> int:
            if op == 'set':
                nv = value
            elif op == 'add':
                nv = current + value
            else:
                nv = max(0, current - value)
            if cap is not None:
                nv = min(nv, cap)
            return nv

        if rt == 'temp_hp':
            current = int(getattr(ent, '_temp_hp', 0) or 0)
            new_value = _apply(current, None)
            ent._temp_hp = new_value
            result = {'entity_uid': entity_uid, 'resource_type': rt,
                      'old_value': current, 'new_value': new_value}
        elif rt == 'spell_slot':
            if not character_class or level is None:
                raise tool_error('character_class and level required for spell_slot')
            slots = getattr(ent, 'spell_slots', None) or {}
            if character_class not in slots:
                raise tool_error(f'Entity has no {character_class} slots')
            try:
                cap = int(ent.max_spell_slots(int(level), character_class))
            except Exception:
                cap = None
            current = int(slots[character_class].get(int(level), 0) or 0)
            new_value = _apply(current, cap)
            slots[character_class][int(level)] = new_value
            result = {'entity_uid': entity_uid, 'resource_type': rt,
                      'character_class': character_class, 'level': int(level),
                      'old_value': current, 'new_value': new_value, 'max_value': cap}
        elif rt == 'resource_pool':
            if not resource_name:
                raise tool_error('resource_name required for resource_pool')
            pool = ent.get_resource(resource_name) if hasattr(ent, 'get_resource') else None
            if not pool:
                raise tool_error(f'Entity has no resource pool {resource_name!r}')
            current = int(pool.current)
            new_value = _apply(current, int(pool.max_value))
            pool.current = new_value
            result = {'entity_uid': entity_uid, 'resource_type': rt,
                      'resource_name': resource_name, 'old_value': current,
                      'new_value': new_value, 'max_value': int(pool.max_value)}
        elif rt in ('inspiration', 'heroic_inspiration'):
            current = 1 if getattr(ent, 'has_inspiration', lambda: False)() else 0
            new_value = 1 if _apply(current, 1) else 0
            if hasattr(ent, 'set_inspiration'):
                ent.set_inspiration(bool(new_value))
            else:
                raise tool_error('Entity cannot hold inspiration')
            result = {'entity_uid': entity_uid, 'resource_type': 'inspiration',
                      'old_value': current, 'new_value': new_value,
                      'label': ent.inspiration_label() if hasattr(ent, 'inspiration_label') else 'Inspiration'}
        else:
            # action / bonus_action / reaction
            battle = _safe_call(lambda: context.current_game.get_current_battle())
            if battle is None:
                raise tool_error('No active battle')
            state = battle.entity_state_for(ent)
            if not state:
                raise tool_error('Entity is not in the current battle')
            current = int(state.get(rt, 0) or 0)
            new_value = _apply(current, 10)
            state[rt] = new_value
            result = {'entity_uid': entity_uid, 'resource_type': rt,
                      'old_value': current, 'new_value': new_value}

        context.log_dm(f"DM {op} {rt} on {ent.label()} -> {result['new_value']}")
        context.emit_refresh()
        return result

    @tool(
        registry,
        name='dm.battle_admin',
        description='Mid-battle initiative/group management. '
                    'op: add_combatant | remove_combatant | reorder | set_group | set_initiative | next_turn | pacing. '
                    '- add_combatant: adds entity to active battle; optional initiative int; '
                    'optional controller manual|ai|llm|hybrid (PCs default to manual when omitted).\n'
                    '- remove_combatant: drops entity from initiative without removing from map.\n'
                    '- reorder: takes `order` array of entity_uids.\n'
                    '- set_group: requires `group` (e.g. "a"/"b"/"c").\n'
                    '- set_initiative: requires `initiative` int; resorts turn order.\n'
                    '- next_turn: advances initiative (ends a held AI turn when one is waiting); '
                    'no entity_uid needed.\n'
                    '- pacing: read or set "DM ends AI turns" (`enabled`) — AI combatants wait for a '
                    'DM to end their turn (even if no DM is logged in yet) — and the pause between '
                    'consecutive AI turns in seconds (`ai_turn_delay`, lets browsers finish each '
                    "turn's animations; 0 disables it). Works without an active battle.",
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {'type': 'string',
                        'enum': ['add_combatant', 'remove_combatant', 'reorder',
                                 'set_group', 'set_initiative', 'next_turn', 'pacing']},
                'enabled': {'type': 'boolean',
                            'description': 'pacing: turn "DM ends AI turns" on/off (omit to read).'},
                'ai_turn_delay': {'type': 'number', 'minimum': 0,
                                  'description': 'pacing: seconds to pause after each AI turn so '
                                                 'clients can play its animations (0 disables; '
                                                 'omit to read the current value).'},
                'entity_uid': {'type': 'string'},
                'group': {'type': 'string'},
                'order': {'type': 'array', 'items': {'type': 'string'}},
                'initiative': {'type': 'integer'},
                'controller': {
                    'type': 'string',
                    'pattern': '^(manual|ai|llm|hybrid|agent:.+)$',
                    'description': 'Combat controller for add_combatant: manual | ai | llm | '
                                   'hybrid | agent:<name> (named .ai.yaml bundle). '
                                   'DM override; PCs default manual.',
                },
            },
        },
        category='dm',
    )
    def battle_admin(context: MCPContext, op: str,
                     entity_uid: Optional[str] = None,
                     group: Optional[str] = None,
                     order: Optional[List[str]] = None,
                     initiative: Optional[int] = None,
                     controller: Optional[str] = None,
                     enabled: Optional[bool] = None,
                     ai_turn_delay: Optional[float] = None):
        from webapp.battle_management import battle_admin as _battle_admin

        try:
            result = _battle_admin(
                context.current_game,
                context.game_session,
                op,
                entity_uid=entity_uid,
                group=group,
                order=order,
                initiative=initiative,
                controller=controller,
                enabled=enabled,
                ai_turn_delay=ai_turn_delay,
            )
        except ValueError as exc:
            raise tool_error(str(exc))
        context.log_dm(f"DM battle_admin {op}")
        context.emit_refresh()
        return result

    @tool(
        registry,
        name='dm.configure_group_relationship',
        description='Change how two campaign faction groups relate (game.yml groups). '
                    'relationship: enemy | ally | neutral | none.',
        input_schema={
            'type': 'object',
            'required': ['group', 'target_group', 'relationship'],
            'properties': {
                'group': {'type': 'string'},
                'target_group': {'type': 'string'},
                'relationship': {'type': 'string', 'enum': ['enemy', 'ally', 'neutral', 'none']},
            },
        },
        category='dm',
    )
    def configure_group_relationship(context: MCPContext, group: str, target_group: str, relationship: str):
        from webapp.battle_management import configure_group_relationship as _configure

        try:
            result = _configure(context.game_session, group, target_group, relationship)
        except ValueError as exc:
            raise tool_error(str(exc))
        context.log_dm(
            f"DM set group relationship {group} <-> {target_group} = {relationship}"
        )
        return result

    @tool(
        registry,
        name='dm.set_controller',
        description='Set the engine-side controller for an entity. '
                    'kind: manual (DM web controller) | ai (heuristic) | llm (LlmMcpController) | '
                    'hybrid (generative LLM strategist plans each turn, JEV executes) | '
                    'agent:<name> (a named bundle from the .ai.yaml agents: registry).',
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'kind'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'kind': {'type': 'string',
                         'pattern': '^(manual|ai|llm|hybrid|agent:.+)$',
                         'description': "Controller kind or 'agent:<name>' "
                                        '(see agent_names in the AI config).'},
            },
        },
        category='dm',
    )
    def set_controller(context: MCPContext, entity_uid: str, kind: str):
        ent = _resolve(context, entity_uid)
        cg = context.current_game
        battle = _safe_call(lambda: cg.get_current_battle())
        kind = (kind or '').lower()
        try:
            if kind == 'manual':
                from natural20.web.web_controller import WebController  # type: ignore
                ctrl = WebController(context.game_session, None)
                ctrl.add_user('dm')
                if hasattr(cg, 'web_controllers'):
                    cg.web_controllers[ent] = ctrl
            else:
                # 'ai' | 'llm' | 'hybrid' | 'agent:<name>' via the shared spec
                # dispatcher (per-NPC profile -> agent bundle -> combat
                # default -> battle provider -> DM provider).
                try:
                    from webapp.blueprints.helpers.runtime_state import get_llm_handler
                    _handler = get_llm_handler()
                except Exception:
                    _handler = None
                from webapp.ai_config import build_combat_controller_for_spec
                ctrl = build_combat_controller_for_spec(
                    context.game_session, _handler, ent, kind)
                if ctrl is None:
                    if kind.startswith('agent:'):
                        raise tool_error(
                            f"Unknown agent bundle {kind[6:]!r}; "
                            "see agent_names in the AI config")
                    raise tool_error("kind must be 'manual', 'ai', 'llm', 'hybrid' "
                                     "or 'agent:<name>'")
        except tool_error.__class__:
            raise
        except Exception as exc:
            raise tool_error(f'Failed to build controller: {exc}')
        try:
            ctrl.register_handlers_on(ent)
        except Exception:
            pass
        if battle is not None:
            try:
                battle.set_controller_for(ent, ctrl)
            except Exception as exc:
                raise tool_error(f'Failed to bind controller: {exc}')
        context.log_dm(f"DM set controller for {ent.label()} -> {kind}")
        return {'entity_uid': entity_uid, 'kind': kind}

    @tool(
        registry,
        name='dm.rest',
        description='Run a short or long rest for an entity. '
                    'Optional `force=true` to override combat restrictions (DM-only). '
                    'arcane_picks (wizard) and hit_die_picks (short rest) are spent in order.',
        input_schema={
            'type': 'object',
            'required': ['entity_uid', 'type'],
            'properties': {
                'entity_uid': {'type': 'string'},
                'type': {'type': 'string', 'enum': ['short', 'long']},
                'force': {'type': 'boolean', 'default': False},
                'arcane_picks': {'type': 'array', 'items': {'type': 'integer'}},
                'hit_die_picks': {'type': 'array', 'items': {'type': 'integer'}},
            },
        },
        category='dm',
    )
    def rest(context: MCPContext, entity_uid: str, type: str,  # noqa: A002
             force: bool = False,
             arcane_picks: Optional[List[int]] = None,
             hit_die_picks: Optional[List[int]] = None):
        from webapp.rest_execution import execute_entity_rest

        ent = _resolve(context, entity_uid)
        rest_type = (type or 'short').lower()
        if rest_type not in ('short', 'long'):
            raise tool_error("type must be 'short' or 'long'")

        outcome = execute_entity_rest(
            context.current_game,
            ent,
            rest_type,
            force=bool(force),
            arcane_picks=arcane_picks,
            hit_die_picks=hit_die_picks,
        )
        if outcome.get('success'):
            context.log_dm(f"DM ran {rest_type} rest for {ent.label()} (force={force})")
        else:
            raise tool_error(outcome.get('error') or 'Rest failed')
        return {
            'entity_uid': entity_uid,
            'type': rest_type,
            'forced': bool(force),
            'hp': _safe_call(lambda: ent.hp()),
            'max_hp': _safe_call(lambda: ent.max_hp()),
            'arcane_picks_consumed': outcome.get('arcane_picks_consumed', []),
            'hit_die_consumed': outcome.get('hit_die_consumed', []),
            'game_time': outcome.get('game_time'),
            'npc_simulation_scheduled': outcome.get('npc_simulation_scheduled', False),
        }

    @tool(
        registry,
        name='dm.npc_long_rest_simulation',
        description='Run background NPC long-rest simulation loops (conversations, routines, memories). '
                    'Does not advance game time or rest player characters. Map refreshes when complete.',
        input_schema={
            'type': 'object',
            'properties': {
                'force': {
                    'type': 'boolean',
                    'default': True,
                    'description': 'When true, run even if long_rest_npc_simulation.enabled is false in game.yml',
                },
            },
        },
        category='dm',
    )
    def npc_long_rest_simulation(context: MCPContext, force: bool = True):
        from webapp.blueprints.helpers.runtime_state import get_game_context_provider

        provider = get_game_context_provider()
        if provider is None:
            raise tool_error('Game context unavailable')
        result = provider.run_npc_long_rest_simulation(force=bool(force))
        if not result.get('success'):
            raise tool_error(result.get('error') or 'Failed to schedule NPC long rest simulation')
        context.log_dm('DM started NPC long rest simulation')
        return result

    @tool(
        registry,
        name='dm.save_load',
        description='Save/load/list campaign save files. '
                    'op: save (optional `name`) | load (optional `filename` or `index`) | list.',
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {'type': 'string', 'enum': ['save', 'load', 'list']},
                'name': {'type': 'string'},
                'filename': {'type': 'string'},
                'index': {'type': 'integer'},
            },
        },
        category='dm',
    )
    def save_load(context: MCPContext, op: str,
                  name: Optional[str] = None,
                  filename: Optional[str] = None,
                  index: Optional[int] = None):
        cg = context.current_game
        op = (op or '').lower()
        if op == 'save':
            try:
                cg.save_game_async(name=name)
            except Exception as exc:
                raise tool_error(f'save failed: {exc}')
            context.log_dm(f"DM queued save {name or '(auto)'}")
            return {'op': op, 'queued': True, 'name': name}
        if op == 'list':
            try:
                saves = cg.list_saves() if hasattr(cg, 'list_saves') else []
            except Exception as exc:
                raise tool_error(f'list failed: {exc}')
            return {'op': op, 'saves': list(saves or [])}
        if op == 'load':
            try:
                if filename:
                    cg.load_save(filename=filename)
                elif index is not None:
                    cg.load_save(index=int(index))
                else:
                    cg.load_save()
            except Exception as exc:
                raise tool_error(f'load failed: {exc}')
            try:
                cg.set_current_battle_map(cg.get_current_battle_map())
            except Exception:
                pass
            try:
                cg.refresh_client_map()
            except Exception:
                pass
            context.emit_refresh()
            context.log_dm(f"DM loaded save filename={filename} index={index}")
            return {'op': op, 'loaded': True, 'filename': filename, 'index': index}
        raise tool_error(f'Unknown op: {op}')

    @tool(
        registry,
        name='dm.sound',
        description='Soundtrack control. op: list | play (track_id) | volume (0-100) | seek (seconds).',
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {'type': 'string', 'enum': ['list', 'play', 'volume', 'seek']},
                'track_id': {'type': 'string'},
                'volume': {'type': 'integer', 'minimum': 0, 'maximum': 100},
                'time': {'type': 'integer', 'minimum': 0},
            },
        },
        category='dm',
    )
    def sound(context: MCPContext, op: str,
              track_id: Optional[str] = None,
              volume: Optional[int] = None,
              time: Optional[int] = None):  # noqa: A002
        cg = context.current_game
        op = (op or '').lower()
        if op == 'list':
            try:
                from webapp.app import SOUNDTRACKS  # lazy import to avoid cycle
            except Exception:
                SOUNDTRACKS = []
            current = getattr(cg, 'current_soundtrack', None)
            return {
                'op': op,
                'tracks': [{'id': t.get('name'), 'name': t.get('name'),
                              'file': t.get('file')} for t in SOUNDTRACKS],
                'current': current.get('name') if current else None,
            }
        if op == 'play':
            if not track_id:
                raise tool_error('track_id required')
            cg.play_soundtrack(track_id)
            context.log_dm(f"DM played soundtrack {track_id}")
            return {'op': op, 'track_id': track_id}
        if op == 'volume':
            if volume is None:
                raise tool_error('volume required')
            cg.set_volume(int(volume))
            return {'op': op, 'volume': int(volume)}
        if op == 'seek':
            if time is None:
                raise tool_error('time required')
            cg.seek_soundtrack(int(time))
            return {'op': op, 'time': int(time)}
        raise tool_error(f'Unknown op: {op}')

    @tool(
        registry,
        name='dm.effect',
        description='Broadcast a visual effect overlay (fog/rain/snow/custom). '
                    'action: start | stop | update. '
                    'scope: global (default) or map (optional `map_name`).',
        input_schema={
            'type': 'object',
            'required': ['effect', 'action'],
            'properties': {
                'effect': {'type': 'string',
                            'description': 'fog | rain | snow | map_default | <custom>'},
                'action': {'type': 'string', 'enum': ['start', 'stop', 'update']},
                'config': {'type': 'object'},
                'scope': {'type': 'string', 'enum': ['global', 'map'], 'default': 'global'},
                'map_name': {'type': 'string'},
            },
        },
        category='dm',
    )
    def effect(context: MCPContext, effect: str, action: str,
               config: Optional[Dict[str, Any]] = None,
               scope: str = 'global',
               map_name: Optional[str] = None):
        socketio = context.socketio
        if socketio is None:
            raise tool_error('socketio not available')
        cfg = dict(config or {})
        payload = {'effect': effect, 'action': action, 'config': cfg}
        try:
            socketio.emit('effect:set', payload)
        except Exception as exc:
            raise tool_error(f'emit failed: {exc}')
        # Persist into the webapp's effect-state caches if importable.
        try:
            from webapp.app import (active_effects, active_effects_map,
                                    LEVEL)  # lazy
            game_key = (getattr(context.game_session, 'root_path', None)
                        or LEVEL)
            if (scope or 'global').lower() == 'map':
                target_map = map_name
                if not target_map:
                    bm = context.resolve_map(None)
                    target_map = getattr(bm, 'name', None)
                bucket = active_effects_map.setdefault(game_key, {}).setdefault(target_map, {})
                if action == 'stop':
                    bucket.pop(effect, None)
                else:
                    bucket[effect] = {'effect': effect, 'action': 'start', 'config': cfg}
            else:
                if action == 'stop':
                    active_effects.get(game_key, {}).pop(effect, None)
                else:
                    active_effects.setdefault(game_key, {})[effect] = {
                        'effect': effect, 'action': 'start', 'config': cfg,
                    }
        except Exception:
            pass
        context.log_dm(f"DM effect {effect}/{action} scope={scope}")
        return {'effect': effect, 'action': action, 'scope': scope,
                'map_name': map_name, 'config': cfg}

    @tool(
        registry,
        name='dm.illumination',
        description='Get or set 3D global ambient illumination for a map '
                    '(0 = dark, 1 = full). Visual only — does not change fog or '
                    'Map.light_at. op: get | set | reset. Default is the map YAML '
                    'illumination. Optional map_name (current map when omitted).',
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {'type': 'string', 'enum': ['get', 'set', 'reset']},
                'illumination': {'type': 'number', 'minimum': 0, 'maximum': 1},
                'map_name': {'type': 'string'},
            },
        },
        category='dm',
    )
    def illumination(
        context: MCPContext,
        op: str,
        illumination: Optional[float] = None,
        map_name: Optional[str] = None,
    ):
        from natural20.web.vtt3d_illumination import apply_illumination, resolve_illumination

        op = (op or '').strip().lower()
        battle_map = context.resolve_map(map_name)
        if battle_map is None:
            raise tool_error('No map resolved for illumination')
        if op == 'get':
            return resolve_illumination(battle_map, context.game_session)
        if op == 'reset':
            lighting = apply_illumination(context.game_session, battle_map, None)
        elif op == 'set':
            if illumination is None:
                raise tool_error('illumination required for op=set')
            lighting = apply_illumination(context.game_session, battle_map, illumination)
        else:
            raise tool_error('op must be one of: get, set, reset')
        socketio = context.socketio
        if socketio is not None:
            event = {'type': 'vtt3d_illumination'}
            event.update(lighting)
            try:
                socketio.emit('message', event)
            except Exception as exc:
                raise tool_error(f'emit failed: {exc}')
        context.log_dm(
            f"DM 3D illumination {op} map={lighting.get('map')} "
            f"value={lighting.get('illumination')}"
        )
        return lighting

    @tool(
        registry,
        name='dm.ai_config',
        description='AI settings (.ai.yaml): providers, combat controllers, NPC conversation/task AI. '
                    'op: get | save (replace a whole layer with `config`) | set_npc (per-NPC override '
                    'keyed by entity_uid or npc type; omit `settings` to clear) | reload. '
                    'scope: campaign (default) | webapp. API keys are masked on read.',
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {'type': 'string', 'enum': ['get', 'save', 'set_npc', 'reload']},
                'scope': {'type': 'string', 'enum': ['campaign', 'webapp']},
                'config': {'type': 'object'},
                'key': {'type': 'string'},
                'settings': {'type': 'object'},
            },
        },
        category='dm',
    )
    def ai_config_tool(
        context: MCPContext,
        op: str,
        scope: Optional[str] = None,
        config: Optional[dict] = None,
        key: Optional[str] = None,
        settings: Optional[dict] = None,
    ):
        from webapp import ai_config as ai_cfg
        from webapp.blueprints.helpers.runtime_state import get_llm_handler

        op = (op or '').strip().lower()
        scope = scope or 'campaign'
        handler = get_llm_handler()
        game = context.current_game
        active = ai_cfg.get_ai_config()
        if active is None:
            raise tool_error('AI configuration is not initialized')
        try:
            if op == 'get':
                pass
            elif op == 'save':
                ai_cfg.save_and_apply(handler, game, scope, config or {})
            elif op == 'set_npc':
                ai_cfg.set_entity_override(active, scope, key, settings)
            elif op == 'reload':
                active.reload()
                ai_cfg.reapply(handler, game)
            else:
                raise tool_error('op must be one of: get, save, set_npc, reload')
        except ValueError as exc:
            raise tool_error(str(exc))
        if op != 'get':
            context.log_dm(f"DM AI config {op} scope={scope}")
        return ai_cfg.config_payload(game, handler)

    @tool(
        registry,
        name='dm.advance_time',
        description='Advance (or set) the in-game clock by `seconds`. '
                    'Use op="set" to assign an absolute value, "add" to advance.',
        input_schema={
            'type': 'object',
            'required': ['seconds'],
            'properties': {
                'seconds': {'type': 'integer'},
                'op': {'type': 'string', 'enum': ['set', 'add'], 'default': 'add'},
            },
        },
        category='dm',
    )
    def advance_time(context: MCPContext, seconds: int, op: str = 'add'):
        sess = context.game_session
        cg = context.current_game
        op = (op or 'add').lower()
        old_time = int(getattr(sess, 'game_time', 0) or 0)
        if op == 'set':
            try:
                sess.game_time = int(seconds)
            except Exception as exc:
                raise tool_error(f'failed to set time: {exc}')
        elif op == 'add':
            try:
                sess.increment_game_time(int(seconds))
            except Exception as exc:
                raise tool_error(f'failed to advance time: {exc}')
        else:
            raise tool_error("op must be 'set' or 'add'")
        new_time = int(sess.game_time)
        try:
            from webapp.blueprints.helpers.time_of_day import on_game_time_changed, time_of_day_turn_payload
            on_game_time_changed(cg, old_time=old_time, new_time=new_time)
            payload = {'game_time': new_time, 'time_of_day': time_of_day_turn_payload(cg)}
        except Exception:
            payload = {'game_time': new_time}
        try:
            context.socketio and context.socketio.emit(
                'message', {'type': 'turn', 'message': payload})
        except Exception:
            pass
        return {'op': op, 'seconds': int(seconds), 'game_time': new_time, 'time_of_day': payload.get('time_of_day')}

    @tool(
        registry,
        name='dm.npc_memory',
        description='Create, list, read, update, or delete persisted NPC memory items '
                    '(stored under the campaign npc_memories/ folder). '
                    'op: list | get | create | update | delete.',
        input_schema={
            'type': 'object',
            'required': ['op', 'entity_uid'],
            'properties': {
                'op': {'type': 'string', 'enum': ['list', 'get', 'create', 'update', 'delete']},
                'entity_uid': {'type': 'string'},
                'memory_id': {'type': 'string'},
                'title': {'type': 'string'},
                'summary': {'type': 'string'},
                'body': {'type': 'string'},
                'tags': {'type': 'array', 'items': {'type': 'string'}},
                'importance': {'type': 'integer', 'minimum': 1, 'maximum': 5},
                'query': {'type': 'string'},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
            },
        },
        category='dm',
    )
    def npc_memory(
        context: MCPContext,
        op: str,
        entity_uid: str,
        memory_id: Optional[str] = None,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        body: Optional[str] = None,
        tags: Optional[List[str]] = None,
        importance: Optional[int] = None,
        query: Optional[str] = None,
        limit: int = 20,
    ):
        store = getattr(context.current_game, 'npc_memory_store', None)
        if store is None:
            raise tool_error('NPC memory store is not available')

        op = (op or '').strip().lower()
        entity_uid = str(entity_uid or '').strip()
        if not entity_uid:
            raise tool_error('entity_uid is required')

        if op == 'list':
            items = store.list_summaries(entity_uid, limit=limit, query=query)
            return {'op': op, 'entity_uid': entity_uid, 'memories': items}

        if op == 'get':
            if not memory_id:
                raise tool_error('memory_id is required for get')
            item = store.get(entity_uid, memory_id)
            if item is None:
                raise tool_error(f'Memory not found: {memory_id}')
            return {'op': op, 'entity_uid': entity_uid, 'memory': item}

        if op == 'create':
            if not title:
                raise tool_error('title is required for create')
            item = store.create(
                entity_uid,
                title=title,
                summary=summary,
                body=body,
                tags=tags,
                importance=importance or 3,
                game_time=context.game_session.game_time,
                source='dm',
                created_by='dm_ai',
            )
            context.log_dm(f"DM created NPC memory for {entity_uid}: {item.get('id')}")
            return {'op': op, 'entity_uid': entity_uid, 'memory': item}

        if op == 'update':
            if not memory_id:
                raise tool_error('memory_id is required for update')
            item = store.update(
                entity_uid,
                memory_id,
                title=title,
                summary=summary,
                body=body,
                tags=tags,
                importance=importance,
                updated_by='dm_ai',
            )
            if item is None:
                raise tool_error(f'Memory not found: {memory_id}')
            context.log_dm(f"DM updated NPC memory {memory_id} for {entity_uid}")
            return {'op': op, 'entity_uid': entity_uid, 'memory': item}

        if op == 'delete':
            if not memory_id:
                raise tool_error('memory_id is required for delete')
            deleted = store.delete(entity_uid, memory_id)
            if not deleted:
                raise tool_error(f'Memory not found: {memory_id}')
            context.log_dm(f"DM deleted NPC memory {memory_id} for {entity_uid}")
            return {'op': op, 'entity_uid': entity_uid, 'memory_id': memory_id, 'deleted': True}

        raise tool_error("op must be one of: list, get, create, update, delete")

    @tool(
        registry,
        name='dm.map_landmark',
        description='List, add, update, or remove YAML map landmarks (points, areas, polygons, object refs) '
                    'used by NPC movement and LLM context. op: list | upsert | delete.',
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {'type': 'string', 'enum': ['list', 'upsert', 'delete']},
                'map_name': {'type': 'string'},
                'annotation': {'type': 'object'},
                'annotation_id': {'type': 'string'},
            },
        },
        category='dm',
    )
    def map_landmark(
        context: MCPContext,
        op: str,
        map_name: Optional[str] = None,
        annotation: Optional[Dict[str, Any]] = None,
        annotation_id: Optional[str] = None,
    ):
        from natural20.map_annotations import normalize_annotation, normalize_annotation_id
        from natural20.map_editor import (
            list_map_annotation_documents,
            remove_map_annotation,
            save_map_annotation,
        )

        op = (op or '').strip().lower()
        battle_map = context.resolve_map(map_name) if map_name else context.resolve_map(None)
        if battle_map is None:
            raise tool_error('No map resolved for landmark operation')
        map_key = battle_map.name

        if op == 'list':
            items = list_map_annotation_documents(context.game_session, map_key)
            return {'op': op, 'map': map_key, 'annotations': items}

        if op == 'upsert':
            if not isinstance(annotation, dict):
                raise tool_error('annotation object is required for upsert')
            try:
                saved = save_map_annotation(context.game_session, map_key, annotation)
            except ValueError as exc:
                raise tool_error(str(exc)) from exc
            except OSError as exc:
                raise tool_error(f'Failed to write map YAML: {exc}') from exc
            context.current_game.reload_map_by_name(map_key)
            context.log_dm(f"DM upserted map landmark {saved.get('id')} on {map_key}")
            context.emit_refresh()
            return {'op': op, 'map': map_key, 'annotation': saved}

        if op == 'delete':
            ann_id = normalize_annotation_id(annotation_id or (annotation or {}).get('id') or '')
            if not ann_id:
                raise tool_error('annotation_id is required for delete')
            try:
                remove_map_annotation(context.game_session, map_key, ann_id)
            except OSError as exc:
                raise tool_error(f'Failed to write map YAML: {exc}') from exc
            context.current_game.reload_map_by_name(map_key)
            context.log_dm(f"DM deleted map landmark {ann_id} on {map_key}")
            context.emit_refresh()
            return {'op': op, 'map': map_key, 'annotation_id': ann_id, 'deleted': True}

        raise tool_error("op must be one of: list, upsert, delete")

    @tool(
        registry,
        name='dm.note',
        description='List, add, update, move, or remove DM-only map pins. '
                    'These notes are invisible to PCs and NPCs (not landmarks). '
                    'op: list | upsert | delete | move.',
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {'type': 'string', 'enum': ['list', 'upsert', 'delete', 'move']},
                'map_name': {'type': 'string'},
                'note_id': {'type': 'string'},
                'x': {'type': 'integer'},
                'y': {'type': 'integer'},
                'text': {'type': 'string'},
                'title': {'type': 'string'},
            },
        },
        category='dm',
    )
    def note(
        context: MCPContext,
        op: str,
        map_name: Optional[str] = None,
        note_id: Optional[str] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        text: Optional[str] = None,
        title: Optional[str] = None,
    ):
        from natural20.dm_notes import delete_note, find_note, list_notes, move_note, upsert_note

        op = (op or '').strip().lower()
        battle_map = context.resolve_map(map_name) if map_name else context.resolve_map(None)
        map_key = map_name or (battle_map.name if battle_map is not None else None)
        if not map_key and op != 'delete':
            raise tool_error('No map resolved for DM note operation')

        def _notify(changed_map):
            socketio = context.socketio
            if socketio is None:
                return
            try:
                socketio.emit('message', {'type': 'dm_notes_changed', 'map': changed_map})
            except Exception:
                pass

        if op == 'list':
            notes = list_notes(context.game_session, map_key)
            return {'op': op, 'map': map_key, 'notes': notes}

        if op == 'upsert':
            try:
                saved = upsert_note(
                    context.game_session,
                    map_key,
                    x=x,
                    y=y,
                    text=text,
                    title=title,
                    note_id=note_id,
                )
            except ValueError as exc:
                raise tool_error(str(exc)) from exc
            context.log_dm(f"DM upserted map note on {map_key} at ({saved['x']},{saved['y']})")
            _notify(map_key)
            return {'op': op, 'map': map_key, 'note': saved}

        if op == 'move':
            try:
                saved = move_note(context.game_session, note_id, x, y, map_name=map_key)
            except ValueError as exc:
                raise tool_error(str(exc)) from exc
            context.log_dm(f"DM moved map note {saved['id']} to ({saved['x']},{saved['y']})")
            _notify(saved.get('map'))
            return {'op': op, 'map': saved.get('map'), 'note': saved}

        if op == 'delete':
            existing = find_note(context.game_session, note_id, map_key)
            if existing is None:
                raise tool_error('note_id is required and must match an existing note')
            delete_note(context.game_session, existing['id'], existing.get('map'))
            context.log_dm(f"DM deleted map note {existing['id']} on {existing.get('map')}")
            _notify(existing.get('map'))
            return {'op': op, 'map': existing.get('map'), 'note_id': existing['id'], 'deleted': True}

        raise tool_error('op must be one of: list, upsert, delete, move')

    @tool(
        registry,
        name='dm.token_model',
        description='3D VTT token models (HeroForge-style figures rendered on the /3d map '
                    'instead of the default token). Bind entity uid / NPC type / name to an '
                    'uploaded model file, or manage the model library. '
                    'op: list | assign | unassign | delete. Uploads go through '
                    'POST /dm/token_models/upload (auto-converts STL/Unity bundles to GLB).',
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {'type': 'string', 'enum': ['list', 'assign', 'unassign', 'delete']},
                'key': {'type': 'string',
                        'description': 'Entity uid, NPC type, or exact entity name to bind'},
                'src': {'type': 'string',
                        'description': 'Model path under <campaign>/assets '
                                       '(e.g. models/tokens/paladin.glb); extension may be '
                                       'omitted on assign and is resolved from the library'},
                'height': {'type': 'number', 'description': 'Figure height in feet'},
                'scale': {'type': 'number'},
                'y': {'type': 'number', 'description': 'Vertical offset in feet'},
                'rotate_y': {'type': 'number', 'description': 'Yaw in degrees'},
                'color': {'type': 'string', 'description': 'Tint for untextured figures, e.g. #8899ff'},
                'label': {'type': 'string'},
            },
        },
        category='dm',
    )
    def token_model(
        context: MCPContext,
        op: str,
        key: Optional[str] = None,
        src: Optional[str] = None,
        height: Optional[float] = None,
        scale: Optional[float] = None,
        y: Optional[float] = None,
        rotate_y: Optional[float] = None,
        color: Optional[str] = None,
        label: Optional[str] = None,
    ):
        from webapp.blueprints.helpers import token_models

        op = (op or '').strip().lower()
        session = context.game_session

        if op == 'list':
            return {
                'op': op,
                'library': token_models.list_library(session),
                'assignments': token_models.get_assignments(session),
            }

        if op == 'assign':
            if not (key or '').strip():
                raise tool_error('key is required (entity uid, NPC type, or name)')
            resolved = src
            if resolved and not str(resolved).startswith(('http://', 'https://')):
                from natural20.web.vtt3d_catalog import resolve_token_src

                resolved = resolve_token_src(
                    getattr(session, 'root_path', None), str(resolved)) or resolved
            spec = {
                name: value
                for name, value in (
                    ('src', resolved), ('height', height), ('scale', scale),
                    ('y', y), ('rotate_y', rotate_y), ('color', color), ('label', label),
                )
                if value is not None
            }
            saved = token_models.set_assignment(session, key, spec)
            if saved is None:
                raise tool_error('src is required and must resolve to a library file')
            context.log_dm(f"DM bound token model {saved.get('src')} to {key}")
            context.emit_refresh()
            return {'op': op, 'key': key.strip(), 'spec': saved}

        if op == 'unassign':
            if not token_models.remove_assignment(session, (key or '').strip()):
                raise tool_error('No token-model assignment for that key')
            context.log_dm(f"DM unbound token model from {key}")
            context.emit_refresh()
            return {'op': op, 'key': key.strip(), 'unassigned': True}

        if op == 'delete':
            result = token_models.delete_model(session, src)
            if result.get('error'):
                raise tool_error(result['error'])
            context.log_dm(f"DM deleted token model {result.get('deleted')}")
            context.emit_refresh()
            return {'op': op, **result}

        raise tool_error('op must be one of: list, assign, unassign, delete')

    @tool(
        registry,
        name='dm.notebook',
        description='Player/campaign notes and journals (not character journals, not map pins). '
                    'Stored in the campaign SQLite notebook. Default scope is campaign (DM). '
                    'op: list | get | search | create | update | delete | mkdir | move | share. '
                    'Use dm.note for map-square pins.',
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {
                    'type': 'string',
                    'enum': [
                        'list', 'get', 'search', 'create', 'update',
                        'delete', 'mkdir', 'move', 'share',
                    ],
                },
                'scope': {'type': 'string', 'enum': ['campaign', 'player'], 'default': 'campaign'},
                'owner': {'type': 'string', 'description': 'Player username when scope=player'},
                'item_id': {'type': 'string'},
                'folder_id': {'type': 'string'},
                'parent_id': {'type': 'string'},
                'title': {'type': 'string'},
                'name': {'type': 'string'},
                'content': {'type': 'string'},
                'query': {'type': 'string'},
                'limit': {'type': 'integer'},
                'kind': {'type': 'string', 'enum': ['note', 'folder', 'item']},
                'usernames': {'type': 'array', 'items': {'type': 'string'}},
                'all_players': {'type': 'boolean'},
            },
        },
        category='dm',
    )
    def notebook(
        context: MCPContext,
        op: str,
        scope: str = 'campaign',
        owner: Optional[str] = None,
        item_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        title: Optional[str] = None,
        name: Optional[str] = None,
        content: Optional[str] = None,
        query: Optional[str] = None,
        limit: Optional[int] = None,
        kind: Optional[str] = None,
        usernames: Optional[List[str]] = None,
        all_players: bool = False,
    ):
        from natural20.notebook_db import CAMPAIGN_OWNER, NotebookError

        db = getattr(context.current_game, 'notebook_db', None)
        if db is None:
            raise tool_error('Notebook database is not initialized')
        op = (op or '').strip().lower()
        scope_key = (scope or 'campaign').strip().lower()
        try:
            if scope_key == 'player':
                owner_id = str(owner or '').strip().lower()
                if not owner_id:
                    raise tool_error('owner (username) is required when scope=player')
                scope_key, owner_id = db.normalize_scope_owner('player', owner_id, is_dm=True)
            else:
                scope_key, owner_id = db.normalize_scope_owner('campaign', CAMPAIGN_OWNER, is_dm=True)
        except NotebookError as exc:
            raise tool_error(str(exc)) from exc

        def _notify():
            socketio = context.socketio
            if socketio is None:
                return
            try:
                socketio.emit('message', {
                    'type': 'notebook_changed',
                    'scope': scope_key,
                    'owner': owner_id,
                })
            except Exception:
                pass

        try:
            if op == 'list':
                tree = db.tree(scope_key, owner_id, sort='updated', order='desc')
                return {'op': op, 'scope': scope_key, 'owner': owner_id, **tree}
            if op == 'search':
                items = db.search(
                    scope_key,
                    owner_id,
                    query or '',
                    limit=int(limit or 40),
                )
                return {'op': op, 'scope': scope_key, 'items': items}
            if op == 'get':
                item = db.get_item(item_id, include_content=True) if item_id else None
                if item is None:
                    raise tool_error('item_id not found')
                return {'op': op, 'item': item}
            if op == 'create':
                item = db.create_note(
                    scope_key,
                    owner_id,
                    title or name or 'Untitled note',
                    content=content or '',
                    folder_id=folder_id,
                    created_by='dm',
                )
                context.log_dm(f"DM created campaign note {item.get('title')}")
                _notify()
                return {'op': op, 'item': item}
            if op == 'update':
                saved = db.update_item(
                    item_id,
                    title=title,
                    content=content,
                    folder_id=folder_id,
                    move_folder=folder_id is not None,
                )
                context.log_dm(f"DM updated notebook item {saved.get('id')}")
                _notify()
                return {'op': op, 'item': saved}
            if op == 'delete':
                node_kind = (kind or 'item').strip().lower()
                if node_kind == 'folder':
                    ok = db.delete_folder(item_id or folder_id)
                else:
                    ok = db.delete_item(item_id)
                if not ok:
                    raise tool_error('not found')
                context.log_dm(f"DM deleted notebook {node_kind} {item_id or folder_id}")
                _notify()
                return {'op': op, 'deleted': True}
            if op == 'mkdir':
                folder = db.create_folder(
                    scope_key,
                    owner_id,
                    name or title or 'Folder',
                    parent_id=parent_id or folder_id,
                )
                context.log_dm(f"DM created notebook folder {folder.get('name')}")
                _notify()
                return {'op': op, 'folder': folder}
            if op == 'move':
                dest = parent_id if parent_id is not None else folder_id
                node = item_id
                if (kind or 'item') == 'folder' and not node:
                    node = folder_id
                    dest = parent_id
                saved = db.move_node(
                    kind or 'item',
                    node,
                    folder_id=dest,
                )
                _notify()
                return {'op': op, 'node': saved}
            if op == 'share':
                recipients = list(usernames or [])
                if all_players:
                    recipients.append('*')
                shares = db.create_shares(item_id, 'dm', recipients)
                item = db.get_item(item_id, include_content=False)
                socketio = context.socketio
                sid_map = getattr(context.current_game, 'username_to_sid', {}) or {}
                if socketio is not None and item is not None:
                    for share in shares:
                        event = {
                            'type': 'notebook_share',
                            'share_id': share['id'],
                            'from_username': share['from_username'],
                            'title': item.get('title'),
                            'kind': item.get('kind'),
                            'is_image': bool(item.get('is_image')),
                        }
                        target = share['to_username']
                        names = list(sid_map.keys()) if target == '*' else [target]
                        for uname in names:
                            for sid in sid_map.get(uname, []) or []:
                                try:
                                    socketio.emit('message', event, to=sid)
                                except Exception:
                                    pass
                context.log_dm(f"DM shared notebook item {item_id}")
                return {'op': op, 'shares': shares}
        except NotebookError as exc:
            raise tool_error(str(exc)) from exc

        raise tool_error(
            'op must be one of: list, get, search, create, update, delete, mkdir, move, share'
        )

    @tool(
        registry,
        name='dm.user_admin',
        description='Manage campaign logins and player-character assignments. '
                    'op: list | create | update | delete | assign_character | unassign_character. '
                    'Persists logins and default_controllers to campaign index.json.',
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {
                    'type': 'string',
                    'enum': [
                        'list',
                        'create',
                        'update',
                        'delete',
                        'assign_character',
                        'unassign_character',
                    ],
                },
                'username': {'type': 'string'},
                'password': {'type': 'string'},
                'roles': {
                    'type': 'array',
                    'items': {'type': 'string', 'enum': ['player', 'dm']},
                },
                'character_uid': {'type': 'string'},
                'spawn': {'type': 'boolean', 'default': True},
            },
        },
        category='dm',
    )
    def user_admin(
        context: MCPContext,
        op: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        roles: Optional[List[str]] = None,
        character_uid: Optional[str] = None,
        spawn: bool = True,
    ):
        from webapp.user_management import UserManagementError, user_admin as _user_admin

        try:
            result = _user_admin(
                op,
                username=username,
                password=password,
                roles=roles,
                character_uid=character_uid,
                spawn=spawn,
            )
        except UserManagementError as exc:
            raise tool_error(str(exc)) from exc
        context.log_dm(f"DM user_admin {op} username={username or ''} character={character_uid or ''}")
        context.emit_refresh()
        return result

    @tool(
        registry,
        name='dm.map_set',
        description='Manage campaign map sets (party POV worlds). '
                    'op: list | activate | create | assign_map | place_party. '
                    'create and assign_map persist to campaign game.yml. '
                    'activate jumps player cameras to their tokens in the set. '
                    'place_party creates PC instances on a map without activating.',
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {
                    'type': 'string',
                    'enum': ['list', 'activate', 'create', 'assign_map', 'place_party'],
                },
                'map_set': {'type': 'string'},
                'label': {'type': 'string'},
                'map_name': {'type': 'string'},
                'x': {'type': 'integer'},
                'y': {'type': 'integer'},
            },
        },
        category='dm',
    )
    def map_set(context: MCPContext, op: str, map_set: Optional[str] = None,
                label: Optional[str] = None, map_name: Optional[str] = None,
                x: Optional[int] = None, y: Optional[int] = None):
        from webapp.blueprints.helpers.map_sets import (
            activate_map_set,
            assign_map_to_set,
            create_map_set,
            map_sets_payload,
            place_party,
        )
        cg = context.current_game
        if cg is None:
            raise tool_error('No game loaded')
        try:
            if op == 'list':
                return map_sets_payload(cg)
            if op == 'activate':
                if not map_set:
                    raise tool_error('map_set is required')
                result = activate_map_set(cg, map_set)
                context.log_dm(f"DM activated map set {map_set}")
                context.emit_refresh()
                return result
            if op == 'create':
                if not map_set:
                    raise tool_error('map_set is required')
                result = create_map_set(cg, map_set, label=label)
                context.log_dm(f"DM created map set {result.get('id')}")
                return result
            if op == 'assign_map':
                if not map_name or not map_set:
                    raise tool_error('map_name and map_set are required')
                result = assign_map_to_set(cg, map_name, map_set)
                context.log_dm(f"DM assigned map {map_name} to set {map_set}")
                context.emit_refresh()
                return result
            if op == 'place_party':
                if not map_name:
                    raise tool_error('map_name is required')
                result = place_party(cg, map_name, x=x, y=y)
                context.log_dm(f"DM placed party on {map_name}")
                context.emit_refresh()
                return result
        except ValueError as exc:
            raise tool_error(str(exc)) from exc
        raise tool_error('op must be one of: list, activate, create, assign_map, place_party')

    @tool(
        registry,
        name='dm.sidekick',
        description='Manage party sidekick NPCs. '
                    'op: list | join | leave | assign_owner | auto_follow. '
                    'join converts an NPC into an in-party sidekick (player-controlled). '
                    'leave drops them back to a regular AI NPC (hostile=true for enemies). '
                    'assign_owner sets player usernames who control movement/actions. '
                    'auto_follow toggles out-of-combat following of the focused (POV) '
                    'character (enabled=true/false).',
        input_schema={
            'type': 'object',
            'required': ['op'],
            'properties': {
                'op': {
                    'type': 'string',
                    'enum': ['list', 'join', 'leave', 'assign_owner', 'auto_follow'],
                },
                'entity_uid': {'type': 'string'},
                'owners': {
                    'type': 'array',
                    'items': {'type': 'string'},
                },
                'owner': {'type': 'string'},
                'hostile': {'type': 'boolean'},
                'sidekick_class': {'type': 'string'},
                'sidekick_level': {'type': 'integer'},
                'role': {'type': 'string'},
                'force': {'type': 'boolean'},
                'enabled': {'type': 'boolean', 'description': 'auto_follow: enable (true) or disable (false) auto-follow'},
            },
        },
        category='dm',
    )
    def sidekick(context: MCPContext, op: str, entity_uid: Optional[str] = None,
                 owners: Optional[List[str]] = None, owner: Optional[str] = None,
                 hostile: bool = False, sidekick_class: Optional[str] = None,
                 sidekick_level: Optional[int] = None, role: Optional[str] = None,
                 force: bool = False, enabled: bool = True):
        from natural20.sidekick import (
            is_auto_follow_sidekick,
            is_in_party_sidekick,
            iter_in_party_sidekicks,
            sidekick_records,
            sidekick_owners,
        )
        from webapp.blueprints.helpers.sidekick_control import (
            assign_owners_and_control,
            join_party_and_control,
            leave_party_and_control,
            set_auto_follow_and_control,
        )
        session = context.game_session
        if session is None:
            raise tool_error('No game loaded')
        if op == 'list':
            listed = []
            for entity in iter_in_party_sidekicks(session):
                listed.append({
                    'entity_uid': getattr(entity, 'entity_uid', None),
                    'name': entity.label() if hasattr(entity, 'label') else str(entity),
                    'owners': sidekick_owners(session, entity),
                    'group': getattr(entity, 'group', None),
                    'auto_follow': is_auto_follow_sidekick(session, entity),
                })
            return {'sidekicks': listed, 'records': sidekick_records(session)}
        if not entity_uid:
            raise tool_error('entity_uid is required')
        ent = _resolve(context, entity_uid)
        owner_list = list(owners or [])
        if owner and owner not in owner_list:
            owner_list.append(owner)
        try:
            if op == 'join':
                rec = join_party_and_control(
                    ent,
                    owner_list,
                    sidekick_class=sidekick_class,
                    sidekick_level=sidekick_level,
                    role=role,
                )
                context.log_dm(f"DM joined {ent.label()} to the party")
                context.emit_refresh()
                return {'entity_uid': entity_uid, 'record': rec}
            if op == 'leave':
                rec = leave_party_and_control(ent, hostile=bool(hostile))
                context.log_dm(f"DM removed {ent.label()} from the party")
                context.emit_refresh()
                return {'entity_uid': entity_uid, 'record': rec}
            if op == 'assign_owner':
                rec = assign_owners_and_control(ent, owner_list)
                context.log_dm(f"DM assigned owners {owner_list} to {ent.label()}")
                context.emit_refresh()
                return {'entity_uid': entity_uid, 'record': rec}
            if op == 'auto_follow':
                if not is_in_party_sidekick(session, ent):
                    raise tool_error('entity is not an in-party sidekick')
                rec = set_auto_follow_and_control(ent, bool(enabled))
                context.log_dm(f"DM set auto-follow {'on' if enabled else 'off'} for {ent.label()}")
                context.emit_refresh()
                return {'entity_uid': entity_uid, 'record': rec}
        except ValueError as exc:
            raise tool_error(str(exc)) from exc
        raise tool_error('op must be one of: list, join, leave, assign_owner, auto_follow')


def _recalc_lights(context: MCPContext) -> None:
    """Invalidate the /update render cache for the DM user.

    Entity-borne lighting (equipped torches, the Light cantrip, ...) is
    computed dynamically by ``Map.light_at``, so the only thing that must be
    refreshed here is the render epoch.  Without it, the client's ``/update``
    request hits the cached pre-equip tile HTML and the map does not pick up
    the new light source until a full page reload.
    """
    cg = context.current_game
    if cg is None:
        return
    recalc = getattr(cg, 'recalc_lights_for_user', None)
    if recalc is None:
        return
    try:
        recalc('dm')
    except Exception:
        pass


def _resolve(context: MCPContext, entity_uid: str):
    ent = context.resolve_entity(entity_uid)
    if ent is None:
        raise tool_error(f'Entity not found: {entity_uid}')
    return ent


def _resolve_player_characters(context: MCPContext, entity_uids: Optional[List[str]] = None):
    if entity_uids:
        players = []
        for entity_uid in entity_uids:
            ent = _resolve(context, entity_uid)
            if isinstance(ent, PlayerCharacter):
                players.append(ent)
        return players
    players = []
    seen = set()
    for battle_map in (getattr(context.current_game, 'maps', {}) or {}).values():
        for ent in list(getattr(battle_map, 'entities', []) or []):
            if isinstance(ent, PlayerCharacter) and ent.entity_uid not in seen:
                seen.add(ent.entity_uid)
                players.append(ent)
    return players


def _resolve_target_on_map(context: MCPContext,
                           target_entity_uid: Optional[str] = None,
                           target_name: Optional[str] = None):
    if target_entity_uid:
        target = _resolve(context, target_entity_uid)
    elif target_name:
        target = _resolve_by_name(context, target_name)
    else:
        raise tool_error('Provide target_entity_uid or target_name')

    battle_map = context.map_for_entity(target)
    if battle_map is None:
        raise tool_error('Target entity is not currently on any map')
    pos = battle_map.entity_or_object_pos(target)
    if pos is None:
        raise tool_error('Target entity position is unknown on current map')
    return target, battle_map, pos


def _resolve_by_name(context: MCPContext, target_name: str):
    current_map = context.resolve_map(None)
    if current_map is None:
        raise tool_error('No active map available')

    query = str(target_name).strip().lower()
    if not query:
        raise tool_error('target_name cannot be empty')

    exact = []
    contains = []
    for ent in (current_map.entities or {}).keys():
        label = str(_safe_call(lambda: ent.label()) or '').strip()
        ent_name = str(getattr(ent, 'name', '') or '').strip()
        hay = [label.lower(), ent_name.lower()]
        if query in hay:
            exact.append(ent)
        elif any(query in part for part in hay if part):
            contains.append(ent)

    matches = exact if exact else contains
    if not matches:
        raise tool_error(f'No entity named "{target_name}" found on current map')
    if len(matches) > 1:
        names = ', '.join(str(_safe_call(lambda: e.label()) or e.entity_uid) for e in matches[:5])
        raise tool_error(f'Ambiguous target_name "{target_name}"; matches: {names}')
    return matches[0]


def _nearby_offsets(max_distance: int = 1, include_diagonals: bool = True):
    offsets = []
    max_distance = max(1, int(max_distance))
    for dist in range(1, max_distance + 1):
        if include_diagonals:
            for dy in range(-dist, dist + 1):
                for dx in range(-dist, dist + 1):
                    if dx == 0 and dy == 0:
                        continue
                    if max(abs(dx), abs(dy)) != dist:
                        continue
                    offsets.append((dx, dy))
        else:
            offsets.extend([(dist, 0), (0, dist), (-dist, 0), (0, -dist)])
    offsets.sort(key=lambda t: (abs(t[0]) + abs(t[1]), abs(t[1]), abs(t[0]), t[1], t[0]))
    return offsets


def _find_nearby_placeable_position(battle_map, entity, tx: int, ty: int,
                                    max_distance: int = 1,
                                    include_diagonals: bool = True,
                                    battle=None):
    for dx, dy in _nearby_offsets(max_distance=max_distance, include_diagonals=include_diagonals):
        x, y = tx + dx, ty + dy
        if x < 0 or y < 0 or x >= battle_map.size[0] or y >= battle_map.size[1]:
            continue
        if battle_map.placeable(entity, x, y, battle):
            return (x, y)
    return None


def _safe_call(fn):
    try:
        return fn()
    except Exception:
        return None
