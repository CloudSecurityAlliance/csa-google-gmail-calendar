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

## 4. The output contract: notice, do not judge

**The analyser does not decide whether a message is dangerous. It says what it noticed.**

This is stronger than "there is no green", which is where this section started. §9 records why
there is no *safe* verdict — the motivating phishing mail passed SPF, DKIM and DMARC honestly, so
a checker built on those "would have marked it green and lent it authority". But the same argument
runs the other way: a verdict of *dangerous* is a judgement the analyser is not equipped to make
either, because it cannot see the thing that decides it.

### What decides severity is the ask, and the analyser cannot see it

The identical set of observations is unremarkable on *"are you free for coffee Thursday?"* and
serious on *"please send the Q3 payroll file to this address"*. Nothing in the headers distinguishes
them. The **ask** is the amplifier, and reading it is comprehension, not parsing.

So the division of labour is three-way:

| | does what | can see |
|---|---|---|
| **the server** | notices, states facts, says what it could not check | headers, the thread, the corpus |
| **the model** | composes observations *against what is being asked* | all of the above, plus the request |
| **the person** | confirms, out of band | the relationship, the context, the stakes |

### What a finding looks like

Not a score, not a label. An observation, what would innocently explain it, and — when several
compose — the composition:

> Two things changed in this thread. Three recipients are now at `partnerco-invoices.example`,
> a domain that has never appeared in it before; the established one is `partnerco.example`.
> Separately, this message reads differently from the 11 previous messages from this sender —
> shorter, more urgent, and it opens differently.
>
> Either alone is often innocent: people add colleagues, and people write differently when
> rushed. Together, on a message asking for a payment detail to change, they are the shape of a
> compromised account.
>
> **Confirm this request by a channel that is not this email thread** — a number you already had,
> not one in this message.

Three properties of that:

- **It states what was observed**, so a person can disagree with the observation rather than with
  a verdict they cannot inspect.
- **It offers the innocent explanation.** A tool that only ever tells you things are suspicious
  gets muted, and then it is worth nothing.
- **It ends in an action that works.** For business email compromise and thread hijacking,
  out-of-band confirmation is not *one* mitigation, it is the *only* reliable one. Better
  detection does not defeat a genuine compromised account; a phone call does.

### One claim, and it names its baseline

The only thing the analyser can honestly establish is **"this is abnormal"** — and the useful part
is *relative to what*. What looked like four categories was really one claim against three
different baselines:

| observation | abnormal relative to |
|---|---|
| `Reply-To` differs from `From` | **the protocol** — what a well-formed message looks like |
| never heard from this domain before | **this mailbox** — what your correspondence looks like |
| the participants changed mid-thread | **this thread** — what this conversation looked like an hour ago |

Naming the baseline is what makes an observation arguable. *"Unusual"* invites "says who?";
*"you have never received mail from this domain, in 4 years of history"* invites a person to
say "yes I have, from my phone" — and be right, which is the outcome a good tool makes easy.

Four labels also invite being read as a severity ladder, and a ladder is a score with extra steps.
One claim cannot ladder.

So findings come in three shapes, and only the first is a claim about the message:

- **abnormal** — departs from a named baseline, with the baseline stated and the innocent
  explanation offered
- **context** — a fact that carries no weight alone and changes what a departure means: a role
  account in the recipients, a domain registered three weeks ago, language about changing payment
  details. On its own, noise. Beside an abnormality, the thing that makes it matter
- **not checked** — and *why*: no network, no history, lookup failed

**`not checked` is load-bearing**, and it is the mistake this project has made five times already:
**absence of information must never render as a positive answer.** A domain whose age could not be
determined is not a young domain and is not an old one.

The split between *abnormal* and *context* is also what keeps the model's job honest. Context is
where "a domain registered three weeks ago" lives — a fact, not a finding. It becomes a finding
only beside "and you have corresponded with this organisation for four years", which is a
different baseline entirely. **The server supplies both; the composition is not its to make.**

### Composition belongs to the model, not to a rule

