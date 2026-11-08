# Adapty Support · web UI (design hand-off)

Design sources for the support web app: Incoming, Ticket, Chat, Agent settings, Analyze, New feedback, Reopen, States and motion, All screens.

## What is in here

The exported screens now live in `resolve/static/ui/`. This folder keeps the brief, the canvas layout and the design tokens.

- `project/*.dc.html` — one file per screen. The markup inside `<x-dc>` is plain HTML + inline CSS; page CSS is in `<helmet><style>`; the behaviour and mock data are in the `<script type="text/x-dc">` block at the bottom (`renderVals()` returns everything the template uses, `{{ name }}` = a value from it, `<sc-for>` = loop, `<sc-if>` = condition).
- `project/canvas.json` — the canvas layout (which screen is where, titles).
- `spec.md` — the original brief.

These files run inside the Design canvas runtime, not as standalone pages. For standalone HTML, use Share › Export on the canvas, or copy the markup and CSS into `static/ui/` and wire it to `app.js`.

## Screens → routes → API

| File | Route | Data |
|---|---|---|
| Main.dc.html (Incoming) | `#/incoming` | `GET /api/tickets?status=&q=` |
| Ticket.dc.html | `#/incoming/{id}` | `GET /api/tickets/{id}`, `POST …/accept`, `…/reject` |
| Reopen.dc.html | `#/reopen/{id}` | `POST /api/tickets/{id}/reopen` |
| NewFeedback.dc.html | `#/new` | new ticket (form / email) |
| Chat.dc.html | `#/chat/{session}` | `POST /chat`, `GET /api/chat/{session}` |
| Settings.dc.html | `#/settings` | `GET/PUT /api/config`, `GET /api/llm` |
| Analyze.dc.html | `#/analyze` | `GET /api/analytics` + new fields below |

**New in Analyze, not in the current API:** per-reply records for the agent (from the app chat logs: time, user, reply text, latency, outcome) and for people (from replies sent in Incoming), to compute: time to first reply, closed with that reply, user came back unhappy, passed on, free days given.

## Tokens

- Base: white. Page `#fafafa` with a soft violet haze (radial `rgba(124,92,255,.12)` top-left, `rgba(176,148,255,.11)` top-right, `rgba(124,92,255,.07)` bottom).
- Cards `#fff`, border `#ebe8f6`, radius 10, a lavender wash at the top (`#faf8ff` → `#fff` over 180px). Controls radius 6.
- Text `#18181b`, secondary `#52525b`, muted `#6b6b75`; hairlines `#ececf0` / `#f2f2f4`.
- Brand violet `#6247f5`, active nav `#ece6ff → #f5f2ff` with `#5b3cf0` text. Hovers fade a violet haze in from the left.
- New feedback button: dark hazy violet, radial `#5b3fd4 → #33198f → #1d0f55`, white text.
- Type: Inter for UI; Newsreader (serif) for page titles, numbers and section labels (italic); Bodoni Moda italic, double-ruled, for risk words.
- Ticket: "What we found" is the hero panel (violet radial haze, serif headline, 88 dial). Answer probabilities are 60-tick dials; tones `#3d6b52` ≥75, `#8a6a2e` 55–74, `#8e3b36` below; ∞ for "Write your own". SEND is a horizontal double-ruled stamp, 184×64, −4°.
- Analyze: machines `#2563eb`, humans `#e5484d`, closed by humans `#8f1d3a`, still open `#c2770f`, other feedback `#52525b`.
- Motion 150–340 ms: OK stamp 300 ms, SEND press 320 ms, ticket → Closed 340 ms, toast 220 ms, menus 180 ms.
