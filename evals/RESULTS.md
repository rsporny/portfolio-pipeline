# Eval results

- Model: `claude-opus-4-8`
- Commit: `ddef511`
- Generated: 2026-08-06T07:59:29.150857+00:00
- Cases: 6 (0 blocked) | error-severity failures: 0 | warnings: 4

| check | baseline | continuity | deep-context | focus | merge-state | over-detailed |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| devlog_word_count | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| social_word_count | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| social_hashtags | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| no_exclamation | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| no_solicitation | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| no_collaborator_leak | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| no_forbidden_phrase | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| faithful_links | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| proof_of_work_present | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| initiative_count | ! | ! | ! | ✓ | ✓ | ! |
| initiative_faithful_links | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| valid_thread_ref | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| continuity_not_reset | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| reader_altitude | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

## Failures

- `baseline` / **initiative_count** [warn] — 1 initiative(s) (want 2–5)
- `continuity` / **initiative_count** [warn] — 1 initiative(s) (want 2–5)
- `deep-context` / **initiative_count** [warn] — 1 initiative(s) (want 2–5)
- `over-detailed` / **initiative_count** [warn] — 1 initiative(s) (want 2–5)

_✓ pass · ✗ error-severity failure · ! warning_