Two weak observations together are often worth more than either alone — but *which* pairs matter
depends on the ask, and enumerating them in the server would mean encoding a threat model in a
place that cannot see the request. The server's job is to make composition *possible*: emit
observations that name their subject precisely enough to be reasoned about together, and say
plainly when it could not look.

## 4b. The mailbox is the intelligence source, and the AI is what makes using it affordable

Tier M above says "correspondence history" as if it were one signal. It is not: it is a corpus,
and it is the only corpus that knows what *normal* looks like **for this mailbox**. Three uses,
increasingly interesting.

**Have we ever spoken?** — the address, and separately the domain. Not a boolean: *when did we
last*, and *how often*. A domain last heard from three years ago replying to a thread from last
week is a different fact from a domain we email weekly.

**Have we discussed this before?** — search the mailbox for related threads. This catches a shape
nothing else does: a message that presents as the *continuation of a conversation that never
happened*. It is also straightforwardly useful outside security, which matters, because a check
that earns its keep only during an attack gets disabled.

**Does this sound like them?** — compare against how this correspondent actually writes. This is
the one that reaches the case where **everything else passes**: a real compromised account, real
domain, real authentication, real thread, real history. The only thing that changed is the person
at the keyboard.

### The economics, which is the actual design constraint

Researching every inbound message is not a good use of a person's time. It is a fine use of a
model's — but "a model can do it" is not the same as "do it every time", and the difference is
where this becomes buildable or doesn't.

Three properties make it affordable:

**Cheap signals gate expensive ones.** Tier L costs nothing and runs always. Tiers M, D and X run
when L or T noticed something, or on an explicit ask. A cascade, not a battery. The point of the
free tier is not that it catches everything — it is that it decides what deserves a closer look.

**The expensive part is the baseline, not the comparison.** Building a correspondent profile —
how they write, what you discuss, when they email, from where — is a one-off cost that amortises
over every later message from them. Checking a new message against an existing profile is cheap.
So the cost model is per *correspondent*, not per *message*, and it falls over time.

**It does not have to be synchronous.** A profile can be built the first time you reply to
someone, refreshed occasionally, and simply be absent otherwise — in which case the finding is
`unavailable`, which the vocabulary above already requires be distinguishable from "nothing
wrong".

### The division of labour: the server surfaces evidence, the model judges

Tone comparison invites building a classifier. **Do not build a classifier.** This server already
feeds message bodies to a model; the model is the analyser, and the server's job is to put the
right evidence in front of it:

> *"This is the first message from this address. The domain has appeared 4 times, last 14 months
> ago. Here are the 3 most recent messages from this domain, for comparison."*

That is a tool returning facts, and the judgement — *does this sound like the same person?* —
happens where judgement already happens. It keeps the server free of an NLP stack it would then
own, it keeps the reasoning inspectable in the transcript, and it degrades honestly: with no
history, the tool says so rather than guessing.

### Tone is a weak signal, and must stay one

People write differently when rushed, from a phone, when angry, in a second language, or about an
unfamiliar topic. A tone mismatch is *routine*. It becomes meaningful only beside something else —
a new device, a dormant thread resurrected, a payment detail changing — and it is exactly the sort
of signal that tempts a score. It must not get one.

### What this costs, and what it discloses

Tier M as described reads **the mailbox at large**, not the thread in front of you. That is a
materially larger capability than anything else here, and it deserves saying plainly rather than
arriving as a side effect of a useful feature. It stays off by default, it says what it read, and
a profile is derived data the person should be able to see and discard.

## 5. Build order

1. **Header capture.** `ParsedMessage` today carries `sender`, `to`, `cc`, `subject` and no
   `reply_to`, `return_path`, `message_id`, `Received` chain or `Authentication-Results`. Most of
   Tier L is unreachable until it does. This is the prerequisite, and it is cheap.
2. **Tier L**, on `get_message`. Establishes the `Finding` vocabulary everything else reports into.
3. **Tier T**, on `get_thread` **and `reply_all`** — the incident, and the hook that matters.
4. **Tier M** (§4b) — the corpus checks. Order inside it: *have we spoken* (cheap, a header
   index), then *have we discussed this* (search, which already exists), then *does this sound
   like them* (needs a profile and a model, and is the one that reaches a compromised real
   account).
