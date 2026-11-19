# Tool results: keeping them small without losing information

Tool results (price bars, option chains, filings, web pages, search results) are the largest part of
what the agents read, and so the largest part of what a question costs. This document describes how
the app keeps them small without losing information, what is built, and what is planned.

## Why tool results drive cost

A specialist works in a loop: it calls a tool, reads the result, maybe calls another, then answers.
**Every step re-sends the whole conversation so far**, including every earlier tool result. A large
result is therefore paid for again at every later step: a 30,000-token web page fetched at step 2 of 6
costs roughly 150,000 input tokens over the run.

This is why a specialist's trace can show "190k in" for one question. It is rarely one giant document;
it is moderate results re-read several times. Anthropic's prompt caching (which the app uses) makes the
repeats much cheaper, but not free. Local models pay in time instead of money: the model re-reads the
whole conversation on your computer at every step.

So shrinking each result pays off several times over, and the earliest, largest results matter most.

## Principles

1. **Never cut blindly.** Cutting a result off mid-text loses whatever came after, and the agent doesn't
   know what it missed. (The app used to cut any result at 40,000 characters; that is gone.)
2. **Compress losslessly first.** Remove repetition and noise before removing content.
3. **Keep everything retrievable.** When a result is shortened, say exactly what was left out and how to
   get it, so the agent (and you, in the trace) can fetch more.
4. **Measure, then target.** Optimize the tools that are actually large, not the ones we guess are.

## Techniques

From safest to most selective. Status: **built** or **planned**.

### 1. Lossless compaction of structured data (built)

JSON results repeat every field name on every row, carry empty fields and print floats to many
decimals. The app rewrites JSON results before the model sees them:

- **Tables:** a list of 3 or more objects that share most of their fields (at least 60% on average)
  becomes `{"columns": [...], "rows": [[...], ...]}`, with field names once instead of on every row.
  Lists of dissimilar objects are left as they are.
- **Empty fields dropped:** `null`, empty strings, empty lists and objects, and NaN. In tables, a column
  that is empty on every row is dropped; a missing value in a kept column stays as `null` so the columns
  still line up.
- **Rounding:** floats at or above 1 are rounded to 4 decimal places (`107.53283691` → `107.5328`);
  floats below 1 keep 4 significant digits (`0.0123456` → `0.01235`), which preserves implied
  volatility, greeks and small percentages. Whole-number floats become integers (`1200000.0` →
  `1200000`). NaN and infinity become `null`.
- **No whitespace:** compact separators, no indentation.
- **Only if smaller:** non-JSON results, and JSON that wouldn't get smaller, are passed through unchanged.

Applies to every built-in Python tool and MCP tool result. Typical saving on price bars and option
chains: about half or more, with every value kept.

### 2. Summary first, detail on request (planned)

Tools return the key statistics (for price history: last price, changes over several periods,
highs/lows, volume trend, indicators) plus the most recent rows, with a parameter to request the full
series. Nothing is lost: the detail is one call away when the analysis needs it.

### 3. Precise tool parameters (planned)

Tools accept exactly what the agent needs (strike range and expiry for option chains, date range for
bars, a section for filings), with defaults sized for the common case, so the agent doesn't pull a full
chain to look at three strikes. Some tools already do this (option chains take an expiry and a strike
window; SEC filings take a section, below).

### 4. Clean extraction of long text (built for SEC filings and search)

Long documents are mostly navigation, boilerplate and repetition. Extract the content, and let the agent
choose the part it needs:

- **SEC filings:** HTML is converted to plain text. Reading a filing without a section first returns a
  list of its sections (Part I, Item 1. Business, Item 1A. Risk Factors, Item 7. Management's
  Discussion, …) and the document length, then the start of the document. The agent jumps to a section
  with `find="Risk Factors"` (the first match is usually the table of contents, so a later match is
  preferred) and continues with `offset`. Each page ends with where it stopped and how to continue.
- **DuckDuckGo search:** results keep only the title, link, snippet, date and source.
- **Web pages fetched by Claude** (web fetch) are processed on Anthropic's side, so the app can't
  extract or page them; it can only cap their size in the tool's settings (see "Next steps").

