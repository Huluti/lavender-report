# AGENTS.md

Lavender Report generates VAT reports used for real declarations
(including auto-liquidated EU VAT filed with French customs).

## Legal-compliance ground rules

Always work with the legal impact in mind:

- **Do not change classification or amount logic casually.** How
  countries, buckets (domestic / intra-EU / extra-EU), fees and totals
  are computed directly feeds VAT declarations. Any change to these
  semantics must be explained in the commit and reconciled against the
  Stripe Dashboard "Reports" tab before being trusted.
- **Categories mirror the invoicing.** Transactions are classified by
  what was actually charged on the Stripe invoice. Do not silently
  re-classify a transaction based on heuristics; contradicting signals
  belong in the warnings, which are advisory and must stay visible.
- **Never lose or silently drop a transaction.** Every payment counted
  in the totals must land in exactly one category, even on retrieval
  errors (as Unknown).
- **Keep currencies separated.** Amounts in different currencies are
  never summed into a single total.
- **Protect personal data.** Customer emails, VAT numbers and
  transaction logs (debug files, cache) stay out of the repository;
  keep them gitignored and never include them in commits.
- **When unsure, ask.** If a change could alter what gets declared,
  flag it to the maintainer before implementing.

Note: this tool assists with reporting; it is not tax advice.
Confirmation of declaration treatment belongs to an accountant.