5. **Tiers D and X**, behind their own capabilities, with the resolver as one hook.

Tier L first is deliberate even though Tier T is more valuable: T needs the vocabulary L defines,
and building the hard check while inventing the output contract gets both wrong.

## 6. What this must not become

**More than one word for the claim.** There is one: *abnormal*, relative to a named baseline.
Adding shades — suspicious, concerning, high — rebuilds the ladder that a single claim exists to
avoid, and each shade is a judgement about danger the analyser cannot make.

**A score.** A single number invites a threshold, a threshold invites tuning, and a tuned
threshold becomes a green tick with extra steps — which §9 already identifies as the failure mode
that lends authority to the exact message it should question.

**A verdict.** "Dangerous" is as unearned as "safe": the analyser cannot see what is being asked,
and that is what decides. It notices; the model composes against the request; the person confirms
out of band.

**A blocker.** Nothing here should stop mail being read or sent on a heuristic. A control that
interrupts ordinary work gets disabled, and a disabled control protects nothing — the goal is that
a person reads one sentence and picks up the phone, not that the software refuses.

## Amendment, 2026-10-01 — four refinements, and one measurement that changed a premise

Appended rather than edited in place, following this project family's `DECISIONS.md` discipline: the
original above is what was agreed on 2026-09-26, and a reader should be able to see what moved. Each
item below names the section it modifies.

### A. Tier T has a rule, not just a list — every Tier L header check has a T counterpart

**Modifies §2, Tier T, and §5 step 3.**

**Correction to this amendment as first drafted (same day):** it asserted that *"Tier T was scoped to
the participant set"*. That is false. §2's Tier T already lists six items, two of which are not
participant checks at all — **thread resurrection** (a reply to a thread dormant for months) and
**subject drift inside a single thread**. The draft also proposed "subject mutation beyond accumulated
`Re:`/`Fwd:`", which *is* the existing subject-drift item under a different name.

The cause is worth recording: the draft took Tier T's content from issue #16, which is about
participant drift, rather than from the section it was amending. Reading the artifact being changed —
not the artifact that motivated the change — would have caught all of it.

**What survives is better than the list that prompted it.** Three of the draft's additions
(`Reply-To`, `Return-Path`, `Message-ID`) already appear in **Tier L**, and the relationship between
the tiers is systematic rather than incidental:

> **Tier L asks: is this message internally consistent?**
> **Tier T asks: did that answer change across the thread?**

So the rule, which generates checks rather than enumerating them: **every Tier L header check has a
Tier T counterpart — the differential of itself.** `Reply-To` ≠ `From` is Tier L; a `Reply-To`
*appearing* in message four of a thread that had none is Tier T. `Return-Path` ≠ `From` is L;
diverging *only in later messages* is T. `Message-ID` domain ≠ `From` domain is L; the `Message-ID`
domain *changing mid-thread* is T.

Stating the rule means a signal added to L gets its T counterpart considered by construction, instead
of the two lists drifting apart as both grow.

**Two items are genuinely new in either tier:**

- **`References` / `In-Reply-To` chain integrity** — a break in the chain, or a parent outside the
  thread. Structural rather than a header comparison, so it has no Tier L counterpart.
- **The `Authentication-Results` result weakening across the thread** — pass, then neutral or none.
  This is the clearest case for the rule: every message can pass its own Tier L authentication check
  while the *trend* is the signal, and no message-scoped check can see a trend. It is #16's argument
  about the cast being forged while each message is authentic, applied to authentication rather than
  to addresses.

No new dependency: all of it is headers the thread already contains, so it stays testable on `.eml`
fixtures with no network and no credentials.

### B. DNS stays two capabilities — §3's names, and why the split is worth enforcing

**Modifies §2, Tiers D and X, and §5 step 5.**

