"""
NPC voice YAML support for natural_20.py.

Provides utilities for loading, validating, and persisting voice
configuration in NPC YAML files.

Voice config format in NPC YAML:
    gender: female          # top-level gender (male|female) — preferred for TTS
    voice:
      prompt: "Old woman with a raspy voice"
      gender: female      # optional duplicate inside voice for tooling
      age: elderly        # optional: young|mature|adult|elderly
      style: "lazy"
      accent: "british"   # CosyVoice instruct accent (american, british, romanian, ...)
      language: "en"      # optional language hint (en, zh, ...)
      reference_audio: null  # or path to custom voice sample
      locked: true
"""

import re
from pathlib import Path
from typing import Any, Optional

from webapp.tts.voice_profile import VoiceProfile, VoiceStrategy


_FEMALE_HINTS = (
    'woman', 'female', 'girl', 'lady', 'maiden', 'mother', 'sister', 'wife',
    'daughter', 'queen', 'princess', 'priestess', 'sorceress', 'witch',
    'matron', 'crone', 'grandmother', 'aunt', 'nun', 'widow', 'barmaid',
    'waitress', 'hostess', 'enchantress', 'empress', 'duchess', 'countess',
)
_MALE_HINTS = (
    'man', 'male', 'boy', 'lord', 'king', 'prince', 'father', 'brother',
    'husband', 'son', 'baron', 'duke', 'count', 'sir', 'uncle', 'grandfather',
    'priest', 'monk', 'soldier', 'guard', 'captain', 'barkeeper', 'barkeep',
    'bartender', 'innkeeper', 'blacksmith', 'merchant', 'wizard', 'sage',
    'bandit', 'thief', 'rogue', 'knight', 'paladin', 'cleric', 'druid',
    'gravelly', 'gruff', 'baritone', 'raspy', 'booming', 'gravel',
)
_CHILD_HINTS = ('child', 'kid', 'young boy', 'young girl', 'youth', 'teen', 'adolescent')
_ELDER_HINTS = ('old', 'aged', 'elderly', 'ancient', 'crone', 'wizened', 'venerable', 'greybeard')
_MATURE_HINTS = (
    'bartender', 'barkeeper', 'barkeep', 'innkeeper', 'tavern keeper', 'tavernkeeper',
    'landlord', 'shopkeeper', 'merchant', 'blacksmith', 'veteran', 'matron', 'widow',
    'stout', 'grizzled', 'weathered',
)


def _hint_matches(hint: str, haystack: str) -> bool:
    if ' ' in hint:
        return hint in haystack
    return bool(re.search(rf'\b{re.escape(hint)}\b', haystack))


def _score_hints(hints: tuple[str, ...], haystack: str) -> int:
    return sum(1 for hint in hints if _hint_matches(hint, haystack))


def _entity_properties(entity) -> dict:
    props = getattr(entity, 'properties', None)
    return props if isinstance(props, dict) else {}


def _entity_label(entity) -> str:
    try:
        if hasattr(entity, 'label'):
            return str(entity.label() or '').strip()
    except Exception:
        pass
    props = _entity_properties(entity)
    return str(props.get('label') or props.get('name') or getattr(entity, 'name', '') or 'NPC').strip()


def _entity_race(entity) -> str:
    props = _entity_properties(entity)
    race = props.get('race') or props.get('species')
    if isinstance(race, list):
        race = race[0] if race else ''
    return str(race or '').strip()


def _voice_dict(props: dict) -> dict:
    voice = props.get('voice')
    return voice if isinstance(voice, dict) else {}


