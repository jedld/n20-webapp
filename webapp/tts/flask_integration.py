"""
Flask app integration helpers for natural_20.py TTS.

Provides functions to wire the TTS manager into the Flask app.
Use these in webapp/app.py to initialize TTS at startup.

Example usage in webapp/app.py:
    
    from webapp.tts.flask_integration import init_tts, register_tts_blueprints
    
    # Initialize TTS (optional - disabled if TTS_PROVIDER not set)
    init_tts(app)
    
    # Register TTS blueprint for audio serving
    tts_manager = get_tts_manager()
    if tts_manager:
        register_tts_blueprints(app, tts_manager)
"""

import os
from typing import Optional

from flask import Flask

from webapp.tts.manager import TTSManager, get_tts_manager, init_tts_manager


def init_tts(app: Optional[Flask] = None) -> Optional[TTSManager]:
    """
    Initialize TTS from environment variables.
    
    Checks these environment variables:
    - TTS_ENABLED: "1", "0", "true", "false" (default: "0" = disabled)
    - TTS_PROVIDER: "openvoice", etc. (default: "openvoice")
    - TTS_DEVICE: "cuda", "cpu" (default: "cpu")
    - N20_TTS_CACHE_DIR: cache directory path
    
    Args:
        app: Flask app instance (optional, for logging)
    
    Returns:
        TTSManager instance if TTS is enabled, None otherwise
    """
    enabled_str = os.environ.get("TTS_ENABLED", "0").lower()
    if enabled_str not in ("1", "true", "yes", "on"):
        if app:
            app.logger.info("[TTS] Disabled (TTS_ENABLED=0)")
        return None
    
    provider = os.environ.get("TTS_PROVIDER", "openvoice")
    device = os.environ.get("TTS_DEVICE", "cpu")
    cache_dir = os.environ.get("N20_TTS_CACHE_DIR", "/tmp/natural20_tts")
    
    if app:
        app.logger.info(f"[TTS] Initializing provider={provider} device={device}")
    
    manager = init_tts_manager(
        provider=provider,
        device=device,
        cache_dir=cache_dir,
    )
    
    if app:
        app.logger.info(f"[TTS] Initialized successfully (voices: {manager.list_locked_voices()})")
    
    return manager


def register_tts_blueprints(app: Flask, tts_manager: TTSManager) -> None:
    """
    Register TTS-related Flask blueprints with the app.
    
    Registers:
    - /tts/*: Audio file serving
    - /tts/status: TTS status endpoint
    - /tts/create_voice: Create voice API
    - /tts/remove_voice: Remove voice API
    
    Args:
        app: Flask app instance
        tts_manager: Initialized TTSManager instance
    """
    bp = tts_manager.create_blueprint()
    app.register_blueprint(bp)
    
    if app.logger:
        app.logger.info("[TTS] Registered TTS blueprint at /tts")


def attach_tts_to_conversation_service(conversation_service, tts_manager: TTSManager) -> None:
    """
    Attach TTS manager to a ConversationService instance.
    
    This enables TTS audio generation for NPC conversation replies.
    
    Args:
        conversation_service: ConversationService instance
        tts_manager: Initialized TTSManager instance
    """
    from webapp.tts.conversation_wiring import TTSConversationMixin
    
    # Try to attach the TTS manager
    if hasattr(conversation_service, 'attach_tts_manager'):
        conversation_service.attach_tts_manager(tts_manager)
    else:
        # Alternative: create a wrapper
        mixer = TTSConversationMixin()
        mixer._tts_manager = tts_manager
        # Store reference for later use
        conversation_service._tts_wiring = mixer
    
    if hasattr(conversation_service, 'logger'):
        conversation_service.logger.info("[TTS] Attached TTS to conversation service")


# Compatibility: export the module-level functions at package level
def get_manager() -> Optional[TTSManager]:
    """Get the current TTS manager (alias for get_tts_manager)."""
    return get_tts_manager()