**Correction to this amendment as first written (same day):** B originally proposed "two
capabilities" as if new, under the names `mail.analysis.dns` and `mail.analysis.registry`. §3
already specifies the capability set — `analysis.local`, `analysis.thread`, `analysis.mailbox`,
`analysis.dns`, `analysis.external`, the last three off by default — and already separates D from X.
So the split existed, the names were wrong, and the invented ones contradicted §3 in this same file.
**§3's names are canonical. Tier D is `analysis.dns`; Tier X is `analysis.external`.** Recorded
rather than quietly corrected, because the error is instructive: the self-review before this was
committed checked §6's prohibitions and never checked names against §3, which is a review that tests
three specific claims and calls itself a consistency check.

What B does contribute is two arguments §3 states the conclusion of but not the reasoning for.

**Why the separation is worth enforcing, not just documenting.** One flag cannot express the posture
a reasonable person wants: MX and SPF checks on every message, while refusing to tell a registry who
they email. Both capabilities therefore stay **out** of `DEFAULT_ENABLED` in `policy.py`, so the
existing fail-closed tests in `tests/test_mcp_capabilities.py` catch either one if it is ever wired
to a tool without being declared — the same guard that already protects `mail.delete` and
`calendar.delete`.

| Tier | Capability (§3) | Covers | Discloses |
|---|---|---|---|
| D | `analysis.dns` | MX present at all; SPF/DMARC records and strictness; DKIM selector resolvable | nothing beyond ordinary DNS |
| X | `analysis.external` | registration age (RDAP, WHOIS fallback); registrar; privacy-proxied; CT first-seen | **which domains you correspond with, to whoever answers** |

A second reason, which is operational rather than about consent: **D is reliable and X is not.** SPF
and MX answers are cheap, cacheable and deterministic in the sense that matters; RDAP is rate-limited,
inconsistently formatted per registry, and its most useful field is often better obtained from
certificate transparency than from RDAP at all. Behind one flag, the reliable half inherits the
unreliable half's failure modes, and the first timeout teaches someone to switch all of it off.

No new default-on network call enters the product. With neither capability enabled the analyser is
exactly what §5 steps 1–3 build: headers only.

### C. The store is a history, not a cache

**Modifies §2 (Tiers D and X) and §3 — §3 is where the cache lives.**

Measured 2026-10-01: `cloudsecurityalliance.org` publishes one MX, `smtp.google.com`, and per the
domain owner it has changed **once in roughly fifteen years**.

A premise correction belongs here, because it was nearly built on. Google-hosted zones default to a
300-second TTL, and most of the domains sampled (`google.com`, `github.com`,
`cloudsecurityalliance.org`) publish MX, SPF and DMARC at 300s. **TTL is not a churn signal.** It is
the operator saying they want to be *able* to change quickly; it says nothing about how often they do.
Reading 300s as "this changes often" inverts the actual property.

The actual property is what makes the signal valuable: if a domain's own mail records change about
once a decade, **any observed change is a finding** rather than noise to be tuned away. A partner's MX
moving, or DMARC weakening from `p=reject`, is the shape of a domain takeover.

Which means the current value is close to worthless on its own. "This domain has MX `smtp.google.com`"
tells a reader nothing. "It had that for fifteen years and changed last Tuesday" tells them everything.
**The stored history is the product**, and that is not a TTL cache — a TTL cache's job is to forget,
and forgetting is precisely the failure mode here.

So the store holds, per record: **first observation, current value, and a change log**. TTL is a floor
on refresh frequency, never the retention policy.

**Two kinds of record, compared by different rules.** This distinction is load-bearing, and getting it
wrong would bury the signal:

| | Changes | On change |
|---|---|---|
| **The domain's own records** — MX hosts, the SPF record *text*, DMARC `p=`/`sp=`/`aspf`/`adkim` | about once a decade | **a finding** |
| **The delegated include tree** — what `include:sendgrid.net` resolves to | continuously, by third parties | routine; compare **structurally** (an include added or removed), never byte-wise |

Measured on `github.com`: 8 `include:` terms resolving to 9 further records operated by 8 different
companies — Outlook, two Google netblocks, Zendesk, Salesforce, Mailchimp, Marketo, SendGrid — with
TTLs from 300 to 3600 and one nested include. SendGrid rotating a netblock changes GitHub's effective
SPF without GitHub touching anything. Byte-comparing the resolved chain would fire constantly, and a
check that fires constantly is the "tuned threshold becomes a green tick" failure §6 already forbids.

