# Danger signals, and where they attach

**Extends §9 of the design spec.** §9 scoped *message analysis* and deliberately left it
undesigned. This designs the **shape** — the hook points, the dependency tiers, and the output
contract — without committing to the full signal list, because the list will grow and the shape
must not have to change when it does.

## 1. The moments, and what each one affords

Checks are not a feature bolted to reading mail. They attach at moments, and the moments differ in
what they can usefully do.

| Hook | Moment | What it affords | Cost of a false positive |
|---|---|---|---|
| `get_message`, `search_messages` | a message is read | **annotate** — findings ride alongside the body | low: noise |
| `get_thread` | several messages are read together | **differential** — what changed across the thread | low |
| `reply`, `reply_all`, `forward` | **the recipient set is being computed** | warn *before* a set nobody chose becomes a set somebody sends to | medium |
| `send_message` | mail is about to leave | last gate; a refusal here is final | **high** |
| `create_event`, `respond_to_event` | attendees are read or written | same address questions, different surface | medium |
| `get_attachment` | a stranger's file lands on disk | name and type, before the write | medium |

**`reply_all` is the highest-value hook in the product.** It already computes
`{From} ∪ {To} ∪ {Cc} − me` — a recipient set the sender did not individually choose and will not
individually read. That is precisely the set an attacker edits, and precisely the moment nobody is
looking.

**`send_message` should almost never refuse.** A gate that blocks sending on a heuristic will be
routed around within a week, and then it protects nothing. Warn loudly, refuse only on something
close to certain.

## 2. Signals, grouped by what they *need* — which is what makes this buildable

The useful axis is not "how clever is the check" but "what does it have to reach". Grouping by
dependency gives a build order, a capability model, and an honest privacy story in one move.

### Tier L — local: headers and text, no network, no history

Cheapest, always available, and where the best value-per-line is.

- `Reply-To` ≠ `From`; `Return-Path` ≠ `From`; `Sender` ≠ `From`
- Display name **contains an address** — `"billing@realco.com" <attacker@evil.example>` is a
  classic and renders convincingly in most clients
- Display name asserts an organisation the domain does not match
- **Free-mail domain** on an address presenting as an organisation — a "partner" replying from
  `gmail.com` mid-thread
- Homoglyph / mixed-script / punycode (`xn--`) in a domain
- `Message-ID` domain ≠ `From` domain
- Authentication results (SPF/DKIM/DMARC) **from the receiving boundary only** — §9's trap: an
  attacker can add their own `Authentication-Results` header, so a parser taking the first match
  is trivially fooled
- Role-account recipients (`billing@`, `accounts@`, `payroll@`) — raises the stake of everything
  else, rather than being a signal alone
- URL display text ≠ href; lookalike domains inside links
- **Payment-detail change language** — "our bank details have changed", new IBAN, new remittance
  address. In business email compromise this is the *whole attack*, and it is a local text signal

### Tier T — thread: differential across messages

Needs the thread, nothing else. This is where the incident that prompted this lives.

- **Participant drift** — an address appears whose local part matches an established participant
  but whose domain differs by an edit or two ([#16](../../../issues/16))
- **Participant removal** — the real counterparty dropped from a reply, so they never see it
- A new participant appearing late in a thread with no introduction
- An established participant's domain changing between messages
- Thread resurrection — a reply to a thread dormant for months
- Subject drift inside a single thread

### Tier M — mailbox: needs correspondence history

- First contact ever from this domain?
- How long have we corresponded with this address, and this domain?
- Have we ever **sent to** this address, or only received from it?
- Cadence break — a quarterly correspondent suddenly urgent

These are the ones that turn a weak signal strong. "Free-mail domain" is nothing on its own and a
great deal when the same human has emailed from a corporate domain for three years.

### Tier D — DNS: resolvable, no third party

- MX present at all — does the domain even receive mail?
- SPF/DMARC policy records present, and how strict
- DKIM selector resolvable

### Tier X — external: discloses who you correspond with

- **Domain registration age** (RDAP, WHOIS fallback) — the check that names a three-week-old
  lookalike beside a four-year partner
- Registrar, and whether privacy-proxied
- Certificate transparency first-seen date, as an age proxy that is often better than RDAP
- Reputation feeds

**Tier X is the only tier that tells someone else something.** An RDAP lookup discloses a domain
you are in contact with, to whoever answers it. That is a different disclosure from anything this
server does today, and it deserves being **opt-in, cached, and stated** — not defaulted on because
it is useful.

## 3. The shape: a signal declares what it needs

```
Signal:
    name:   str                      # stable, greppable, appears in findings
    tier:   L | T | M | D | X
    scope:  address | message | thread
    run(context) -> list[Finding]
```

The runner assembles a context per scope and runs **only the signals whose tier the deployment
permits**. That maps onto the existing capability system rather than inventing a second one:

```
analysis.local       on by default   — no network, no history, no disclosure
analysis.thread      on by default   — needs the thread this server already fetches
analysis.mailbox     off             — reads correspondence history
analysis.dns         off             — resolves records
analysis.external    off             — discloses a domain to a third party
```

Off-by-default for the last three follows the project's posture rule directly, and each refusal
names the variable that would enable it, as every other capability here already does.

**This is also how a "domain check hook" gets built once rather than per-signal.** Domain age,
registrar, MX presence and CT first-seen are four signals sharing one resolver, one cache, and one
disclosure decision. The hook is the resolver; the signals are its callers.

## 4. The output contract, and the one rule that matters most

**There is no green.**

§9 records why: a phishing mail impersonating a brand *passed SPF, DKIM and DMARC honestly*,
because the attacker owned the sending domain. A checker built on those alone "would have marked
that mail green and lent it authority."

So the vocabulary has no *safe* state. It has:

- **`noted`** — an observation, with what it is and what would explain it innocently
- **`unusual`** — a departure from this mailbox's own pattern
- **`inconsistent`** — two things in the message contradict each other
- **`unavailable`** — the check could not run (no network, no history, lookup failed)

`unavailable` is load-bearing and is the mistake this project has made five times already:
**absence of information must never render as a positive answer.** A domain whose age could not be
determined is not a young domain and is not an old one. Say which check did not run, and why.

Every finding carries **what would resolve it**: "this address has not appeared in this thread
before — confirm the change by a channel that is not this email" is worth more than a score.

## 5. Build order

1. **Header capture.** `ParsedMessage` today carries `sender`, `to`, `cc`, `subject` and no
   `reply_to`, `return_path`, `message_id`, `Received` chain or `Authentication-Results`. Most of
   Tier L is unreachable until it does. This is the prerequisite, and it is cheap.
2. **Tier L**, on `get_message`. Establishes the `Finding` vocabulary everything else reports into.
3. **Tier T**, on `get_thread` **and `reply_all`** — the incident, and the hook that matters.
4. **Tier M**, once there is a reason to read history.
5. **Tiers D and X**, behind their own capabilities, with the resolver as one hook.

Tier L first is deliberate even though Tier T is more valuable: T needs the vocabulary L defines,
and building the hard check while inventing the output contract gets both wrong.

## 6. What this must not become

A score. A single number invites a threshold, a threshold invites tuning, and a tuned threshold
becomes a green tick with extra steps — which §9 already identifies as the failure mode that lends
authority to the exact message it should question.
