# UI Baseline Policy (AgentDebate)

**Effective date:** 2026-04-08  
**Approved by:** James (Discord #research)

## Baseline
The current live UI is the official base template for AgentDebate.

- Base app URL: `https://agentdebate-backend-production.up.railway.app/`
- Debate room URL: `https://agentdebate-backend-production.up.railway.app/debate-room`

## Change Control Rules
1. Do **not remove** existing UI elements without explicit James approval.
2. Do **not silently change** core user flow structure without approval.
3. Bug fixes are allowed if they preserve existing UI elements and intended behavior.
4. Additive improvements are allowed when clearly labeled as an upgrade.

## Approval Requirement
Any UI element removal, major layout rework, or flow simplification requires explicit James sign-off before deploy.

## Implementation Note
When shipping UI changes, include:
- what changed,
- whether elements were removed (should be `none` unless approved),
- before/after verification links or screenshots.
