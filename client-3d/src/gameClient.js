/** Same-origin fetch + Socket.IO client for the 3D VTT. */

import { io } from 'socket.io-client';

const jsonHeaders = { Accept: 'application/json', 'Content-Type': 'application/json' };

async function parseBody(res) {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export function createGameClient({ username } = {}) {
  const socket = io({
    transports: ['polling', 'websocket'],
    path: '/socket.io',
    withCredentials: true,
  });

  socket.on('connect', () => {
    if (username) {
      socket.emit('register', { username });
    }
  });

  const pendingEffects = [];
  let effectHandler = null;
  socket.on('effect:set', (data) => {
    if (effectHandler) {
      effectHandler(data);
    } else {
      pendingEffects.push(data);
    }
  });

  return {
    socket,

    async getMapState() {
      const res = await fetch('/map_state', { credentials: 'include', headers: { Accept: 'application/json' } });
      if (res.status === 401) {
        window.location.href = '/login';
        throw new Error('unauthorized');
      }
      if (!res.ok) {
        throw new Error(`map_state failed: ${res.status}`);
      }
      return res.json();
    },

    async getPath(from, to, { accumulatedPath } = {}) {
      const params = new URLSearchParams();
      params.set('from[x]', String(from.x));
      params.set('from[y]', String(from.y));
      params.set('to[x]', String(to.x));
      params.set('to[y]', String(to.y));
      if (accumulatedPath && accumulatedPath.length) {
        params.set('accumulatedPath', JSON.stringify(accumulatedPath));
      }
      const res = await fetch(`/path?${params}`, { credentials: 'include' });
      return res.json();
    },

    async getTarget(payload) {
      const params = new URLSearchParams({ payload: JSON.stringify(payload) });
      const res = await fetch(`/target?${params}`, { credentials: 'include' });
      return res.json();
    },

    async postAction(body) {
      const res = await fetch('/action', {
        method: 'POST',
        credentials: 'include',
        headers: jsonHeaders,
        body: JSON.stringify(body),
      });
      return parseBody(res);
    },

    async postEndTurn() {
      const res = await fetch('/end_turn', {
        method: 'POST',
        credentials: 'include',
        headers: jsonHeaders,
        body: '{}',
      });
      return parseBody(res);
    },

    async postIllumination({ illumination, reset = false, mapName } = {}) {
      const body = {};
      if (reset) body.reset = true;
      else if (illumination != null) body.illumination = illumination;
      if (mapName) body.map_name = mapName;
      const res = await fetch('/admin/illumination', {
        method: 'POST',
        credentials: 'include',
        headers: jsonHeaders,
        body: JSON.stringify(body),
      });
      return parseBody(res);
    },

    async getActionsBatch(ids) {
      const params = new URLSearchParams({ ids: ids.join(',') });
      const res = await fetch(`/actions/batch?${params}`, { credentials: 'include' });
      return res.json();
    },

    onMessage(handler) {
      socket.on('message', handler);
      return () => socket.off('message', handler);
    },

    /** Cosmetic 3D token facing (degrees): server validates + relays. */
    rotateToken(uid, yawDeg) {
      socket.emit('token:rotate', { uid, yaw: yawDeg });
    },

    /** DM camera POV: focus one entity; null/"all" resets to see everything. */
    async switchPov(entityUid) {
      const res = await fetch('/switch_pov', {
        method: 'POST',
        credentials: 'include',
        headers: jsonHeaders,
        body: JSON.stringify({ entity_uid: entityUid ?? null }),
      });
      return parseBody(res);
    },

    onTokenRotation(handler) {
      socket.on('token:rotation', handler);
      return () => socket.off('token:rotation', handler);
    },

    onEffectSet(handler) {
      effectHandler = handler;
      if (pendingEffects.length) {
        const queued = pendingEffects.splice(0, pendingEffects.length);
        for (const payload of queued) handler(payload);
      }
      return () => {
        if (effectHandler === handler) effectHandler = null;
      };
    },

    requestEffects() {
      socket.emit('request_effects');
    },
  };
}
