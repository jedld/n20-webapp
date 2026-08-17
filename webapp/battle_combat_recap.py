"""Post-battle first-person recaps for PCs (journals) and dialog NPCs (memory).

After a fight that lasted at least one combat round, the NPC LLM summarizes the
combat log from each participant/witness point of view. Players receive a journal
entry; conversation-aware NPCs (``dialog: true``) receive a durable memory item.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from natural20.player_character import PlayerCharacter
from natural20.utils.conversation import entity_label

logger = logging.getLogger(__name__)

_META_ATTR = '_combat_recap_meta'
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_LOG_TIMESTAMP_RE = re.compile(r'^\d{4}:\d{2}:\d{2}\.\d{2}:\d{2}:\d{2}:\s*')
_TITLE_RE = re.compile(r'(?im)^\s*TITLE:\s*(.+)$')
_SUMMARY_RE = re.compile(r'(?im)^\s*SUMMARY:\s*(.+)$')
_DEATH_RE = re.compile(r'\b(died|dies|is slain|was slain|killed|falls dead)\b', re.IGNORECASE)
_UNCONSCIOUS_RE = re.compile(r'\bunconscious\b', re.IGNORECASE)


def load_combat_recap_config(game_session) -> Dict[str, Any]:
    props = getattr(game_session, 'game_properties', None) or {}
    block = props.get('battle_combat_recap') or {}
    if not isinstance(block, dict):
        block = {}
    enabled = block.get('enabled', True)
    return {
        'enabled': bool(enabled),
    }


def battle_had_combat_round(battle) -> bool:
    """True when at least one round of fighting actually happened.

    Engine ``round`` stays 0 during the first initiative pass, so a short fight
    that ends before wrapping still counts if someone acted or a later turn began.
    """
    if battle is None:
        return False
    try:
        if int(getattr(battle, 'round', 0) or 0) >= 1:
            return True
    except (TypeError, ValueError):
        pass
    try:
        if int(getattr(battle, 'current_turn_index', 0) or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    log = getattr(battle, 'battle_log', None) or []
    return bool(log)


def _output_logger(game):
    logger_obj = getattr(game, 'output_logger', None)
    if logger_obj is not None:
        return logger_obj
    try:
        from webapp.blueprints.helpers.runtime_state import get_output_logger

        return get_output_logger()
    except Exception:
        return None


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub('', text or '').strip()


def _clean_log_line(text: str) -> str:
    line = _strip_html(str(text or ''))
    line = _LOG_TIMESTAMP_RE.sub('', line)
    return re.sub(r'\s+', ' ', line).strip()


def _entity_uid(entity) -> str:
    return str(getattr(entity, 'entity_uid', '') or '')


def _is_player(entity) -> bool:
    if isinstance(entity, PlayerCharacter):
        return True
    npc_fn = getattr(entity, 'npc', None)
    if callable(npc_fn):
        try:
            return not bool(npc_fn())
        except Exception:
            return False
    return False


def _is_dialog_npc(entity) -> bool:
    if entity is None or _is_player(entity):
        return False
    return bool(getattr(entity, 'dialog', False))


def _entity_status_label(entity) -> str:
    try:
        if callable(getattr(entity, 'dead', None)) and entity.dead():
            return 'dead'
    except Exception:
        pass
    try:
        if callable(getattr(entity, 'unconscious', None)) and entity.unconscious():
            return 'unconscious'
    except Exception:
        pass
    try:
        if callable(getattr(entity, 'conscious', None)) and not entity.conscious():
            return 'incapacitated'
    except Exception:
        pass
    return 'standing'


def _hp_line(entity) -> str:
    try:
        hp = entity.hp() if callable(getattr(entity, 'hp', None)) else None
        max_hp = entity.max_hp() if callable(getattr(entity, 'max_hp', None)) else None
        if hp is not None and max_hp is not None:
            return f'{hp}/{max_hp} HP'
        if hp is not None:
            return f'{hp} HP'
    except Exception:
        pass
    return ''


def snapshot_battle_start(game, session, battle=None) -> Optional[Dict[str, Any]]:
    """Record combat-log and conversation watermarks when initiative starts."""
    battle = battle or (game.get_current_battle() if game is not None else None)
    if battle is None:
        return None

    output_logger = _output_logger(game)
    log_index = 0
    if output_logger is not None:
        entries = list(getattr(output_logger, 'logging_queue', []) or [])
        log_index = len(entries)
        for idx, entry in enumerate(entries):
            message = ''
            if isinstance(entry, dict):
                message = str(entry.get('message') or '')
            else:
                message = str(entry)
            if 'Combat begins' in message:
                log_index = idx

    conversation_lens: Dict[str, int] = {}
    for entity in _iter_subject_entities(game, battle, include_non_subjects=True):
        uid = _entity_uid(entity)
        if not uid:
            continue
        conversation_lens[uid] = len(list(getattr(entity, 'conversation_buffer', None) or []))

    meta = {
        'game_time': int(getattr(session, 'game_time', 0) or 0) if session is not None else 0,
        'log_index': log_index,
        'conversation_lens': conversation_lens,
    }
    setattr(battle, _META_ATTR, meta)
    return meta


def _battle_maps(game, battle) -> List[Any]:
    maps: List[Any] = []
    seen: Set[int] = set()

    def _add(map_obj):
        if map_obj is None:
            return
        key = id(map_obj)
        if key in seen:
            return
        seen.add(key)
        maps.append(map_obj)

    for map_obj in list(getattr(battle, 'maps', None) or []):
        _add(map_obj)
    try:
        _add(game.get_current_battle_map() if game is not None else None)
    except Exception:
        pass
    for entity in list(getattr(battle, 'combat_order', None) or []):
        try:
            _add(battle.map_for(entity))
        except Exception:
            continue
    return maps


def _iter_map_entities(battle_map) -> Iterable[Any]:
    try:
        entities = getattr(battle_map, 'entities', None)
        if isinstance(entities, dict):
            return list(entities.keys())
        return list(entities or [])
    except Exception:
        return []


def _iter_subject_entities(game, battle, *, include_non_subjects: bool = False) -> List[Any]:
    seen: Set[str] = set()
    found: List[Any] = []

    def _add(entity):
        uid = _entity_uid(entity)
        if not uid or uid in seen:
            return
        seen.add(uid)
        found.append(entity)

    for entity in list(getattr(battle, 'combat_order', None) or []):
        _add(entity)
    for battle_map in _battle_maps(game, battle):
        for entity in _iter_map_entities(battle_map):
            _add(entity)
    if include_non_subjects:
        return found
    subjects = []
    for entity in found:
        if _is_player(entity) or _is_dialog_npc(entity):
            subjects.append(entity)
    return subjects


def _location_context(game, battle, entity=None) -> Dict[str, Any]:
    from natural20.map_annotations import (
        current_location_annotations_at_position,
        format_location_hierarchy_for_llm,
    )

    battle_map = None
    position = None
    if entity is not None:
        try:
            battle_map = battle.map_for(entity) if battle is not None else None
        except Exception:
            battle_map = None
        if battle_map is None and game is not None:
            getter = getattr(game, 'get_map_for_entity', None)
            if callable(getter):
                try:
                    battle_map = getter(entity)
                except Exception:
                    battle_map = None
        if battle_map is not None:
            try:
                position = battle_map.position_of(entity)
            except Exception:
                position = None

    if battle_map is None:
        maps = _battle_maps(game, battle)
        battle_map = maps[0] if maps else None

    map_name = getattr(battle_map, 'name', None) if battle_map is not None else None
    annotations: List[Dict[str, Any]] = []
    if battle_map is not None and position is not None:
        try:
            annotations = current_location_annotations_at_position(
                getattr(battle_map, 'properties', None) or {},
                int(position[0]),
                int(position[1]),
            )
        except Exception:
            annotations = []
    elif battle_map is not None:
        try:
            annotations = list(battle_map.map_annotations() or [])[:4]
        except Exception:
            annotations = []

    where = format_location_hierarchy_for_llm(
        annotations,
        map_name=map_name,
        position=tuple(position) if position is not None else None,
    )
    if not where:
        if map_name and position is not None:
            where = f'on {map_name} at grid {int(position[0])},{int(position[1])}'
        elif map_name:
            where = f'on {map_name}'
        else:
            where = ''

    labels = []
    for item in annotations:
        label = str(item.get('label') or item.get('id') or '').strip()
        if label:
            labels.append(label)
    return {
        'map_name': map_name,
        'position': list(position) if position is not None else None,
        'annotation_labels': labels,
        'description': where,
    }


def _when_context(game, session, game_time: Optional[int] = None) -> Dict[str, Any]:
    from natural20.time_of_day import load_time_of_day_config, resolve_time_of_day, world_time_context_lines

    if game_time is None:
        game_time = int(getattr(session, 'game_time', 0) or 0) if session is not None else 0
    props = getattr(session, 'game_properties', None) or {} if session is not None else {}
    try:
        from webapp.blueprints.helpers.time_of_day import get_time_of_day_config, get_time_of_day_state

        if game is not None:
            state = get_time_of_day_state(game)
            config = get_time_of_day_config(game)
        else:
            config = load_time_of_day_config(props)
            state = resolve_time_of_day(int(game_time or 0), config)
    except Exception:
        config = load_time_of_day_config(props)
        state = resolve_time_of_day(int(game_time or 0), config)

    lines = world_time_context_lines(state)
    return {
        'game_time': int(game_time or 0),
        'clock_label': getattr(state, 'clock_label', None),
        'day_number': getattr(state, 'day_number', None),
        'period_label': getattr(state, 'period_label', None),
        'description': ' '.join(lines),
        'lines': lines,
    }


def _outcome_label(battle) -> str:
    try:
        if battle.tpk():
            return 'tpk'
    except Exception:
        pass
    try:
        if battle.battle_ends() and battle.has_player_combatants():
            return 'victory'
    except Exception:
        pass
    return 'ended'


def _combatant_rows(battle) -> List[Dict[str, Any]]:
    rows = []
    for entity in list(getattr(battle, 'combat_order', None) or []):
        group = None
        try:
            group = (battle.entities.get(entity) or {}).get('group')
        except Exception:
            group = getattr(entity, 'group', None)
        hp = _hp_line(entity)
        status = _entity_status_label(entity)
        row = {
            'uid': _entity_uid(entity),
            'name': entity_label(entity) or _entity_uid(entity),
            'group': group,
            'status': status,
            'hp': hp,
            'is_player': _is_player(entity),
            'dialog': _is_dialog_npc(entity),
        }
        rows.append(row)
    return rows


def _major_events(combatant_rows: Sequence[Dict[str, Any]], log_lines: Sequence[str], conversation_lines: Sequence[str]) -> List[str]:
    events: List[str] = []
    for row in combatant_rows:
        name = row.get('name') or 'Someone'
        status = row.get('status')
        if status == 'dead':
            kind = 'player' if row.get('is_player') else 'NPC'
            events.append(f'{name} ({kind}) died.')
        elif status == 'unconscious':
            kind = 'player' if row.get('is_player') else 'NPC'
            events.append(f'{name} ({kind}) was left unconscious.')
    for line in log_lines:
        cleaned = _clean_log_line(line)
        if _DEATH_RE.search(cleaned) or _UNCONSCIOUS_RE.search(cleaned):
            if cleaned not in events:
                events.append(cleaned)
    if conversation_lines:
        events.append(f'{len(conversation_lines)} conversation beat(s) occurred during the fight.')
    return events


def _conversation_beats(entity, *, start_index: int = 0, start_game_time: Optional[int] = None) -> List[str]:
    buffer = list(getattr(entity, 'conversation_buffer', None) or [])
    lines: List[str] = []
    for idx, entry in enumerate(buffer):
        if not isinstance(entry, dict):
            continue
        if idx < int(start_index or 0):
            continue
        if start_game_time is not None:
            entry_time = entry.get('time')
            try:
                if entry_time is not None and int(entry_time) < int(start_game_time):
                    continue
            except (TypeError, ValueError):
                pass
        message = str(entry.get('message') or '').strip()
        if not message:
            continue
        source = entry.get('source')
        speaker = entity_label(source) if source is not None else 'someone'
        lines.append(f'{speaker}: {message}')
    return lines[-40:]


def _visible_log_lines(output_logger, entity, *, start_index: int = 0, limit: int = 200) -> List[str]:
    if output_logger is None:
        return []
    try:
        entries = list(getattr(output_logger, 'logging_queue', None) or [])
    except Exception:
        entries = []
    sliced = entries[max(0, int(start_index or 0)):]
    visible_check = getattr(output_logger, '_entry_visible_to_entity', None)
    lines = []
    for entry in sliced[-limit:]:
        if callable(visible_check) and entity is not None:
            try:
                if not visible_check(entry, entity):
                    continue
            except Exception:
                pass
        if isinstance(entry, dict):
            raw = entry.get('message') or ''
        else:
            raw = entry
        cleaned = _clean_log_line(raw)
        if cleaned:
            lines.append(cleaned)
    return lines


def build_recap_snapshot(game, session, battle) -> Optional[Dict[str, Any]]:
    """Capture everything needed to write recaps after the battle object is cleared."""
    if battle is None or not battle_had_combat_round(battle):
        return None

    meta = getattr(battle, _META_ATTR, None) or {}
    start_game_time = int(meta.get('game_time') or getattr(session, 'game_time', 0) or 0)
    log_index = int(meta.get('log_index') or 0)
    conversation_lens = meta.get('conversation_lens') or {}

    output_logger = _output_logger(game)
    combatant_rows = _combatant_rows(battle)
    outcome = _outcome_label(battle)
    when = _when_context(game, session, game_time=int(getattr(session, 'game_time', 0) or start_game_time))
    round_count = int(getattr(battle, 'round', 0) or 0)
    if round_count < 1 and (getattr(battle, 'current_turn_index', 0) or getattr(battle, 'battle_log', None)):
        round_count = max(1, round_count)

    subjects = []
    for entity in _iter_subject_entities(game, battle):
        uid = _entity_uid(entity)
        if not uid:
            continue
        location = _location_context(game, battle, entity)
        logs = _visible_log_lines(output_logger, entity, start_index=log_index)
        beats = _conversation_beats(
            entity,
            start_index=int(conversation_lens.get(uid) or 0),
            start_game_time=start_game_time,
        )
        was_combatant = any(row.get('uid') == uid for row in combatant_rows)
        subjects.append({
            'uid': uid,
            'name': entity_label(entity) or uid,
            'is_player': _is_player(entity),
            'dialog': _is_dialog_npc(entity),
            'status': _entity_status_label(entity),
            'was_combatant': was_combatant,
            'location': location,
            'logs': logs,
            'conversation_beats': beats,
            'major_events': _major_events(combatant_rows, logs, beats),
        })

    if not subjects:
        return None

    shared_beats: List[str] = []
    seen_beats: Set[str] = set()
    for subject in subjects:
        for beat in subject.get('conversation_beats') or []:
            if beat in seen_beats:
                continue
            seen_beats.add(beat)
            shared_beats.append(beat)
    if shared_beats:
        for subject in subjects:
            if subject.get('conversation_beats') or not subject.get('was_combatant'):
                continue
            subject['conversation_beats'] = list(shared_beats)
            subject['major_events'] = _major_events(
                combatant_rows,
                subject.get('logs') or [],
                subject.get('conversation_beats') or [],
            )

    fallback_location = subjects[0].get('location') or _location_context(game, battle)
    return {
        'outcome': outcome,
        'round_count': round_count,
        'when': when,
        'location': fallback_location,
        'combatants': combatant_rows,
        'subjects': subjects,
        'conversation_beats': shared_beats,
        'game_time': when.get('game_time') or start_game_time,
    }


def _heuristic_recap(subject: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, str]:
    name = subject.get('name') or 'I'
    when = snapshot.get('when') or {}
    location = subject.get('location') or snapshot.get('location') or {}
    when_text = when.get('description') or ''
    where_text = location.get('description') or location.get('map_name') or 'an unknown place'
    outcome = snapshot.get('outcome') or 'ended'
    outcome_text = {
        'tpk': 'the party fell.',
        'victory': 'we prevailed.',
        'ended': 'the fighting ended.',
    }.get(outcome, 'the fighting ended.')

    parts = [
        f"We fought {where_text}. {when_text} {outcome_text}".strip(),
    ]
    events = list(subject.get('major_events') or [])
    deaths = [event for event in events if 'died' in event.lower() or 'unconscious' in event.lower()]
    if deaths:
        parts.append(' '.join(deaths[:6]))
    beats = list(subject.get('conversation_beats') or [])
    if beats:
        quoted = '; '.join(beats[-4:])
        parts.append(f'During the fight people spoke: {quoted}')
    logs = list(subject.get('logs') or [])
    notable = [line for line in logs if _DEATH_RE.search(line) or 'damage' in line.lower() or 'hit' in line.lower()]
    if notable and not deaths:
        parts.append(' '.join(notable[-4:]))
    if subject.get('status') == 'dead':
        parts.append(f'{name} did not survive the battle.')
    elif subject.get('status') == 'unconscious':
        parts.append(f'{name} was left unconscious.')

    body = ' '.join(part.strip() for part in parts if part and str(part).strip())
    map_name = location.get('map_name') or 'the battlefield'
    title = f"Battle at {map_name}"
    labels = location.get('annotation_labels') or []
    if labels:
        title = f"Battle at {labels[0]}"
    summary = body[:500]
    return {'title': title[:200], 'summary': summary, 'body': body[:8000]}


def parse_llm_recap(text: str, fallback: Dict[str, str]) -> Dict[str, str]:
    raw = str(text or '').strip()
    if not raw:
        return dict(fallback)
    title_match = _TITLE_RE.search(raw)
    summary_match = _SUMMARY_RE.search(raw)
    body = raw
    if title_match or summary_match:
        body = _TITLE_RE.sub('', body)
        body = _SUMMARY_RE.sub('', body)
        body = body.strip()
    title = (title_match.group(1).strip() if title_match else fallback.get('title') or 'Battle recap')[:200]
    summary = (summary_match.group(1).strip() if summary_match else (body.split('\n', 1)[0] if body else fallback.get('summary')))[:500]
    if not body:
        body = fallback.get('body') or summary
    return {
        'title': title or fallback.get('title') or 'Battle recap',
        'summary': summary or fallback.get('summary') or title,
        'body': body[:8000],
    }


def _build_prompts(subject: Dict[str, Any], snapshot: Dict[str, Any]) -> Tuple[str, str]:
    name = subject.get('name') or 'this character'
    when = snapshot.get('when') or {}
    location = subject.get('location') or snapshot.get('location') or {}
    combatants = snapshot.get('combatants') or []
    roster_lines = []
    for row in combatants:
        hp = f" ({row['hp']})" if row.get('hp') else ''
        roster_lines.append(f"- {row.get('name')}: {row.get('status')}{hp} group={row.get('group')}")
    log_lines = subject.get('logs') or []
    beats = subject.get('conversation_beats') or []
    events = subject.get('major_events') or []
    role = 'a player character' if subject.get('is_player') else 'an NPC who can hold conversations'
    vantage = 'You took part in the fight.' if subject.get('was_combatant') else 'You witnessed the fight nearby, even if you were not on the initiative list.'

    system_prompt = (
        f"You write a first-person combat recap for {name}, {role} in a D&D 5e game. "
        "Write in first-person past tense, 2-5 short paragraphs. "
        "Include when and where the battle happened when that information is given. "
        "Mention deaths, unconsciousness, and any conversation or shouted words from the fight. "
        "Do not invent facts that are not in the provided material. "
        "If you were unconscious or dead, write only what you last perceived. "
        "No markdown headings. No OOC commentary.\n"
        "Start with:\nTITLE: <short title>\nSUMMARY: <1-2 sentences>\n"
        "Then the recap body."
    )
    user_parts = [
        f"You are {name}. {vantage} Your status at the end: {subject.get('status')}.",
        f"When: {when.get('description') or 'unknown time'}",
        f"Where: {location.get('description') or location.get('map_name') or 'unknown place'}",
        f"Outcome: {snapshot.get('outcome')} after about {snapshot.get('round_count')} combat round(s).",
        "Combatants:\n" + ('\n'.join(roster_lines) if roster_lines else '- (none listed)'),
    ]
    if events:
        user_parts.append("Major events you must mention if you could perceive them:\n" + '\n'.join(f"- {item}" for item in events))
    if beats:
        user_parts.append("Conversation during the fight:\n" + '\n'.join(f"- {item}" for item in beats))
    if log_lines:
        user_parts.append("Combat log (what you could perceive):\n" + '\n'.join(f"- {item}" for item in log_lines[-120:]))
    else:
        user_parts.append("Combat log: (no visible lines; rely on roster, outcome, and conversation).")
    return system_prompt, '\n\n'.join(user_parts)


def _llm_summarize(llm_handler, *, conversation_id: str, system_prompt: str, user_prompt: str) -> Optional[str]:
    if llm_handler is None:
        return None
    try:
        from webapp.conversation_service import is_npc_llm_failure_response
    except Exception:
        def is_npc_llm_failure_response(text):  # type: ignore
            return not str(text or '').strip()

    try:
        send = getattr(llm_handler, 'send_message', None)
        if callable(send):
            response = send(
                [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt},
                ],
                {
                    'response_mode': 'conversation',
                    'log_label': conversation_id,
                    'skip_continuation': True,
                },
            )
        else:
            create = getattr(llm_handler, 'create_conversation', None)
            if not callable(create):
                return None
            create(conversation_id, system_prompt)
            llm_handler.add_message(conversation_id, 'user', user_prompt)
            response = llm_handler.generate_response(conversation_id)
    except Exception:
        logger.exception('Combat recap LLM call failed for %s', conversation_id)
        return None
    text = str(response or '').strip()
    if not text or is_npc_llm_failure_response(text):
        return None
    return text


def summarize_subject(subject: Dict[str, Any], snapshot: Dict[str, Any], llm_handler=None) -> Dict[str, str]:
    fallback = _heuristic_recap(subject, snapshot)
    system_prompt, user_prompt = _build_prompts(subject, snapshot)
    llm_text = _llm_summarize(
        llm_handler,
        conversation_id=f"combat_recap:{subject.get('uid')}",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    if not llm_text:
        return fallback
    return parse_llm_recap(llm_text, fallback)


def _write_player_journal(game, entity, recap: Dict[str, str], snapshot: Dict[str, Any]) -> bool:
    if not hasattr(entity, 'add_journal_entry'):
        return False
    location = snapshot.get('location') or {}
    subject_location = None
    for item in snapshot.get('subjects') or []:
        if item.get('uid') == _entity_uid(entity):
            subject_location = item.get('location') or {}
            break
    map_name = (subject_location or location).get('map_name')
    tags = ['combat', 'recap', snapshot.get('outcome') or 'ended']
    if snapshot.get('outcome') == 'tpk':
        tags.append('tpk')
    seed_id = f"combat_recap:{snapshot.get('game_time')}:{map_name or 'map'}:{_entity_uid(entity)}"
    existing = getattr(entity, 'journal', None) or []
    if any(entry.get('seed_id') == seed_id for entry in existing if isinstance(entry, dict)):
        return False
    body = recap.get('body') or recap.get('summary') or ''
    when = snapshot.get('when') or {}
    where = (subject_location or location).get('description')
    header_bits = [bit for bit in (when.get('clock_label'), where) if bit]
    if header_bits and body and header_bits[0] not in body:
        prefix = ' — '.join(header_bits)
        body = f"{prefix}\n\n{body}"
    stored = entity.add_journal_entry(
        body,
        kind='combat',
        title=recap.get('title') or 'Battle recap',
        source='battle_recap',
        map_name=map_name,
        tags=tags,
        read=False,
        seed_id=seed_id,
    )
    if stored is None:
        return False
    try:
        from webapp.blueprints.helpers.journal_utils import (
            _emit_journal_update,
            _log_journal_entry_to_campaign_db,
            _persist_journal_change,
        )

        _log_journal_entry_to_campaign_db(entity, stored)
        _persist_journal_change(entity)
        _emit_journal_update([_entity_uid(entity)], reason='combat_recap')
    except Exception:
        logger.debug('Combat recap journal side-effects failed for %s', _entity_uid(entity), exc_info=True)
    return True


def _write_npc_memory(game, entity, recap: Dict[str, str], snapshot: Dict[str, Any]) -> bool:
    store = getattr(game, 'npc_memory_store', None) if game is not None else None
    if store is None:
        try:
            from webapp.blueprints.helpers.runtime_state import get_entity_rag_handler

            rag = get_entity_rag_handler()
            if rag is not None:
                store = rag._npc_memory_store()  # noqa: SLF001
        except Exception:
            store = None
    uid = _entity_uid(entity)
    if store is None or not uid:
        return False

    importance = 3
    status = None
    events = []
    for item in snapshot.get('subjects') or []:
        if item.get('uid') == uid:
            status = item.get('status')
            events = item.get('major_events') or []
            break
    if status == 'dead' or any('died' in str(event).lower() for event in events):
        importance = 5 if any(row.get('is_player') and row.get('status') == 'dead' for row in snapshot.get('combatants') or []) else 4
    elif snapshot.get('outcome') == 'tpk':
        importance = 5

    tags = ['combat', 'battle_recap', snapshot.get('outcome') or 'ended']
    location = snapshot.get('location') or {}
    for item in snapshot.get('subjects') or []:
        if item.get('uid') == uid:
            location = item.get('location') or location
            break
    for label in (location.get('annotation_labels') or [])[:3]:
        tags.append(str(label))

    try:
        store.create(
            uid,
            title=recap.get('title') or 'Battle recap',
            summary=recap.get('summary'),
            body=recap.get('body'),
            tags=tags,
            importance=importance,
            game_time=int(snapshot.get('game_time') or 0),
            source='battle_recap',
            created_by='system',
        )
    except Exception:
        logger.exception('Failed to store combat recap memory for %s', uid)
        return False
    return True


def _resolve_entity(game, uid: str):
    if not game or not uid:
        return None
    getter = getattr(game, 'get_entity_by_uid', None)
    if callable(getter):
        try:
            entity = getter(uid)
            if entity is not None:
                return entity
        except Exception:
            pass
    session = getattr(game, 'game_session', None)
    if session is not None and hasattr(session, 'entity_by_uid'):
        try:
            return session.entity_by_uid(uid)
        except Exception:
            return None
    return None


def apply_recap_snapshot(game, snapshot: Dict[str, Any], *, llm_handler=None) -> Dict[str, int]:
    stats = {'players': 0, 'npcs': 0, 'skipped': 0}
    if not snapshot:
        return stats
    for subject in snapshot.get('subjects') or []:
        entity = _resolve_entity(game, subject.get('uid'))
        if entity is None:
            stats['skipped'] += 1
            continue
        recap = summarize_subject(subject, snapshot, llm_handler=llm_handler)
        try:
            if subject.get('is_player') or _is_player(entity):
                if _write_player_journal(game, entity, recap, snapshot):
                    stats['players'] += 1
                else:
                    stats['skipped'] += 1
            elif subject.get('dialog') or _is_dialog_npc(entity):
                if _write_npc_memory(game, entity, recap, snapshot):
                    stats['npcs'] += 1
                else:
                    stats['skipped'] += 1
            else:
                stats['skipped'] += 1
        except Exception:
            logger.exception('Combat recap persist failed for %s', subject.get('uid'))
            stats['skipped'] += 1
    if stats['players'] or stats['npcs']:
        save = getattr(game, 'save_game_async', None) if game is not None else None
        if callable(save):
            try:
                save()
            except Exception:
                logger.debug('Combat recap autosave skipped', exc_info=True)
    return stats


def _llm_handler_for_recap():
    try:
        from webapp.blueprints.helpers.runtime_state import get_llm_handler

        handler = get_llm_handler()
        if handler is not None:
            return handler
    except Exception:
        pass
    try:
        from webapp.blueprints.helpers.runtime_state import get_llm_conversation_handler

        return get_llm_conversation_handler()
    except Exception:
        return None


def process_battle_combat_recap(game_manager, session, *, background: bool = True, llm_handler=None) -> bool:
    """Summarize the just-finished battle into journals and NPC memories."""
    config = load_combat_recap_config(session)
    if not config.get('enabled', True):
        return False
    battle = game_manager.get_current_battle() if game_manager is not None else None
    if battle is None or not battle_had_combat_round(battle):
        return False

    snapshot = build_recap_snapshot(game_manager, session, battle)
    if not snapshot:
        return False

    handler = llm_handler if llm_handler is not None else _llm_handler_for_recap()

    if not background:
        apply_recap_snapshot(game_manager, snapshot, llm_handler=handler)
        return True

    def _worker():
        try:
            stats = apply_recap_snapshot(game_manager, snapshot, llm_handler=handler)
            logger.info(
                'Combat recap complete players=%s npcs=%s skipped=%s map=%s',
                stats.get('players'),
                stats.get('npcs'),
                stats.get('skipped'),
                (snapshot.get('location') or {}).get('map_name'),
            )
        except Exception:
            logger.exception('Combat recap worker failed')

    thread = threading.Thread(target=_worker, name='n20-combat-recap', daemon=True)
    thread.start()
    return True


def attach_battle_combat_recap():
    """Register start-of-combat watermark and battle-end recap handlers."""
    from webapp.blueprints.helpers.runtime_state import get_current_game, get_event_manager

    def _on_start_of_combat(event):
        try:
            game = get_current_game()
            session = getattr(game, 'game_session', None)
            snapshot_battle_start(game, session)
        except Exception:
            logger.debug('Combat recap start snapshot failed', exc_info=True)

    def _on_battle_end(game_manager, session):
        return process_battle_combat_recap(game_manager, session, background=True)

    try:
        event_manager = get_event_manager()
        event_manager.register_event_listener('start_of_combat', _on_start_of_combat)
    except Exception:
        logger.exception('Failed to register combat recap start_of_combat listener')

    try:
        get_current_game().register_event_handler('on_battle_end', _on_battle_end)
    except Exception:
        logger.exception('Failed to register combat recap battle-end handler')
    return True
