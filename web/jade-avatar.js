const Jade = (() => {
  const MODEL_URL = 'https://jade.local/jadecat.model3.json';
  const UNSAFE_EVAL_SHIM = 'https://cdn.jsdelivr.net/npm/@pixi/unsafe-eval@6.5.10/dist/browser/unsafe-eval.min.js';
  const state = {
    app: null, model: null, ready: false, failed: null, host: null,
    mood: 'idle', moodUntil: 0, moodNext: 'idle',
    gaze: null, pointer: { x: 0, y: 0, t: 0 }, wander: 0,
    blink: { next: 0, t: -1 }, nodT: -1, earT: -1,
    speaking: false, mouth: 0, mouthTarget: 0, bump: 0, utterId: 0,
    cur: {}, frame: 'portrait', onStatus: () => {}, onSpeak: () => {},
  };
  const P = {
    AX: 'ParamAngleX', AY: 'ParamAngleY', AZ: 'ParamAngleZ', EYE_L: 'ParamEyeLOpen', EYE_R: 'ParamEyeROpen',
    SMILE_L: 'ParamEyeLSmile', SMILE_R: 'ParamEyeRSmile', BROW_LY: 'ParamBrowLY', BROW_RY: 'ParamBrowRY',
    BROW_LA: 'ParamBrowLAngle', BROW_RA: 'ParamBrowRAngle', BROW_LF: 'ParamBrowLForm', BROW_RF: 'ParamBrowRForm',
    MOUTH_FORM: 'ParamMouthForm', MOUTH_OPEN: 'ParamMouthOpenY', CHEEK: 'ParamCheek', EAR_X: 'Param6', EAR_Y: 'Param7',
  };
  // Expression targets per mood (absolute values, blended every frame)
  const MOODS = {
    idle:      { gaze: null,          [P.BROW_LY]: 0,    [P.BROW_RY]: 0,    [P.MOUTH_FORM]: 0.25, [P.SMILE_L]: 0,   [P.SMILE_R]: 0,   [P.CHEEK]: 0,   label: 'online' },
    listening: { gaze: [-0.75, -0.35], [P.BROW_LY]: 0.25, [P.BROW_RY]: 0.25, [P.MOUTH_FORM]: 0.4,  [P.SMILE_L]: 0,   [P.SMILE_R]: 0,   [P.CHEEK]: 0,   label: 'listening' },
    thinking:  { gaze: [0.55, 0.65],   [P.BROW_LY]: 0.45, [P.BROW_RY]: 0.15, [P.MOUTH_FORM]: -0.2, [P.SMILE_L]: 0,   [P.SMILE_R]: 0,   [P.CHEEK]: 0,   label: 'thinking' },
    writing:   { gaze: [-0.7, -0.15],  [P.BROW_LY]: 0.1,  [P.BROW_RY]: 0.1,  [P.MOUTH_FORM]: 0.3,  [P.SMILE_L]: 0,   [P.SMILE_R]: 0,   [P.CHEEK]: 0,   label: 'writing' },
    working:   { gaze: [0.05, -0.85],  [P.BROW_LY]: -0.15,[P.BROW_RY]: -0.15,[P.MOUTH_FORM]: 0,    [P.SMILE_L]: 0,   [P.SMILE_R]: 0,   [P.CHEEK]: 0,   label: 'jacked in' },
    happy:     { gaze: [0, 0.1],       [P.BROW_LY]: 0.35, [P.BROW_RY]: 0.35, [P.MOUTH_FORM]: 1,    [P.SMILE_L]: 1,   [P.SMILE_R]: 1,   [P.CHEEK]: 1,   label: 'done' },
    error:     { gaze: [0, -0.1],      [P.BROW_LY]: -0.8, [P.BROW_RY]: -0.8, [P.MOUTH_FORM]: -1,   [P.SMILE_L]: 0,   [P.SMILE_R]: 0,   [P.CHEEK]: 0,   [P.BROW_LA]: -1, [P.BROW_RA]: -1, [P.BROW_LF]: -1, [P.BROW_RF]: -1, label: 'error' },
  };
  const EXPR_KEYS = [P.BROW_LY, P.BROW_RY, P.BROW_LA, P.BROW_RA, P.BROW_LF, P.BROW_RF, P.MOUTH_FORM, P.SMILE_L, P.SMILE_R, P.CHEEK];

  const b64gz = async (id) => {
    const bin = Uint8Array.from(atob(document.getElementById(id).textContent.replace(/\s+/g, '')), c => c.charCodeAt(0));
    return new Response(new Blob([bin]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  };
  const jsonOf = (id) => JSON.parse(document.getElementById(id).textContent);
  const loadScript = (src) => new Promise((res, rej) => { const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = () => rej(new Error('could not load ' + src)); document.head.appendChild(s); });
  const evalAllowed = () => { try { return new Function('return 1')() === 1; } catch { return false; } };

  async function ensureEvalShim() {
    if (evalAllowed()) return;
    // Pixi 6 compiles shader sync code with new Function; under a strict CSP use the official no-eval build
    await loadScript(UNSAFE_EVAL_SHIM);
    const ue = window.PIXI?.unsafeEval || window._pixi_unsafe_eval || window.__pixi_unsafe_eval;
    try { ue?.install?.(window.PIXI); } catch {}
  }

  function setP(core, id, v) { try { core.setParameterValueById(id, v); } catch {} }
  function addP(core, id, v) { try { core.addParameterValueById(id, v); } catch {} }

  async function init({ host, onStatus, onSpeak } = {}) {
    state.host = host; state.onStatus = onStatus || (() => {}); state.onSpeak = onSpeak || (() => {});
    if (!window.PIXI || !PIXI.live2d) throw new Error('Live2D runtime failed to load');
    state.onStatus('waking her up…');
    const files = {
      'jadecat.model3.json': jsonOf('l2d-model'), 'jadecat.physics3.json': jsonOf('l2d-physics'), 'jadecat.cdi3.json': jsonOf('l2d-cdi'),
      'jadecat.moc3': await b64gz('l2d-moc'),
    };
    const L = PIXI.live2d;
    if (!L.Live2DLoader.__jade) {
      L.Live2DLoader.middlewares.unshift((ctx, next) => {
        const name = String(ctx.url).split(/[?#]/)[0].split('/').pop();
        if (Object.prototype.hasOwnProperty.call(files, name)) {
          const v = files[name]; ctx.result = v instanceof ArrayBuffer ? v.slice(0) : JSON.parse(JSON.stringify(v)); return Promise.resolve();
        }
        return next();
      });
      L.Live2DLoader.__jade = true;
    }
    // texture: decode the embedded WebP and pre-seed PIXI's cache under the URL the model will resolve
    const img = new Image(); img.src = document.getElementById('l2d-tex').textContent.trim();
    await img.decode();
    const texUrl = new URL(files['jadecat.model3.json'].FileReferences.Textures[0], MODEL_URL).href;
    if (!PIXI.utils.TextureCache[texUrl]) PIXI.Texture.addToCache(PIXI.Texture.from(img), texUrl);

    let app;
    const mk = () => new PIXI.Application({ resizeTo: host, backgroundAlpha: 0, antialias: true, autoDensity: true, resolution: Math.min(devicePixelRatio || 1, 2) });
    try { app = mk(); } catch (e) {
      if (!/unsafe-eval/i.test(String(e?.message))) throw e;
      await ensureEvalShim(); app = mk();
    }
    app.view.className = 'jade-canvas';
    host.prepend(app.view);
    state.app = app;
    const model = await L.Live2DModel.from(MODEL_URL, { autoInteract: false, autoUpdate: true });
    state.model = model;
    model.visible = false;
    app.stage.addChild(model);
    const core = model.internalModel.coreModel;
    for (const k of EXPR_KEYS) state.cur[k] = 0;
    model.internalModel.on('beforeModelUpdate', () => frame(core));
    app.ticker.addOnce(() => { layout(); model.visible = true; });
    new ResizeObserver(() => { app.resize(); layout(); }).observe(host);
    window.addEventListener('pointermove', e => { state.pointer = { x: e.clientX, y: e.clientY, t: performance.now() }; }, { passive: true });
    state.ready = true; state.onStatus('');
    return api;
  }

  function layout() {
    const { app, model } = state; if (!model) return;
    model.scale.set(1);
    const b = model.getLocalBounds(); const w = app.renderer.width / app.renderer.resolution, h = app.renderer.height / app.renderer.resolution;
    if (!w || !h) return;
    if (h < 130) {
      // compact strip: just her face, parked on the left
      const viewH = b.height * 0.155;
      const s = h / viewH;
      model.scale.set(s); model.pivot.set(b.x + b.width / 2, b.y + b.height * 0.012); model.position.set(Math.max(40, h * 0.55), 0);
    } else if (state.frame === 'full') {
      const s = Math.min(w * 0.92 / b.width, h * 0.94 / b.height);
      model.scale.set(s); model.pivot.set(b.x + b.width / 2, b.y + b.height / 2); model.position.set(w / 2, h / 2 + h * 0.01);
    } else {
      // portrait: head + upper body fills the stage, cropped below the chest
      const viewH = b.height * 0.44, viewW = b.width * 0.66;
      const s = Math.min(h / viewH, w / viewW);
      model.scale.set(s); model.pivot.set(b.x + b.width / 2, b.y); model.position.set(w / 2, h * 0.035);
    }
  }

  function frame(core) {
    const now = performance.now();
    if (state.mood !== 'idle' && state.moodUntil && now > state.moodUntil) { state.mood = state.moodNext; state.moodUntil = 0; }
    const M = MOODS[state.mood] || MOODS.idle;
    // gaze: mood target > recent pointer > lazy wander
    const fc = state.model.internalModel.focusController;
    let gx, gy;
    if (state.speaking) { gx = 0; gy = 0.05; }
    else if (M.gaze) { [gx, gy] = M.gaze; gx += Math.sin(now / 900) * 0.05; }
    else if (now - state.pointer.t < 4000) {
      const r = state.app.view.getBoundingClientRect();
      gx = Math.max(-1, Math.min(1, (state.pointer.x - (r.left + r.width / 2)) / (window.innerWidth * 0.35)));
      gy = Math.max(-1, Math.min(1, -(state.pointer.y - (r.top + r.height * 0.3)) / (window.innerHeight * 0.4)));
    } else { state.wander += 0.006; gx = Math.sin(state.wander) * 0.35; gy = Math.cos(state.wander * 0.6) * 0.15; }
    fc.focus(gx, gy);
    // expressions (smoothed)
    for (const k of EXPR_KEYS) { const t = M[k] ?? 0; state.cur[k] += (t - state.cur[k]) * 0.12; setP(core, k, state.cur[k]); }
    // blink (the model has no EyeBlink group, so we drive it)
    const B = state.blink; let open = 1;
    if (B.t < 0 && now > B.next) B.t = now;
    if (B.t >= 0) { const d = now - B.t; open = d < 70 ? 1 - d / 70 : d < 110 ? 0 : d < 230 ? (d - 110) / 120 : 1; if (d >= 230) { B.t = -1; B.next = now + 1800 + Math.random() * 4200 * (Math.random() < 0.15 ? 0.1 : 1); } }
    const smile = Math.max(state.cur[P.SMILE_L], 0);
    setP(core, P.EYE_L, open * (1 - smile * 0.35)); setP(core, P.EYE_R, open * (1 - smile * 0.35));
    // mouth: speech lipsync or closed
    if (state.speaking) { const n = 0.5 + 0.5 * Math.sin(now / 55) * Math.sin(now / 137 + 1.3); state.mouthTarget = 0.18 + 0.62 * n + state.bump; state.bump *= 0.86; }
    else state.mouthTarget = state.mood === 'happy' ? 0.25 : 0;
    state.mouth += (state.mouthTarget - state.mouth) * (state.mouthTarget > state.mouth ? 0.55 : 0.25);
    setP(core, P.MOUTH_OPEN, Math.min(1, state.mouth));
    for (const [k, v] of Object.entries(state.extra || {})) setP(core, k, v);
    // nod + ear twitch (transient)
    if (state.nodT >= 0) { const d = (now - state.nodT) / 600; if (d > 1) state.nodT = -1; else addP(core, P.AY, -Math.sin(d * Math.PI) * 14); }
    if (state.earT < 0 && Math.random() < (state.mood === 'working' ? 0.012 : 0.002)) state.earT = now;
    if (state.earT >= 0) { const d = (now - state.earT) / 260; if (d > 1) state.earT = -1; else { addP(core, P.EAR_X, Math.sin(d * Math.PI * 2) * 0.8); addP(core, P.EAR_Y, Math.sin(d * Math.PI) * 0.6); } }
  }

  // ---------------- public api ----------------
  function setMood(m, ms = 0, then = 'idle') {
    if (!MOODS[m]) m = 'idle';
    state.mood = m; state.moodUntil = ms ? performance.now() + ms : 0; state.moodNext = then;
  }
  function nod() { state.nodT = performance.now(); }
  function setFrame(f) { state.frame = f === 'full' ? 'full' : 'portrait'; layout(); }

  // speech (Web Speech API) with procedural lipsync
  let voices = [];
  const loadVoices = () => { try { voices = speechSynthesis.getVoices(); } catch { voices = []; } return voices; };
  if (!!window.speechSynthesis) { loadVoices(); speechSynthesis.onvoiceschanged = loadVoices; }
  function pickVoice(name) {
    const v = voices.length ? voices : loadVoices(); if (!v.length) return null;
    if (name) { const f = v.find(x => x.name === name); if (f) return f; }
    const pref = [/Libby|Sonia|Maisie|Mia|Hazel/i, /Google UK English Female/i, /Serena|Kate|Martha|Stephanie/i, /en-GB/i, /Female|Samantha|Zira|Aria|Jenny/i, /^en/i];
    for (const re of pref) { const f = v.find(x => re.test(x.name) || re.test(x.lang)); if (f) return f; }
    return v[0];
  }
  function speechText(md) {
    return String(md || '')
      .replace(/```[\s\S]*?```/g, '\n(code below)\n').replace(/`([^`]+)`/g, '$1')
      .replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/^#+\s*/gm, '').replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, '').replace(/[*_~>|#]/g, '')
      .replace(/https?:\/\/\S+/g, 'a link')
      .replace(/([^.!?:;,\s])[ \t]*\n+/g, '$1. ')          // line / list-item ends become pauses
      .replace(/\s+/g, ' ').trim();
  }
  // Split into short pieces: browsers silently stop long utterances (~15 s) and some drop big queues.
  function chunkText(t, max = 180) {
    const out = [];
    for (let s of t.match(/[^.!?…]+[.!?…]*["')\]]*\s*/g) || [t]) {
      s = s.trim(); if (!s) continue;
      while (s.length > max) {
        let cut = s.lastIndexOf(', ', max); if (cut < max * 0.4) cut = s.lastIndexOf(' ', max); if (cut < max * 0.4) cut = max;
        out.push(s.slice(0, cut + 1).trim()); s = s.slice(cut + 1).trim();
      }
      if (s) out.push(s);
    }
    return out;
  }
  // Native TTS bridge (the Android app exposes window.NetrunnerAndroid; WebView has no speechSynthesis)
  const nativeTTS = () => typeof window.NetrunnerAndroid?.speak === 'function';
  const nativeCbs = new Map(); let nativeSeq = 0;
  window.__jadeTTS = (ev, uid) => { const cb = nativeCbs.get(uid); if (cb) cb(ev); };
  function speakChunk(c, v, rate, pitch, onstart, ondone) {
    if (nativeTTS()) {
      const uid = 'u' + (++nativeSeq); let started = false;
      nativeCbs.set(uid, ev => { if (ev === 'start' && !started) { started = true; onstart(); } else if (ev !== 'start') { nativeCbs.delete(uid); ondone(); } });
      try { window.NetrunnerAndroid.speak(uid, c, pitch, rate); } catch { nativeCbs.delete(uid); ondone(); }
      return;
    }
    const u = new SpeechSynthesisUtterance(c); if (v) { u.voice = v; u.lang = v.lang; } u.rate = rate; u.pitch = pitch;
    let done = false; const fin = () => { if (!done) { done = true; clearTimeout(guard); ondone(); } };
    u.onstart = onstart; u.onboundary = () => { state.bump = 0.35; }; u.onend = fin; u.onerror = fin;
    // safety net: if the engine never fires onend, move on after a generous estimate
    const guard = setTimeout(fin, 4000 + c.length * 140 / Math.max(rate, 0.3));
    speechSynthesis.speak(u);
  }
  function speak(text, { voice, rate = 1.03, pitch = 1.25 } = {}) {
    if (!(!!window.speechSynthesis) && !nativeTTS()) return false;
    const t = speechText(text); if (!t) return false;
    stop();
    const id = ++state.utterId; const v = nativeTTS() ? null : pickVoice(voice);
    const chunks = chunkText(t); let i = 0;
    const next = () => {
      if (id !== state.utterId) return;
      if (i >= chunks.length) { state.speaking = false; state.onSpeak(''); return; }
      const c = chunks[i++];
      speakChunk(c, v, rate, pitch, () => { if (id === state.utterId) { state.speaking = true; state.onSpeak(c); } }, next);
    };
    next();
    return true;
  }
  function stop() {
    state.utterId++; state.speaking = false;
    try { speechSynthesis.cancel(); } catch {}
    if (nativeTTS()) { try { window.NetrunnerAndroid.stop(); } catch {} nativeCbs.clear(); }
  }

  const api = { init, setMood, nod, setFrame, speak, stop, voices: () => (voices.length ? voices : loadVoices()), get ready() { return state.ready; }, get mood() { return state.mood; }, label: () => (MOODS[state.mood] || MOODS.idle).label, state, P };
  return api;
})();
