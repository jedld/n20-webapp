"""
Example: How to integrate TTS into natural_20.py webapp.

This file shows exactly where and how to add TTS support to the existing
Flask application. It is NOT part of the production code - just a reference.

See docs/tts_voice_recommendations.md for the full architecture guide.

Quick integration steps:
1. Set environment variables (TTS_ENABLED=1, TTS_DEVICE=cuda, etc.)
2. Add initialization to webapp/app.py (see CODE_SNIPPET below)
3. Load voice configs from NPC YAML files
4. TTS audio player loads automatically in HTML templates
"""

# =============================================================================
# CODE SNIPPET: Minimal app.py modifications
# =============================================================================
# 
# Copy this code into webapp/app.py at the indicated locations:
#

# STEP 1: Add imports at top of app.py
"""
from webapp.tts import init_tts_manager
import os  # if not already imported
"""

# STEP 2: After Flask app creation, before blueprint registration
"""
# --- TTS Initialization ---
tts_manager = None
if os.environ.get('TTS_ENABLED', '0').lower() in ('1', 'true', 'yes'):
    try:
        tts_provider = os.environ.get('TTS_PROVIDER', 'openvoice')
        tts_device = os.environ.get('TTS_DEVICE', 'cpu')
        tts_cache = os.environ.get('N20_TTS_CACHE_DIR', '/tmp/natural20_tts')
        
        tts_manager = init_tts_manager(
            provider=tts_provider,
            device=tts_device,
            cache_dir=tts_cache,
        )
        app.logger.info(f"[TTS] Initialized with {tts_provider} on {tts_device}")
        
        # Register TTS blueprint for audio serving
        app.register_blueprint(tts_manager.create_blueprint())
    except Exception as e:
        app.logger.warning(f"[TTS] Failed to initialize: {e}")
        tts_manager = None
# --- End TTS Initialization ---
"""

# STEP 3: After conversation_service is created
"""
# --- TTS Attachment ---
if tts_manager and hasattr(conversation_service, 'attach_tts_manager'):
    conversation_service.attach_tts_manager(tts_manager)
    app.logger.info("[TTS] Attached TTS to conversation service")
# --- End TTS Attachment ---
"""

# STEP 4: Add to HTML template (webapp/templates/base.html or battle.html)
"""
<!-- TTS Player -->
<script src="{{ url_for('static', filename='js/tts_player.js') }}"></script>
<script>
    document.addEventListener('DOMContentLoaded', function() {
        if (window.TTSPlayer) {
            window.TTSPlayer.init();
        }
    });
</script>
<!-- End TTS Player -->
"""

# =============================================================================
# ENVIRONMENT VARIABLES
# =============================================================================
# 
# Add to your .env file:
#
# TTS_ENABLED=1                    # Enable TTS (0=disabled, 1=enabled)
# TTS_PROVIDER=openvoice           # TTS provider (openvoice)
# TTS_DEVICE=cuda                  # cuda=GPU, cpu=CPU-only
# N20_TTS_CACHE_DIR=/tmp/natural20_tts  # Audio cache directory
#
# =============================================================================


# =============================================================================
# HELPER: Load NPC voice configs from YAML
# =============================================================================
def load_npc_with_voice(npc_data, tts_manager=None):
    """
    Load an NPC with TTS voice configuration.
    
    If the NPC has a voice config and TTS is enabled,
    this function creates the NPC voice automatically.
    
    Args:
        npc_data: Dictionary of NPC YAML data
        tts_manager: Initialized TTSManager instance
    
    Returns:
        Updated NPC data dictionary
    """
    if not npc_data:
        return npc_data
    
    voice_data = npc_data.get('voice', {})
    if not isinstance(voice_data, dict):
        return npc_data
    
    prompt = voice_data.get('prompt')
    locked = voice_data.get('locked', False)
    reference_audio = voice_data.get('reference_audio')
    npc_uid = npc_data.get('uid')
    
    if not prompt or not npc_uid:
        return npc_data
    
    if tts_manager and locked:
        try:
            tts_manager.create_voice(
                npc_uid=npc_uid,
                voice_prompt=prompt,
                reference_audio=reference_audio,
            )
        except Exception as e:
            print(f"[TTS] Failed to create voice for {npc_uid}: {e}")
    
    return npc_data
