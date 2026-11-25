#!/usr/bin/env python3
"""Capture the README screenshots for the VTT.

Starts nothing itself: point it at a running server (``./start_web.sh`` or
``N20_SHOTS_BASE_URL=...``) and it drives the DM session with Playwright,
writing optimized PNGs into ``docs/screenshots/``.

    ./start_web.sh                       # default template campaign
    pip install playwright && playwright install chromium
    python scripts/capture_readme_screenshots.py

Optional overrides:
    N20_SHOTS_BASE_URL   default http://127.0.0.1:5001
    N20_SHOTS_USER / N20_SHOTS_PASSWORD   default dm / admin
    N20_SHOTS_OUT        output directory (default docs/screenshots)
    N20_SHOTS_MAX_WIDTH  downscale width (default 1280)
    N20_SHOTS_CHROME     chromium executable (default: Playwright's own)
    N20_SHOTS_SKIP       comma-separated shot names to skip
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = Path(os.getenv("N20_SHOTS_OUT", REPO_ROOT / "docs" / "screenshots"))
BASE_URL = os.getenv("N20_SHOTS_BASE_URL", "http://127.0.0.1:5001").rstrip("/")
USERNAME = os.getenv("N20_SHOTS_USER", "dm")
PASSWORD = os.getenv("N20_SHOTS_PASSWORD", "admin")
MAX_WIDTH = int(os.getenv("N20_SHOTS_MAX_WIDTH", "1280"))
CHROME = os.getenv("N20_SHOTS_CHROME", "")
SKIP = {name.strip() for name in os.getenv("N20_SHOTS_SKIP", "").split(",") if name.strip()}


def save(page, name: str) -> None:
    """Screenshot the viewport, then shrink + quantize for README weight."""
    if name in SKIP:
        print(f"  skip   {name}")
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = page.screenshot(type="png")
    path = OUT_DIR / f"{name}.png"
    try:
        from PIL import Image
    except ImportError:
        path.write_bytes(raw)
    else:
        import io

        img = Image.open(io.BytesIO(raw)).convert("RGB")
        if img.width > MAX_WIDTH:
            height = round(img.height * MAX_WIDTH / img.width)
            img = img.resize((MAX_WIDTH, height), Image.LANCZOS)
        # UI shots quantize cleanly and shrink 2-4x versus a plain PNG.
        quantize = getattr(Image, "Quantize", Image)
        img.quantize(colors=256, method=quantize.MAXCOVERAGE, dither=Image.FLOYDSTEINBERG).save(
            path, "PNG", optimize=True
        )
    print(f"  wrote  {path.relative_to(REPO_ROOT)} ({path.stat().st_size // 1024} KB)")


def wait_settled(page, ms: int = 1200) -> None:
    """Let socketio tile refreshes and CSS transitions finish."""
    page.wait_for_timeout(ms)


def vtt_menu(page, selector: str) -> None:
    """Open the VTT hamburger menu and click one item."""
    page.click("#expand-menu")
    page.wait_for_timeout(250)
    page.click(selector)
    page.wait_for_timeout(250)


def login(page) -> None:
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
    page.wait_for_selector("#username")
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click("button.btn-login")
    page.wait_for_selector("#tiles-area .tile", timeout=30000)
    page.wait_for_selector("#tiles-area .entity", timeout=30000)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright is not installed:\n"
            "  pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        return 2

    launch_kwargs = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if CHROME:
        launch_kwargs["executable_path"] = CHROME

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": 1600, "height": 900})
        page = context.new_page()
        page.set_default_timeout(20000)

        login(page)
        wait_settled(page)

        # 1. The tabletop: server-rendered map, tokens, portraits, action bar.
        save(page, "vtt-tabletop")

        # 2. Combined spawner: NPCs tab (unique / duplicate / delete per row).
        #    The CR accordion sections start collapsed, so open two of them.
        vtt_menu(page, "#toggle-spawner")
        page.wait_for_selector("#spawner:visible")
        page.wait_for_selector("#npc-list .npc-cr-group", timeout=15000)
        for group in page.query_selector_all("#npc-list .npc-cr-group.collapsed")[:2]:
            group.query_selector(".npc-cr-group-header").click()
            page.wait_for_timeout(200)
        page.wait_for_selector("#npc-list .npc-item:visible", timeout=10000)
        # Row actions (unique / duplicate / delete) are hover-revealed.
        hover_target = (page.query_selector("#npc-list .npc-item:has(.npc-action-delete)")
                        or page.query_selector("#npc-list .npc-item"))
        hover_target.hover()
        wait_settled(page, 600)
        save(page, "spawner-npcs")

        # 3. Adding a new campaign NPC (base type + unique flag). Filled in but
        #    never submitted, so the campaign is left untouched.
        page.click("#npc-create-toggle")
        page.wait_for_selector("#npc-create-form:visible")
        page.fill("#npc-create-name", "Bandit Captain")
        try:
            page.select_option("#npc-create-base", index=1)  # first bundled base type
        except Exception:
            pass  # base-type catalogue may be empty in a stripped campaign
        page.fill("#npc-create-hp", "65")
        page.fill("#npc-create-ac", "15")
        page.check("#npc-create-unique")
        wait_settled(page, 500)
        save(page, "spawner-npc-new")
        page.click("#npc-create-toggle")  # toggle closes the form

        # 4. Objects / terrain tab, with the new-object form open.
        page.click(".spawner-tab[data-spawner-tab='object']")
        page.wait_for_selector("#object-list .object-item:visible", timeout=15000)
        page.click("#object-create-toggle")
        page.wait_for_selector("#object-create-form:visible")
        page.fill("#object-create-name", "Grandfather Clock")
        page.check("#object-create-interactable")
        object_row = page.query_selector("#object-list .object-item:has(.object-action-delete)")
        if object_row:
            object_row.hover()  # reveal the per-row delete button
        wait_settled(page, 500)
        save(page, "spawner-objects")
        page.click("#close-spawner")

        # 5. Combat: turn order with initiative, a live combat log, battle on
        #    the map. /battle (JSON) is the same call the "Start Match" button
        #    makes, so the AI loop drives real turns; then DM battle pacing
        #    holds the fight open while we photograph the table. Fresh spawns
        #    keep the fight healthy no matter what earlier runs did.
        fighter = os.getenv("N20_SHOTS_FIGHTER", "wolf")
        spawned: list[str] = []
        for _ in range(4):
            uid = page.evaluate(
                """async (npcType) => {
                    const r = await fetch('/spawn_npc', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({npc_type: npcType, duplicate: true}),
                    });
                    const data = await r.json();
                    return data.entity_uid || null;
                }""",
                fighter,
            )
            if uid:
                spawned.append(uid)
        combatant_uids = spawned or page.eval_on_selector_all(
            "#tiles-area .entity", "els => els.map(e => e.dataset.id).filter(Boolean)"
        )[:7]
        battle_live = False
        if combatant_uids:
            turn_order = [
                {"id": uid, "group": "a" if i % 2 == 0 else "b", "controller": "ai"}
                for i, uid in enumerate(combatant_uids)
            ]
            page.evaluate(
                """async ([endpoint, payload]) => {
                    const r = await fetch(endpoint, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(payload),
                    });
                    return r.status;
                }""",
                ["/battle", {"battle_turn_order": turn_order}],
            )
            wait_settled(page, 5000)  # initiative + a few AI turns
            page.evaluate(
                """async () => {
                    await fetch('/battle_pacing', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({dm_ends_ai_turns: true}),
                    });
                }"""
            )
            wait_settled(page, 1500)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#tiles-area .entity", timeout=30000)
            try:
                page.wait_for_selector("#battle-turn-order .turn-order-item:visible", timeout=8000)
                battle_live = True
            except Exception:
                pass

        if battle_live:
            page.click("#zoom-reset")  # 100% ...
            page.evaluate(
                "(uid) => window.centerOnEntityId && centerOnEntityId(uid)",
                combatant_uids[0],
            )  # ... then put the fighting on screen
            wait_settled(page, 1500)
            vtt_menu(page, "#open-console")
            page.wait_for_selector("#console-container:visible", timeout=10000)
            page.evaluate("""() => {
                const order = document.querySelector('#battle-turn-order');
                const log = document.querySelector('#console-container');
                order.style.left = '16px'; order.style.top = '110px'; order.style.right = 'auto';
                log.style.left = '1050px'; log.style.top = '110px'; log.style.right = 'auto';
                log.style.height = '520px';
                const chat = document.querySelector('#localConversationPanel');
                if (chat) chat.style.display = 'none';
            }""")
            # Pacing holds each AI turn: end a handful of turns so the log shows
            # real attacks and damage rolls, then scroll it to the newest line.
            for _ in range(6):
                try:
                    page.click("#next-turn", timeout=3000)
                except Exception:
                    break
                wait_settled(page, 900)
            page.evaluate("""() => {
                const log = document.querySelector('#console-container');
                log.scrollTop = log.scrollHeight;
            }""")
            page.mouse.move(800, 880)  # park the cursor so hover tooltips stay hidden
            wait_settled(page, 1200)
            save(page, "combat")
        else:
            print("  skip   combat (could not open a battle on the starting map)")

        if combatant_uids:
            # Leave the table as we found it: end the fight, remove our spawns.
            page.evaluate(
                """async (uids) => {
                    await fetch('/battle_pacing', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({dm_ends_ai_turns: false}),
                    });
                    await fetch('/stop', {method: 'POST'});
                    for (const entity_uid of uids) {
                        await fetch('/delete_entity', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({entity_uid}),
                        });
                    }
                }""",
                spawned,
            )
            wait_settled(page, 1500)

        # 6. Notebook dock (campaign / player notes, map pins, DM assistant).
        #    Seed a couple of campaign notes so the tree is not empty, then
        #    delete them again after the shots.
        seeded = page.evaluate(
            """async () => {
                const post = async (url, body) =>
                    (await fetch(url, {method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(body)})).json();
                const folder = (await post('/notebook/folders',
                    {scope: 'campaign', name: 'Locations'})).folder;
                const notes = [];
                if (folder) {
                    notes.push((await post('/notebook/items', {
                        scope: 'campaign', folder_id: folder.id,
                        title: 'Goblin Cave',
                        content: 'Ambush spot: two goblins on the north ledge, one wolf patrol.',
                    })).item);
                    notes.push((await post('/notebook/items', {
                        scope: 'campaign', folder_id: folder.id,
                        title: 'Ruby Collective',
                        content: 'Who are they? Ask the blacksmith in town.',
                    })).item);
                }
                return {folder: folder && folder.id,
                        notes: notes.filter(Boolean).map(n => n.id)};
            }"""
        )
        page.evaluate("() => { const c = document.querySelector('#console-container'); if (c) c.style.display = 'none'; }")
        vtt_menu(page, "#toggle-notebook")
        page.wait_for_selector("#notebook-panel", timeout=10000)
        wait_settled(page, 1800)
        save(page, "notebook")
        try:
            page.click("#notebook-panel .notebook-tab[data-tab='assistant']")
            wait_settled(page, 1500)
            save(page, "ai-assistant")
        except Exception:
            print("  skip   ai-assistant (tab unavailable for this login)")
        page.click(".notebook-close")
        wait_settled(page, 600)
        page.evaluate(
            """async ([folderId, noteIds]) => {
                for (const id of noteIds) await fetch(`/notebook/items/${id}`, {method: 'DELETE'});
                if (folderId) await fetch(`/notebook/folders/${folderId}`, {method: 'DELETE'});
            }""",
            [seeded.get("folder"), seeded.get("notes") or []],
        )

        # 7. Character builder (opened by the PC tray's "+ New").
        builder = context.new_page()
        builder.set_default_timeout(20000)
        builder.goto(f"{BASE_URL}/character_builder", wait_until="domcontentloaded")
        builder.wait_for_timeout(2500)
        save(builder, "character-builder")
        builder.close()

        # 8. Three.js sibling client (skips itself when WebGL is unavailable).
        if "vtt-3d" not in SKIP:
            client3d = context.new_page()
            client3d.set_default_timeout(20000)
            try:
                client3d.goto(f"{BASE_URL}/3d", wait_until="domcontentloaded")
                client3d.wait_for_selector("canvas", timeout=15000)
                client3d.wait_for_timeout(4000)
                save(client3d, "vtt-3d")
            except Exception as exc:  # headless WebGL is not guaranteed
                print(f"  skip   vtt-3d ({type(exc).__name__}: {exc})")
            finally:
                client3d.close()

        browser.close()
    print(f"done -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
