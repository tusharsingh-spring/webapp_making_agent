"""
Design system injected into the coder prompt when the user asks to
improve / beautify a generated project.

Each palette is a complete, self-contained CSS block with variables,
base resets, component styles, and animations — ready to paste into a
<style> tag or .css file.
"""

# ─── Keyword detection ────────────────────────────────────────────────────────

ENHANCE_KEYWORDS = {
    "better", "beautiful", "beautify", "improve", "enhance", "stylish",
    "modern", "nice", "pretty", "good looking", "good-looking", "redesign",
    "fancy", "polish", "polished", "elegant", "aesthetic", "design",
    "colorful", "colourful", "attractive", "appealing", "professional",
    "clean", "sleek", "cool", "awesome", "amazing", "stunning", "gorgeous",
}

def is_enhance_request(prompt: str) -> bool:
    lower = prompt.lower()
    return any(kw in lower for kw in ENHANCE_KEYWORDS)


# ─── Palettes ─────────────────────────────────────────────────────────────────

PALETTES = {
    "dusk": {
        "label": "Dusk (dark, warm orange accent)",
        "css_vars": """
  --bg:        #0f0f13;
  --surface:   #1a1a24;
  --surface2:  #24243a;
  --border:    #2e2e44;
  --accent:    #f0883e;
  --accent2:   #e05c1a;
  --text:      #e8e8f4;
  --muted:     #7878a0;
  --success:   #4caf82;
  --danger:    #e05555;
""",
    },
    "sage": {
        "label": "Sage (light, earthy green)",
        "css_vars": """
  --bg:        #f4f1ec;
  --surface:   #fffef9;
  --surface2:  #edeae3;
  --border:    #d8d3c8;
  --accent:    #5b7c5b;
  --accent2:   #3d5c3d;
  --text:      #1e1e1e;
  --muted:     #7a7a6a;
  --success:   #5b7c5b;
  --danger:    #c0392b;
""",
    },
    "ocean": {
        "label": "Ocean (dark, cyan accent)",
        "css_vars": """
  --bg:        #080f1e;
  --surface:   #0d1b35;
  --surface2:  #142548;
  --border:    #1e3566;
  --accent:    #00d4ff;
  --accent2:   #0099cc;
  --text:      #d8eeff;
  --muted:     #5580a8;
  --success:   #00e5a0;
  --danger:    #ff5566;
""",
    },
    "bloom": {
        "label": "Bloom (light, coral red accent)",
        "css_vars": """
  --bg:        #fdf6f0;
  --surface:   #ffffff;
  --surface2:  #f5ede6;
  --border:    #ead8ce;
  --accent:    #e8523a;
  --accent2:   #c0381f;
  --text:      #1a1218;
  --muted:     #9a8880;
  --success:   #2e8b57;
  --danger:    #e8523a;
""",
    },
}

# ─── Shared base styles ───────────────────────────────────────────────────────

BASE_CSS = """
/* ── Reset & base ──────────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html { font-size: 16px; -webkit-font-smoothing: antialiased; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
  line-height: 1.6;
  min-height: 100vh;
}

/* ── Typography ─────────────────────────────────────────────────────── */
h1 { font-size: clamp(1.8rem, 4vw, 2.8rem); font-weight: 700; letter-spacing: -0.02em; }
h2 { font-size: clamp(1.3rem, 3vw, 1.9rem); font-weight: 600; }
h3 { font-size: 1.2rem; font-weight: 600; }
p  { color: var(--muted); }

/* ── Layout helpers ─────────────────────────────────────────────────── */
.container {
  width: min(900px, 92vw);
  margin-inline: auto;
  padding-block: 2rem;
}
.flex   { display: flex; }
.center { align-items: center; justify-content: center; }
.gap-1  { gap: 0.5rem; }
.gap-2  { gap: 1rem; }
.col    { flex-direction: column; }

/* ── Card ───────────────────────────────────────────────────────────── */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 1.5rem;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 32px rgba(0,0,0,0.15);
}

/* ── Buttons ────────────────────────────────────────────────────────── */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.55rem 1.2rem;
  border-radius: 8px;
  border: none;
  font-size: 0.9rem;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.18s, transform 0.12s, box-shadow 0.18s;
  user-select: none;
}
.btn:active { transform: scale(0.96); }

.btn-primary {
  background: var(--accent);
  color: #fff;
}
.btn-primary:hover {
  background: var(--accent2);
  box-shadow: 0 4px 16px color-mix(in srgb, var(--accent) 40%, transparent);
}
.btn-ghost {
  background: transparent;
  color: var(--text);
  border: 1px solid var(--border);
}
.btn-ghost:hover { background: var(--surface2); }

.btn-danger {
  background: var(--danger);
  color: #fff;
}
.btn-danger:hover { filter: brightness(0.88); }

/* ── Inputs ─────────────────────────────────────────────────────────── */
input, textarea, select {
  background: var(--surface2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.55rem 0.9rem;
  font-size: 0.95rem;
  font-family: inherit;
  width: 100%;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
}
input:focus, textarea:focus, select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 20%, transparent);
}
input::placeholder { color: var(--muted); }

/* ── Badge / Tag ────────────────────────────────────────────────────── */
.badge {
  display: inline-block;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 600;
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  color: var(--accent);
}

/* ── Divider ────────────────────────────────────────────────────────── */
hr {
  border: none;
  border-top: 1px solid var(--border);
  margin-block: 1.5rem;
}

/* ── Scrollbar ──────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* ── Google Fonts import ────────────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
"""

# ─── Animation library ────────────────────────────────────────────────────────

