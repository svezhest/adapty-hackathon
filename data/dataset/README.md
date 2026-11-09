# The demo dataset

Everything here is synthetic, with real complaint labels and real cases underneath. What came from where, and which model made it, is in `provenance.json`; the summary is `docs/dataset-study.md`.

| File | What it holds |
| --- | --- |
| `cards.json` | 200 cards: a person, a writing style, a real label, a case, an account |
| `writer_brief.md` | The task given to the writers, `claude-sonnet-5-5` |
| `tickets.json` | The 200 tickets after the check and the edits |
| `edits.json` | Later edits by `claude-opus-5-5` |
| `check.json` | Counts from `scripts/dataset_check.py`, no review text |
| `bot_library.json` | 12 flows of 40 dialogues each, the source of the bot's chats |
| `decisions.json` | The answers on the 10 closed tickets, standing in for a person |
| `provenance.json` | One record per ticket and per bot chat, with every model call |
| `triage_first_run.log` | The first pass of the triage, with what its checks refused |
| `read_sample.json` | 22 triages read against the truth, by `claude-opus-5-5` |
| `measured.json` | The DeepSeek balance before and after |

To trace one ticket: `python scripts/dataset_study.py --trace f-0018`.
