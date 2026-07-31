"""Campaign-configurable login screen layout and styling."""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional

_DEFAULT_LOGIN_UI: Dict[str, Any] = {
    'show_title_text': True,
    'title_margin_top': '100px',
    'title_color': '#ffffff',
    'title_font_size': '48px',
    'title_shadow': '2px 2px #000000',
    'background_fit': 'cover',
    'background_position': 'center center',
    'background_color': '#0d0d12',
    'font_family': "system-ui, -apple-system, 'Segoe UI', sans-serif",
    'google_fonts': '',
    'panel': {
        'layout': 'center',
        'width': '400px',
        'margin_top': '50px',
        'offset_x': '5vw',
        'offset_y': '8vh',
        'background': 'rgba(255, 255, 255, 0.8)',
        'padding': '20px',
        'border_radius': '10px',
        'border': 'none',
        'box_shadow': 'none',
    },
    'labels': {
        'color': '#333333',
        'font_size': '1rem',
    },
    'inputs': {
        'background': '#ffffff',
        'border': '1px solid #cccccc',
        'color': '#222222',
        'border_radius': '5px',
    },
    'button': {
        'background': '#337ab7',
        'color': '#ffffff',
        'border': '1px solid #2e6da4',
        'border_radius': '5px',
        'font_weight': '400',
        'text': 'Login',
    },
    'error': {
        'background': '#f2dede',
        'color': '#a94442',
        'border': '1px solid #ebccd1',
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def campaign_asset_url(filename: Optional[str]) -> str:
    """Resolve a campaign asset filename to the web ``/assets/`` URL."""
    if not filename:
        return ''
    name = str(filename).strip().lstrip('/')
    if name.startswith('assets/'):
        return f'/{name}'
    return f'/assets/{name}'


def resolve_login_ui(index_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return merged login UI config from campaign ``index.json``."""
    data = index_data or {}
    raw = data.get('login_ui')
    if not isinstance(raw, dict):
        raw = {}
    return _deep_merge(_DEFAULT_LOGIN_UI, raw)


def login_background_filename(index_data: Optional[Dict[str, Any]] = None) -> str:
    data = index_data or {}
    ui = resolve_login_ui(data)
    explicit = ui.get('background')
    if explicit:
        return str(explicit)
    return str(data.get('login_background') or '')


def _css_value(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    if ';' in text or '}' in text:
        return ''
    return text


def _panel_position_css(panel: Dict[str, Any]) -> str:
    layout = str(panel.get('layout') or 'center').strip().lower()
    width = _css_value(panel.get('width') or '400px')
    if layout == 'bottom-right':
        return (
            f'position: fixed; right: {_css_value(panel.get("offset_x") or "5vw")}; '
            f'bottom: {_css_value(panel.get("offset_y") or "8vh")}; width: {width};'
        )
    if layout == 'bottom-left':
        return (
            f'position: fixed; left: {_css_value(panel.get("offset_x") or "5vw")}; '
            f'bottom: {_css_value(panel.get("offset_y") or "8vh")}; width: {width};'
        )
    if layout == 'top-right':
        return (
            f'position: fixed; right: {_css_value(panel.get("offset_x") or "5vw")}; '
            f'top: {_css_value(panel.get("offset_y") or "8vh")}; width: {width};'
        )
    if layout == 'top-left':
        return (
            f'position: fixed; left: {_css_value(panel.get("offset_x") or "5vw")}; '
            f'top: {_css_value(panel.get("offset_y") or "8vh")}; width: {width};'
        )
    margin_top = _css_value(panel.get('margin_top') or '50px')
    return f'max-width: {width}; margin: {margin_top} auto 0;'


def build_login_page_context(
    *,
    title: str,
    index_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Template context for ``login.html``."""
    ui = resolve_login_ui(index_data)
    background_file = login_background_filename(index_data)
    panel = ui.get('panel') or {}
    labels = ui.get('labels') or {}
    inputs = ui.get('inputs') or {}
    button = ui.get('button') or {}
    error = ui.get('error') or {}

    custom_css = ''
    raw_custom = ui.get('custom_css')
    if isinstance(raw_custom, str) and raw_custom.strip():
        custom_css = raw_custom.strip()

    bg_url = campaign_asset_url(background_file)
    # CSS url() must use literal quotes — not html.escape (breaks in <style> blocks).
    if not bg_url.startswith('/assets/'):
        bg_url = ''

    return {
        'title': title,
        'background_url': campaign_asset_url(background_file),
        'login_ui': ui,
        'show_title_text': bool(ui.get('show_title_text', True)),
        'google_fonts_url': str(ui.get('google_fonts') or '').strip(),
        'panel_style': _panel_position_css(panel),
        'page_css': '\n'.join(
            line for line in [
                f"html, body {{ height: 100%; margin: 0; padding: 0; font-family: {_css_value(ui.get('font_family'))}; }}",
                (
                    f"body {{ background-color: {_css_value(ui.get('background_color'))}; min-height: 100vh; }}"
                ),
                (
                    f".login-background {{ position: fixed; inset: 0; width: 100%; height: 100%; "
                    f"object-fit: {_css_value(ui.get('background_fit'))}; "
                    f"object-position: {_css_value(ui.get('background_position'))}; "
                    f"z-index: 0; pointer-events: none; }}"
                ),
                ".login-shell { position: relative; z-index: 1; min-height: 100vh; }",
                ".login-title, .login-panel { z-index: 2; }",
                (
                    f".login-title {{ text-align: center; margin-top: {_css_value(ui.get('title_margin_top'))}; "
                    f"color: {_css_value(ui.get('title_color'))}; "
                    f"font-size: {_css_value(ui.get('title_font_size'))}; "
                    f"text-shadow: {_css_value(ui.get('title_shadow'))}; }}"
                ),
                (
                    f".login-panel {{ {_panel_position_css(panel)} "
                    f"background: {_css_value(panel.get('background'))}; "
                    f"padding: {_css_value(panel.get('padding'))}; "
                    f"border-radius: {_css_value(panel.get('border_radius'))}; "
                    f"border: {_css_value(panel.get('border'))}; "
                    f"box-shadow: {_css_value(panel.get('box_shadow'))}; }}"
                ),
                f".login-panel label {{ color: {_css_value(labels.get('color'))}; font-size: {_css_value(labels.get('font_size'))}; }}",
                (
                    f".login-panel .form-control {{ background: {_css_value(inputs.get('background'))}; "
                    f"border: {_css_value(inputs.get('border'))}; color: {_css_value(inputs.get('color'))}; "
                    f"border-radius: {_css_value(inputs.get('border_radius'))}; }}"
                ),
                (
                    f".login-panel .btn-login {{ background: {_css_value(button.get('background'))}; "
                    f"color: {_css_value(button.get('color'))}; border: {_css_value(button.get('border'))}; "
                    f"border-radius: {_css_value(button.get('border_radius'))}; "
                    f"font-weight: {_css_value(button.get('font_weight'))}; width: 100%; }}"
                ),
                (
                    f".login-panel .login-error {{ background: {_css_value(error.get('background'))}; "
                    f"color: {_css_value(error.get('color'))}; border: {_css_value(error.get('border'))}; }}"
                ),
                custom_css,
            ]
            if line
        ),
        'button_text': str(button.get('text') or 'Login'),
    }
