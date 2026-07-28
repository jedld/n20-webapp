"""
TTS Manager for natural_20.py.

Central singleton that manages TTS provider lifecycle, voice registry,
and coordinates between the conversation service and TTS providers.
"""

import os
import threading
import uuid
from pathlib import Path
from typing import Iterator, Optional

from webapp.tts.base import TTSEngine, VoiceConfig
from webapp.tts.cosyvoice_provider import CosyVoiceProvider, MockCosyVoiceProvider
from webapp.tts.openvoice_provider import OpenVoiceTTSProvider
from webapp.tts.qwen3_provider import MockQwen3Provider, Qwen3TTSProvider
from webapp.tts.qwen3_vllm_provider import MockQwen3VLLMProvider, Qwen3VLLMProvider
from webapp.tts.voice_profile import VoiceProfile


class TTSManager:
    """
    Central TTS manager that coordinates provider selection, voice registry,
    and integration with the conversation service.
    
    Usage:
        # In conversation_service.py or app initialization:
        tts_manager = TTSManager()
        tts_manager.initialize(provider="openvoice", device="cuda")
        
        # Register with Flask app for file serving
        app.register_blueprint(tts_manager.create_blueprint())
    """
    
    PROVIDERS = {
        "cosyvoice": CosyVoiceProvider,
        "mock_cosyvoice": MockCosyVoiceProvider,
        "openvoice": OpenVoiceTTSProvider,
        "qwen3": Qwen3TTSProvider,
        "mock_qwen3": MockQwen3Provider,
        "qwen3_vllm": Qwen3VLLMProvider,
        "mock_qwen3_vllm": MockQwen3VLLMProvider,
    }
    
    def __init__(
        self,
        device: Optional[str] = None,
        cache_dir: Optional[str] = None,
    ):
        """
        Initialize the TTS manager.
        
        Args:
            device: "cuda" or "cpu". Defaults to TTS_DEVICE env var or "cpu".
            cache_dir: Directory for cached audio. Defaults to N20_TTS_CACHE_DIR or /tmp/natural20_tts.
        """
        raw_device = device if device is not None else os.environ.get("TTS_DEVICE", "cpu")
        self.device = self._normalize_device_env(raw_device)
        self.cache_dir = cache_dir or os.environ.get(
            "N20_TTS_CACHE_DIR", "/tmp/natural20_tts"
        )
        self._engine: Optional[TTSEngine] = None
        self._extra_engines: dict[str, TTSEngine] = {}
        # Default provider: read from TTS_PROVIDER env var, fall back to "cosyvoice"
        self._provider_name: str = os.environ.get("TTS_PROVIDER", "cosyvoice")
        self._auto_create_on_first_speech: bool = True
        self._npc_voice_prompts: dict[str, str] = {}  # npc_uid -> voice_prompt
        self._inference_lock = threading.Lock()
        self._line_cache_locks_guard = threading.Lock()
        self._line_cache_locks: dict[str, threading.Lock] = {}

    @staticmethod
    def _normalize_device_env(device: Optional[str]) -> str:
        value = (device or "cpu").strip().lower()
        if value in ("gpu", "cuda", "cuda:0"):
            return "cuda"
        if value.startswith("cuda:"):
            return value
        return "cpu"
    
    def initialize(self, provider: Optional[str] = None, device: Optional[str] = None) -> None:
        """
        Initialize the TTS provider.
        
        Args:
            provider: Provider name ("cosyvoice", "mock_cosyvoice", "openvoice", etc.).
                     If None, uses TTS_PROVIDER env var or defaults to "cosyvoice".
            device: Override device ("cuda", "cpu")
        """
        # Resolve provider: explicit arg > env var > default
        resolved_provider = provider or os.environ.get("TTS_PROVIDER", "cosyvoice")
        self._provider_name = resolved_provider
        engine_class = self.PROVIDERS.get(resolved_provider)
        if not engine_class:
            available = ", ".join(self.PROVIDERS.keys())
            raise ValueError(
                f"Unknown TTS provider '{resolved_provider}'. Available: {available}"
            )
        
        kwargs = {}
        if device:
            kwargs["device"] = device
        elif self.device != "cpu":
            kwargs["device"] = self.device
        
        kwargs["cache_dir"] = self.cache_dir
        
        self._engine = engine_class(**kwargs)
        self._engine.initialize()

        # If the real CosyVoice/OpenVoice stack failed to become ready, fall
        # back to the mock provider so dialogue still produces playable audio.
        if (
            resolved_provider == "cosyvoice"
            and hasattr(self._engine, "is_ready")
            and not self._engine.is_ready()
        ):
            print(
                "[TTS] CosyVoice provider has no usable backend; "
                "falling back to mock_cosyvoice"
            )
            self._provider_name = "mock_cosyvoice"
            self._engine = MockCosyVoiceProvider(
                device=kwargs.get("device", self.device),
                cache_dir=self.cache_dir,
            )
            self._engine.initialize()
    
    def _build_engine(self, provider_name: str) -> TTSEngine:
        engine_class = self.PROVIDERS.get(provider_name)
        if not engine_class:
            available = ", ".join(self.PROVIDERS.keys())
            raise ValueError(
                f"Unknown TTS provider '{provider_name}'. Available: {available}"
            )
        kwargs = {"cache_dir": self.cache_dir}
        if self.device != "cpu":
            kwargs["device"] = self.device
        engine = engine_class(**kwargs)
        engine.initialize()
        return engine

    def get_engine_for_provider(self, provider_name: Optional[str] = None) -> TTSEngine:
        """Return the engine for a provider, creating secondary engines on demand."""
        name = (provider_name or self._provider_name or "").strip().lower()
        if not name or name == self._provider_name:
            return self.get_engine()
        if name not in self._extra_engines:
            self._extra_engines[name] = self._build_engine(name)
        return self._extra_engines[name]
    
    def get_engine(self) -> TTSEngine:
        """Get the current TTS engine. Raises if not initialized."""
        if self._engine is None:
            raise RuntimeError(
                "TTS manager not initialized. Call initialize() first, "
                "or check TTS_ENABLED env var."
            )
        return self._engine

    def model_config(self):
        """Resolved chunking/streaming config for the active engine."""
        from webapp.tts.model_config import get_model_config
        return get_model_config(self.get_engine(), self._provider_name)

    def model_profile(self) -> str:
        from webapp.tts.model_config import resolve_model_profile
        return resolve_model_profile(self.get_engine(), self._provider_name)
    
    def is_enabled(self) -> bool:
        """Check if TTS is enabled (provider configured and initialized)."""
        # If engine is initialized, TTS is enabled unless explicitly disabled
        if self._engine is None:
            return False
        
        # Only check TTS_ENABLED if engine is initialized
        # This allows tests that initialize the engine to work
        enabled_str = os.environ.get("TTS_ENABLED", "1")  # Default to enabled when engine exists
        if enabled_str.lower() in ("0", "no", "false"):
            return False

        if hasattr(self._engine, "is_ready") and not self._engine.is_ready():
            return False
        
        return True
    
    def create_voice(
        self,
        npc_uid: str,
        voice_prompt: str,
        reference_audio: Optional[str] = None,
        accent: Optional[str] = None,
        language: Optional[str] = None,
        voice_metadata: Optional[dict] = None,
    ) -> VoiceConfig:
        """
        Create and lock a voice for an NPC.
        
        Args:
            npc_uid: Unique identifier for the NPC
            voice_prompt: Text description of the voice
            reference_audio: Optional reference audio file path
            accent: Optional CosyVoice accent/dialect label
            language: Optional language hint (e.g. en, zh)
            voice_metadata: Optional gender/age profile from NPC YAML
        
        Returns:
            VoiceConfig with locked voice path
        """
        return self.get_engine().create_voice(
            npc_uid=npc_uid,
            voice_prompt=voice_prompt,
            reference_audio=reference_audio,
            accent=accent,
            language=language,
            voice_metadata=voice_metadata,
        )

    def create_voice_from_profile(self, profile: VoiceProfile) -> VoiceConfig:
        """
        Create and lock a voice using the multi-backend resolver.

        Picks strategy (clone/design/preset/instruct) from NPC YAML + provider
        capabilities, then delegates to the appropriate engine.
        """
        from webapp.tts.voice_resolver import resolve_voice_plan

        provider = (profile.provider or self._provider_name or "cosyvoice").strip().lower()
        engine = self.get_engine_for_provider(provider)
        plan = resolve_voice_plan(profile, provider, engine=engine)

        metadata = profile.to_metadata()
        metadata["resolved_strategy"] = plan.strategy.value
        metadata["design_prompt"] = plan.design_prompt
        if profile.campaign_root:
            metadata["campaign_root"] = profile.campaign_root
        if plan.notes:
            metadata["resolver_notes"] = plan.notes

        return engine.create_voice(
            npc_uid=profile.npc_uid,
            voice_prompt=plan.voice_prompt,
            reference_audio=plan.reference_audio or profile.reference_audio,
            accent=profile.accent,
            language=profile.language,
            voice_metadata=metadata,
        )

    def bake_voice_for_profile(
        self,
        profile: VoiceProfile,
        *,
        force: bool = False,
        provider_override: Optional[str] = None,
    ) -> Optional[str]:
        """Bake a stable reference WAV for an NPC profile when supported."""
        from webapp.tts.voice_baking import should_bake_voice_profile, voice_sample_path

        if not should_bake_voice_profile(profile) and not force:
            return profile.reference_audio
        if not profile.campaign_root:
            raise ValueError(f"Cannot bake voice for {profile.npc_uid}: missing campaign_root")
        sample = voice_sample_path(profile.campaign_root, profile.npc_uid)
        if sample and sample.is_file() and not force:
            return str(sample)
        if force and sample and sample.is_file():
            sample.unlink()

        provider = (
            provider_override
            or profile.provider
            or self._provider_name
            or "qwen3"
        ).strip().lower()
        engine = self.get_engine_for_provider(provider)
        baker = getattr(engine, "bake_voice_sample", None)
        if not callable(baker):
            raise RuntimeError(f"TTS provider '{provider}' does not support voice baking")
        language = profile.language or "en"
        return baker(
            profile.npc_uid,
            profile.design_prompt(),
            language=language,
            campaign_root=profile.campaign_root,
        )

    def generate_for_profile(
        self,
        profile: VoiceProfile,
        text: str,
        *,
        emotion: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> str:
        """Generate speech using the engine selected by the NPC profile."""
        provider = (profile.provider or self._provider_name or "cosyvoice").strip().lower()
        engine = self.get_engine_for_provider(provider)
        self._ensure_voice_locked_for_engine(engine, profile.npc_uid, profile.prompt)
        return self._call_engine_generate(
            engine,
            text=text,
            npc_uid=profile.npc_uid,
            emotion=emotion,
            output_path=output_path,
            instruct=instruct,
            volume=volume,
        )

    def update_voice_options(
        self,
        npc_uid: str,
        accent: Optional[str] = None,
        language: Optional[str] = None,
    ) -> bool:
        """Update accent/language for a locked voice when the engine supports it."""
        engine = self.get_engine()
        updater = getattr(engine, "update_voice_options", None)
        if callable(updater):
            return bool(updater(npc_uid, accent=accent, language=language))
        config = getattr(engine, "_voice_registry", {}).get(npc_uid)
        if not config:
            return False
        if accent is not None:
            config.extra_metadata["accent"] = accent
        if language is not None:
            config.extra_metadata["language"] = language
        return True
    
    def _ensure_voice_locked(self, engine: TTSEngine, npc_uid: str) -> None:
        if npc_uid in engine.list_locked_voices():
            return
        voice_prompt = self._npc_voice_prompts.get(npc_uid)
        if voice_prompt and self._auto_create_on_first_speech:
            engine.create_voice(npc_uid=npc_uid, voice_prompt=voice_prompt)
            return
        raise ValueError(
            f"No voice locked for NPC '{npc_uid}' and no voice prompt registered. "
            f"Register a prompt with register_voice_prompt() or call create_voice() first."
        )

    def _ensure_voice_locked_for_engine(
        self,
        engine: TTSEngine,
        npc_uid: str,
        voice_prompt: Optional[str] = None,
    ) -> None:
        if npc_uid in engine.list_locked_voices():
            return
        prompt = voice_prompt or self._npc_voice_prompts.get(npc_uid)
        if prompt and self._auto_create_on_first_speech:
            engine.create_voice(npc_uid=npc_uid, voice_prompt=prompt)
            return
        raise ValueError(
            f"No voice locked for NPC '{npc_uid}' on provider engine. "
            f"Call create_voice_from_profile() first."
        )

    def _call_engine_generate(
        self,
        engine: TTSEngine,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ) -> str:
        try:
            return engine.generate(
                text=text,
                npc_uid=npc_uid,
                emotion=emotion,
                output_path=output_path,
                instruct=instruct,
                volume=volume,
            )
        except TypeError:
            try:
                return engine.generate(
                    text=text,
                    npc_uid=npc_uid,
                    emotion=emotion,
                    output_path=output_path,
                    instruct=instruct,
                )
            except TypeError:
                return engine.generate(
                    text=text,
                    npc_uid=npc_uid,
                    emotion=emotion,
                    output_path=output_path,
                )

    def _call_engine_generate_stream(
        self,
        engine: TTSEngine,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ):
        stream_fn = getattr(engine, 'generate_stream', None)
        if not callable(stream_fn):
            return None
        try:
            return stream_fn(
                text=text,
                npc_uid=npc_uid,
                emotion=emotion,
                output_path=output_path,
                instruct=instruct,
                volume=volume,
            )
        except TypeError:
            try:
                return stream_fn(
                    text=text,
                    npc_uid=npc_uid,
                    emotion=emotion,
                    output_path=output_path,
                    instruct=instruct,
                )
            except TypeError:
                return stream_fn(
                    text=text,
                    npc_uid=npc_uid,
                    emotion=emotion,
                    output_path=output_path,
                )

    def _line_cache_lock(self, cache_key: str) -> threading.Lock:
        with self._line_cache_locks_guard:
            lock = self._line_cache_locks.get(cache_key)
            if lock is None:
                lock = threading.Lock()
                self._line_cache_locks[cache_key] = lock
            return lock

    def lookup_cached_line(self, synthesis_key: Optional[str]) -> Optional[str]:
        from webapp.tts.synthesis_cache import cached_line_path, line_cache_enabled

        if not synthesis_key or not line_cache_enabled():
            return None
        path = cached_line_path(self.cache_dir, synthesis_key)
        if path.is_file() and path.stat().st_size > 44:
            return str(path)
        return None

    def generate(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
        synthesis_key: Optional[str] = None,
    ) -> str:
        """
        Generate speech audio for an NPC.
        
        If auto-create is enabled and the NPC has no voice, creates one
        from the stored voice prompt.
        
        Args:
            text: The dialogue text to speak
            npc_uid: The NPC whose voice to use
            emotion: Optional emotion/style override
            output_path: Optional output path
            instruct: Optional free-form CosyVoice delivery notes
            volume: Conversation volume (whisper|normal|shout)
        
        Returns:
            Path to the generated audio file
        """
        from natural20.utils.conversation import sanitize_spoken_text_for_tts
        from webapp.tts.streaming import concat_wav_files
        from webapp.tts.synthesis_cache import cached_line_path, line_cache_enabled
        from webapp.tts.text_chunking import split_tts_text

        text = sanitize_spoken_text_for_tts(text)

        if synthesis_key and line_cache_enabled():
            cached = self.lookup_cached_line(synthesis_key)
            if cached:
                return cached
            if output_path is None:
                output_path = str(cached_line_path(self.cache_dir, synthesis_key))

        def _generate_locked() -> str:
            engine = self.get_engine()
            self._ensure_voice_locked(engine, npc_uid)
            cfg = self.model_config()

            text_chunks = split_tts_text(text, config=cfg)
            if getattr(self, '_provider_name', '') in ('qwen3_vllm', 'mock_qwen3_vllm'):
                text_chunks = [text] if text else []
            if len(text_chunks) <= 1:
                return self._call_engine_generate(
                    engine,
                    text,
                    npc_uid,
                    emotion=emotion,
                    output_path=output_path,
                    instruct=instruct,
                    volume=volume,
                )

            segment_paths = []
            try:
                for chunk in text_chunks:
                    segment_path = str(
                        Path(self.cache_dir) / f"tts_part_{npc_uid}_{uuid.uuid4().hex[:8]}.wav"
                    )
                    segment_paths.append(
                        self._call_engine_generate(
                            engine,
                            chunk,
                            npc_uid,
                            emotion=emotion,
                            output_path=segment_path,
                            instruct=instruct,
                            volume=volume,
                        )
                    )
                merged_output = output_path
                if merged_output is None:
                    merged_output = str(
                        Path(self.cache_dir) / f"tts_{npc_uid}_{uuid.uuid4().hex[:8]}.wav"
                    )
                return concat_wav_files(segment_paths, merged_output, pause_ms=cfg.chunk_pause_ms)
            finally:
                for segment_path in segment_paths:
                    try:
                        if segment_path and Path(segment_path).exists():
                            Path(segment_path).unlink()
                    except OSError:
                        pass

        if synthesis_key and line_cache_enabled():
            with self._line_cache_lock(synthesis_key):
                cached = self.lookup_cached_line(synthesis_key)
                if cached:
                    return cached
                with self._inference_lock:
                    result = _generate_locked()
                if result and Path(result).is_file():
                    return result
                return result

        with self._inference_lock:
            return _generate_locked()
    
    def _synthesize_segment_pcm(
        self,
        engine: TTSEngine,
        chunk_text: str,
        npc_uid: str,
        *,
        emotion: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ) -> list[tuple[int, bytes]]:
        """Synthesize one text segment and return ordered (sample_rate, pcm) chunks."""
        from webapp.tts.streaming import iter_wav_pcm_chunks

        chunks: list[tuple[int, bytes]] = []
        stream_iter = self._call_engine_generate_stream(
            engine,
            chunk_text,
            npc_uid,
            emotion=emotion,
            output_path=None,
            instruct=instruct,
            volume=volume,
        )
        if stream_iter is not None:
            for rate, pcm in stream_iter:
                chunks.append((rate, pcm))
            return chunks

        segment_path = str(
            Path(self.cache_dir) / f"tts_stream_part_{npc_uid}_{uuid.uuid4().hex[:8]}.wav"
        )
        try:
            path = self._call_engine_generate(
                engine,
                chunk_text,
                npc_uid,
                emotion=emotion,
                output_path=segment_path,
                instruct=instruct,
                volume=volume,
            )
            for rate, pcm in iter_wav_pcm_chunks(path):
                chunks.append((rate, pcm))
        finally:
            try:
                Path(segment_path).unlink(missing_ok=True)
            except OSError:
                pass
        return chunks

    def _iter_segment_pcm_stream(
        self,
        engine: TTSEngine,
        chunk_text: str,
        npc_uid: str,
        *,
        emotion: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
        hold_inference_lock: bool = False,
    ) -> Iterator[tuple[int, bytes]]:
        """Yield PCM for one segment, optionally under the shared inference lock."""
        from webapp.tts.streaming import iter_wav_pcm_chunks

        def _stream() -> Iterator[tuple[int, bytes]]:
            stream_iter = self._call_engine_generate_stream(
                engine,
                chunk_text,
                npc_uid,
                emotion=emotion,
                output_path=None,
                instruct=instruct,
                volume=volume,
            )
            if stream_iter is not None:
                yield from stream_iter
                return

            segment_path = str(
                Path(self.cache_dir) / f"tts_stream_part_{npc_uid}_{uuid.uuid4().hex[:8]}.wav"
            )
            try:
                path = self._call_engine_generate(
                    engine,
                    chunk_text,
                    npc_uid,
                    emotion=emotion,
                    output_path=segment_path,
                    instruct=instruct,
                    volume=volume,
                )
                yield from iter_wav_pcm_chunks(path)
            finally:
                try:
                    Path(segment_path).unlink(missing_ok=True)
                except OSError:
                    pass

        if hold_inference_lock:
            with self._inference_lock:
                yield from _stream()
            return

        yield from _stream()

    def _finalize_stream_output(
        self,
        engine: TTSEngine,
        stored_pcm: list[bytes],
        sample_rate: int,
        output_path: Optional[str],
        npc_uid: str,
    ) -> None:
        from webapp.tts.streaming import write_pcm_chunks_to_wav

        if not stored_pcm:
            return
        if output_path is None:
            output_path = str(
                Path(self.cache_dir) / f"tts_{npc_uid}_{uuid.uuid4().hex[:8]}.wav"
            )
        write_pcm_chunks_to_wav(stored_pcm, output_path, sample_rate)
        setattr(engine, '_last_stream_output_path', output_path)
        self._last_stream_output_path = output_path

    def _generate_stream_sequential(
        self,
        engine: TTSEngine,
        text_chunks: list[str],
        npc_uid: str,
        *,
        emotion: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
        output_path: Optional[str] = None,
        pause_ms: int = 0,
    ):
        from webapp.tts.streaming import silence_pcm_bytes

        stored_pcm: list[bytes] = []
        sample_rate = 22050

        for index, chunk_text in enumerate(text_chunks):
            for rate, pcm in self._iter_segment_pcm_stream(
                engine,
                chunk_text,
                npc_uid,
                emotion=emotion,
                instruct=instruct,
                volume=volume,
                hold_inference_lock=True,
            ):
                sample_rate = rate
                stored_pcm.append(pcm)
                yield rate, pcm

            if pause_ms > 0 and index < len(text_chunks) - 1:
                pause_pcm = silence_pcm_bytes(sample_rate, pause_ms)
                stored_pcm.append(pause_pcm)
                yield sample_rate, pause_pcm

        self._finalize_stream_output(engine, stored_pcm, sample_rate, output_path, npc_uid)

    def _generate_stream_pipelined(
        self,
        engine: TTSEngine,
        text_chunks: list[str],
        npc_uid: str,
        *,
        emotion: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
        output_path: Optional[str] = None,
        pause_ms: int = 0,
        concurrent: bool = True,
    ):
        """Stream segments with optional prefetch of the next segment."""
        from webapp.tts.pipeline import SegmentPrefetcher
        from webapp.tts.streaming import silence_pcm_bytes

        stored_pcm: list[bytes] = []
        sample_rate = 22050

        def synthesize(chunk_text: str) -> list[tuple[int, bytes]]:
            return self._synthesize_segment_pcm(
                engine,
                chunk_text,
                npc_uid,
                emotion=emotion,
                instruct=instruct,
                volume=volume,
            )

        prefetcher = SegmentPrefetcher(
            synthesize,
            concurrent=concurrent,
            inference_lock=self._inference_lock,
        )

        try:
            for index, chunk_text in enumerate(text_chunks):
                upcoming = (
                    text_chunks[index + 1]
                    if index + 1 < len(text_chunks)
                    else None
                )

                if index == 0 and concurrent:
                    if upcoming:
                        prefetcher.schedule(upcoming)
                    for rate, pcm in self._iter_segment_pcm_stream(
                        engine,
                        chunk_text,
                        npc_uid,
                        emotion=emotion,
                        instruct=instruct,
                        volume=volume,
                        hold_inference_lock=False,
                    ):
                        sample_rate = rate
                        stored_pcm.append(pcm)
                        yield rate, pcm
                elif prefetcher.has_pending():
                    segment_pcm = prefetcher.take()
                    if upcoming and concurrent:
                        prefetcher.schedule(upcoming)
                    for rate, pcm in segment_pcm:
                        sample_rate = rate
                        stored_pcm.append(pcm)
                        yield rate, pcm
                else:
                    segment_pcm = synthesize(chunk_text)
                    if upcoming:
                        prefetcher.schedule(upcoming)
                    for rate, pcm in segment_pcm:
                        sample_rate = rate
                        stored_pcm.append(pcm)
                        yield rate, pcm

                if pause_ms > 0 and index < len(text_chunks) - 1:
                    pause_pcm = silence_pcm_bytes(sample_rate, pause_ms)
                    stored_pcm.append(pause_pcm)
                    yield sample_rate, pause_pcm

            self._finalize_stream_output(engine, stored_pcm, sample_rate, output_path, npc_uid)
        finally:
            prefetcher.shutdown()

    def generate_stream(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ):
        """Yield (sample_rate, pcm_bytes) chunks for streaming playback."""
        from natural20.utils.conversation import sanitize_spoken_text_for_tts
        from webapp.tts.pipeline import (
            pipeline_concurrent_enabled,
            pipeline_segments_enabled,
        )
        from webapp.tts.text_chunking import split_tts_text

        text = sanitize_spoken_text_for_tts(text)

        engine = self.get_engine()
        self._ensure_voice_locked(engine, npc_uid)
        cfg = self.model_config()

        text_chunks = split_tts_text(text, config=cfg)
        if getattr(self, '_provider_name', '') in ('qwen3_vllm', 'mock_qwen3_vllm'):
            text_chunks = [text] if text else []

        if len(text_chunks) <= 1 or not pipeline_segments_enabled():
            yield from self._generate_stream_sequential(
                engine,
                text_chunks,
                npc_uid,
                emotion=emotion,
                instruct=instruct,
                volume=volume,
                output_path=output_path,
                pause_ms=cfg.chunk_pause_ms,
            )
            return

        yield from self._generate_stream_pipelined(
            engine,
            text_chunks,
            npc_uid,
            emotion=emotion,
            instruct=instruct,
            volume=volume,
            output_path=output_path,
            pause_ms=cfg.chunk_pause_ms,
            concurrent=pipeline_concurrent_enabled(),
        )
    
    def register_voice_prompt(self, npc_uid: str, voice_prompt: str) -> None:
        """
        Register a voice prompt for an NPC.
        
        This allows auto-creation of voices on first speech.
        """
        self._npc_voice_prompts[npc_uid] = voice_prompt
    
    def list_locked_voices(self) -> list[str]:
        """Return all NPC UIDs that have locked voices."""
        if not self.is_enabled():
            return []
        return self.get_engine().list_locked_voices()
    
    def remove_voice(self, npc_uid: str) -> bool:
        """Remove a locked voice for an NPC."""
        if not self.is_enabled():
            return False
        return self.get_engine().remove_voice(npc_uid)
    
    def persist_all_voices(self) -> dict[str, dict]:
        """Export all voice configs for save/load."""
        if not self.is_enabled():
            return {}
        engine = self.get_engine()
        result = {}
        for npc_uid in engine.list_locked_voices():
            data = engine.persist_voice(npc_uid)
            if data:
                result[npc_uid] = data
        return result
    
    def restore_all_voices(self, voices_data: dict[str, dict]) -> None:
        """Restore voice configs from save data."""
        if not self.is_enabled():
            return
        engine = self.get_engine()
        for npc_uid, voice_data in voices_data.items():
            try:
                engine.restore_voice(npc_uid, voice_data)
            except Exception as e:
                print(f"[TTS] Failed to restore voice for {npc_uid}: {e}")
    
    def set_auto_create(self, enabled: bool) -> None:
        """Enable/disable auto-creation of voices on first speech."""
        self._auto_create_on_first_speech = enabled
    
    def create_blueprint(self):
        """
        Create a Flask blueprint for serving generated TTS audio files.
        
        This allows the web UI to play audio via <audio src="/tts/...">.
        """
        from flask import Blueprint, send_file, abort, request, jsonify, session
        
        from webapp.tts.preferences import (
            available_delivery_modes,
            default_delivery_mode,
            get_delivery_mode,
        )
        
        from webapp.tts.model_config import list_model_profiles

        bp = Blueprint("tts", __name__, url_prefix="/tts", static_folder=None)
        
        # No auth check - TTS audio serving is public for frontend <audio> tag usage.
        # Management endpoints (POST) may need auth in production; for now, keep open.
        
        @bp.route("/reply/<reply_id>")
        def reply_ready(reply_id: str):
            """Poll async TTS completion when Socket.IO delivery was missed."""
            from webapp.tts.reply_cache import get_ready_reply

            payload = get_ready_reply(reply_id)
            if not payload:
                return jsonify({"ready": False, "reply_id": reply_id}), 200
            return jsonify({"ready": True, **payload})

        @bp.route("/<filename>")
        def serve_audio(filename: str):
            """Serve a generated TTS audio file."""
            safe_filename = Path(filename).name  # Prevent directory traversal
            file_path = Path(self.cache_dir) / safe_filename
            
            if not file_path.exists():
                abort(404)
            
            return send_file(file_path, mimetype="audio/wav")
        
        @bp.route("/status")
        def status():
            """Return TTS status for debugging."""
            username = session.get('username') if session else None
            model_cfg = self.model_config() if self.is_enabled() else None
            return {
                "enabled": self.is_enabled(),
                "provider": self._provider_name,
                "model_profile": self.model_profile() if self.is_enabled() else None,
                "model_config": {
                    "max_chars": model_cfg.max_chars,
                    "chunk_pause_ms": model_cfg.chunk_pause_ms,
                    "supports_streaming": model_cfg.supports_streaming,
                    "description": model_cfg.description,
                } if model_cfg else None,
                "model_profiles": list_model_profiles(),
                "device": self.device,
                "locked_voices": self.list_locked_voices(),
                "cache_dir": self.cache_dir,
                "delivery_mode": get_delivery_mode(username),
                "default_delivery_mode": default_delivery_mode(),
                "available_delivery_modes": available_delivery_modes(),
                "streaming_supported": bool(model_cfg and model_cfg.supports_streaming),
            }

        @bp.route("/preferences", methods=["GET"])
        def preferences_route():
            """Return campaign TTS delivery mode (read-only; configured in game.yml)."""
            return jsonify({
                "delivery_mode": get_delivery_mode(),
                "default_delivery_mode": default_delivery_mode(),
                "available_delivery_modes": available_delivery_modes(),
                "source": "campaign",
            })
        
        @bp.route("/create_voice", methods=["POST"])
        def create_voice_route():
            """API endpoint to create a voice for an NPC."""
            from flask import request, jsonify
            
            data = request.get_json()
            npc_uid = data.get("npc_uid")
            voice_prompt = data.get("voice_prompt")
            reference_audio = data.get("reference_audio")
            
            if not npc_uid or not voice_prompt:
                return jsonify({"error": "npc_uid and voice_prompt required"}), 400
            
            try:
                config = self.create_voice(npc_uid, voice_prompt, reference_audio)
                return jsonify({
                    "success": True,
                    "npc_uid": npc_uid,
                    "style": config.style,
                    "se_path": config.se_path,
                })
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        
        @bp.route("/generate", methods=["POST"])
        def generate_route():
            """API endpoint to generate TTS audio for an NPC."""
            from flask import request, jsonify
            
            data = request.get_json()
            text = data.get("text")
            npc_uid = data.get("npc_uid")
            emotion = data.get("emotion")
            
            if not text or not npc_uid:
                return jsonify({"error": "text and npc_uid required"}), 400
            
            try:
                audio_path = self.generate(text, npc_uid, emotion=emotion)
                return jsonify({
                    "success": True,
                    "audio_url": f"/tts/{Path(audio_path).name}" if audio_path else None,
                })
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        
        @bp.route("/remove_voice", methods=["POST"])
        def remove_voice_route():
            """API endpoint to remove a voice for an NPC."""
            from flask import request, jsonify
            
            data = request.get_json()
            npc_uid = data.get("npc_uid")
            
            if not npc_uid:
                return jsonify({"error": "npc_uid required"}), 400
            
            removed = self.remove_voice(npc_uid)
            return jsonify({"success": removed, "npc_uid": npc_uid})
        
        return bp


# Module-level singleton instance
_default_manager: Optional[TTSManager] = None


def get_tts_manager() -> TTSManager:
    """Get or create the module-level TTS manager singleton."""
    global _default_manager
    if _default_manager is None:
        _default_manager = TTSManager()
    return _default_manager


def init_tts_manager(
    provider: str = "openvoice",
    device: Optional[str] = None,
    cache_dir: Optional[str] = None,
) -> TTSManager:
    """
    Initialize and return the global TTS manager.
    
    Call this during Flask app startup.
    """
    global _default_manager
    _default_manager = TTSManager(device=device, cache_dir=cache_dir)
    _default_manager.initialize(provider=provider)
    return _default_manager
