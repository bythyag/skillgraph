# Dashboard design system

The dashboard's look comes from a design system made with
[OpenDesigner](https://github.com/ckryptickunal/OpenDesigner) (MIT). The brief was a tool
people use all day, in screens full of data. It should feel technical, focused and calm,
with brand color `#3987e5`, light and dark themes, and dashboard screens.

| File | What it is |
|---|---|
| `DESIGN.md` | The living spec: every decision with its reason, the color ramps and their contrast, type, spacing, corners, depth, motion |
| `PRODUCT.md` | Product facts the system was built for |
| `opendesigner/state.json`, `decisions.md`, `standards-state.json` | OpenDesigner's own record, so the system can be extended later |
| `opendesigner/tokens/` | DTCG 2025.10 tokens: the source of truth |
| `../src/skillgraph/dashboard/static/tokens.css` | The CSS export the dashboard loads (`--ds-*` variables) |

Rules the dashboard follows:

- **Accent.** One accent color (`#2375d4`, the shade of `#3987e5` that carries text). It is
  for links, buttons and focus only.
- **Verdict colors.** These come from the success, warning, danger and info ramps. Each
  always has a label or glyph, never color alone.
- **Density and shape.** Compact density: 14px body text and 32px controls. Flat surfaces
  with a faint ring; 6px corners on controls, 8px on cards.
- **Tokens.** Every value in `app.css` is a `--ds-*` token. There are no raw colors or sizes.

## Changing it

Get OpenDesigner, then run its engine against this folder (its scripts use only the standard library):

```bash
python3 <opendesigner>/skills/opendesigner/scripts/engine.py set <path> <json-value> --why "..." --dir design/opendesigner
python3 <opendesigner>/skills/opendesigner/scripts/engine.py build --dir design/opendesigner
cp design/opendesigner/build/css/tokens.css src/skillgraph/dashboard/static/tokens.css
```

`build` regenerates the tokens and exports, and checks contrast for every color pair in both themes.
`engine.py design-md --dir design/opendesigner --out design` rewrites `DESIGN.md` and `PRODUCT.md`.
Keep `opendesigner/build/` out of commits; only the CSS export is used.

The system was created with:

```bash
engine.py init --name skillgraph
engine.py sketch --name skillgraph --audience dense --platforms web --feel technical,focused,calm \
  --brand "#3987e5" --theme system-light-dark --surfaces "dashboard:operate" \
  --brief "a lab notebook for agent runs: quiet surfaces, sharp verdict colors" --delegated feel
```
