# Eval results

- Model: `claude-opus-4-8`
- Commit: `a780ef2`
- Generated: 2026-07-22T12:14:10.761380+00:00
- Cases: 4 (0 blocked) | error-severity failures: 0 | warnings: 3

| check | baseline | continuity | deep-context | focus |
|---|:-:|:-:|:-:|:-:|
| devlog_word_count | ✓ | ✓ | ✓ | ✓ |
| social_word_count | ✓ | ✓ | ✓ | ✓ |
| social_hashtags | ✓ | ✓ | ✓ | ✓ |
| no_exclamation | ✓ | ✓ | ✓ | ✓ |
| no_solicitation | ✓ | ✓ | ✓ | ✓ |
| no_collaborator_leak | ✓ | ✓ | ✓ | ✓ |
| no_forbidden_phrase | ✓ | ✓ | ✓ | ✓ |
| faithful_links | ✓ | ✓ | ✓ | ✓ |
| proof_of_work_present | ✓ | ✓ | ✓ | ✓ |
| initiative_count | ! | ! | ! | ✓ |
| initiative_faithful_links | ✓ | ✓ | ✓ | ✓ |
| valid_thread_ref | ✓ | ✓ | ✓ | ✓ |

## Failures

- `baseline` / **initiative_count** [warn] — 1 initiative(s) (want 2–5)
- `continuity` / **initiative_count** [warn] — 1 initiative(s) (want 2–5)
- `deep-context` / **initiative_count** [warn] — 1 initiative(s) (want 2–5)

_✓ pass · ✗ error-severity failure · ! warning_
