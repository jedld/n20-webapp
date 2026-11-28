/** Entity nameplate HUD chips for the 3D VTT (name + health bar).
 *
 * Pure view-model helpers so the DM/player HP-visibility gating can be unit
 * tested without a DOM. The nameplate renders the entity name above a health
 * bar; exact HP numbers are only shown where the viewer is allowed to see
 * them (DM sees everything; players see numbers on PCs, party sidekicks and
 * their own characters, and only the bare bar on enemy/neutral NPCs).
 */

/** Same role shapes the server template may emit (list, object or string). */
export function parseDmRole(role) {
  if (Array.isArray(role)) {
    return role.some((item) => String(item).trim().toLowerCase() === 'dm');
  }
  if (role && typeof role === 'object') {
    return Boolean(role.dm);
  }
  return String(role || '').toLowerCase().split(/[\s,]+/).includes('dm');
}

/** True when the viewer may see exact HP numbers for this entity. */
export function showHpNumbers(entity = {}, { isDm = false, ownUids = [] } = {}) {
  if (isDm) return true;
  if (!entity.is_npc) return true; // player characters are party-side
  if (entity.sidekick) return true; // party NPC companions
  const uid = entity.uid == null ? '' : String(entity.uid);
  if (uid && ownUids.includes(uid)) return true;
  return false; // enemy / neutral NPC: health bar only
}

/** 0..1 HP fraction, or null when the entity has no HP readout. */
export function hpRatio(entity = {}) {
  const hp = Number(entity.hp);
  const max = Number(entity.max_hp);
  if (!Number.isFinite(hp) || !Number.isFinite(max) || max <= 0) return null;
  return Math.max(0, Math.min(1, hp / max));
}

/** Same 4-step color scale the 2D nameplates use. */
export function healthColor(ratio) {
  if (ratio < 0.25) return '#e74c3c';
  if (ratio < 0.5) return '#f39c12';
  if (ratio < 0.75) return '#f1c40f';
  return '#27ae60';
}

/** Everything the DOM nameplate chip needs for one entity. */
export function nameplateView(entity = {}, { isDm = false, ownUids = [] } = {}) {
  const ratio = hpRatio(entity);
  const showHp = showHpNumbers(entity, { isDm, ownUids });
  return {
    name: entity.name || '',
    ratio,
    color: ratio == null ? null : healthColor(ratio),
    hpText: showHp && ratio != null ? `${entity.hp}/${entity.max_hp}` : '',
    dead: Boolean(entity.dead),
  };
}