**Retention differs by tier, following the disclosure.** Tier D answers are cheap to refresh and change
legitimately. Tier X's registration age is near-immutable once known — a domain's creation date does
not change — so it caches approximately forever, which is also what minimises how often the product
tells a registry who CSA emails.

**Where it lives, and the axis the 2026-09-01 design spec named.** That spec records: *"Writing
`.eml` to disk is a new axis — a filesystem write, which no existing capability covers."* A persistent
observation store is the same axis, and the first draft of this amendment did not engage it.

Two directories, because two different things are being conflated by the word "cache":

| | Directory | Why |
|---|---|---|
| **Observation history** | `~/.csa_google_gmail_calendar/`, beside the token | machine state whose **whole value is that it persists**. Calling it temporary would be exactly wrong — forgetting is the failure mode this section exists to prevent. |
| **`.eml` working files** | `~/.csa_google_gmail_calendar_tmp_files/` | files the **tool** needs and the person does not. Hidden, clearly labelled, and safe to delete in its entirety. |

Not `CSA_GGC_DOWNLOAD_DIR`. That directory is where a person puts things they asked for; an `.eml`
written so a signal can parse it is not something they asked for, and burying a wanted download among
hundreds of them is a cost paid by the person for the tool's convenience.

**Retention, settled.** The design spec records that *"an `.eml` on disk is the complete message,
which is a larger exposure than any API response this project otherwise handles."* A directory that
accumulates complete messages indefinitely is that exposure multiplied, and hiding it does not reduce
it.

What makes an aggressive policy safe here is that **nothing in this directory is irreplaceable** — the
tool can fetch the message again. There is therefore no recovery story to design, and no reason to keep
anything longer than it is being used.

**Two mechanisms, deliberately, and neither replaces the other:**

1. **Delete after use.** An `.eml` is removed once the signals that needed it have run.
2. **Sweep on write.** Every time the server writes to this directory it first removes any file in it
   older than 24 hours.

The second exists because the first fails. A crash, a kill, a power loss or an exception on an
unexpected path leaves an orphan, and a cleanup that only runs on the success path cleans only the
cases that did not need cleaning. The sweep is the backstop, and tying it to writing means there is no
scheduler, no background thread and no separate lifecycle to get wrong: cleanup is a side effect of the
activity that creates the mess, so a busy mailbox cleans often and an idle one has nothing to clean.

**What "boring and deterministic" has to mean in the implementation**, because each of these is a way a
delete routine goes wrong:

- **Split the decision from the deletion.** *Which files are too old* is a pure function of a listing
  and a clock — trivially testable without waiting a day or stubbing time badly. *Deleting them* is
  I/O that is allowed to fail.
- **`mtime`, not creation time.** Creation time is not portable: on Windows `st_ctime` is creation, on
  POSIX it is inode change. `mtime` means the same thing on both.
- **The sweep must never fail the operation it is attached to.** If a delete is refused — Windows will
  not unlink a file another process holds open — the write that triggered the sweep still has to
  succeed. Log it, skip it, continue; the next sweep gets it.
- **Tolerate the race.** Two clients can run two servers, each sweeping. A file that vanished between
  the listing and the unlink is a success, not an error. The 24-hour threshold is also far longer than
  any analysis, so a sweep cannot plausibly remove a file still in use.
- **Confine it absolutely.** Non-recursive, no symlink traversal, and only files matching the pattern
  this server writes — not everything present. A deletion routine aimed at a directory is a dangerous
  thing to get subtly wrong, and the cost of being narrow is nothing.

**The sweep applies to this directory only.** `~/.csa_google_gmail_calendar/` holds the token and the
observation history, and must never be swept: its contents are not replaceable and its value is that it
persists. The two directories are adjacent and one of them is safe to empty at any time — which is
exactly why they are two.