def normalize_gender(value: Any) -> Optional[str]:
    """Normalize YAML gender/sex values to male|female."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in ('f', 'female', 'woman', 'girl', 'lady', 'she', 'she/her', 'feminine'):
        return 'female'
    if text in ('m', 'male', 'man', 'boy', 'he', 'he/him', 'masculine'):
        return 'male'
    if 'female' in text or 'woman' in text or 'girl' in text:
        return 'female'
    if 'male' in text or 'man' in text or 'boy' in text:
        return 'male'
    return None


def normalize_age_band(value: Any) -> Optional[str]:
    """Normalize YAML age hints to young|mature|elderly (adult is implicit)."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in ('young', 'youth', 'child', 'kid', 'teen', 'adolescent', 'juvenile'):
        return 'young'
    if text in ('mature', 'middle-aged', 'middle aged', 'midlife', 'adult', 'grown', 'grown-up', 'grownup'):
        return 'mature'
    if text in ('elderly', 'elder', 'old', 'aged', 'senior', 'ancient', 'venerable'):
        return 'elderly'
    if any(hint in text for hint in _CHILD_HINTS):
        return 'young'
    if any(hint in text for hint in _ELDER_HINTS):
        return 'elderly'
    if any(hint in text for hint in _MATURE_HINTS):
        return 'mature'
    return None


def _explicit_gender(props: dict, voice: dict) -> Optional[str]:
    for key in ('gender', 'sex', 'voice_gender'):
        for source in (voice, props):
            if key in source:
                normalized = normalize_gender(source.get(key))
                if normalized:
                    return normalized
    return None


def _explicit_age_band(props: dict, voice: dict) -> Optional[str]:
    for key in ('age', 'voice_age', 'age_band'):
        for source in (voice, props):
            if key in source:
                normalized = normalize_age_band(source.get(key))
                if normalized:
                    return normalized
    return None


def infer_voice_profile(voice_prompt: str = '', entity=None, npc_data: Optional[dict] = None) -> dict[str, Any]:
    """Infer gender/age cues from YAML voice config, NPC properties, and prompt text."""
    if npc_data is not None:
        props = npc_data if isinstance(npc_data, dict) else {}
    elif entity is not None:
        props = _entity_properties(entity)
    else:
        props = {}
    voice = _voice_dict(props)
    fragments: list[str] = []

    for key in ('gender', 'sex', 'age', 'voice_gender', 'voice_age', 'age_band'):
        value = voice.get(key) if key in voice else props.get(key)
        if value:
            fragments.append(str(value).strip())

    description = str(props.get('description') or props.get('backstory') or '').strip()
    if description:
        fragments.append(description[:240])

    label = _entity_label(entity) if entity is not None else str(props.get('kind') or props.get('name') or '').strip()
    if label:
        fragments.append(label)

    prompt = str(voice_prompt or voice.get('prompt') or voice.get('voice_prompt') or '').strip()
    if prompt:
        fragments.append(prompt)

    haystack = ' '.join(fragments).lower()
    gender = _explicit_gender(props, voice)
    if gender is None:
        female_score = _score_hints(_FEMALE_HINTS, haystack)
        male_score = _score_hints(_MALE_HINTS, haystack)
        if re.search(r'\b(female|woman|girl|she/her)\b', haystack):
            female_score += 2
        if re.search(r'\b(male|man|boy|he/him)\b', haystack):
            male_score += 2
        if re.search(r'\b(she|her|hers)\b', haystack):
            female_score += 2
        if re.search(r'\b(he|him|his)\b', haystack):
            male_score += 2
        if female_score > male_score:
            gender = 'female'
        elif male_score > female_score:
            gender = 'male'

    age_band = _explicit_age_band(props, voice)
    if age_band is None:
        if _score_hints(_CHILD_HINTS, haystack):
            age_band = 'young'
        elif _score_hints(_ELDER_HINTS, haystack):
            age_band = 'elderly'
        elif _score_hints(_MATURE_HINTS, haystack):
            age_band = 'mature'

    return {
        'gender': gender,
        'age_band': age_band,
        'race': _entity_race(entity) if entity is not None else str(
            (props.get('race') or props.get('species') or [''])[0]
            if isinstance(props.get('race') or props.get('species'), list)
            else (props.get('race') or props.get('species') or '')
        ).strip(),
        'label': label,
    }


def _parse_voice_strategy(value: Any) -> VoiceStrategy:
    if value is None:
        return VoiceStrategy.AUTO
    text = str(value).strip().lower()
    if not text or text == 'auto':
        return VoiceStrategy.AUTO
    try:
        return VoiceStrategy(text)
    except ValueError:
        return VoiceStrategy.AUTO