ANIMATIONS_CSS = """
/* ── Keyframes ──────────────────────────────────────────────────────── */
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(18px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes fadeIn {
  from { opacity: 0; }
  to   { opacity: 1; }
}
@keyframes slideInLeft {
  from { opacity: 0; transform: translateX(-24px); }
  to   { opacity: 1; transform: translateX(0); }
}
@keyframes scaleIn {
  from { opacity: 0; transform: scale(0.88); }
  to   { opacity: 1; transform: scale(1); }
}
@keyframes bounceIn {
  0%   { transform: scale(0.7); opacity: 0; }
  60%  { transform: scale(1.08); opacity: 1; }
  80%  { transform: scale(0.96); }
  100% { transform: scale(1); }
}
@keyframes shimmer {
  0%   { background-position: -400px 0; }
  100% { background-position:  400px 0; }
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.5; }
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
@keyframes float {
  0%, 100% { transform: translateY(0); }
  50%       { transform: translateY(-6px); }
}
@keyframes ripple {
  to { transform: scale(4); opacity: 0; }
}
@keyframes strikethrough {
  from { width: 0; }
  to   { width: 100%; }
}

/* ── Animation utility classes ──────────────────────────────────────── */
.anim-fade-up   { animation: fadeUp      0.4s ease both; }
.anim-fade-in   { animation: fadeIn      0.35s ease both; }
.anim-slide-in  { animation: slideInLeft 0.4s ease both; }
.anim-scale-in  { animation: scaleIn     0.3s ease both; }
.anim-bounce-in { animation: bounceIn    0.5s ease both; }
.anim-float     { animation: float       3s ease-in-out infinite; }
.anim-pulse     { animation: pulse       2s ease-in-out infinite; }
.anim-spin      { animation: spin        1s linear infinite; }

/* Stagger delays for lists */
.stagger > *:nth-child(1) { animation-delay: 0.05s; }
.stagger > *:nth-child(2) { animation-delay: 0.10s; }
.stagger > *:nth-child(3) { animation-delay: 0.15s; }
.stagger > *:nth-child(4) { animation-delay: 0.20s; }
.stagger > *:nth-child(5) { animation-delay: 0.25s; }
.stagger > *:nth-child(6) { animation-delay: 0.30s; }

/* Shimmer skeleton loader */
.skeleton {
  background: linear-gradient(90deg, var(--surface2) 25%, var(--border) 50%, var(--surface2) 75%);
  background-size: 400px 100%;
  animation: shimmer 1.4s infinite;
  border-radius: 6px;
}

/* Button ripple effect (add via JS: btn.classList.add('ripple-active')) */
.btn { position: relative; overflow: hidden; }
.btn::after {
  content: '';
  position: absolute;
  inset: 50% auto auto 50%;
  width: 6px; height: 6px;
  background: rgba(255,255,255,0.4);
  border-radius: 50%;
  transform: scale(0);
  opacity: 1;
  pointer-events: none;
}
.btn:active::after { animation: ripple 0.5s ease-out; }

/* Smooth completed-item strikethrough */
.completed-text {
  position: relative;
  color: var(--muted);
}
.completed-text::after {
  content: '';
  position: absolute;
  top: 50%; left: 0;
  height: 1.5px;
  background: var(--muted);
  animation: strikethrough 0.25s ease forwards;
}
"""

# ─── Public API ───────────────────────────────────────────────────────────────

def get_design_prompt(palette_name: str = "dusk") -> str:
    """
    Returns a string to inject into the coder system prompt when
    enhance mode is active.
    """
    palette = PALETTES.get(palette_name, PALETTES["dusk"])
    vars_block = palette["css_vars"]

    return f"""
DESIGN SYSTEM — use this for all CSS (inject into <style> or the .css file):
You MUST apply the full design system below. Do not write minimal/skeleton CSS.

/* ── CSS Custom Properties ── */
:root {{
{vars_block}
  --radius:    12px;
  --shadow-sm: 0 2px 8px rgba(0,0,0,0.08);
  --shadow-md: 0 6px 24px rgba(0,0,0,0.14);
  --shadow-lg: 0 16px 48px rgba(0,0,0,0.22);
  --transition: 0.2s ease;
}}

{BASE_CSS}

{ANIMATIONS_CSS}

RULES when using this design system:
- Use var(--accent) for primary actions, highlights, and links.
- Use var(--surface) for card/panel backgrounds, var(--bg) for page background.
- Wrap all page content in <div class="container">.
- Add class="anim-fade-up stagger" to lists so items animate in sequentially.
- Add class="card" to all content panels.
- Use class="btn btn-primary" / "btn btn-ghost" / "btn btn-danger" for all buttons.
- Every input must get the base input styles (already in the design system).
- Use CSS transitions on hover for ALL interactive elements.
- Include smooth scroll: html {{ scroll-behavior: smooth; }}
- Use CSS Grid or Flexbox for layout — no tables for layout.
- The result must look like a polished SaaS product, not a tutorial example.
"""


def pick_palette(user_prompt: str) -> str:
    """Pick the most fitting palette based on prompt keywords."""
    lower = user_prompt.lower()
    if any(w in lower for w in ["dark", "night", "black", "neon", "glow"]):
        if any(w in lower for w in ["ocean", "blue", "cyber", "tech"]):
            return "ocean"
        return "dusk"
    if any(w in lower for w in ["nature", "green", "plant", "earth", "minimal", "clean"]):
        return "sage"
    if any(w in lower for w in ["warm", "red", "coral", "pink", "soft", "light"]):
        return "bloom"
    # default: dusk (dark, versatile)
    return "dusk"