### 5. Paging and search instead of cutting (built)

When a result is still long after compaction:

- A result longer than **16,000 characters** (about 4,000 tokens) is sent as its first page. The page
  ends at a line break where possible (so table rows and sentences aren't split) and closes with a note:
  `…[showing characters 0-15,980 of 85,000. To continue, call read_more with result_id="r1" and
  offset=15980. To search the whole result, call read_more with result_id="r1" and find="<keyword>".]`
- The full result is kept for the rest of that agent's run. The **`read_more`** tool (available to
  every agent) either returns the next page from an offset, or, with `find`, returns the passages that
  mention a keyword: each match with about 400 characters of context on either side, overlapping
  passages merged, up to 8,000 characters, with a count of further matches.
- Nothing is cut off silently. The agent always knows how much it hasn't read and how to get it.

### 6. Dropping stale tool results (planned, optional)

In very long specialist loops, once a large result has been used and the specialist has moved on, it
can be removed from the re-sent conversation while keeping the model's own notes about it. Anthropic's
API offers context management for this. It is the most aggressive technique, so it would only apply
beyond a size threshold.

## Team limits (related)

Every specialist consultation adds a report the Lead must read, and with a local Lead each consultation
can be another slow round. The Lead is instructed to ask each specialist once per question, with every
ticker in one consultation, and to make all consultations in the same step.

| | Default (every Lead) | Strict team mode (opt-in, per model) |
|---|---|---|
| Consultations per specialist, per question | 2 | 1 |
| Consultations in total, per question | 5 | 3 |
| Specialist reports | as written | asked for about 250 words; over 6,000 characters trimmed |

Requests over the limits aren't run; the Lead is told to answer with the findings it has. Strict team
mode is set in Tools → Models. It is faster, which helps with slow local models, but may reduce quality.

## Measuring

Every tool call records two sizes:

- **`raw_chars`:** what the tool returned.
- **`chars`:** what the model actually received after compaction and paging.

The trace shows each tool call's size in tokens (about 4 characters per token), the saving from
compaction (e.g. "≈775 tok · −68%"), and the total per run. Results of about 4,000 tokens or more are
shown in amber. Claude's server-side web search and fetch results are measured too, marked
"(Anthropic)".

## Evaluating a change

1. Run the same question twice on the same day (market data changes), before and after the change.
2. Compare in the trace: tool result sizes, each run's input tokens and cost, and total time.
3. Compare the answers: the same tickers and facts covered, the same conclusions, no gaps the agent
   had to work around.
4. A change is good when cost or time drops and the answer doesn't get worse.

## Where it lives in the code

| Piece | File |
|---|---|
| Compaction, paging, keyword search | `backend/app/tools/compact.py` |
| Applying them to every tool result; `read_more` | `backend/app/tools/registry.py` (`Toolbox.execute`) |
| Size recording | `backend/app/agents/loop.py`, `backend/app/llm/openai_loop.py`, `backend/app/llm/mock_loop.py` |
| SEC filing sections and offset | `backend/app/tools/sec_edgar.py` (`sec_filing_text`, `filing_sections`) |
| DuckDuckGo result trimming | `backend/app/tools/python_tools.py` (`ddg_search`) |
| Team limits and strict team mode | `backend/app/agents/orchestrator.py` (`consult_limits`) |
| Trace display of sizes | `frontend/src/Trace.tsx` |
| Tests | `backend/tests/test_compact.py`, `test_ddg.py`, `test_run_models.py` |

Tunable constants: `PAGE_CHARS` (16,000) and `MAX_FIND_CHARS` (8,000) in `compact.py`; the team limits
in `orchestrator.py`.

## Next steps

1. Look at real traces to find the largest results. Likely candidates: Claude's web fetch and web
   search, SEC filing text, option chains.
2. If Claude's web fetch dominates, cap its page size in the tool's settings (Tools → Data sources →
   Claude web fetch).
3. Add summary-first results (technique 2) to price history and option chains.
4. Consider dropping stale tool results (technique 6) for runs that exceed a size threshold.
