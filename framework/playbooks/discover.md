# Playbook: Discover New Names Along a Thesis

Given `data/theses/<id>.md`, find new names along this narrative worth adding
to the watchlist. Read `framework/methodology.md` first. Write the candidate
report in English.

## Steps

1. **Enumerate the bottleneck layers.** Derive all beneficiary layers from the
   thesis's pass-through chain (e.g. EV penetration doubling → power-semis /
   charging / battery-materials / thermal / test-equipment). List layers
   already on the watchlist too, noting the existing names in each.
2. **Search for candidates per layer.** Search online for the major listed
   companies in each layer (including smaller-cap names), at least 2–3
   candidates per layer, excluding: names too large-cap to have meaningful
   upside optionality (state the threshold), and names where thesis-relevant
   revenue is <15% of the total. Also check `data/screen.json` (if fresh —
   note its `computed_at`): a market-wide screen candidate that sits in one
   of this thesis's layers is a pre-vetted lead; cite its score/track as
   screening signal only (spec: `framework/screen.md`).
3. **Quick-screen each candidate** (use quotes.json where available, otherwise
   look up online and cite the source):
   - Thesis-relevant revenue share (now → direction in 12 months)
   - Estimate-revision momentum where available (quotes.json `revisions`, or
     the same data looked up online): a candidate whose forward EPS is being
     revised up while its price lags the layer is the classic discover hit;
     persistent downward revisions disqualify a "cheap" classification —
     cheap-and-deteriorating is a value trap, not an opportunity
   - Scenario elasticity: where this name's EPS/order elasticity ranks within
     its layer once the thesis plays out
   - Rough good-buy estimate: consensus fwd EPS × scenario growth × a
     reasonable multiple for that business model, vs. current price
4. **Classify** each ticker into exactly one bucket:
   - `cheap`: current price below the rough range
   - `fair`: within the range
   - `priced_in`: above the range — right story, but the market has already
     paid for it
   - `good_story_bad_price`: top-tier elasticity but valuation is out of line
     (state by how much)
5. **Produce the candidate list.** Pick the best 1–2 per layer (or state "no
   qualifying candidate in this layer"), as a table: ticker / layer /
   classification / one-line rationale / rough range vs. current price.

## Output

- Write pre-confirmation candidates to the watchlist: for each selected name,
  make an equivalent data change to running
  `luxtock add <TICKER> --thesis <id> --layer <layer> --note "discover: <one-line rationale>"`
  (editing `data/watchlist.json` directly is fine too, as long as the fields
  stay consistent).
- Report the list back to the user, noting: these are **candidates only** —
  analysis isn't complete until each one runs through the analyze playbook.
