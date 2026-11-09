# Signal rankings: decisions

**The ladder.** Confidence is shown as the network label a real phone displays, best to worst: 5G (90–100), LTE+ (80–89), LTE (70–79), H+ (60–69), H (50–59), 3G (40–49, three bars), E (25–39, two bars), G (0–24, one bar). Full Wi-Fi marks "Write your own". With no number, nothing is shown. The cut-offs are a proposal; the old run's median of 82 lands on LTE+.

**The icon.** Ultra-wide bars drawn in the accent line, hatched in asphalt grey (`--ink-2`, the Analyze chart's dark grey) where the signal is there, empty where it is missing. The network label is hollow Nunito Black stretched to 1.6× width, with the + set small and raised as phones show it. No confidence number is shown anywhere: the exact value lives in the tooltip and the screen-reader label. Icons are static at every rank and size.

**How it is built.** All icons are outlines in one inline SVG sprite at the top of the page (`.sig-sprite`), so they need no webfont and render identically everywhere. A placement is `<span class="sig sig-5g"><svg><use href="#sig-5g"></use></svg></span>`; `font-size` sets the height. The sprite draws with the `--sig-line` and `--sig-hatch` tokens; `--sig-sw` sets the line weight and `--sig-label-fill` fills the letters.

**Placement: the riso frieze.** Behind the found card and behind every answer runs the agent's signal as a frieze: the icon printed twice, purple over grey, slightly out of register and multiplied together like a two-colour riso print, repeated along the x-axis and bleeding off both sides and the bottom edge (118 px in the found card, 92 px behind answers, 76 and 62 on phone). The band sits low, cropped by the bottom edge just past the middle of the letters, and the text carries a soft halo in its own background colour so it always reads. The strengths of the two inks are `--riso-a` (20%) and `--riso-b` (15%). The big chonky emoji floats at the top right of each answer (60 px, 46 on phone); the reply wraps around it and the OK stamp lands on it. "Write your own" carries Wi-Fi the same way. The closed ballot's emojis grow to 44 px, and the letter's to 46 px.

**Motion.** Kept as it was. New: the emoji squashes and stretches when you pick it (460 ms, the house overshoot), and is still under reduced motion. The SENT postmark drops the clock ticks for a plain double ring.

**States.dc.html** shows the whole ladder (Wi-Fi, 5G, LTE+, LTE, H+, H, 3G, E, G, and no number), the icons on a picked answer, and the frieze behind text: picked, not picked, your own words, no number.

**Text selection.** Flat accent with white text and no halo; on solid purple it flips to white with accent text.

**Tokens added:** `--sig-line`, `--sig-hatch`, `--riso-a`, `--riso-b`, `--pane`, `--pane-dot` (the dotted ground used on States), plus the `.sig`, `.pane` and per-rank size rules.

**For the owner to decide:** the confidence cut-offs above; the frieze's strength (`--riso-a`, `--riso-b`).
