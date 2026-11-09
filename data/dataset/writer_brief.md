# Brief for the ticket writers

You are writing real-sounding support messages from people who use FitBuddy, a subscription fitness app with an AI coach. They become the demo data of a support tool, and a small study reads them, so the goal is simple: a support lead reading these should believe that 200 different human beings wrote them, on different days, in different moods, about things that really happen.

## What you get

A JSON file of cards. One card is one person with one problem:

- `face`: who this is. Name, country, city, age, job, what is going on in their life.
- `style`: how they write: a `habit`, a `tone`, and how the message `opening` goes.
- `real_labels`: taken from a real labelled complaint: what the person wants, what happened with support before (`unhelpful`, `no_reply`, `bot_only`, `not_contacted`, `helped`, `unknown`), whether they went further (`bank` = chargeback or card dispute, `apple` = went to the store, `threat` = says they will report or sue).
- `truth`: what actually happened in the account. The person may not know it.
- `belief`: what the person thinks happened. Write from this.
- `plan`, `store`, `price`, `credits_price`, `prices`, `dates`: the facts of the account. `app_store` is the App Store, `play_store` is Google Play, `stripe` is the website.
- `lang`: `en`, `ru` or `sr`. `source`: `chat` or `email`. `created_at`: the day they write.

## What makes a message good

**It has a face.** Each message should be recognisable as that person. Read `face` and `style`, then imagine them at that moment: where they are, what they just saw, what they were doing before. A nurse on a break, a trucker with a cracked screen, a retired teacher writing a proper letter. Let job and life show through a concrete detail, not a self-introduction. Most people don't say what their job is; it leaks out. Take the habit and tone seriously: an all-lowercase person stays that way throughout, a formal one stays formal.

**It is grounded in the real world.** People mention their actual bank, the alert they got, the phone they switched from, the race they trained for, the storm, the rent. These details must be true to how things work: how App Store and Google Play billing work, what a bank statement line looks like, what Family Sharing and Ask to Buy do. Facts about real companies stay ordinary and plausible. Nothing invented that would be news.

**Every number and date agrees with the card.** Use `price` and `dates` when the person would mention them. People are vague about many of these ("last Tuesday", "like 50 bucks"), and bank amounts in another currency are approximate, so vagueness is fine and a contradiction is not. Someone with no idea what happened does not suddenly quote the renewal date.

**It says what a real person would say and no more.** A person knows what they did and what they saw. They do not know about profiles, access levels, SDKs or paywall flows. `truth` shapes what happened, and only `belief` goes into their mouth. Some people are wrong, some are unfair, some are right and furious. Length varies a lot: from one line to several paragraphs, as the person would write it. Not everyone is articulate.

**It sounds written by a person, not by a model.** Real messages are uneven. Skip the shapes a language model reaches for: em dashes, "I hope this finds you well", neat three-item lists, a closing sentence that sums up, "frustrating" and "unacceptable" in every message, an even rhythm. No two messages should start the same way. Your cards all have different openings; follow them.

## Languages

- `en`: English, as this person writes it. A German engineer, an Indian student and a Texan bus driver write differently. Second-language English keeps its natural patterns.
- `ru`: live Russian, the way people write in messengers and in letters to support. Natural, not translated from English. They usually live abroad: the card and bank in the message come from that country.
- `sr`: real, natural Serbian. Latin script unless the card's life or job says Cyrillic; some people drop diacritics when typing on a phone. Ekavian (`mleko`, `lepo`), and ijekavian only for someone from Bosnia or Montenegro when the card calls for it. Colloquial where the person is, not textbook. If a phrase would sound odd to someone in Belgrade, change it.

The agent's messages in a chat are in the same language as the user's.

## The two formats

**`source: email`**: a first message to support@fitbuddy. Write `subject` and `body`. Some people write a useless subject, some none at all (use an empty string), some forward a bank message. Sign the way they would, or not at all.

**`source: chat`**: a conversation with FitBuddy's in-app assistant that ended with it passing the case to a person. Write `messages`, a list of `{"role": "user" | "agent", "text": ...}` starting with the user, 1 to 4 user messages long. The assistant's voice stays the same in every chat: warm, short, plain. It can see the account, so it can state the plan, the store and the dates from the card. It can give steps for restoring or cancelling in the store. It cannot give refunds, and it never promises an outcome. Its last message tells the user a teammate will reply in this chat. Then add:

- `handoff_reason`: why it went to a person. One of `refund` (wants money back), `chargeback` (mentions a bank dispute), `threat` (legal action or a public campaign), `asks_for_human` (asks for a person), `profile_mismatch` (what they say contradicts the account, or the purchase is not there), `other` (the assistant could not help further: steps did not work, an app question it cannot see).
- `summary`: in English, one or two sentences as the assistant would write them for the teammate: what the user wants and what was tried. Plain and factual, whatever language the chat is in.

The conversation has to earn its handoff: if the case is a simple "how do I cancel", the user tried the steps and they did not help, or they asked for a person, or something else moved it along.

## What to hand back

Write one JSON file to the path given in your task: a list with one object per card, same order, valid JSON (check it by loading it):

```json
{"id": "c-001", "subject": "...", "body": "..."}
{"id": "c-002", "messages": [{"role": "user", "text": "..."}, {"role": "agent", "text": "..."}], "handoff_reason": "refund", "summary": "..."}
```

Each object also has `voice`: five to ten words on how this person sounds, for the study.

Before you finish, reread the whole batch as a stranger would. If two people sound alike or two messages share a turn of phrase, rewrite one of them. Then answer with the file path and two lines on anything you were unsure of. Nothing else.
