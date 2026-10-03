// Mounts src/f1dash/ui/components/replay_player into jsdom the way Streamlit's
// components v2 does: the component's HTML and CSS inside an open shadow
// root (isolate_styles=True), then the default export called with
// { name, key, data, parentElement, setStateValue, setTriggerValue } -
// again whenever `data` changes - and its return value kept as the cleanup.
//
// Payloads come from tests/js/build_payloads.py (the real payload format).
// tests/test_player_js.py builds them and sets PLAYER_FIXTURE_DIR; run
// standalone, the harness builds them with $PYTHON (default: the repo's
// .venv interpreter, else "python").

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
const COMPONENT = path.join(ROOT, "src", "f1dash", "ui", "components", "replay_player");

let fixtureDir = process.env.PLAYER_FIXTURE_DIR;

function python() {
  if (process.env.PYTHON) return process.env.PYTHON;
  for (const candidate of [
    path.join(ROOT, ".venv", "Scripts", "python.exe"),
    path.join(ROOT, ".venv", "bin", "python"),
  ]) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return "python";
}

function fixtures() {
  if (!fixtureDir) {
    fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "player-js-"));
    execFileSync(python(), [path.join(HERE, "build_payloads.py"), fixtureDir], {
      cwd: ROOT,
      stdio: "inherit",
      // f1dash imports from the checkout's src/ without an install (REPO-10).
      env: {
        ...process.env,
        PYTHONPATH: [path.join(ROOT, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      },
    });
  }
  return fixtureDir;
}

export function loadPayload(name) {
  return JSON.parse(fs.readFileSync(path.join(fixtures(), `${name}.json`), "utf-8"));
}

export function loadMeta() {
  return JSON.parse(fs.readFileSync(path.join(fixtures(), "meta.json"), "utf-8"));
}

export function componentSource() {
  const read = (kind) => fs.readFileSync(path.join(COMPONENT, `player.${kind}`), "utf-8");
  return { html: read("html"), css: read("css"), js: read("js") };
}

const EXPORT = "export default function";

// The player is an ES module with one default export. It is evaluated inside
// the jsdom window (so `document`, `window` and timers are jsdom's) with the
// export rewritten into a window property.
function install(window, js) {
  const count = js.split(EXPORT).length - 1;
  if (count !== 1) throw new Error(`player.js must have exactly one "${EXPORT}", found ${count}`);
  window.eval(js.replace(EXPORT, "window.__replayPlayer = function"));
  return window.__replayPlayer;
}

/**
 * Mount the player. Returns helpers:
 *   root      - the .rp element inside the shadow root
 *   shadow    - the shadow root (Streamlit's parentElement)
 *   state     - every setStateValue call, in order: [name, value]
 *   rerun(d)  - Streamlit re-invoking the export with new data
 *   unmount() - the cleanup Streamlit calls on unmount
 *   ready()   - resolves once asynchronous decoding has finished
 */
export async function mount(payload, extra = {}) {
  const source = componentSource();
  const dom = new JSDOM('<!doctype html><html><body><div id="host"></div></body></html>', {
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;
  // Browser APIs jsdom lacks but every supported browser has.
  if (!window.DecompressionStream) window.DecompressionStream = globalThis.DecompressionStream;
  const host = window.document.getElementById("host");
  const shadow = host.attachShadow({ mode: "open" });
  const wrapper = window.document.createElement("div");
  wrapper.innerHTML = source.html;
  const style = window.document.createElement("style");
  style.textContent = source.css;
  shadow.append(wrapper, style);
  const render = install(window, source.js);
  const state = [];
  const call = (data) =>
    render({
      name: "f1_replay_player",
      key: "test",
      data,
      parentElement: shadow,
      setStateValue: (name, value) => state.push([name, value]),
      setTriggerValue: () => {},
    });
  const base = { cursor: payload.clock.lights_out, seek: 0, ...extra };
  let data = { ...payload, ...base };
  let cleanup = call(data);
  const root = shadow.querySelector(".rp");
  // The player marks its root once the payload is decoded and drawn.
  const ready = async () => {
    const started = Date.now();
    while (root.dataset.ready !== "1") {
      if (Date.now() - started > 5000) throw new Error("the player never became ready");
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
  };
  await ready();
  return {
    window,
    dom,
    shadow,
    root,
    state,
    get data() {
      return data;
    },
    async rerun(changes) {
      data = { ...data, ...changes };
      cleanup = call(data);
      await ready();
    },
    unmount() {
      if (typeof cleanup === "function") cleanup();
      window.close();
    },
    ready,
  };
}

export function key(root, init) {
  const event = new root.ownerDocument.defaultView.KeyboardEvent("keydown", {
    bubbles: true,
    cancelable: true,
    composed: true,
    ...init,
  });
  (init.target || root).dispatchEvent(event);
  return event;
}

function styleRules(node) {
  const rules = [];
  const walk = (list) => {
    for (const rule of list) {
      if (rule.selectorText !== undefined) rules.push(rule);
      else if (rule.cssRules) walk(rule.cssRules);
    }
  };
  for (const style of node.getRootNode().querySelectorAll("style")) {
    if (style.sheet) walk(style.sheet.cssRules);
  }
  return rules;
}

// A browser's `[hidden] { display: none }` is a user-agent rule, so any
// author rule that sets `display` on the element beats it - unless the
// author's own rule says `display: none !important`. jsdom does not model
// that, so this does (the UI-09 bug: `.rp-chip { display: inline-block }`).
function hiddenWins(element, rules) {
  let authorDisplay = false;
  for (const rule of rules) {
    let matches = false;
    try {
      matches = element.matches(rule.selectorText);
    } catch {
      matches = false;
    }
    if (!matches) continue;
    const display = rule.style.getPropertyValue("display");
    if (!display) continue;
    if (display === "none" && rule.style.getPropertyPriority("display") === "important") return true;
    authorDisplay = true;
  }
  return !authorDisplay;
}

/** Whether a node is rendered, as a browser would decide it. */
export function rendered(node) {
  const view = node.ownerDocument.defaultView;
  const rules = styleRules(node);
  for (let at = node; at && at.nodeType === 1; at = at.parentNode) {
    if (at.hidden && hiddenWins(at, rules)) return false;
    if (!at.hidden && view.getComputedStyle(at).display === "none") return false;
    if (at.style && at.style.display === "none") return false;
  }
  return true;
}
