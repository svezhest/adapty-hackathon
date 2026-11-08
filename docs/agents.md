# Agents: how Resolve works

The behaviour both agents follow, and the contract the screens and phone apps code against. Change this file before the code; tests are written from it. Endpoint schemas are generated into [`openapi.json`](openapi.json). Earlier drafts and the plan are in [`history/`](history/).

## Contents

1. [Overview](#overview)
2. [Level 2: first-line agent](#level-2-first-line-agent)
3. [Level 1: triage agent](#level-1-triage-agent)
4. [Insights](#insights)
5. [Jira: bug reports to the teams](#jira-bug-reports-to-the-teams)
6. [API and data](#api-and-data)
7. [LLM provider](#llm-provider)
8. [Internal interfaces](#internal-interfaces)
9. [Eval](#eval)
10. [Brand: Company, FitBuddy and the Lumen data](#brand-company-fitbuddy-and-the-lumen-data)

## Overview

Level 1 turns raw feedback into a ready ticket for the app's support person. Level 2 talks to the app's users, fixes what is safe to fix, and sends the rest to Level 1.

### Parts

| Part | Who uses it | What it is |
|---|---|---|
| **Level 1: triage agent** (core deliverable) | the app's support person | Feedback in → the agent gathers what it needs about the user, finds the problem, builds a ticket with 2–3 ranked options. The person accepts one, edits one, or rejects all. |
| **Level 2: first-line agent** | the app's end users | Chat inside the app. Can do only what the config allows. When it cannot help, or the request is risky, it hands off to Level 1 with the conversation and what it already tried. |
| **Agent settings page** | the app's owner | One page in the same UI ("in Adapty"): which Level 2 tools are on, limits, tone, rules that always go to a human. In a real product this would live in the Adapty dashboard; here it is our page and our DB. |
| **Showcase mobile app** | demo | Built by our mobile teammate. Calls `/chat`. |
| **Mock APK** (backup) | demo, if the showcase is not ready | Minimal Android app: chat screen + debug buttons. Same `/chat`. |
| **Web debug chat** (backup) | us, and demo if no phone | Same chat in the browser, Adapty-styled. Same `/chat`. |

### What the agents can know about a user

- `getProfile` by `customer_user_id` (or `profile_id`): access levels, subscriptions, non-subscriptions, refunds, billing issue, grace period, cancellation, offer, custom attributes (`app_version`, ...).
- Our DB: earlier tickets, earlier grants by us, the Level 2 conversation.
- **Not available through the API:** event history, lookup by email, lookup by transaction ID. The Adapty dashboard can search by email and transaction ID (docs: profiles-crm, "Finding users"), and webhooks carry `email`, `customer_user_id` and `profile_id` — so the data exists, the public server API does not expose a search. Our workaround: keep our own index `email → customer_user_id`, filled from webhooks in production; for the demo, filled when we set `email` on seeded profiles with `updateProfile`. Pitch it as a small API request to Adapty.
- From the app chat the `customer_user_id` always comes with the message, so Level 2 never needs a lookup.

## Level 2: first-line agent

Tools are given to the model only if the config enables them. **Limits are checked in code, not in the prompt.** The user can say anything; the code decides.

| Tool | What it does | Limits (config) |
|---|---|---|
| `get_profile` | reads the user's profile | always on |
| `explain` | reply only: plan, dates, amounts, from the profile | always on |
| `store_instructions(kind)` | restore / cancel / refund-request steps for App Store or Google Play | always on |
| `grant_access(days)` | `grantAccessLevel` on the configured access level | `max_days` per grant, `max_grants_per_user` per 30 days, only if the user has paid at least once, never for lifetime, never if we granted in the last N days |
| `handoff(reason)` | creates feedback for Level 1 with the conversation | always on |

Always `handoff`, whatever the model thinks (config, default on): refund or chargeback, threats, ticket contradicts the profile, any grant over the limit, the user asks for a human, a second complaint after our grant.

Every grant is stored in our DB and stamped on the profile (`updateProfile`, as now), so complaining again does not give more days.

### Code loads the facts

- Before the first model call of every message, code loads the profile (same `profile_summary` as `get_profile`, plus `free_days_available: true|false` and `free_days_blocked_reason: str|null` from `policy.check_grant` with days=1) and appends it to the system prompt under `Account facts (loaded by the system just now):`. `get_profile` stays as a refresh (after a restore). The prefetch is not a tool call: nothing in `actions` or `l2_events`.
- `grant_access` is offered to the model only when `free_days_available` is true (and the config enables it). The code check on a call stays as is (a hidden tool can still be called; it is checked and blocked the same way).
- If the account cannot be loaded for a reason other than 404 (Adapty not configured, error, timeout): no model call; handoff `other` with summary `account could not be loaded`; fixed reply `ACCOUNT_DOWN_REPLY` = "I can't load your account right now, so I've passed this to a teammate. They will reply here." After a handoff the same failure gives `HANDED_OFF_REPLY`. A 404 goes to the model as `account: not found` (the profile_mismatch rule applies).
- Demo mode / no LLM: unchanged (no prefetch).

### Blocked grants

Level 2: a blocked `grant_access` hands off in code (ticket created, outcome `handed_off`, no further tool calls this turn, fixed reply "I've passed this to a teammate who can help with this; they will reply here."), mapped by reason when that rule is in `always_handoff`: `grant_limit_30d` / `already_granted_this_session` → `repeat_after_grant`; `lifetime` / `not_paid` → `profile_mismatch`; `tool_disabled` → `other` (always). `over_max_days`: the first time in a turn the model gets `blocked: over_max_days (max N days)` and may retry; the second time → handoff `other`. When the mapped rule is not in `always_handoff`, the model just gets `blocked: <reason>`.

### After a handoff

While the session is `handed_off`, each user message still gets a model reply, but only with tools `get_profile`, `store_instructions`, `cancel_handoff {note: str}`; any other tool call is logged as not run ("not run: a teammate has the conversation"); no second ticket. New messages are visible to the teammate through the ticket's conversation. Each such message also reruns Level 1 on the open ticket with the whole conversation (one run per ticket at a time, a message during a run gets one more run after it), so the summary, options and translations follow what the user said after the handoff. A ticket already decided is left as it is. The ticket's model-call budget is `MAX_CALLS` per user message in that chat. `POST /accept` takes `expected_action`, the action the person saw; if a rerun changed it, 409 and nothing runs. `cancel_handoff` dismisses the ticket if it is still new/triaged/proposed (`decision_json = {"rejected": true, "note": "user cancelled the handoff: …", "reply": null}`) and sets the outcome back to `open`. Demo mode / no LLM: fixed reply `HANDED_OFF_REPLY`.

### States only what it knows

From real chats on DeepSeek on 8 Nov: a "dev team" sender got 7 free days on an account with nothing wrong; the agent said Family Sharing "isn't supported" and gave an iOS path "Settings → Lumen → clear cache" that does not exist; a lifetime bought on the App Store was sent to Google Play "Restore purchases" on a Pixel; refund steps were in a reply while the log said the store steps did not run. The causes were in what the model saw and what code checked; the fixes are there, not in single replies.

What the model knows, and nothing else: the account facts (computed in code), the store steps (`store_instructions`, canned), the config and its instructions. For anything outside them (how the app works, its tiers and features, Family Sharing, device or in-app menus, why something breaks) the agent says what it cannot see and who will find out, and hands off when the user needs that answer. The prompt says this as the goal; `REPLY_CRAFT` (shared with Level 1) asks for paths only from the steps the system gave, and its examples use the canned store steps.

Account facts (`profile_summary`, also the result of `get_profile`), additions:
- `premium`: computed for the configured access level: `active`, `until` (date or null), `lifetime`, `backed_by` (the non-refunded store purchases that pay for it now: `{store, product, kind: subscription|one_time, renews, free_trial}`), `added_on_account` (true when the access level was set on the account directly, by us or the developer, not bought in a store; the raw store value of such a level is never shown), `works_on` (fixed: every device signed in to this account, iPhone or Android; premium belongs to the account, not to the phone's store).
- `purchase_stores`: the stores of all store purchases on the account (subscriptions and one-time purchases, refunded included), sorted.
- `one_time_purchases[].store` (was missing). Access levels show `source` (`app_store` | `play_store` | `added_on_account`) and no internal product id for a level set on the account.
- `this_chat`: fixed: the user writes from the app signed in to this account.

Store steps, policy in code (`policy.check_store_steps(profile, store) -> (allowed, reason)`): steps (restore, cancel, refund request) are given only for a store where the account has a purchase; an account with no store purchase (or not found) gets any store. Otherwise the call is logged not allowed with `store_not_on_account` and the model gets `blocked: store_not_on_account (this account's purchases are in: app_store)`; no handoff in code. The canned restore steps say that a restore finds only purchases made with that store account.

Refund steps hand off in code: when `store_instructions` gives `refund_request` steps and `refund` is in `always_handoff`, the conversation is handed off with reason `refund` (unless it already was in this turn, or a teammate already has it); the tool result says so, so the reply can say a teammate will follow up. The model's own reply is kept (this is not a blocked action).

What the agent says it did must be what it did: "a teammate will reply" only after `handoff` returned a ticket, "days added" only after `grant_access` returned the date (prompt and tool descriptions; the craft's example offers to pass a question on instead of claiming it).

Free days, policy in code: `grant_access {days, reason, user_words}`. Free days make up for a problem the user has with their own premium or charge (they pay and premium is locked, a paid feature fails for them, a charge they did not expect); they go to the session's account only, so they never answer a request for access as such, whoever the sender says they are. `user_words` is the user's own sentence describing that problem, copied from their messages. Code checks it (`policy.check_problem_quote(user_words, user_messages) -> str | None`): every part of the quote (split on "..." / "…") must occur in this session's user messages after normalising (case, Unicode form, punctuation and quotes removed, whitespace collapsed), with at least 10 letters or digits in total; otherwise `no_problem_quote`, checked after the limits. Like `over_max_days`, it may be corrected once in a turn: the model gets `blocked: no_problem_quote (...)`; a second correctable block (`over_max_days` or `no_problem_quote`) in the same turn hands off `other`. `free_days_available` in the facts still means only that the limits allow a grant.

Log and handoff in a turn:
- After a handoff the model called in the same turn, `get_profile` and `store_instructions` still run (the same read-only tools as after a handoff); other calls are not run. After a handoff in code (a blocked grant) nothing more runs in that turn, as before. So the refund handoff and the store's refund steps can come in one reply, and the log shows the steps ran.
- Every call that does not run is logged with a fixed `blocked_reason`: `after_handoff` (handed off in this turn or a teammate has the conversation), `call_limit` (the last model call of a message), `unknown_tool`, `invalid_arguments`. `allowed=false` with `blocked_reason=null` no longer occurs.

Eval (`scripts/eval.py --l2-only --repeat N`): the Level 2 scenarios (`data/l2_scenarios.json`) are scored on the code outcomes (grant, handoff, store steps that must or must not run, the log rule above) and on `reply` checks judged by the model over the transcript, the tool results and the raw profile. The clock is frozen at the scenario's `as_of`; a scenario may carry a live profile snapshot; grants the code allows are simulated (nothing reaches Adapty, the agent sees a success).

### Fixed replies in the user's language

`HANDED_OFF_REPLY`, `TOO_LONG_REPLY`, `FALLBACK_REPLY`, `AUTO_HANDOFF_REPLY` and `ACCOUNT_DOWN_REPLY` have a Russian text, chosen in code with no model call, because they also cover a failed model. Russian means: most letters of the session's user messages are Cyrillic and none is a letter Russian does not use (`frontline.writes_russian`). Everyone else gets English, including other languages; a Cyrillic language with no letters of its own (Bulgarian) gets Russian. The demo stub `DEMO_REPLY` stays English.

## Level 1: triage agent

Input: feedback = text + source (`chat_handoff`, `email`, `form`) + whatever identity came with it (`customer_user_id`, email, nothing).

Code loads the user, profile, history and config before the first model call (below). Tools for a second id or email named in the ticket: `get_profile`, `find_user_by_email` (our index), `get_user_history` (our DB). At most `MAX_TOOL_CALLS` per run.

Output: a ticket.

```
ticket:
  user: customer_user_id or "not found" (+ how it was searched)
  summary: one sentence, what the user wants
  category: one of the existing enums (label for grouping, not a restriction)
  evidence: ["field: value", ...] from the profile and history
  root_cause: hypothesis + confidence
  options: 2–3, ranked, each:
    action: grant_access(days) | reply_only | store_instructions(kind) | refund_to_human | ask_user(question) | no_reply
    reply: draft to the user
    risk: low | medium | high, one line why
  bug_report: for the engineers, when the cause is a product defect, else null (see Jira)
```

The support person: accept one option, edit it (reply text, days), or reject all and write their own. Only then anything runs.

### Code loads the facts

- Before the first model call, code resolves the user: the ticket's `customer_user_id`, else the email in the index (the found id is allowed, as with `find_user_by_email`). For the resolved id it loads the profile and the history (same summaries as the tools) and renders the config; all of it is appended to the first user message under `Facts loaded by the system:`, with `lookup: by customer_user_id | by email (unverified sender) | not identified (…)`. A profile loaded here counts as fetched for the grant check.
- `get_agent_config` is removed (the config is in the facts). `get_profile`, `find_user_by_email`, `get_user_history` stay, for a second id or email named in the ticket.
- New check: two options with the same `action` → rejected ("options[i] repeats the action of options[j]; each option must be a different action"), same single retry.
- When the user was found only by email: the prompt says replies must not state plan, dates, amounts or payment details (the sender is unverified).
- `triage.ungrounded_evidence(ticket, facts_text) -> list[str]`: evidence lines whose value (after the first `: `) has a token with a digit, `_` or `.` (amounts, dates, ids, versions) that does not occur, case-insensitively, in the facts the run loaded (the model merges facts into one line, so whole-line matching is useless). Used by eval only; the run does not drop them.

Decided by the owner after the review (2026-11-08): keep `confidence` (the models are DeepSeek and Opus, not a small model), keep three equal options, no keyword backstop for hand-off rules.

### Checks on the submitted ticket

Level 1 `submit_ticket` is checked in code; a failed check gets the same single corrective retry as a schema error:
- `customer_user_id` must be null, the ticket's own id, or an id found by `find_user_by_email` in this run.
- An option with `grant_access` requires that `get_profile` was called in this run and the user is not lifetime; days 1–365.
- If the best option is `grant_access` or `refund_to_human`, at least one other option must be neither (a safe option).
- If the user is not identified (`customer_user_id` null), one option must be `ask_user`.
- `ask_user` needs `params.question`; every reply is non-empty except `no_reply`, ≤120 words (v4; was 160).
- A text answer without a tool call gets one nudge to call `submit_ticket`.
- `get_user_history` has the same allowed-id rule as `get_profile`. An id found by email is written to the ticket's `customer_user_id` when it had none.

### Options: three equal answers with an emoji

Level 1 aims for 3 equally strong options (2 are still accepted), the first is the agent's pick but the UI shows them with equal weight. Each option has `emoji`: one sincere emoji for what the answer means to the user (👍 give, ❌ refuse/cancel, 🙏 apologise, ❓ ask, 🧾 explain a charge, 🔁 restore, 💸 store refund). Old tickets without it: the UI falls back by action (`triage.LEGACY_EMOJI`). Each option also has its own `confidence` (0–1, how sure the agent is that this answer is right; the ticket's `confidence` is about the diagnosis); the Ticket screen shows it as a ring under the emoji and marks the agent's pick with a star. Older tickets have `null` and show no ring.

### Analysis quality

From reading the stored triage of the Nov 8 live chats (f-0009…f-0015) as a support lead: grants offered to users the policy excludes (the facts said `compensation.available: true` for a lapsed payer, a never-paid trial and a user we had granted that morning), invented store paths ("Subscriptions → Lumen → Update payment"), claims the profile contradicts left unnoticed ("bought last year" vs Oct 26; "cancelled two days early" vs renewal off at trial end), replies that announce "a teammate will reply" although the teammate sends them, the bot's turns repeated, replies of 90–115 words in a chat, filler third options, "he/she" from a name.

Facts (code, `triage.model_profile`, used by the prefetch and by `get_profile`; Level 2 keeps `profile_summary`):
- Each subscription gets `plan` and `state`, one sentence computed from its dates and flags (`triage.subscription_state`): in a free trial / trial ended, first charge failed, never paid / paid, renews <date> / renewal off / payment failing, store retrying / ended after a failed payment, renewal off, the store will not retry / refunded; a trial that converted is named with both dates. Each access level gets `since` and `state` (active until / ended / lifetime; bought in which store, or "granted server-side" for store `adapty`). Dates read "Oct 11, 2026 (28 days ago)".
- `non_subscriptions` carry their `store`. `total_revenue_usd` is shown as `total_paid_usd_all_time`.
- `compensation` is replaced by `free_days`: `within_policy` and `why_not` from `policy.check_grant` (the Level 2 check, days = config max, our grants of the last 30 days), `days`, `would_run_to`; a `note` when premium already runs more than 60 days ahead.
- `store_steps`: fixed paths (restore, cancel, update_payment, refund_request, purchase_history) for the stores on the profile (both when none).
- History tickets carry `subject` and `triage_summary`; history sessions leave out the ticket's own chat.
- A handed-off chat is shown once (the transcript inside the ticket body is cut), each message labelled user / assistant / support teammate, with the line that the reply is the next message in that chat, sent by the support teammate.

Prompt (goals): what the app sells and where (nothing else exists: no web checkout, no other tier); how access works (premium lives on the app account and unlocks on any device signed in to it; Restore in the other store finds nothing); no prices on the profile; refunds by store (Apple only; Google Play: the user in 48 h, later a teammate from the Play Console). The diagnosis follows the field order: evidence copied as `field: value` plus `user says: …`, then a root cause that explains every fact and claim and names the claim the facts contradict (or what the bot got wrong), confidence, summary, category (with two issues, the one that needs the lead's decision). Options: 2–3 different strategies a lead would defend; a third only as a real alternative. Replies: written as the teammate; chat 25–60 words, email/form 40–90; a reply promises only what accepting its option does; store paths quoted from `store_steps`.

Schema order: the `submit_ticket` schema the model sees has the TriageTicket fields in thinking order (`customer_user_id, lookup, evidence, root_cause, confidence, summary, category, options`; option: `action, params, risk, risk_reason, reply, emoji, confidence`) with sharper descriptions (`triage.TicketDraft`); arguments are still validated with `models.TriageTicket`, so the stored shape does not change.

Checks on the submitted ticket (same single correction):
- A `grant_access` option that `policy.check_grant` blocks (not paying now, a grant by us in the last 30 days, over max days, tool off) is the lead's exception, not the agent's pick: code moves it after the other options, sets `risk: high` and starts `risk_reason` with `Outside the policy: <why>` (no model call). `check_ticket` still rejects such a grant as options[0] or below high risk (defence in depth). Lifetime stays a rejection.
- Arguments that are not valid JSON (the provider passes `_invalid_json`) get a correction naming the JSON error and its position, never the text. This is one retry of its own and does not spend the one correction.
- `renewal_cancelled_at` of a server-side access level is hidden from the model: Adapty stamps it on every grant, and the store plan under it may still renew.
- The tool schema reorders the fields of `TriageTicket`/`TriageOption` only for the model; a field added to those models is kept, after the named ones.
- Gendered pronouns (he, she, his, her, …) in `summary`, `root_cause` or `evidence` are named in the correction; on their own they never fail a run (a second attempt with them is stored).
- The correction names the rule broken plus every pronoun slip, and asks for the whole ticket again.

Eval: data/eval.json `hard_cases` h-001…h-006 carry their own feedback (`feedback`, `chat`, `setup.prior_grant`) and are inserted by scripts/eval.py; `profiles` overrides a cached profile; `forbidden_actions` (no option may use them) and `forbidden_first` are reported, with reply length, options per ticket and pronoun slips.

### Any language for the user, English for the person

The user writes and reads their own language; the support person sees and decides in English. `summary`, `root_cause`, `evidence`, `risk_reason` and `params.question` are always English.

- `submit_ticket` (the same one call) adds `user_language` (English name, `English` for English users), `messages_en` (one English entry per numbered message: the chat's messages in order, or the ticket body as `[1]`) and `options[].reply_en`. `reply` is in the user's language.
- `triage.check_translations`, with the same single corrective retry: a non-English ticket needs `messages_en` of the right length and `reply_en` on every non-empty reply, different from `reply`; when the user's text is mostly non-Latin, `user_language` cannot be English and each `reply` must be mostly in that script. For an English user the fields are cleared when stored and nothing changes.
- `OptionEdits.reply_en` and `Rejection.reply_en`: the person's English. On a non-English ticket it goes through one small model call on the click (`workflow.translate_reply`, counted in the ticket's cap), before anything runs or is sent. No model, a model error, an empty or unchanged result, or a lost number → 502 with the reason, the ticket stays open, no grant, no message. `reply` is the user-language text as written and wins over `reply_en`. English ticket: `reply_en` is sent as is.
- What was sent: `action.final` `{reply, reply_en, language}` on accept, `decision` `{reply, reply_en, language}` on a manual reply; `reply_en` is null when the person edited the user-language text directly. The chat gets only `reply`.
- Messages that arrive after triage have no translation and are shown as written.

### Evidence labels

The model keeps copying evidence as `field: value` (`ungrounded_evidence` depends on it). The Ticket screen shows a readable label for the field (`custom_attributes.simulated_install_date` → Install date, `access_levels[0].expires_at` → Access expires); the value is shown as written. Level 1's prompt calls the first-line agent "the assistant", so its summaries do too; stored tickets keep their text until they are triaged again.

## Insights

### Agent: facts, schema, checks

One model call turns the inbox into one A/B hypothesis a developer can act on in Adapty. As in v3, code owns the facts and the numbers; the model reads, picks and writes.

Grouping (`insights.group_tickets(tickets)`, no model; existing keys unchanged):
- Paywall seen is a property of the user (the offline CSV row), not of the ticket. A ticket without `context.flow` (chat handoff, form, email) takes the context of another ticket of the same `customer_user_id` in the list, else that user's row in the seed file `data/tickets.json` (`insights.SEED_CONTEXTS`), else `unknown`.
- New keys: `users` (distinct customers among triaged tickets), `flow_known` (triaged tickets with a known flow), `cells` (category × flow: `{category, flow, title, placement, tickets, users, share_of_category, products, examples}`, most users first, then most tickets, known flows before unknown, then the flow with more tickets; `examples`: up to 3 `{id, summary}` from the triage summary, newest first, one per user first), `ab_tests` (the A/B test names seen in the contexts → `{flow title: tickets}`). A customer with several tickets counts once in `users`.

Facts given to the model (`insights.facts(groups, previous)`): totals, categories with their one-line meaning (`tickets.CATEGORY_MEANING`, the same lines the Level 1 prompt shows), tickets, users and share; the cells (up to 30), the first 5 with examples (ticket summaries are user-derived text, in a data block); per flow: tickets, share and tickets by category; A/B tests seen, with complaint tickets per arm (split and views unknown); the placement catalogue (each flow's offer, which placements have two flows and so can be drafted now); a note that counts are tickets, not rates (no paywall views are known). With a stored insight: `previous` (the test it proposed: hypothesis, placement, control and variant flow, main cell; not its headline, whose old counts would be copied as current) and, when it carries a `snapshot`, `changes` computed in code (triaged before → now, per-category delta, the previous main cell's users and tickets then → now, and the known-flow cells that now have more users than it, or none).

`Insight` (the `submit_insight` arguments), grounding first: `cited_cells [{category, flow, tickets, users}]`, `ticket_ids`, `evidence [str]`, `headline`, `placement`, `control_flow`, `variant_source` (`existing_flow`: the placement's other existing flow is the variant, the test can be drafted now; `edited_copy`: a copy of the control with the change, built first), `variant_flow` (that flow's title, or null with `edited_copy`), `variant`, `hypothesis` (written after the arms, so it states the test they make), `expected_effect` (primary metric and direction, plus a guardrail), `caveat` (the main limit of the data). The fields the screen already shows keep their names and meaning.

Prompt: goals, not rules. The reader is the app developer; pick the largest cell (by users) that a paywall can move (offer, price, trial terms, plan order, disclosure, cancel path); say what the example tickets show; one change, framed as an Adapty test on that placement; honest about sample size and unknown flows; with a previous analysis, keep its test while no paywall-movable cell has outgrown its main cell (a tie is not a change) and refresh the numbers, otherwise move.

Checks in code (`insights.check_insight(insight, facts) -> str | None`; a failure gets the one corrective retry, then `TriageError`): every cited cell exists with exactly those `tickets` and `users`; `control_flow` is a flow of `placement`; `variant_flow` is another flow of the same placement with `existing_flow` and null with `edited_copy`; `cited_cells[0]` (the cell the test rests on) has a known flow, no other cited cell has more users, and `control_flow` is its title; with a previous analysis whose main cell nothing has outgrown (`cells_now_larger_than_it` is none), `cited_cells[0]` is that same cell; while `cited_cells[0]` is the previous main cell, `placement`, `control_flow`, `variant_source` and `variant_flow` are the previous ones (the text may change, the test does not); `ticket_ids` are among the examples shown; every number in `headline` and `evidence` occurs in the facts.

`run_insights(services, groups) -> dict` keeps its signature. It reads the stored insight (`db.get_insights`) as `previous`, and returns `Insight` fields plus `snapshot` (`{triaged, categories: {category: tickets}, main_cell: {category, flow, tickets, users}}`, the main cell being `cited_cells[0]` with its current counts) for the next run's `changes`; `what_changed` (`insights.what_changed`, from counts, never from the model: "First analysis.", "Kept the test on A × B (3 → 4 users); no cell outgrew it.", "Moved the test from A × B (3 users) to C × D (5 users).", or null when the previous report has no snapshot); `model_calls` and `latency_s` of this run. Still ≤2 model calls.

### JSON API and live screen

The side screen of Level 1 (it stays one). Grouping is `insights.group_tickets` (code, no model); the hypothesis is `triage.run_insights` (≤2 model calls per run, cached in the `insights` table); the draft is `insights.draft_ab_test` (Adapty CLI `ab-tests create`, draft only). The JSON layer is `resolve/insights_live.py`; it shapes, decides when to run, and never changes those three.

Endpoints (tag `insights`):

```
GET  /api/insights               → InsightsView (read only, no model call; may schedule an auto run, below)
POST /api/insights/run           → InsightsView after one run (a person's click). 409 if a run is in flight
POST /api/insights/ab-draft      {placement, goal?} → InsightsView with the new draft first in `drafts`
```

`InsightsView`:
- `updated_at` (server time of this answer), `demo`, `llm` (a model client is configured), `auto` (auto refresh on: not demo, `llm`, `INSIGHTS_AUTO` ≠ 0).
- `total`, `triaged`, `untriaged`; `categories: [{category, count, share, products: [{product, count}], flows: [{flow, title, count}]}]`, largest first (share = count / triaged); `top: {category, flow, title, count} | null` (the largest category and the flow most of it saw, by tickets); `tested: {category, flow, title, count} | null` (the report's `cited_cells[0]` with its ticket count now). The highlighted cell is `tested`, or `top` before the first run.
- `report: {headline, hypothesis, caveat, placement, placement_title, control_flow, variant_source, variant_flow, variant, expected_effect, evidence[], ticket_ids[], cited_cells[], what_changed, model_calls, latency_s, created_at, trigger: "manual"|"auto"|null, reason: str|null} | null` (fields a stored report predates are null or empty); `previous`: the report it replaced, same shape, or null.
- `run: {state: "idle"|"running"|"error", trigger, reason, started_at, finished_at, error}`; `pending: {reason, since, due_in_s, blocked} | null` (a material change waiting for the debounce; `blocked` names why it will not run, e.g. the hourly cap).
- `placements: [{key, title, flows: [title], draftable}]` (draftable = two or more flows); `drafts: [{ab_test_id, title, state, placement, placement_id, arms: [{title, weight}], goal, created_at}]`, newest first.

Paywall seen: the flow comes from the offline dataset (the live profile has no such field), filled per user by `insights.group_tickets` (see "Insights agent"); this layer has no fill of its own.

Run (manual and auto): one at a time (a lock; a manual click while a run is in flight → 409, an auto run is skipped). Errors are not HTTP errors: demo mode, no LLM, no triaged tickets, an LLM failure → `run.state = "error"`, `run.error` = the `TriageError` text, the last good report stays. A success stores the report with `trigger`, `reason`, the grouping it was built from (`basis`: top category, top flow, triaged, shares, the tested cell, the known-flow cells already larger than it) and the replaced report as `previous` (`insights_live.PREVIOUS_KEYS` only: one level, no nested `previous`, no `snapshot`, no `basis`). `run_insights` reads the stored report's own top-level fields, never `previous`.

Auto refresh (only while someone has the screen open: it is evaluated on `GET /api/insights`, which the screen polls every 2.5 s; nothing runs in the background otherwise):
- Material change against the report's `basis`: a known-flow cell not already larger at the last run now has more users than the tested cell (`A × B (5 users) outgrew the tested C × D (3 users)`: the rule `check_insight` holds the model to; a run that keeps a smaller cell records the larger one and is not retriggered by it), else the top category changed (`top issue changed: A → B`), else the top cell's flow changed (`top paywall changed: A → B`), else some category's share moved by ≥ 5 points (`Refund request share 20% → 31%`). No report, or a report without `basis` (built before this, or in the snapshot) → never auto (the person clicks Run once).
- Debounce: the grouping must stay identical for 3 s after a material change is first seen; then one run starts in a background task, `trigger = "auto"`, `reason` = the text above.
- Caps (code): at least 20 s between auto starts, at most 10 auto runs per rolling hour (then `pending.blocked = "hourly cap"`), none in demo mode or without an LLM. Each run is still `run_insights` (≤2 model calls).

Draft: `placement` must be one of `insights.PLACEMENTS` (else 422); a single-flow placement → 422 with the `DraftError` text; the CLI failing (not installed, non-zero exit) → 502 with its text; demo mode or no CLI runner → 409. Nothing ever starts, stops or edits a test.

There is no web screen: the owner removed it (2026-11-09). The endpoints above stay as the API, and since auto refresh is evaluated on `GET /api/insights`, it only runs if something calls that endpoint.

## Jira: bug reports to the teams

Most tickets end with an answer to the user. Some also point at a defect a team has to fix. Level 1 writes the report for those, and it goes straight to the team that fixes it, as one issue per ticket.

**The agent.** `submit_ticket` has an optional `bug_report`: `{team, summary, details, priority}`. It is set when the cause is a defect in the product, and stays null for questions, refund or cancel requests, store decisions and user mistakes. The model sees the teams as `config.teams` (`name`, `handles`) and never the project keys. A team outside the list gets one correction. With no teams or no Jira settings the model is offered no team, and a report is dropped without a correction, so the replies promise nothing.

**Who files it.** Three paths, one function (`jira.export`):

| Path | When | `by` |
|---|---|---|
| The button on the ticket | The person creates it, with team, title, priority and details editable. Works on any ticket, with or without the agent's report | `person` |
| With the reply | A reply went out on a ticket with a report, because that reply may tell the user the team has it. Closing without a reply files nothing | `person` |
| On its own | `jira.auto_create` is on: right after triage, at most `max_auto_per_day` in 24 hours. Off by default | `agent` |

**What code guarantees.**

- One issue per ticket: `jira_json` on the ticket, one process-wide lock, and a `resolve-<ticket id>` label searched before each create, so a crash after the create does not file a second one.
- A failure from Jira is recorded in `jira_error`, shown on the ticket and can be retried. It never fails the triage and never undoes a reply.
- Email addresses are removed from everything sent. The issue carries the ticket id and `customer_user_id`.
- The issue body is plain text nodes in Atlassian Document Format, and says that it rests on what a user wrote. A prompt injection can at worst file a wrong bug within the daily limit: an issue is the only thing this writes, and the token never reaches the model.
- `priority` and `labels` are dropped when a project refuses them. Anything else Jira refuses is shown as it said it.
- `JIRA_BASE_URL` must be https, except on a local address.

**Settings.** `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` in `.env` (Jira Cloud, REST API v3); `RESOLVE_PUBLIC_URL` optionally adds a link back to the ticket. Teams and auto-create are in Agent settings. Demo mode makes no call and records `{demo: true}`.

**Not done.** The same bug from ten users files ten issues: nothing groups them yet. Nothing reads the issue's status back, so a fixed bug does not reach the ticket or the user. Only Jira Cloud.

## API and data

### Web tabs

| Tab | What |
|---|---|
| **Incoming** | Tickets from Level 1: list, filters by status/source/category, ticket page with the options; accept / edit / reject all |
| **Analyze** | Stats on tickets, Level 2 outcomes and AI output. Contents are the owner's call; the first version is just numbers (below) |
| **Agent settings** | Level 2 config: tools on/off, limits, tone, always-handoff rules |
| **Chat** | Level 2 chat in the browser: pick a seeded user, talk, see each tool call and whether the code allowed it |

### JSON API

```
POST /chat                      {customer_user_id, session_id, message}
  → {reply, actions: [{tool, args, allowed, blocked_reason, result}], handed_off, ticket_id}
GET  /api/chat/{session_id}     → {customer_user_id, messages[], events[], outcome}
POST /feedback                  {text, source, customer_user_id?, email?}  → {ticket_id}

GET  /api/tickets?status=&source=&category=   → [ticket summary]
GET  /api/tickets/{id}          → ticket (with options, profile snapshot, conversation if any)
POST /api/tickets/{id}/triage   → runs Level 1 (cached; ?force=1 reruns)
POST /api/tickets/{id}/accept   {option: int, edits: {reply?, days?}} → result of the action
POST /api/tickets/{id}/reject   {note, reply?} → ticket
POST /api/tickets/{id}/reopen   {note?} → ticket
POST /api/tickets/{id}/jira     {team?, summary?, details?, priority?} → ticket with jira or jira_error (409 not connected, 422 no such team)

GET  /api/config  /  PUT /api/config   → config (below)
GET  /api/llm                   → provider and model in use (never key values)
GET  /api/jira                  → {connected, site, demo} (never the token)
GET  /api/analytics             → numbers for Analyze (below)
GET  /api/users                 → seeded customer_user_ids with a short status, for the Chat tab and debug buttons
GET  /healthz                   → {ok, demo}
```

The mobile showcase, the mock APK and the Chat tab all use `POST /chat` and `GET /api/chat/{session_id}`. APK debug buttons: switch user, reset conversation, show last actions.

### JSON shapes

Ticket title: `title` (`tickets.ticket_title`) is what the screens show. A subject a person wrote stays; a machine subject (`Chat: refund`) or a cut of the body gives way to the triage summary (without "The user", first clause, at most 90 characters), else to a readable line for the handoff reason. `subject` stays as stored.
Ticket summary (`GET /api/tickets`): `{id, status, source, category, customer_user_id, email, subject, title, created_at, updated_at, confidence, top_action}`.
Ticket (`GET /api/tickets/{id}`): summary fields + `{body, session_id, triage (TriageTicket | null), legacy (bool), profile (raw Adapty profile | null), decision, action, action_error, triage_error, conversation ([{role, content, ts}] | null)}`.
`GET /api/users`: `[{customer_user_id, badge: "active"|"expired"|"lifetime"|"none"|"unknown", product: str | null}]`.
`GET /api/chat/{session_id}`: `{session_id, customer_user_id, outcome, ticket_id, messages: [{role, content, ts}], events: [{ts, tool, args, allowed, blocked_reason, result}]}`; 404 if unknown.
`GET /api/analytics`:
```json
{
  "tickets": {"total": 0, "by_status": {}, "by_source": {}, "by_category": {}, "per_day": [{"day": "2026-11-07", "n": 0}]},
  "level2": {"sessions": 0, "resolved": 0, "handed_off": 0, "open": 0, "handoff_reasons": {}, "grants": 0, "days_granted": 0, "blocked": {}},
  "triage": {"triaged": 0, "confidence_buckets": {"0-0.5": 0, "0.5-0.7": 0, "0.7-0.9": 0, "0.9-1": 0},
             "accepted_option": {"1": 0, "2": 0, "3": 0, "edited": 0, "rejected": 0}, "top_root_causes": [{"root_cause": "", "n": 0}]},
  "eval": {"model": "", "cases": 0, "category_correct": 0, "action_correct": 0}
}
```
(`eval` is null when no results file.) Errors: 404 unknown id, 409 wrong status for accept/reject, 422 invalid body. `web.py` calls `api.install(app, services)`, which adds CORS (all origins, for the APK and other UIs) and includes the router.

### Decisions

Decisions: accept stores `decision_json = Decision.model_dump()`; reject always stores `{"rejected": true, "note", "reply"}`; reject is allowed from new/triaged/proposed. Accepting a reply on a `chat_handoff` ticket appends it as a `support` message to the conversation. Accept/reject return the full ticket.

### Ticket statuses

`new` (feedback stored) → `proposed` (Level 1 built the ticket) → `done` (an option accepted, or rejected with a manual reply) | `dismissed` (rejected, nothing to do).

### Reopen

`POST /api/tickets/{id}/reopen {note?}` → only from `done`/`dismissed` (else 409): status `proposed` if the ticket has a triage or v1 proposal, else `new`; the earlier `{at, from_status, decision, action, note}` is appended to `history` (Ticket field); decision/action/reply_sent_at are cleared. Nothing is undone in Adapty: an earlier grant stays.

### Config

```json
{
  "app_name": "FitBuddy",
  "tone": "warm, short, plain English",
  "tools": {"grant_access": true},
  "grant": {"access_level": "premium", "max_days": 7, "max_grants_per_user_30d": 1, "require_paid": true},
  "always_handoff": ["refund", "chargeback", "threat", "asks_for_human", "profile_mismatch", "repeat_after_grant"],
  "max_turns": 8,
  "jira": {"auto_create": false, "max_auto_per_day": 20, "teams": [{"name": "Mobile", "project_key": "APP", "handles": "crashes and screens in the apps", "issue_type": "Bug"}]}
}
```

### Level 2 log

One row per tool call: `session_id, ts, tool, args, allowed, blocked_reason, result_summary`. Session outcome: `resolved` (user confirmed or the agent closed after an action), `handed_off`, `open`.

### Analyze

Demo mode (`DEMO_MODE=1`): periods are measured from the last event in the snapshot (`analytics.last_event`, fixed at startup), not from today, and the screen opens on 7 days, so the numbers don't empty out as the file ages. `data/demo.sqlite` is built by `scripts/snapshot.py`: the live DB without developers' throwaway chats and without tickets that have no Level 1 triage. The eval and the profile seeding read `data/eval-base.sqlite` instead (the 60 seed tickets their labels were written against).

- tickets: by status, by source, by category, per day
- Level 2: sessions, resolved by Level 2 vs handed off (and why), grants and days granted, actions blocked by limits (by reason)
- Level 1 / AI: confidence distribution, which option the person accepts (1st / 2nd / 3rd / edited / rejected), top root causes
- eval score (from the last eval run file)

## LLM provider

`AgentConfig.llm_provider`: `env` (default: `LLM_PROVIDER` from `.env`; `auto` = openai when `OPENAI_API_KEY` + `OPENAI_MODEL` are set, else anthropic when `ANTHROPIC_API_KEY` is set) | `openai` | `anthropic`. `PUT /api/config` with a new provider rebuilds the client for both agents; a provider without keys → 422, nothing saved. `GET /api/llm` → `{selected, env_default, active, model, available: {openai, anthropic}, demo}` (never key values). OpenAI-compatible extras from `.env`: `OPENAI_BASE_URL`, `OPENAI_EXTRA_BODY` (JSON, e.g. DeepSeek `{"thinking": {"type": "disabled"}}`), `OPENAI_STRICT_TOOLS` (1/0), `OPENAI_MAX_TOKENS`.

## Internal interfaces

Already in the repo (contract core, do not change shapes without asking): `resolve/llm.py` (ToolSpec, ToolCall, Step, LLM, FakeLLM, `assistant_message`, `tool_message`, `make_llm`), `resolve/models.py` (AgentConfig, TriageTicket, TriageOption, OptionParams, OptionEdits, Decision, Rejection, ChatRequest, ChatResult, ActionLog, FeedbackRequest, ACTIONS, DB_TO_API_STATUS), `resolve/db.py` v2 helpers (`insert_feedback`, `ensure_conversation`, `get_conversation`, `set_outcome`, `append_message`, `list_messages`, `log_event`, `list_events`, `record_grant`, `grants_since`, `get_config`, `save_config`, `index_email`, `lookup_email`; ticket dict now also has `source`, `email`, `session_id`, `triage`, `decision`).

`workflow.Services` (v2): `conn: db.Database, demo: bool, adapty: adapty.Client | None, llm: llm.LLM | None`. No `anthropic`/`model` fields any more.

| Module | Function | Contract |
|---|---|---|
| `resolve/policy.py` | `load_config(conn) -> AgentConfig` | stored config or defaults |
| | `check_grant(profile: dict, cfg: AgentConfig, grants_30d: list[dict], days: int, granted_this_session: bool, now=None) -> tuple[bool, str \| None]` | pure. `require_paid` means paying now: a non-refunded store subscription, not in a free trial, expiring after `now` (or a non-refunded one-time purchase); past payers are `not_paid`. Reasons (exact strings): `tool_disabled`, `over_max_days`, `not_paid`, `lifetime`, `grant_limit_30d`, `already_granted_this_session` |
| `resolve/actions.py` | `execute(services, customer_user_id: str, action: str, params: dict, source: str, ref: str) -> dict` | the only place that writes to Adapty for an action. `grant_access`: fresh `get_profile` → `compensation_expiry(profile, access_level, days, now)` → `grant_access_level` → `update_profile({"last_support_ticket": ref})` (stamp failure is recorded, not fatal) → `db.record_grant`. Returns `{"type", "days", "expires_at", ...}`; raises `AdaptyError` on failure. Other actions: no Adapty call, returns `{"type": action}`. Demo mode / no client: no call, returns `{"type", "demo": true}` |
| `resolve/frontline.py` | `chat(services, customer_user_id: str, session_id: str, message: str) -> ChatResult` | Level 2 loop. Tools exposed only if enabled; each call checked (`policy`), executed (`actions.execute(..., source="l2", ref=session_id)`), logged (`db.log_event`). Blocked → tool result `blocked: <reason>`. ≤4 model calls per message, ≤`max_turns` user messages per session (then handoff). `handoff` → `db.insert_feedback(source="chat_handoff", session_id=..., customer_user_id=...)`, outcome `handed_off`, returns `ticket_id`; the caller schedules triage. `mark_resolved` → outcome `resolved`. LLMError → polite fallback reply + handoff |
| `resolve/triage.py` | `run(services, ticket_id: str, force: bool = False) -> dict` | Level 1 loop with tools `get_profile`, `find_user_by_email`, `get_user_history`, `submit_ticket` (args = `TriageTicket`), after the prefetch. ≤6 tool calls, model calls counted with `db.reserve_model_call` (cap 8). Invalid `submit_ticket` → one corrective retry. Stores `triage_json`, `triage_at`, `category`, `profile_json` (if fetched), status → `proposed` (only from `new`/`triaged`); on failure `triage_error`. Cached unless `force`. Demo mode: no model call, sets `triage_error` |
| | `run_insights(services, groups: dict) -> dict` | replaces `insights.analyse`; `submit_insight` tool with `insights.Insight`; facts, checks and `previous` in "Insights agent" below |
| `resolve/workflow.py` | `accept(services, ticket_id, decision: Decision) -> dict` | only from `proposed`; conditional `proposed → approved` (idempotent); option + edits → `actions.execute(..., source="l1", ref=ticket_id)`; success → `done`, `decision_json`, `action_json`, `reply_sent_at`; AdaptyError → back to `proposed`, `action_error` |
| | `reject(services, ticket_id, rejection: Rejection) -> dict` | reply given → `done` (reply marked sent), else `dismissed` |
| | `propose(services, ticket_id, force=False)` / `approve(services, ticket_id)` / `dismiss` / `open_ticket` | used by `scripts/batch_triage.py` and the tests: `propose` = `triage.run`, `approve` = `accept(Decision(option=0))` |
| `resolve/api.py` | `build_router(services) -> fastapi.APIRouter` | the JSON API above. Triage in the background (`BackgroundTasks`) after `/feedback` and after a chat handoff. Ticket JSON = ticket dict minus `*_json` columns, with `status` mapped by `DB_TO_API_STATUS` |
| `resolve/analytics.py` | `compute(conn) -> dict` | numbers for Analyze (list above) |

Screen names are free; endpoints and JSON shapes are not.

### Tool names and arguments

Level 2 (`frontline`), the customer id is always the session's, never a model argument:
- `get_profile {}` → profile summary
- `grant_access {days: int, reason: str, user_words: str}` (`user_words` since v4, below)
- `store_instructions {kind: "restore"|"cancel"|"refund_request", store: "app_store"|"play_store"}` → canned steps text, only for a store the account bought in (v4)
- `handoff {reason: one of HANDOFF_RULES or "other", summary: str}`
- `mark_resolved {note: str}`

Level 1 (`triage`):
- `get_profile {customer_user_id: str}`: only the ticket's id or an id returned by `find_user_by_email` in this run; anything else → error result `not allowed`
- `find_user_by_email {email: str}` → `{customer_user_id}` or `{not_found: true}`
- `get_user_history {customer_user_id: str}` → earlier tickets (id, created_at, category, status, decision), grants, Level 2 sessions (outcome, last messages)
- `submit_ticket {TriageTicket}` → ends the run

Legacy proposals (v1 `proposal_json`, e.g. in `data/eval-base.sqlite`) are shown as a one-option ticket via `triage.legacy_to_ticket(proposal)`: grant_access→grant_access(days 7), explain→reply_only, escalate_refund→refund_to_human, no_action→no_reply.

## Eval

Synthetic cases only: `data/eval.json` (Level 1) and `data/l2_scenarios.json` (Level 2). `scripts/eval.py` calls a real model, so it costs money. Level 1 is scored on category and on the action of the first option; `ungrounded_evidence` checks evidence against the facts the run loaded. Level 2 is scored on the code outcomes and on replies judged by the model, as described in "States only what it knows".

## Brand: Company, FitBuddy and the Lumen data

The owner's decision. Our web product is called Company (the nav says so), a placeholder, so that nobody takes this project for an official one. The backend stays Adapty, and that name appears only where it has to: code, technical docs, setup, citations and the event's name. The screens say "the subscription service" or "the platform". The end-user app is what the phone app `appy/` shows: FitBuddy, a fitness app. The agents and the screens say the same thing.

- The name comes from one place: `AgentConfig.app_name` (default `FitBuddy`, editable in Agent settings). Level 1 (`triage.system_prompt`, its `store_steps`), Insights (`insights.system_prompt`) and Level 2 (`frontline`) all read it from the stored config; no prompt hard-codes a name. Level 1 and Insights describe the app as a fitness app; no prompt or example describes it as a photo editor.
- Seed and demo text (tickets, hard cases, Level 2 scenarios, mock text on the screens) says FitBuddy. A body that only made sense for a photo editor is reworded to a fitness equivalent with the same support meaning and the same labels.
- What stays Lumen, because it is true: profile ids `lumen_443814_*` and store product ids `lumen.*` / `lumen_*` (real Adapty ids), the organisers' dataset name in data sources (`data/offline/`, README), and the dated reports of what happened before this decision (older sections here, the AI log).
- Data from Adapty or the offline dataset is shown as it is, even when it reads as a photo editor: flow and paywall titles ("AI limit reached - Pro"), placement titles built on them, custom attributes (`ai_edits_first_day`, `photos_imported`, `onboarding_goal`).
- Impact (`static/ui/Impact.dc.html`): the owner agreed the text with Milos. FitBuddy in the heading and the rows; the source note says the rows come from the organisers' dataset, which the demo app runs on. Layout and numbers unchanged.
- Stored data (a DB that has the old name in its config, tickets and replies) is not migrated by the app: a one-off script does it at the restart. Stored Adapty profiles are not touched.