def _parse_traits(voice: dict, props: dict) -> list[str]:
    raw = voice.get('traits') or voice.get('voice_traits') or props.get('voice_traits')
    if raw is None:
        return []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(',') if part.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def build_voice_profile_from_entity(
    entity,
    *,
    npc_uid: Optional[str] = None,
    npc_yaml_dir: Optional[str] = None,
) -> VoiceProfile:
    """
    Build a canonical VoiceProfile from an in-game NPC entity.

    This is the preferred entry point for conversation/TTS code paths.
    Merges generated campaign assets from assets/voice_profiles/ when present.
    """
    props = _entity_properties(entity)
    voice = _voice_dict(props)
    uid = npc_uid or str(getattr(entity, 'entity_uid', '') or props.get('uid') or '').strip()
    if not uid:
        uid = 'npc_unknown'

    campaign_root = getattr(getattr(entity, 'session', None), 'root_path', None)
    asset_voice: Optional[dict] = None
    if campaign_root:
        try:
            from natural20.tts.campaign_voice_profiles import load_campaign_voice_asset

            asset = load_campaign_voice_asset(
                campaign_root,
                entity_uid=uid,
                npc_type=str(getattr(entity, 'npc_type', '') or props.get('kind') or ''),
            )
            if isinstance(asset, dict) and isinstance(asset.get('voice'), dict):
                asset_voice = asset['voice']
        except Exception:
            asset_voice = None

    if asset_voice:
        merged_props = dict(props)
        merged_voice = dict(voice)
        merged_voice.update(asset_voice)
        merged_props['voice'] = merged_voice
        props = merged_props
        voice = merged_voice

    prompt = build_npc_voice_prompt(entity)
    if asset_voice and str(asset_voice.get('prompt') or '').strip():
        prompt = str(asset_voice['prompt']).strip()
    inferred = infer_voice_profile(prompt, entity, npc_data=props)

    ref_raw = voice.get('reference_audio')
    campaign_root = getattr(getattr(entity, 'session', None), 'root_path', None)
    reference_audio = resolve_reference_audio_path(
        str(ref_raw) if ref_raw else None,
        npc_yaml_dir=npc_yaml_dir,
        campaign_root=str(campaign_root) if campaign_root else None,
        npc_uid=uid,
        npc_type=str(getattr(entity, 'npc_type', '') or props.get('kind') or '').strip() or None,
    )
    if not reference_audio and campaign_root:
        from webapp.tts.voice_baking import discover_baked_sample

        reference_audio = discover_baked_sample(
            str(campaign_root),
            uid,
            npc_type=str(getattr(entity, 'npc_type', '') or props.get('kind') or '').strip() or None,
        )

    strategy = _parse_voice_strategy(voice.get('strategy') or voice.get('mode'))
    provider = voice.get('provider') or voice.get('tts_provider')
    if provider:
        provider = str(provider).strip().lower() or None

    return VoiceProfile(
        npc_uid=uid,
        prompt=prompt,
        gender=normalize_gender(voice.get('gender') or props.get('gender')) or inferred.get('gender'),
        age_band=normalize_age_band(
            voice.get('age') or voice.get('age_band') or props.get('age')
        ) or inferred.get('age_band'),
        race=inferred.get('race') or _entity_race(entity),
        label=inferred.get('label') or _entity_label(entity),
        accent=str(voice.get('accent')).strip() if voice.get('accent') is not None else None,
        language=str(voice.get('language')).strip() if voice.get('language') else None,
        style=str(voice.get('style') or 'calm').strip() or 'calm',
        reference_audio=reference_audio,
        strategy=strategy,
        provider=provider,
        traits=_parse_traits(voice, props),
        locked=bool(voice.get('locked', False)),
        campaign_root=str(campaign_root) if campaign_root else None,
    )


