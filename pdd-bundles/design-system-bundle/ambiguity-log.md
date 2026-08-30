# Ambiguity log — design-system descriptor v1.0.0

## RA-DS-01 — Resolved: scope of "descriptor"
**Ambiguity:** Does this bundle govern brand identities or only their shape?
**Resolution:** Only the shape. Concrete palettes/typefaces are design/*
bundles embedding a conforming descriptor; this bundle is generator-neutral
vocabulary (taxonomy layer). Classified structural; blast radius none —
no existing bundle affected.

## RA-DS-02 — Resolved: value grammar breadth
**Ambiguity:** Tokens may be colors, dimensions, durations, gradients,
shadows... which to admit?
**Resolution:** Admit color (#RGB/#RRGGBB), dimension (px/rem), duration
(ms) in schema v1. Gradients/shadows ride as style_context or future minor
additions (backward-compatible). Proxy: strict grammar now beats permissive
parsing later. Logged as resolved assumption.

## RA-DS-03 — Open question: pairing relations beyond contrast
**Ambiguity:** Future relations (hue distance, luminance ordering)?
**Resolution:** Schema constrains relation=contrast_ratio in v1; extension
is a MINOR version event with registry review. Not blocking seal.

## RA-DS-04 — Resolved: demote_reason controlled set
**Ambiguity:** Free-text reasons vs enum.
**Resolution:** Enum {unmeasurable, proxy-rejected, style-intent} — free
text goes in intent_delta. Deterministic linting requires enumerable causes.
