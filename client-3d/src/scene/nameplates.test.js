import { describe, expect, it } from 'vitest';
import {
  healthColor,
  hpRatio,
  nameplateView,
  parseDmRole,
  showHpNumbers,
} from './nameplates.js';

describe('dm role parsing', () => {
  it('accepts the role shapes the template emits', () => {
    expect(parseDmRole(['dm'])).toBe(true);
    expect(parseDmRole(['player'])).toBe(false);
    expect(parseDmRole({ dm: true })).toBe(true);
    expect(parseDmRole({ dm: false })).toBe(false);
    expect(parseDmRole('dm')).toBe(true);
    expect(parseDmRole('player dm')).toBe(true);
    expect(parseDmRole(null)).toBe(false);
  });
});

describe('hp number visibility', () => {
  const npc = { uid: 'gob-1', is_npc: true };
  const sidekick = { uid: 'sk-1', is_npc: true, sidekick: true };
  const pc = { uid: 'pc-1', is_npc: false };

  it('the DM sees exact HP everywhere', () => {
    expect(showHpNumbers(npc, { isDm: true })).toBe(true);
  });

  it('players see numbers on PCs, sidekicks and their own entities', () => {
    expect(showHpNumbers(pc, { isDm: false })).toBe(true);
    expect(showHpNumbers(sidekick, { isDm: false })).toBe(true);
    expect(showHpNumbers(npc, { isDm: false, ownUids: ['gob-1'] })).toBe(true);
  });

  it('players see only the bar on enemy/neutral NPCs', () => {
    expect(showHpNumbers(npc, { isDm: false, ownUids: ['pc-1'] })).toBe(false);
  });
});

describe('nameplate view model', () => {
  it('shows hp text for a PC', () => {
    const view = nameplateView({ name: 'Gomerin', hp: 12, max_hp: 24 });
    expect(view.name).toBe('Gomerin');
    expect(view.ratio).toBeCloseTo(0.5);
    // exact 50% falls in the yellow bucket, same boundary as the 2D scale
    expect(view.color).toBe('#f1c40f');
    expect(view.hpText).toBe('12/24');
  });

  it('hides hp text but keeps the bar for enemy NPCs (non-DM)', () => {
    const view = nameplateView(
      { name: 'Goblin', hp: 3, max_hp: 10, is_npc: true },
      { isDm: false },
    );
    expect(view.ratio).toBeCloseTo(0.3);
    expect(view.color).toBe('#f39c12');
    expect(view.hpText).toBe('');
  });

  it('keeps hp text for the same NPC on a DM view', () => {
    const view = nameplateView(
      { name: 'Goblin', hp: 3, max_hp: 10, is_npc: true },
      { isDm: true },
    );
    expect(view.hpText).toBe('3/10');
  });

  it('renders no health row without HP values', () => {
    const view = nameplateView({ name: 'Statue' });
    expect(view.ratio).toBeNull();
    expect(view.color).toBeNull();
    expect(view.hpText).toBe('');
  });

  it('clamps and colors dead tokens', () => {
    const view = nameplateView(
      { name: 'Wolf', hp: 0, max_hp: 12, dead: true },
      { isDm: true },
    );
    expect(view.ratio).toBe(0);
    expect(healthColor(view.ratio)).toBe('#e74c3c');
    expect(view.dead).toBe(true);
  });
});