def ensure_npc_voice_locked(tts_manager, entity, *, npc_yaml_dir: Optional[str] = None) -> Optional[VoiceProfile]:
    """
    Register and lock an NPC voice if needed. Returns the VoiceProfile or None.
    """
    if tts_manager is None or not getattr(tts_manager, 'is_enabled', lambda: False)():
        return None
    profile = build_voice_profile_from_entity(entity, npc_yaml_dir=npc_yaml_dir)
    if not profile.npc_uid:
        return None
    locked = set(tts_manager.list_locked_voices() or [])
    if profile.npc_uid not in locked:
        tts_manager.register_voice_prompt(profile.npc_uid, profile.prompt)
        creator = getattr(tts_manager, 'create_voice_from_profile', None)
        if callable(creator):
            creator(profile)
        else:
            tts_manager.create_voice(
                npc_uid=profile.npc_uid,
                voice_prompt=profile.prompt,
                reference_audio=profile.reference_audio,
                accent=profile.accent,
                language=profile.language,
                voice_metadata=profile.to_metadata(),
            )
    else:
        try:
            tts_manager.update_voice_options(
                profile.npc_uid,
                accent=profile.accent,
                language=profile.language,
            )
        except Exception:
            pass
        try:
            engine = tts_manager.get_engine()
            config = getattr(engine, '_voice_registry', {}).get(profile.npc_uid)
            if config is not None:
                config.prompt_description = profile.prompt
                config.extra_metadata = config.extra_metadata or {}
                config.extra_metadata['profile'] = profile.to_metadata()
                config.extra_metadata['design_prompt'] = profile.design_prompt()
        except Exception:
            pass
    return profile


def build_npc_voice_prompt(entity, fallback_label: str = 'NPC') -> str:
    """Build a descriptive TTS voice prompt from NPC YAML/properties."""
    props = _entity_properties(entity)
    voice = props.get('voice') if isinstance(props.get('voice'), dict) else {}
    explicit = voice.get('prompt') or voice.get('voice_prompt')
    if explicit:
        return str(explicit).strip()

    label = _entity_label(entity) or fallback_label
    profile = infer_voice_profile('', entity)
    parts: list[str] = []

    if profile.get('age_band') == 'elderly':
        parts.append('elderly')
    elif profile.get('age_band') == 'young':
        parts.append('young')
    elif profile.get('age_band') == 'mature':
        parts.append('mature')

    if profile.get('gender') == 'male':
        parts.append('male')
    elif profile.get('gender') == 'female':
        parts.append('female')

    race = profile.get('race')
    if race:
        parts.append(f"{race}")

    parts.append(label)
    return ' '.join(part for part in parts if part).strip() or f"Fantasy NPC voice for {label}"


def voice_character_instruct(profile: dict[str, Any], voice_prompt: str = '') -> str:
    """Turn inferred NPC traits into a CosyVoice delivery clause."""
    bits: list[str] = []
    prompt = str(voice_prompt or '').strip()
    if prompt:
        bits.append(prompt)

    gender = profile.get('gender')
    age_band = profile.get('age_band')
    if gender == 'male':
        bits.append('use an adult male voice')
    elif gender == 'female':
        bits.append('use an adult female voice')

    if age_band == 'elderly':
        bits.append('sound elderly')
    elif age_band == 'young':
        bits.append('sound youthful')
    elif age_band == 'mature':
        bits.append('sound like a mature middle-aged adult, not a teenager')

    race = str(profile.get('race') or '').strip()
    if race and race.lower() not in prompt.lower():
        bits.append(f"with a {race} fantasy character quality")

    # Preserve order while dropping duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for bit in bits:
        normalized = bit.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(bit.strip())
    return ', '.join(ordered)


