# Answer Library

Same rules as the bullet library: **selection and light editing, never generation.** Each answer is pre-written, provenance-checked, and reused. At 10 applications a day these fields are more total typing than the resume, the brief, and the outreach combined — this is the file that makes volume possible.

**How to use:** the scorer flags which question types a posting is likely to ask. You paste the stored answer and swap the slot variables. Slots are marked `{{like_this}}`.

**Status key:** `READY` = use as-is · `DRAFT` = I wrote it, needs your voice · `NEEDS YOU` = I can't write this without your input.

---

## A1 · Why do you want to work here? — DRAFT

The highest-frequency question and the one most often answered badly. Keep a two-sentence core and swap the company slot. Never reuse a company sentence.

> What caught me about {{company}} is {{specific_signal}} — that's the exact problem I've spent the last year on. I've been building AI automation systems end to end, most recently an autonomous decision platform running live in production, and what I want next is to do that inside an organization where the leverage is bigger than a five-person shop. {{role_specific_hook}}

**Slot rules:** `specific_signal` comes verbatim from the JD or a recent company announcement — never "your innovative culture." `role_specific_hook` is one sentence tying their stated first priority to something you've built.

---

## A2 · Describe your experience with workflow automation / AI — READY

> I build and operate production AI systems rather than pilots. The clearest example: an autonomous forecasting and decision-automation platform I architected and shipped that runs live today — 68 API endpoints, 540 configurable strategy profiles, ensemble modeling, and automated risk controls with real-time hedging. I also built the statistical validation layer that gates it, so no strategy reaches live execution without passing walk-forward testing, Monte Carlo simulation, and drawdown analysis.
>
> Before the technical work I spent twelve years running operations — scaling a CPG brand from $250K to $12M, building the finance, logistics, and compliance systems behind it. That combination is the useful part: I build automations against how a business actually runs, not against an idealized process diagram.

---

## A3 · Why are you leaving your own company? — NEEDS YOU

Your own words from our conversation, tightened. Confirm this is how you want to say it:

> I like automation and I've spent the last year proving out AI-native operations on my own dime. I'm self-funding my ventures rather than raising, so I want stable income while they mature — and I want to apply what I've learned somewhere the leverage is much bigger than a five-person shop.

**Add unprompted, every time** (this converts the concern into a credential):

> To be direct about the obvious question: the ventures run nights and weekends, and they're deliberately built to run on automation rather than on my hours. That's the reason I'm a good hire rather than a flight risk.

---

## A4 · You've been an SVP / Chief of Staff — why an IC role? — READY

> I've done the leadership track and I'm good at it. I'm better at building, and I enjoy it more. I'm looking for a role where the deliverable is a working system rather than a headcount plan. I'm not looking to manage people, and I'd rather say that clearly up front than discover a mismatch in month three.

---

## A5 · Salary expectations — READY

For the form field (most require a number):

> {{target_range}} base, flexible depending on total package and equity.

For a conversation, always turn it around:

> I'm targeting {{target_range}} base depending on the total package. What's the band for this role?

**Note for you:** Oregon employers can't ask salary history, but they can ask preferred salary. Never volunteer history — the law protects you from being asked, not from your own disclosure.

---

## A6 · Biggest system you've built — READY

Use A2's first paragraph, then add:

> The part I'd point to isn't the endpoint count — it's the validation gate. Anyone can ship an automation. The engineering judgment is deciding what's allowed to run unattended and what isn't, and building the thing that enforces it.

---

## A7 · Tell me about a failure — READY

This is your strongest answer and most candidates have nothing this specific.

> While drafting a resume I caught a bullet describing an internal system as built and deployed. I'd specified it in detail. I hadn't shipped it. Nothing in my notes distinguished those two states — six weeks on, an ambitious plan and a running system read identically — and it was one screen-share question away from being indefensible.
>
> The fix was structural, not a promise to be more careful: every claim in my library now carries a provenance tag, and a ten-line validator refuses to assemble a document containing an unverifiable one.
>
> The general version matters more than the resume version. When a language model summarizes your own notes back to you, it will quietly promote intention to accomplishment, and it will do it in your voice. Any system putting model output in front of people needs a layer that asks where each claim came from.

---

## A8 · How do you approach deploying AI in a business? — DRAFT

> Start with where the blast radius lands, not with what's possible. The capability is usually trivial now; the question is what breaks when it's wrong and who finds out.
>
> A concrete example: I built a pipeline for my own job search that automates discovery, screening, and document assembly. The version that also auto-submits applications was about three more hours of work. I didn't build it — ATS terms prohibit it, the platforms detect it, and a flag in a shared instance quietly costs you every company on that platform. Most AI deployment decisions look like that.
>
> Second thing: choose the boring architecture. That pipeline has no vector store and no agent framework, because the corpus is a few hundred rows and keyword matching plus one model call is correct. Reaching for the sophisticated option when the simple one works is the most common failure I see in AI projects right now.

---

## A9 · Your automation philosophy in one line — DRAFT

> Automate the work, keep the human on the decisions that have consequences — and be deliberate about which is which.

---

## A10 · Domain gap ("you haven't worked in {{industry}}") — DRAFT

Answer directly rather than deflecting.

> I haven't. What I have is twelve years of learning new operating environments fast — CPG, healthcare services, digital media, higher ed — and the automation problems rhyme across all of them: manual reconciliation between systems, reporting nobody trusts, workflows that exist because someone left. I'd expect the first month to be mostly listening.

---

# NEEDS YOU — I can't write these

These require facts I don't have. Answer roughly and I'll turn them into library entries.

**B1 · A time you influenced without authority.** Behavioral staple. Comarkco as a fractional exec is the natural source — a specific instance where you got something done across a team you didn't own?

**B2 · A conflict or disagreement.** Same format, needs a real instance.

**B3 · Reference-check-safe reason for leaving Worksite Labs and Comarkco.** What actually happened, and what will those employers say? This shapes several answers and I don't want to guess.

**B4 · Your availability / start date.** Immediate? Notice needed?

**B5 · The Comarkco equity outcome.** Did it pay? Affects A3's framing materially — "I took the bet and it didn't land, so I'm self-funding differently now" is a stronger and more honest version if true.

**B6 · What you actually want to be doing in three years.** Asked constantly. Your real answer probably involves the ventures, which needs careful framing — I don't want to invent an ambition for you.

**B7 · Anything from past interviews that consistently lands or consistently stalls.** If you've been asked something that reliably trips you up, that's the highest-value entry in this file.

---

## Schema for the database

```sql
create table answers (
  id            bigserial primary key,
  ref           text unique not null,     -- A2
  question_type text not null,            -- why_company | automation_experience | ...
  text          text not null,
  slots         text[],                   -- {{company}}, {{specific_signal}}
  status        text default 'ready',     -- ready|draft|needs_input
  tags          text[],
  times_used    int default 0,
  last_used_at  timestamptz
);
```

`times_used` matters more than it looks: if one answer is carrying twenty applications, it's worth twenty applications' worth of polish.
