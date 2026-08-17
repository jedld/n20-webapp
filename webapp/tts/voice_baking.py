"""
Bake stable NPC voice reference clips for timbre-locked TTS.

VoiceDesign re-samples timbre on every utterance (and folds delivery/emotion into
the same instruct string), so voices drift mid-conversation. Baking generates one
calibration line per NPC, saves it under the campaign's ``assets/voice_samples/``,
then runtime synthesis uses Qwen3 Base clone (in-process or ``qwen3_vllm``).

Set ``N20_TTS_BAKE_PROVIDER=cosyvoice`` or ``elevenlabs`` (or ``--bake-provider``) to
model reference clips with a different engine than runtime ``TTS_PROVIDER``.
The bundled CosyVoice Chinese clips do not produce intelligible English.
Runtime TTS is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

# Baked clips are written as WAV. Dropped references may be MP3.
VOICE_SAMPLE_EXTENSIONS = (".wav", ".mp3")
_DEFAULT_REF_TEXT = (
    "A steady voice for dialogue. Warm, clear, and natural, just as I always speak."
)

def bake_sample_text(accent: Optional[str] = None) -> str:
    """Spoken calibration line for VoiceDesign bake (must match the saved .ref.txt)."""
    override = os.environ.get("N20_TTS_BAKE_SAMPLE_TEXT", "").strip()
    if override:
        return override
    from webapp.tts.accent_instruct import bake_text_for_accent

    return bake_text_for_accent(accent)


def _safe_npc_uid(npc_uid: str) -> str:
    return (npc_uid or "npc_unknown").strip().replace("/", "_")


def pick_newest_audio(paths: list[Path]) -> Optional[Path]:
    existing = [path for path in paths if path.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda path: (path.stat().st_mtime_ns, path.suffix.lower() != ".wav"))


def voice_sample_paths_for_stem(directory: Path, stem: str) -> list[Path]:
    return [directory / f"{stem}{ext}" for ext in VOICE_SAMPLE_EXTENSIONS]


def first_existing_voice_sample(*paths: Path) -> Optional[str]:
    newest = pick_newest_audio(list(paths))
    return str(newest) if newest is not None else None


def list_campaign_voice_samples(samples_dir: Path) -> list[Path]:
    """One audio file per stem under ``assets/voice_samples`` (newest WAV/MP3 wins)."""
    if not samples_dir.is_dir():
        return []
    by_stem: dict[str, list[Path]] = {}
    for ext in VOICE_SAMPLE_EXTENSIONS:
        for path in samples_dir.glob(f"*{ext}"):
            if path.name.endswith(".ref.txt"):
                continue
            by_stem.setdefault(path.stem, []).append(path)
    chosen: list[Path] = []
    for stem in sorted(by_stem):
        newest = pick_newest_audio(by_stem[stem])
        if newest is not None:
            chosen.append(newest)
    return chosen


def ref_text_sidecar_paths(audio_path: Path) -> list[Path]:
    """Sidecars: ``npc.ref.txt`` (preferred) and ``npc.wav.ref.txt`` / ``npc.mp3.ref.txt``."""
    audio_path = Path(audio_path)
    return [
        audio_path.with_suffix(".ref.txt"),
        Path(str(audio_path) + ".ref.txt"),
    ]


def load_ref_text_for_audio(audio_path: Optional[str], *, default: Optional[str] = None) -> str:
    fallback = default if default is not None else _DEFAULT_REF_TEXT
    if not audio_path or not Path(audio_path).name:
        return fallback
    for sidecar in ref_text_sidecar_paths(Path(audio_path)):
        if not sidecar.is_file():
            continue
        try:
            text = sidecar.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return fallback


def baked_ref_text_path(campaign_root: Optional[str], npc_uid: str) -> Optional[Path]:
    sample = discover_baked_sample(campaign_root, npc_uid)
    path = Path(sample) if sample else voice_sample_path(campaign_root, npc_uid)
    if path is None:
        return None
    return Path(path).with_suffix(".ref.txt")


def load_baked_ref_text(campaign_root: Optional[str], npc_uid: str) -> str:
    sample = discover_baked_sample(campaign_root, npc_uid)
    if sample:
        text = load_ref_text_for_audio(sample, default="")
        if text:
            return text
    sidecar = baked_ref_text_path(campaign_root, npc_uid)
    if sidecar and sidecar.is_file():
        try:
            text = sidecar.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
    return bake_sample_text()


def save_baked_ref_text(campaign_root: str, npc_uid: str, ref_text: str) -> None:
    sidecar = baked_ref_text_path(campaign_root, npc_uid)
    if sidecar is None:
        return
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(ref_text.strip(), encoding="utf-8")

DEFAULT_MODEL_DESIGN = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"

# Engines that synthesize reference WAVs via Qwen3 VoiceDesign.
_QWEN3_BAKE_PROVIDERS = frozenset({
    "qwen3",
    "mock_qwen3",
    "qwen3_vllm",
    "mock_qwen3_vllm",
})

# Cloud / external modellers used only to write campaign voice_samples.
BAKE_ONLY_PROVIDERS = frozenset({
    "elevenlabs",
    "mock_elevenlabs",
})


def bake_provider_name() -> Optional[str]:
    """Engine used only to model reference clips (``N20_TTS_BAKE_PROVIDER``).

    Independent of runtime ``TTS_PROVIDER``. ``cosyvoice`` / ``elevenlabs`` bake
    clips that Qwen3 Base / vLLM then clones at play time.
    """
    raw = os.environ.get("N20_TTS_BAKE_PROVIDER", "").strip().lower()
    return raw or None


def uses_qwen3_voice_design_bake() -> bool:
    name = bake_provider_name()
    if not name:
        return True
    if name in BAKE_ONLY_PROVIDERS or name in ("cosyvoice", "mock_cosyvoice"):
        return False
    return name in _QWEN3_BAKE_PROVIDERS


def bake_voices_enabled() -> bool:
    return os.environ.get("N20_TTS_BAKE_VOICES", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def clone_model_id() -> str:
    explicit = os.environ.get("QWEN3_TTS_CLONE_MODEL", "").strip()
    if explicit:
        return explicit
    return "Qwen/Qwen3-TTS-12Hz-1.7B-Base"


def voice_sample_relative_path(npc_uid: str, suffix: str = ".wav") -> Path:
    safe_uid = _safe_npc_uid(npc_uid)
    ext = suffix if suffix.startswith(".") else f".{suffix}"
    return Path("assets") / "voice_samples" / f"{safe_uid}{ext}"


def voice_sample_path(
    campaign_root: Optional[str],
    npc_uid: str,
    suffix: str = ".wav",
) -> Optional[Path]:
    """Write path for a baked sample. Default ``.wav``; ElevenLabs bake uses ``.mp3``."""
    if not campaign_root:
        return None
    return Path(campaign_root) / voice_sample_relative_path(npc_uid, suffix=suffix)


def discover_baked_sample(
    campaign_root: Optional[str],
    npc_uid: str,
    *,
    npc_type: Optional[str] = None,
) -> Optional[str]:
    """Find a baked WAV or MP3 for an entity uid, optionally falling back to ``type_<npc_type>``."""
    if campaign_root:
        samples_dir = Path(campaign_root) / "assets" / "voice_samples"
        found = first_existing_voice_sample(*voice_sample_paths_for_stem(samples_dir, _safe_npc_uid(npc_uid)))
        if found:
            return found
        if npc_type:
            from natural20.tts.campaign_voice_profiles import _slug

            type_uid = f"type_{_slug(npc_type)}"
            type_found = first_existing_voice_sample(
                *voice_sample_paths_for_stem(samples_dir, _safe_npc_uid(type_uid))
            )
            if type_found:
                return type_found
    return None


def audio_file_signature(path: Optional[str]) -> Optional[dict[str, Any]]:
    if not path:
        return None
    audio = Path(path)
    if not audio.is_file():
        return None
    try:
        stat = audio.stat()
    except OSError:
        return None
    return {
        "path": str(audio),
        "name": audio.name,
        "mtime_ns": int(stat.st_mtime_ns),
        "size": int(stat.st_size),
    }


def voice_profile_state_path(campaign_root: Optional[str], npc_uid: str) -> Optional[Path]:
    if not campaign_root:
        return None
    return Path(campaign_root) / "assets" / "voice_samples" / f"{_safe_npc_uid(npc_uid)}.profile.json"


def load_voice_profile_state(campaign_root: Optional[str], npc_uid: str) -> Optional[dict[str, Any]]:
    path = voice_profile_state_path(campaign_root, npc_uid)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def save_voice_profile_state(
    campaign_root: Optional[str],
    npc_uid: str,
    *,
    fingerprint: str,
    audio_path: Optional[str],
    source: str = "baked",
) -> None:
    path = voice_profile_state_path(campaign_root, npc_uid)
    if path is None:
        return
    payload: dict[str, Any] = {
        "fingerprint": fingerprint or "",
        "source": source,
        "audio": Path(audio_path).name if audio_path else "",
    }
    signature = audio_file_signature(audio_path)
    if signature:
        payload.update(
            {
                "mtime_ns": signature["mtime_ns"],
                "size": signature["size"],
            }
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        pass


def voice_profile_fingerprint(profile: Any) -> str:
    """Stable hash of YAML voice identity (prompt, accent, traits, strategy)."""
    if profile is None:
        return ""
    nested: dict[str, Any] = {}
    if hasattr(profile, "design_prompt"):
        try:
            strategy = getattr(profile, "strategy", None)
            payload = {
                "accent": getattr(profile, "accent", None) or "",
                "age_band": getattr(profile, "age_band", None) or "",
                "design_prompt": profile.design_prompt() or "",
                "gender": getattr(profile, "gender", None) or "",
                "language": getattr(profile, "language", None) or "",
                "prompt": getattr(profile, "prompt", None) or "",
                "reference_audio": getattr(profile, "reference_audio", None) or "",
                "strategy": getattr(strategy, "value", strategy) or "",
                "traits": list(getattr(profile, "traits", None) or []),
            }
        except Exception:
            payload = {"prompt": str(getattr(profile, "prompt", "") or "")}
    elif isinstance(profile, dict):
        nested = profile.get("profile") if isinstance(profile.get("profile"), dict) else {}
        traits = nested.get("traits") or profile.get("traits") or []
        if not isinstance(traits, list):
            traits = [str(traits)]
        payload = {
            "accent": profile.get("accent") or nested.get("accent") or "",
            "age_band": nested.get("age_band") or profile.get("age_band") or "",
            "design_prompt": profile.get("design_prompt") or "",
            "gender": nested.get("gender") or profile.get("gender") or "",
            "language": profile.get("language") or nested.get("language") or "",
            "prompt": profile.get("voice_prompt") or profile.get("prompt") or "",
            "reference_audio": profile.get("reference_audio") or nested.get("reference_audio") or "",
            "strategy": nested.get("strategy") or profile.get("strategy") or profile.get("resolved_strategy") or "",
            "traits": [str(t).strip() for t in traits if str(t).strip()],
        }
    else:
        payload = {"prompt": str(profile)}
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def is_user_provided_sample(audio_path: Optional[str], *, state: Optional[dict[str, Any]] = None) -> bool:
    if state and str(state.get("source") or "").strip().lower() == "provided":
        return True
    if not audio_path:
        return False
    return Path(audio_path).suffix.lower() == ".mp3"


def sample_source_for_profile(profile: Any, audio_path: Optional[str] = None) -> str:
    explicit = getattr(profile, "reference_audio", None) if not isinstance(profile, dict) else (
        profile.get("reference_audio") if isinstance(profile, dict) else None
    )
    if isinstance(profile, dict) and not explicit:
        nested = profile.get("profile") if isinstance(profile.get("profile"), dict) else {}
        explicit = nested.get("reference_audio")
    if explicit:
        return "provided"
    state = None
    campaign_root = getattr(profile, "campaign_root", None) if not isinstance(profile, dict) else profile.get("campaign_root")
    npc_uid = getattr(profile, "npc_uid", None) if not isinstance(profile, dict) else profile.get("npc_uid")
    if campaign_root and npc_uid:
        state = load_voice_profile_state(str(campaign_root), str(npc_uid))
    if is_user_provided_sample(audio_path, state=state):
        return "provided"
    return "baked"


def voice_profile_needs_refresh(
    profile: Any,
    *,
    locked_config: Any = None,
    audio_path: Optional[str] = None,
) -> bool:
    """True when YAML identity or the on-disk sample no longer matches last lock/register."""
    campaign_root = getattr(profile, "campaign_root", None) if not isinstance(profile, dict) else profile.get("campaign_root")
    npc_uid = getattr(profile, "npc_uid", None) if not isinstance(profile, dict) else profile.get("npc_uid")
    sample = audio_path or (
        discover_baked_sample(str(campaign_root), str(npc_uid)) if campaign_root and npc_uid else None
    )
    current_fp = voice_profile_fingerprint(profile)
    current_sig = audio_file_signature(sample)
    state = load_voice_profile_state(str(campaign_root), str(npc_uid)) if campaign_root and npc_uid else None

    if state:
        if str(state.get("fingerprint") or "") != current_fp:
            return True
        if current_sig:
            if int(state.get("mtime_ns") or 0) != current_sig["mtime_ns"]:
                return True
            if int(state.get("size") or 0) != current_sig["size"]:
                return True
            stored_name = str(state.get("audio") or "")
            if stored_name and stored_name != current_sig["name"]:
                return True
        return False

    if locked_config is not None:
        meta = getattr(locked_config, "extra_metadata", None) or {}
        stored_fp = str(meta.get("profile_fingerprint") or "")
        if stored_fp and stored_fp != current_fp:
            return True
        stored_sig = meta.get("sample_signature")
        if current_sig and stored_sig and stored_sig != current_sig:
            return True
        return False

    return False


def design_model_id() -> str:
    model = (os.environ.get("QWEN3_TTS_MODEL") or "").strip()
    if model and "voicedesign" in model.lower():
        return model
    explicit = (os.environ.get("QWEN3_TTS_DESIGN_MODEL") or "").strip()
    if explicit:
        return explicit
    return DEFAULT_MODEL_DESIGN


def should_bake_voice_profile(profile) -> bool:
    """Whether this NPC profile should get a baked reference clip."""
    if not bake_voices_enabled():
        return False
    from webapp.tts.voice_profile import VoiceStrategy

    strategy = getattr(profile, "strategy", VoiceStrategy.AUTO)
    if strategy in (VoiceStrategy.CLONE, VoiceStrategy.PRESET):
        return False

    prompt = (getattr(profile, "prompt", None) or "").strip()
    locked = bool(getattr(profile, "locked", False))
    if not prompt and not locked:
        return False

    ref = getattr(profile, "reference_audio", None)
    if ref and Path(ref).is_file() and Path(ref).suffix.lower() != ".wav":
        # Hand-dropped MP3 (or other) is the source of truth — do not overwrite.
        if Path(ref).suffix.lower() == ".mp3":
            return False
    if ref and Path(ref).is_file():
        # Explicit YAML clone path: only rebake if it is our generated WAV and stale.
        campaign_root = getattr(profile, "campaign_root", None)
        npc_uid = getattr(profile, "npc_uid", None)
        baked_path = voice_sample_path(str(campaign_root), str(npc_uid)) if campaign_root and npc_uid else None
        if baked_path is None or Path(ref).resolve() != baked_path.resolve():
            return False

    campaign_root = getattr(profile, "campaign_root", None)
    npc_uid = getattr(profile, "npc_uid", None)
    existing = None
    if campaign_root and npc_uid:
        existing = discover_baked_sample(str(campaign_root), str(npc_uid))
    if existing and is_user_provided_sample(
        existing,
        state=load_voice_profile_state(str(campaign_root), str(npc_uid)) if campaign_root and npc_uid else None,
    ):
        return False
    if existing and not voice_profile_needs_refresh(profile, audio_path=existing):
        return False
    return True


def should_bake_qwen3_design_voice(
    *,
    resolved_strategy: str,
    reference_audio: Optional[str],
    campaign_root: Optional[str],
    npc_uid: str,
    profile_strategy: Optional[str] = None,
    profile: Any = None,
) -> bool:
    if not bake_voices_enabled():
        return False
    if not uses_qwen3_voice_design_bake():
        return False
    strategies = {resolved_strategy, profile_strategy or ""}
    strategies = {s.strip().lower() for s in strategies if s}
    if not strategies.intersection({"design", "voice_design", "auto"}):
        return False
    if reference_audio and Path(reference_audio).is_file():
        if Path(reference_audio).suffix.lower() == ".mp3":
            return False
        existing = discover_baked_sample(campaign_root, npc_uid)
        try:
            same_as_baked = bool(
                existing and Path(reference_audio).resolve() == Path(existing).resolve()
            )
        except OSError:
            same_as_baked = False
        if not same_as_baked:
            return False
    existing = discover_baked_sample(campaign_root, npc_uid)
    state = load_voice_profile_state(campaign_root, npc_uid)
    sample = existing or (reference_audio if reference_audio and Path(reference_audio).is_file() else None)
    if sample and is_user_provided_sample(sample, state=state):
        return False
    if existing is None and not (reference_audio and Path(reference_audio).is_file()):
        return True
    if profile is not None and voice_profile_needs_refresh(profile, audio_path=sample):
        return True
    return existing is None