def load_voice_config(npc_data: dict) -> dict:
    """
    Load and validate voice configuration from NPC YAML data.
    
    Args:
        npc_data: The NPC YAML data dictionary
    
    Returns:
        Normalized voice config dict with defaults
    """
    voice_data = npc_data.get('voice', {})
    if not isinstance(voice_data, dict):
        voice_data = {}
    
    return {
        'prompt': voice_data.get('prompt', ''),
        'style': voice_data.get('style', 'calm'),
        'gender': normalize_gender(voice_data.get('gender') or npc_data.get('gender')),
        'age': normalize_age_band(voice_data.get('age') or voice_data.get('age_band') or npc_data.get('age')),
        'accent': voice_data.get('accent'),
        'language': voice_data.get('language'),
        'reference_audio': voice_data.get('reference_audio'),
        'locked': bool(voice_data.get('locked', False)),
        'strategy': voice_data.get('strategy') or voice_data.get('mode'),
        'provider': voice_data.get('provider') or voice_data.get('tts_provider'),
        'traits': voice_data.get('traits') or voice_data.get('voice_traits') or [],
    }


def save_voice_config(npc_data: dict, voice_config: dict) -> dict:
    """
    Save voice configuration to NPC YAML data.
    
    Args:
        npc_data: The NPC YAML data dictionary
        voice_config: Voice config dict to save
    
    Returns:
        Updated NPC data dict
    """
    npc_data['voice'] = {
        'prompt': voice_config.get('prompt', ''),
        'style': voice_config.get('style', 'calm'),
        'gender': voice_config.get('gender'),
        'age': voice_config.get('age'),
        'accent': voice_config.get('accent'),
        'language': voice_config.get('language'),
        'reference_audio': voice_config.get('reference_audio'),
        'locked': bool(voice_config.get('locked', False)),
        'strategy': voice_config.get('strategy'),
        'provider': voice_config.get('provider'),
        'traits': voice_config.get('traits') or [],
    }
    if voice_config.get('gender') and not npc_data.get('gender'):
        npc_data['gender'] = voice_config.get('gender')
    return npc_data


def has_voice_config(npc_data: dict) -> bool:
    """Check if an NPC has voice configuration."""
    voice_data = npc_data.get('voice', {})
    if not isinstance(voice_data, dict):
        return False
    return bool(voice_data.get('prompt')) or voice_data.get('locked', False)


def get_voice_prompt(npc_data: dict) -> Optional[str]:
    """Get the voice prompt for an NPC."""
    voice_data = npc_data.get('voice', {})
    if isinstance(voice_data, dict):
        return voice_data.get('prompt') or voice_data.get('voice_prompt')
    return None


def set_voice_prompt(npc_data: dict, prompt: str) -> None:
    """Set the voice prompt for an NPC."""
    if 'voice' not in npc_data or not isinstance(npc_data.get('voice'), dict):
        npc_data['voice'] = {}
    npc_data['voice']['prompt'] = prompt
    npc_data['voice']['locked'] = True


def clear_voice_config(npc_data: dict) -> None:
    """Clear the voice configuration for an NPC."""
    if 'voice' in npc_data:
        del npc_data['voice']


def resolve_reference_audio_path(
    reference_audio: Optional[str],
    npc_yaml_dir: Optional[str] = None,
    *,
    campaign_root: Optional[str] = None,
    npc_uid: Optional[str] = None,
    npc_type: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve a reference audio path relative to the NPC's YAML directory.

    Args:
        reference_audio: The reference audio path from YAML
        npc_yaml_dir: Directory containing the NPC YAML file
        campaign_root: Campaign root for assets/voice_samples lookup
        npc_uid: NPC uid for default baked sample path

    Returns:
        Resolved absolute path, or None if not found
    """
    if reference_audio:
        ref_path = Path(reference_audio)

        # If already absolute, check existence
        if ref_path.is_absolute():
            return str(ref_path) if ref_path.exists() else None

        # If relative, try NPC YAML directory first, then project root
        if npc_yaml_dir:
            combined = Path(npc_yaml_dir) / ref_path
            if combined.exists():
                return str(combined)

        if campaign_root:
            combined = Path(campaign_root) / ref_path
            if combined.exists():
                return str(combined)

        # Try as-is (relative to working directory)
        if ref_path.exists():
            return str(ref_path)

    if campaign_root and npc_uid:
        from webapp.tts.voice_baking import discover_baked_sample

        baked = discover_baked_sample(campaign_root, npc_uid, npc_type=npc_type)
        if baked:
            return baked

    return None
