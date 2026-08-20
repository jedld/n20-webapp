# n20-client-3d

Vite + Three.js sibling VTT. See [docs/VTT_3D.md](../../docs/VTT_3D.md).

```bash
npm install
npm test          # geometry + spell/status FX catalog (no WebGL)
npm run build     # → ../webapp/static/client-3d/
```

Flask serves the built bundle at `/client-3d/` and the shell at `GET /3d`.
