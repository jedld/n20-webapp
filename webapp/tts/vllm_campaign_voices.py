"""Register baked campaign voice samples with a vLLM-Omni TTS sidecar."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def register_on_start_enabled() -> bool:
    return _env_bool("VLLM_OMNI_TTS_REGISTER_ON_START", True)


@dataclass(frozen=True)
class VoiceRegistrationSummary:
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    total: int = 0


def _ref_text(wav_path: Path) -> str:
    sidecar = wav_path.with_suffix(".ref.txt")
    if sidecar.is_file():
        try:
            text = sidecar.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
    return (
        "A steady voice for dialogue. Warm, clear, and natural, just as I always speak."
    )


def _list_voice_names(client, base_url: str) -> set[str]:
    response = client.get(f"{base_url.rstrip('/')}/v1/audio/voices", timeout=30.0)
    response.raise_for_status()
    payload = response.json()
    names: set[str] = set()
    if isinstance(payload, dict):
        items = payload.get("voices") or payload.get("data") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("name") or item.get("id")
            if name:
                names.add(str(name))
        elif isinstance(item, str):
            names.add(item)
    return names


def register_campaign_voices(
    campaign_root: str | Path,
    *,
    base_url: Optional[str] = None,
    voice_prefix: Optional[str] = None,
    consent: Optional[str] = None,
    force: bool = False,
) -> VoiceRegistrationSummary:
    """Upload ``assets/voice_samples/*.wav`` to the vLLM-Omni sidecar (idempotent)."""
    import httpx

    from webapp.tts.qwen3_vllm_provider import VLLMOmniTTSConfig

    campaign_path = Path(campaign_root).resolve()
    samples_dir = campaign_path / "assets" / "voice_samples"
    if not samples_dir.is_dir():
        logger.info("[TTS] No voice_samples directory for campaign %s", campaign_path)
        return VoiceRegistrationSummary()

    wavs = sorted(samples_dir.glob("*.wav"))
    if not wavs:
        return VoiceRegistrationSummary()

    config = VLLMOmniTTSConfig.from_env()
    resolved_base = (base_url or config.base_url).rstrip("/")
    prefix = voice_prefix if voice_prefix is not None else config.voice_prefix
    resolved_consent = consent if consent is not None else config.consent

    uploaded = skipped = failed = 0
    with httpx.Client(timeout=120.0) as client:
        try:
            existing = _list_voice_names(client, resolved_base)
        except httpx.HTTPError as exc:
            logger.warning(
                "[TTS] vLLM-Omni voice registration skipped (unreachable %s): %s",
                resolved_base,
                exc,
            )
            return VoiceRegistrationSummary(total=len(wavs), failed=len(wavs))

        for wav_path in wavs:
            voice_name = f"{prefix}{wav_path.stem}"
            if voice_name in existing and not force:
                skipped += 1
                continue
            ref_text = _ref_text(wav_path)
            # Resample to target sample rate if needed
            ref_for_upload = str(wav_path)
            try:
                from webapp.tts.resample import ensure_sample_rate

                resample_cache = campaign_path / "assets" / "voice_samples" / "resampled"
                resample_cache.mkdir(parents=True, exist_ok=True)
                ref_for_upload = ensure_sample_rate(
                    str(wav_path),
                    config.sample_rate,
                    cache_dir=str(resample_cache),
                )
            except ImportError:
                logger.warning(
                    "[TTS] scipy/numpy not available — skipping resampling "
                    "for %s (sample rate mismatch may cause issues)",
                    voice_name,
                )
            except Exception as exc:
                logger.warning("[TTS] Failed to resample %s: %s", voice_name, exc)
            try:
                upload_path = Path(ref_for_upload)
                with upload_path.open("rb") as audio_file:
                    response = client.post(
                        f"{resolved_base}/v1/audio/voices",
                        data={
                            "consent": resolved_consent,
                            "name": voice_name,
                            "ref_text": ref_text,
                        },
                        files={
                            "audio_sample": (upload_path.name, audio_file, "audio/wav"),
                        },
                    )
                response.raise_for_status()
                uploaded += 1
                existing.add(voice_name)
                logger.debug("[TTS] Registered vLLM voice %s (from %s)", voice_name, upload_path)
            except httpx.HTTPError as exc:
                failed += 1
                logger.warning("[TTS] Failed to register vLLM voice %s: %s", voice_name, exc)

    summary = VoiceRegistrationSummary(
        uploaded=uploaded,
        skipped=skipped,
        failed=failed,
        total=len(wavs),
    )
    if uploaded or failed:
        logger.info(
            "[TTS] vLLM campaign voice registration: uploaded=%s skipped=%s failed=%s total=%s",
            summary.uploaded,
            summary.skipped,
            summary.failed,
            summary.total,
        )
    return summary


def register_campaign_voices_if_needed(
    campaign_root: str | Path,
    *,
    provider_name: str,
) -> Optional[VoiceRegistrationSummary]:
    """Register campaign voices on webapp bootstrap when using the vLLM sidecar."""
    if provider_name not in ("qwen3_vllm",):
        return None
    if not register_on_start_enabled():
        return None
    return register_campaign_voices(campaign_root)