**Scope held deliberately narrow:** per-machine. Whether a *shared* registry should exist is
deliberately not decided here, because a central record of "which domains CSA corresponds with, and
since when" is a map of CSA's correspondents and engages `DATA-BOUNDARIES.md` and
`SOURCE-OF-TRUTH.md`. Filed as CINO-Platform-Engineering#170.


### D. Every resolver is an extension point, and providers are not evaluators

**Modifies §3. Generalises a sentence §3 already contains.**

§3 ends with: *"This is also how a 'domain check hook' gets built once rather than per-signal. Domain
age, registrar, MX presence and CT first-seen are four signals sharing one resolver, one cache, and
one disclosure decision. **The hook is the resolver; the signals are its callers.**"*

That is stated for DNS. It is true of every lookup in the design, and saying so changes what the
architecture is for.

**The resolvers, named.** Each is a place the analyser asks a question and gets data back:

| Resolver | The question | Tier |
|---|---|---|
| headers | — (already in hand) | L, T |
| correspondence | have we spoken with this address or domain, when last, how often | M |
| content | what does this body or attachment contain | L |
| domain | MX, SPF, DMARC, DKIM selector | D |
| registry | registration age, registrar, CT first-seen | X |

A signal declares a need; a resolver satisfies it. Nothing in a signal says *who* satisfies it, which
is what makes a resolver replaceable.

**Why that matters: "have we ever spoken" has more than one right answer.** The local mailbox index
can answer it. So can Customer360, which already holds a daily copy of Zendesk and other sources. So
can a member's own CRM — and for them the useful question is not "have we corresponded" but **"is
this address a customer"**, which no mailbox can answer and their database answers instantly. Same
declared need, three providers, and the signal is indifferent to which one is present.

#### Two kinds of extension, and conflating them would be the mistake

| | A **provider** | An **evaluator** |
|---|---|---|
| Shape | question in, **data** out | data in, **findings** out |
| Changes | what the analyser **knows** | what the analyser **concludes** |
| Examples | internal CRM, Customer360, threat-intel feed, a DNS cache | policy engine, AV scanner, prompt-injection detector |
| A wrong answer | **poisons every signal downstream of it, silently** | produces one bad finding, visibly |

That last row is the reason they cannot share one interface. A provider that returns "yes, four years
of correspondence" for a domain first seen yesterday defeats Tier M, Tier X and the thread checks at
once, and nothing downstream can tell. An evaluator that returns a wrong finding is wrong *in the
output*, where a reader sees it next to the others. Same plug, very different trust, so: **a provider
states its source in every answer, and a finding derived from a provider names it.** A reader must be
able to see that "known correspondent for four years" came from a CRM rather than from the mailbox.

#### What this does not change

- **§4's output contract still binds.** An evaluator returns findings in the one vocabulary —
  *abnormal*, against a named baseline. §6 forbids scores, severity ladders and verdicts, and an
  external engine will want to return all three. Either the boundary translates or the discipline
  leaks away at the first integration.
- **§3's capability model still gates.** A provider inherits the tier of the question it answers: a
  CRM answering a Tier M question is off unless `analysis.mailbox` is enabled, because the *question*
  is the disclosure, not the implementation. A member's CRM may be less disclosing than reading the
  mailbox — it is still the same question, and the person still chose.
- **The cascade still holds.** Cheap gates expensive. A provider does not get to be consulted on
  every message because it happens to be fast.

#### Scope

Not built now. Steps 1–3 need exactly one resolver — headers — and building a plug-in system against
a single internal caller designs for imagined consumers. The sequence is: build the resolvers as
plain internal seams with declared needs, let Tiers M/D/X prove the seam is in the right place, and
only then publish it. Tracked as CINO-Platform-Engineering#171, which now covers both hook kinds; the
first implementation plan should leave the resolver boundary clean enough that #171 is a publication
rather than a refactor.

### Also noted, not amended

CINO-Platform-Engineering#171 asks whether §1's hook table should be published as an extension contract
so a member can attach their own policy engine. That issue argues for building the internal signals
against the table first: a contract derived from a table nothing has exercised will specify the wrong
things. Nothing in this spec changes for it.
