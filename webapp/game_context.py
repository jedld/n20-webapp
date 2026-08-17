"""
Game Context Functions for LLM RAG System

This module provides functions that can be called by the LLM to retrieve
current game state information for the VTT system.
"""

import logging
from typing import Dict, List, Any, Optional, Tuple, Iterator

from natural20.player_character import PlayerCharacter
from natural20.utils.conversation import entity_label
from natural20.utils.character_profile import extract_profile, profile_for_entity
from webapp.rest_execution import execute_entity_rest

logger = logging.getLogger(__name__)


class GameContextProvider:
    """Provides game context information for the LLM RAG system."""

    def __init__(self, game_session, current_game):
        self.game_session = game_session
        self.current_game = current_game
        self._entity_bundle_cache: Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]] = None
        self._context_username: Optional[str] = None

    def set_chat_username(self, username: Optional[str]) -> None:
        """Prefer this user's map when building DM assistant context."""
        self._context_username = (str(username).strip() or None) if username else None

    def begin_chat_context_snapshot(self) -> None:
        """Invalidate cached entity lists before a new DM chat context build."""
        self._entity_bundle_cache = None

    def _primary_battle_map(self):
        if self._context_username:
            try:
                return self.current_game.get_map_for_user(self._context_username)
            except Exception:
                pass
        battle = self.current_game.get_current_battle()
        if battle is not None:
            battle_map = getattr(battle, 'map', None)
            if battle_map is not None:
                return battle_map
        return self.current_game.get_current_battle_map()

    def _iter_campaign_entities(self) -> Iterator[Tuple[str, Any, Any]]:
        maps = getattr(self.current_game, 'maps', None) or {}
        for map_name, battle_map in maps.items():
            try:
                entities = list(battle_map.entities)
            except Exception:
                continue
            for entity in entities:
                yield map_name, battle_map, entity

    def _find_entity_by_ref(self, entity_ref: str) -> Tuple[Optional[Any], Optional[Any], Optional[str]]:
        ref = str(entity_ref or '').strip()
        if not ref:
            return None, None, 'Entity reference is required'

        try:
            by_uid = self.current_game.get_entity_by_uid(ref)
            if by_uid is not None:
                return by_uid, self.current_game.get_map_for_entity(by_uid), None
        except Exception:
            pass

        ref_lower = ref.lower()
        exact_matches = []
        partial_matches = []
        for map_name, battle_map, entity in self._iter_campaign_entities():
            label = entity_label(entity).lower()
            uid = str(getattr(entity, 'entity_uid', '') or '')
            keys = {label, uid.lower()}
            if ref_lower in keys:
                exact_matches.append((entity, battle_map, map_name))
                continue
            if ref_lower in label or ref_lower in uid.lower():
                partial_matches.append((entity, battle_map, map_name))

        matches = exact_matches or partial_matches
        if not matches:
            return None, None, f"Entity '{entity_ref}' not found"
        if len(matches) > 1 and not exact_matches:
            names = sorted({
                (entity.label() if hasattr(entity, 'label') else str(entity))
                for entity, _battle_map, _map_name in matches
            })
            return None, None, f"Ambiguous entity reference '{entity_ref}'; matches: {', '.join(names)}"
        entity, battle_map, _map_name = matches[0]
        return entity, battle_map, None

    def _entity_bundle(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Single pass over the active map entities for get_entities / players / npcs."""
        if self._entity_bundle_cache is not None:
            return self._entity_bundle_cache
        battle_map = self._primary_battle_map()
        if not battle_map:
            self._entity_bundle_cache = ([], [], [])
            return self._entity_bundle_cache

        entities: List[Dict[str, Any]] = []
        player_characters: List[Dict[str, Any]] = []
        npcs: List[Dict[str, Any]] = []

        for entity in battle_map.entities:
            try:
                entities.append(self._summarize_entity_row(entity, battle_map))
            except Exception as e:
                logger.error(f"Error processing entity {entity}: {e}")

            try:
                if isinstance(entity, PlayerCharacter):
                    player_characters.append(self._summarize_pc_row(entity, battle_map))
            except Exception as e:
                logger.error(f"Error processing player character {entity}: {e}")

            try:
                if not (hasattr(entity, 'player_character') and entity.player_character):
                    npcs.append(self._summarize_npc_row(entity, battle_map))
            except Exception as e:
                logger.error(f"Error processing NPC {entity}: {e}")

        self._entity_bundle_cache = (entities, player_characters, npcs)
        return self._entity_bundle_cache

    def _summarize_entity_row(self, entity, battle_map) -> Dict[str, Any]:
        entity_info = {
            "name": entity.label() if hasattr(entity, 'label') else str(entity),
            "type": entity.__class__.__name__,
            "entity_uid": getattr(entity, 'entity_uid', None),
            "position": battle_map.entity_or_object_pos(entity) if hasattr(battle_map, 'entity_or_object_pos') else None
        }

        if hasattr(entity, 'npc_type'):
            entity_info["npc_type"] = entity.npc_type

        if isinstance(entity, PlayerCharacter):
            entity_info["player_character"] = True
            entity_info["classes"] = entity.class_descriptor()

        if hasattr(entity, 'group'):
            entity_info["group"] = entity.group

        if hasattr(entity, 'hp') and callable(getattr(entity, 'hp')):
            entity_info["hp"] = entity.hp()
        elif hasattr(entity, 'hp'):
            entity_info["hp"] = entity.hp

        if hasattr(entity, 'max_hp') and callable(getattr(entity, 'max_hp')):
            entity_info["max_hp"] = entity.max_hp()
        elif hasattr(entity, 'max_hp'):
            entity_info["max_hp"] = entity.max_hp

        if hasattr(entity, 'ac') and callable(getattr(entity, 'ac')):
            entity_info["ac"] = entity.ac()
        elif hasattr(entity, 'ac'):
            entity_info["ac"] = entity.ac

        if hasattr(entity, 'level') and callable(getattr(entity, 'level')):
            entity_info["level"] = entity.level()
        elif hasattr(entity, 'level'):
            entity_info["level"] = entity.level

        if hasattr(entity, 'dead') and callable(getattr(entity, 'dead')):
            entity_info["dead"] = entity.dead()
        elif hasattr(entity, 'dead'):
            entity_info["dead"] = entity.dead

        if hasattr(entity, 'unconscious') and callable(getattr(entity, 'unconscious')):
            entity_info["unconscious"] = entity.unconscious()
        elif hasattr(entity, 'unconscious'):
            entity_info["unconscious"] = entity.unconscious

        if hasattr(entity, 'prone') and callable(getattr(entity, 'prone')):
            entity_info["prone"] = entity.prone()
        elif hasattr(entity, 'prone'):
            entity_info["prone"] = entity.prone

        if hasattr(entity, 'hidden') and callable(getattr(entity, 'hidden')):
            entity_info["hidden"] = entity.hidden()
        elif hasattr(entity, 'hidden'):
            entity_info["hidden"] = entity.hidden

        return entity_info

    def _summarize_pc_row(self, entity: PlayerCharacter, battle_map) -> Dict[str, Any]:
        pc_info = {
            "name": entity.label() if hasattr(entity, 'label') else str(entity),
            "entity_uid": getattr(entity, 'entity_uid', None),
            "position": battle_map.entity_or_object_pos(entity) if hasattr(battle_map, 'entity_or_object_pos') else None,
            "class": entity.class_descriptor()
        }

        if hasattr(entity, 'hp') and callable(getattr(entity, 'hp')):
            pc_info["hp"] = entity.hp()
        elif hasattr(entity, 'hp'):
            pc_info["hp"] = entity.hp

        if hasattr(entity, 'max_hp') and callable(getattr(entity, 'max_hp')):
            pc_info["max_hp"] = entity.max_hp()
        elif hasattr(entity, 'max_hp'):
            pc_info["max_hp"] = entity.max_hp

        if hasattr(entity, 'armor_class') and callable(getattr(entity, 'armor_class')):
            pc_info["ac"] = entity.armor_class()

        pc_info["inventory"] = entity.inventory_items(self.game_session)
        pc_info["equipped"] = entity.equipped_items()
        pc_info["attributes"] = entity.attributes

        if hasattr(entity, 'current_effects') and callable(getattr(entity, 'current_effects')):
            pc_info["effects"] = [str(effect['effect']) for effect in entity.current_effects()]
        elif hasattr(entity, 'current_effects'):
            pc_info["effects"] = entity.current_effects

        return pc_info

    def _summarize_npc_row(self, entity, battle_map) -> Dict[str, Any]:
        npc_info = {
            "name": entity.label() if hasattr(entity, 'label') else str(entity),
            "entity_uid": getattr(entity, 'entity_uid', None),
            "position": battle_map.entity_or_object_pos(entity) if hasattr(battle_map, 'entity_or_object_pos') else None,
            "type": entity.__class__.__name__,
        }

        if hasattr(entity, 'hp') and callable(getattr(entity, 'hp')):
            npc_info["hp"] = entity.hp()
        elif hasattr(entity, 'hp'):
            npc_info["hp"] = entity.hp

        if hasattr(entity, 'max_hp') and callable(getattr(entity, 'max_hp')):
            npc_info["max_hp"] = entity.max_hp()
        elif hasattr(entity, 'max_hp'):
            npc_info["max_hp"] = entity.max_hp

        if hasattr(entity, 'ac') and callable(getattr(entity, 'ac')):
            npc_info["ac"] = entity.ac()
        elif hasattr(entity, 'ac'):
            npc_info["ac"] = entity.ac

        if hasattr(entity, 'cr'):
            npc_info["challenge_rating"] = entity.cr

        if hasattr(entity, 'alignment') and callable(getattr(entity, 'alignment')):
            npc_info["alignment"] = entity.alignment()
        elif hasattr(entity, 'alignment'):
            npc_info["alignment"] = entity.alignment

        if hasattr(entity, 'size') and callable(getattr(entity, 'size')):
            npc_info["size"] = entity.size()
        elif hasattr(entity, 'size'):
            npc_info["size"] = entity.size

        if hasattr(entity, 'dead') and callable(getattr(entity, 'dead')):
            npc_info["dead"] = entity.dead()
        elif hasattr(entity, 'dead'):
            npc_info["dead"] = entity.dead

        if hasattr(entity, 'unconscious') and callable(getattr(entity, 'unconscious')):
            npc_info["unconscious"] = entity.unconscious()
        elif hasattr(entity, 'unconscious'):
            npc_info["unconscious"] = entity.unconscious

        if hasattr(entity, 'hostile'):
            npc_info["hostile"] = getattr(entity, 'hostile', False)

        return npc_info

    def get_map_info(self) -> Dict[str, Any]:
        """Get current map information including terrain, layout, and basic details."""
        try:
            # Get current map for the session
            battle_map = self.current_game.get_current_battle_map()
            if not battle_map:
                return {"error": "No current map available"}
            
            map_name = getattr(battle_map, 'name', None)
            session = self.game_session
            map_info = {
                "name": map_name,
                "size": battle_map.size if hasattr(battle_map, 'size') else None,
                "feet_per_grid": battle_map.feet_per_grid if hasattr(battle_map, 'feet_per_grid') else 5,
            }
            if session is not None and hasattr(session, 'map_set_for'):
                try:
                    map_info['map_set'] = session.map_set_for(map_name)
                    map_info['active_map_set'] = getattr(session, 'active_map_set', None)
                except Exception:
                    pass
            return map_info
            
        except Exception as e:
            logger.error(f"Error getting map info: {e}")
            return {"error": str(e)}
    
    def get_entities(self) -> List[Dict[str, Any]]:
        """Get all entities on the current map with their positions and basic information."""
        try:
            return self._entity_bundle()[0]
        except Exception as e:
            logger.error(f"Error getting entities: {e}")
            return []

    def get_player_characters(self) -> List[Dict[str, Any]]:
        """Get player characters across all campaign maps."""
        try:
            seen_uids = set()
            player_characters: List[Dict[str, Any]] = []
            for map_name, battle_map, entity in self._iter_campaign_entities():
                if not isinstance(entity, PlayerCharacter):
                    continue
                uid = getattr(entity, 'entity_uid', None)
                if uid in seen_uids:
                    continue
                seen_uids.add(uid)
                try:
                    row = self._summarize_pc_row(entity, battle_map)
                    row['map'] = map_name
                    if self.game_session is not None and hasattr(self.game_session, 'map_set_for'):
                        try:
                            row['map_set'] = self.game_session.map_set_for(map_name)
                        except Exception:
                            pass
                    player_characters.append(row)
                except Exception as exc:
                    logger.error('Error processing player character %s: %s', entity, exc)
            return player_characters
        except Exception as e:
            logger.error(f"Error getting player characters: {e}")
            return []

    def get_npcs(self) -> List[Dict[str, Any]]:
        """Get information about NPCs on the current map."""
        try:
            return self._entity_bundle()[2]
        except Exception as e:
            logger.error(f"Error getting NPCs: {e}")
            return []
    
    def get_entity_details(self, entity_name: str) -> Dict[str, Any]:
        """Get detailed information about a specific entity by name or uid."""
        try:
            target_entity, battle_map, error = self._find_entity_by_ref(entity_name)
            if error:
                return {"error": error}
            if target_entity is None or battle_map is None:
                return {"error": f"Entity '{entity_name}' not found"}
            
            # Build detailed entity information
            details = {
                "name": target_entity.label() if hasattr(target_entity, 'label') else str(target_entity),
                "entity_uid": getattr(target_entity, 'entity_uid', None),
                "type": target_entity.__class__.__name__,
                "map": getattr(battle_map, 'name', None),
                "position": battle_map.entity_or_object_pos(target_entity) if hasattr(battle_map, 'entity_or_object_pos') else None
            }

            if isinstance(target_entity, PlayerCharacter):
                details['player_character'] = True
                try:
                    details['class'] = target_entity.class_descriptor()
                except Exception:
                    pass
                try:
                    profile = profile_for_entity(target_entity)
                    if profile:
                        details['character_profile'] = profile
                except Exception:
                    pass
                try:
                    details['inventory'] = target_entity.inventory_items(self.game_session)
                except Exception:
                    pass
                try:
                    details['equipped'] = target_entity.equipped_items()
                except Exception:
                    pass
                try:
                    details['attributes'] = target_entity.attributes
                except Exception:
                    pass
                try:
                    availability = target_entity.rest_status(
                        battle=self.current_game.get_current_battle(),
                        battle_map=battle_map,
                        require_rations=True,
                    )
                    if availability:
                        details['rest_availability'] = availability
                except Exception:
                    pass
            
            # Add combat stats
            if hasattr(target_entity, 'hp') and callable(getattr(target_entity, 'hp')):
                details["hp"] = target_entity.hp()
            elif hasattr(target_entity, 'hp'):
                details["hp"] = target_entity.hp
            
            if hasattr(target_entity, 'max_hp') and callable(getattr(target_entity, 'max_hp')):
                details["max_hp"] = target_entity.max_hp()
            elif hasattr(target_entity, 'max_hp'):
                details["max_hp"] = target_entity.max_hp
            
            if hasattr(target_entity, 'ac') and callable(getattr(target_entity, 'ac')):
                details["ac"] = target_entity.ac()
            elif hasattr(target_entity, 'ac'):
                details["ac"] = target_entity.ac
            
            if hasattr(target_entity, 'initiative') and callable(getattr(target_entity, 'initiative')):
                details["initiative"] = target_entity.initiative()
            elif hasattr(target_entity, 'initiative'):
                details["initiative"] = target_entity.initiative
            
            # Add ability scores
            if hasattr(target_entity, 'strength'):
                abilities = {}
                for ability in ['strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma']:
                    if hasattr(target_entity, ability) and callable(getattr(target_entity, ability)):
                        abilities[ability] = getattr(target_entity, ability)()
                    elif hasattr(target_entity, ability):
                        abilities[ability] = getattr(target_entity, ability)
                    else:
                        abilities[ability] = 10
                details["abilities"] = abilities
            
            # Add status information
            if hasattr(target_entity, 'dead') and callable(getattr(target_entity, 'dead')):
                details["dead"] = target_entity.dead()
            elif hasattr(target_entity, 'dead'):
                details["dead"] = target_entity.dead
            
            if hasattr(target_entity, 'unconscious') and callable(getattr(target_entity, 'unconscious')):
                details["unconscious"] = target_entity.unconscious()
            elif hasattr(target_entity, 'unconscious'):
                details["unconscious"] = target_entity.unconscious
            
            if hasattr(target_entity, 'prone') and callable(getattr(target_entity, 'prone')):
                details["prone"] = target_entity.prone()
            elif hasattr(target_entity, 'prone'):
                details["prone"] = target_entity.prone
            
            if hasattr(target_entity, 'hidden') and callable(getattr(target_entity, 'hidden')):
                details["hidden"] = target_entity.hidden()
            elif hasattr(target_entity, 'hidden'):
                details["hidden"] = target_entity.hidden
            
            if hasattr(target_entity, 'grappled') and callable(getattr(target_entity, 'grappled')):
                details["grappled"] = target_entity.grappled()
            elif hasattr(target_entity, 'grappled'):
                details["grappled"] = target_entity.grappled
            
            # Add current effects
            if hasattr(target_entity, 'current_effects') and callable(getattr(target_entity, 'current_effects')):
                details["effects"] = [str(effect['effect']) for effect in target_entity.current_effects()]
            elif hasattr(target_entity, 'current_effects'):
                details["effects"] = target_entity.current_effects
            
            # Add inventory if available
            if hasattr(target_entity, 'inventory_items') and callable(getattr(target_entity, 'inventory_items')):
                try:
                    inventory = target_entity.inventory_items(self.game_session)
                    if inventory:
                        details["inventory"] = [item.get('name', 'Unknown Item') for item in inventory]
                except:
                    pass
            elif hasattr(target_entity, 'inventory_items'):
                details["inventory"] = target_entity.inventory_items
            
            # Add equipment if available
            if hasattr(target_entity, 'equipment') and callable(getattr(target_entity, 'equipment')):
                try:
                    equipment = target_entity.equipment()
                    if equipment:
                        details["equipment"] = equipment
                except:
                    pass
            elif hasattr(target_entity, 'equipment'):
                details["equipment"] = target_entity.equipment
            
            return details
            
        except Exception as e:
            logger.error(f"Error getting entity details for '{entity_name}': {e}")
            return {"error": str(e)}
    
    def get_battle_status(self) -> Dict[str, Any]:
        """Get current battle information if combat is active."""
        try:
            from webapp.battle_management import get_battle_snapshot, list_campaign_groups

            snapshot = get_battle_snapshot(self.current_game)
            if not snapshot.get('active'):
                return snapshot
            snapshot['campaign_groups'] = list_campaign_groups(self.game_session).get('groups', {})
            return snapshot
        except Exception as e:
            logger.error(f"Error getting battle status: {e}")
            return {"error": str(e)}

    def _resolve_entity_uid(self, entity_ref: str) -> Dict[str, Any]:
        entity, _battle_map, error = self._find_entity_by_ref(entity_ref)
        if error:
            return {'success': False, 'error': error}
        if entity is None:
            return {'success': False, 'error': f"Entity '{entity_ref}' not found"}
        return {'success': True, 'entity': entity, 'entity_uid': getattr(entity, 'entity_uid', None)}

    def start_battle(
        self,
        combatant_uids: Optional[List[str]] = None,
        map_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        from webapp.battle_management import start_battle as _start_battle

        try:
            return {'success': True, **_start_battle(
                self.current_game,
                self.game_session,
                combatant_uids=combatant_uids,
                map_name=map_name,
            )}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}

    def end_battle(self) -> Dict[str, Any]:
        from webapp.battle_management import end_battle as _end_battle

        try:
            return {'success': True, **_end_battle(self.current_game)}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}

    def manage_battle(
        self,
        op: str,
        *,
        entity_ref: Optional[str] = None,
        group: Optional[str] = None,
        order: Optional[List[str]] = None,
        initiative: Optional[int] = None,
    ) -> Dict[str, Any]:
        from webapp.battle_management import battle_admin

        entity_uid = entity_ref
        if entity_ref:
            resolved = self._resolve_entity_uid(entity_ref)
            if not resolved.get('success'):
                return resolved
            entity_uid = resolved.get('entity_uid')
        try:
            initiative_value = int(initiative) if initiative is not None else None
        except (TypeError, ValueError):
            return {'success': False, 'error': 'initiative must be an integer'}
        try:
            return {
                'success': True,
                **battle_admin(
                    self.current_game,
                    self.game_session,
                    op,
                    entity_uid=entity_uid,
                    group=group,
                    order=order,
                    initiative=initiative_value,
                ),
            }
        except Exception as exc:
            return {'success': False, 'error': str(exc)}

    def set_entity_group(self, entity_ref: str, group: str) -> Dict[str, Any]:
        from webapp.battle_management import set_entity_group as _set_entity_group

        resolved = self._resolve_entity_uid(entity_ref)
        if not resolved.get('success'):
            return resolved
        try:
            return {
                'success': True,
                **_set_entity_group(
                    self.current_game,
                    self.game_session,
                    resolved['entity_uid'],
                    group,
                ),
            }
        except Exception as exc:
            return {'success': False, 'error': str(exc)}

    def configure_group_relationship(
        self,
        group: str,
        target_group: str,
        relationship: str,
    ) -> Dict[str, Any]:
        from webapp.battle_management import configure_group_relationship as _configure

        try:
            return {
                'success': True,
                **_configure(self.game_session, group, target_group, relationship),
            }
        except Exception as exc:
            return {'success': False, 'error': str(exc)}

    def list_campaign_groups(self) -> Dict[str, Any]:
        from webapp.battle_management import list_campaign_groups as _list_groups

        return {'success': True, **_list_groups(self.game_session)}

    def list_map_sets(self) -> Dict[str, Any]:
        """Snapshot of campaign map sets for the DM assistant context."""
        from webapp.blueprints.helpers.map_sets import map_sets_payload

        try:
            battle_map = self._primary_battle_map()
            current_name = getattr(battle_map, 'name', None) if battle_map is not None else None
            return {'success': True, **map_sets_payload(self.current_game, current_name)}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}

    def manage_map_set(
        self,
        op: str,
        map_set: Optional[str] = None,
        label: Optional[str] = None,
        map_name: Optional[str] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        set_id: Optional[str] = None,
        id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """DM map-set ops: list | activate | create | assign_map | place_party."""
        from webapp.blueprints.helpers.map_sets import (
            activate_map_set,
            assign_map_to_set,
            create_map_set,
            map_sets_payload,
            place_party,
        )

        map_set = map_set or set_id or id
        try:
            if op == 'list':
                return {'success': True, **map_sets_payload(self.current_game)}
            if op == 'activate':
                if not map_set:
                    return {'success': False, 'error': 'map_set is required'}
                return {'success': True, **activate_map_set(self.current_game, map_set)}
            if op == 'create':
                if not map_set:
                    return {'success': False, 'error': 'map_set is required'}
                return {'success': True, **create_map_set(self.current_game, map_set, label=label)}
            if op == 'assign_map':
                if not map_name or not map_set:
                    return {'success': False, 'error': 'map_name and map_set are required'}
                return {'success': True, **assign_map_to_set(self.current_game, map_name, map_set)}
            if op == 'place_party':
                if not map_name:
                    return {'success': False, 'error': 'map_name is required'}
                return {
                    'success': True,
                    **place_party(self.current_game, map_name, x=x, y=y),
                }
            return {
                'success': False,
                'error': 'op must be one of: list, activate, create, assign_map, place_party',
            }
        except ValueError as exc:
            return {'success': False, 'error': str(exc)}
        except Exception as exc:
            logger.error('Error in manage_map_set: %s', exc)
            return {'success': False, 'error': str(exc)}
    
    def get_available_actions(self, entity_name: str) -> Dict[str, Any]:
        """Get available actions for a specific entity."""
        try:
            battle_map = self.current_game.get_current_battle_map()
            if not battle_map:
                return {"error": "No current map available"}
            
            # Find entity by name
            target_entity = None
            for entity in battle_map.entities:
                if hasattr(entity, 'label') and entity.label().lower() == entity_name.lower():
                    target_entity = entity
                    break
            
            if not target_entity:
                return {"error": f"Entity '{entity_name}' not found"}
            
            battle = self.current_game.get_current_battle()
            
            # Get available actions
            if hasattr(target_entity, 'available_actions'):
                try:
                    actions = target_entity.available_actions({}, battle, auto_target=False, map=battle_map)
                    action_list = []
                    
                    for action in actions:
                        action_info = {
                            "name": str(action),
                            "action_type": action.__class__.__name__,
                            "description": getattr(action, 'description', 'No description available')
                        }
                        action_list.append(action_info)
                    
                    return {"actions": action_list}
                    
                except Exception as e:
                    logger.error(f"Error getting actions for {entity_name}: {e}")
                    return {"error": f"Could not retrieve actions: {str(e)}"}
            
            return {"error": "Entity does not support action retrieval"}
            
        except Exception as e:
            logger.error(f"Error getting available actions for '{entity_name}': {e}")
            return {"error": str(e)}
    
    def get_map_terrain_info(self, x: int, y: int) -> Dict[str, Any]:
        """Get terrain information for a specific location on the map."""
        try:
            battle_map = self.current_game.get_current_battle_map()
            if not battle_map:
                return {"error": "No current map available"}
            
            terrain_info = {
                "position": {"x": x, "y": y},
                "difficult_terrain": False,
                "impassable": False,
                "lighting": "bright"
            }
            
            # Check if position is within map bounds
            if hasattr(battle_map, 'size'):
                map_width, map_height = battle_map.size
                if x < 0 or x >= map_width or y < 0 or y >= map_height:
                    return {"error": "Position outside map bounds"}
            
            # Check terrain properties
            if hasattr(battle_map, 'terrain'):
                if (x, y) in getattr(battle_map.terrain, 'difficult_terrain', []):
                    terrain_info["difficult_terrain"] = True
                
                if (x, y) in getattr(battle_map.terrain, 'impassable', []):
                    terrain_info["impassable"] = True
            
            # Check lighting
            if hasattr(battle_map, 'light_at'):
                light_level = battle_map.light_at(x, y)
                if light_level == 0.0:
                    if hasattr(battle_map, 'magical_darkness_at') and battle_map.magical_darkness_at(x, y):
                        terrain_info["lighting"] = "magical_darkness"
                    else:
                        terrain_info["lighting"] = "darkness"
                elif light_level == 0.5:
                    terrain_info["lighting"] = "dim"
                else:
                    terrain_info["lighting"] = "bright"
            
            # Check for objects/entities at this position
            if hasattr(battle_map, 'thing_at'):
                things = battle_map.thing_at(x, y)
                if things:
                    terrain_info["objects"] = [
                        thing.label() if hasattr(thing, 'label') else str(thing)
                        for thing in things
                    ]
            
            return terrain_info
            
        except Exception as e:
            logger.error(f"Error getting terrain info for ({x}, {y}): {e}")
            return {"error": str(e)}

    def run_rest(
        self,
        entity_ref: str,
        rest_type: str = 'long',
        force: bool = True,
    ) -> Dict[str, Any]:
        """Run a short or long rest for one entity (DM assistant helper)."""
        entity, _battle_map, error = self._find_entity_by_ref(entity_ref)
        if error:
            return {'success': False, 'error': error}
        if entity is None:
            return {'success': False, 'error': f"Entity '{entity_ref}' not found"}
        return execute_entity_rest(
            self.current_game,
            entity,
            rest_type,
            force=bool(force),
        )

    def rest_all_player_characters(
        self,
        rest_type: str = 'long',
        force: bool = True,
    ) -> Dict[str, Any]:
        """Run a rest for every player character in the campaign."""
        pcs = self.get_player_characters()
        if not pcs:
            return {'success': False, 'error': 'No player characters found in the campaign'}

        results = []
        errors = []
        npc_simulation_scheduled = False
        for pc in pcs:
            uid = pc.get('entity_uid')
            if not uid:
                continue
            entity, _battle_map, error = self._find_entity_by_ref(uid)
            if error or entity is None:
                errors.append({'entity_uid': uid, 'error': error or 'not found'})
                continue
            outcome = execute_entity_rest(
                self.current_game,
                entity,
                rest_type,
                force=bool(force),
                emit_refresh=False,
            )
            results.append(outcome)
            if not outcome.get('success'):
                errors.append({'entity_uid': uid, 'error': outcome.get('error')})
            if outcome.get('npc_simulation_scheduled'):
                npc_simulation_scheduled = True

        if not npc_simulation_scheduled:
            try:
                self.current_game.socketio.emit('message', {'type': 'refresh_map'})
            except Exception:
                logger.exception('Failed to emit refresh_map after party rest')

        return {
            'success': not errors,
            'rest_type': rest_type,
            'results': results,
            'errors': errors,
            'game_time': getattr(self.game_session, 'game_time', None),
            'npc_simulation_scheduled': npc_simulation_scheduled,
        }

    def get_time_of_day(self) -> Dict[str, Any]:
        """Current campaign clock and day/night phase for DM assistant context."""
        from webapp.blueprints.helpers.time_of_day import get_time_of_day_config, get_time_of_day_state
        return {
            'state': get_time_of_day_state(self.current_game).to_dict(),
            'config': get_time_of_day_config(self.current_game),
        }

    def run_npc_long_rest_simulation(self, force: bool = True) -> Dict[str, Any]:
        """Manually run background NPC long-rest simulation loops (DM only)."""
        from webapp.long_rest_npc_simulation import (
            load_long_rest_sim_config,
            npc_long_rest_simulation_running,
            schedule_long_rest_npc_simulation,
        )

        if npc_long_rest_simulation_running():
            return {'success': False, 'error': 'NPC long rest simulation is already running'}

        from webapp.npc_background_llm import npc_autonomous_loops_enabled
        if not npc_autonomous_loops_enabled(self.game_session):
            return {
                'success': False,
                'error': 'NPC autonomous loops are disabled (npc_autonomous_loops / N20_NPC_LOOPS)',
            }

        if self.current_game.get_current_battle() is not None:
            return {'success': False, 'error': 'Cannot run NPC long rest simulation during combat'}

        config = load_long_rest_sim_config(self.game_session)
        if not config.get('enabled') and not force:
            return {
                'success': False,
                'error': 'NPC long rest simulation is disabled in game.yml (pass force=true to override)',
            }

        scheduled = schedule_long_rest_npc_simulation(
            self.current_game,
            game_time=self.game_session.game_time,
            force=bool(force),
        )
        if not scheduled:
            return {'success': False, 'error': 'Failed to schedule NPC long rest simulation'}

        return {
            'success': True,
            'scheduled': True,
            'game_time': self.game_session.game_time,
            'config': config,
        }