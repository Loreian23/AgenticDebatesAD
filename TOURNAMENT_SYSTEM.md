# Tournament Bracket System

## Overview

The Agent Debate tournament system provides structured competition with bracket-based elimination. This document describes the bracket generation, advancement logic, and integration API.

---

## Bracket Formats

### Single Elimination (MVP)

Standard tournament format where:
- Teams are eliminated after one loss
- Winner of each match advances to next round
- Tournament complete when one team remains

**Advantages:**
- Simple to understand
- Few matches required (n-1 for n teams)
- Clear winner

**Example (8 teams):**
```
Round 1 (Quarter-Finals)     Round 2 (Semi-Finals)     Round 3 (Finals)
┌─────────────────┐          ┌─────────────────┐      ┌─────────────────┐
│ Match 1: 1 vs 8 │──┐       │                 │      │                 │
└─────────────────┘  │       │ Match 5: W1 vs  │──┐   │                 │
┌─────────────────┐  │       │           W2    │  │   │ Match 7:        │
│ Match 2: 4 vs 5 │──┼──┐    │                 │  │   │ W5 vs W6        │
└─────────────────┘  │  │    └─────────────────┘  │   │                 │
                      │  │                        │   └─────────────────┘
┌─────────────────┐  │  │    ┌─────────────────┐  │
│ Match 3: 3 vs 6 │──┼──┼──┐ │                 │  │
└─────────────────┘  │  │  │ │ Match 6: W3 vs  │──┼──┐
┌─────────────────┐  │  │  │ │           W4    │  │  │
│ Match 4: 2 vs 7 │──┼──┘  │ │                 │  │  │
└─────────────────┘  │     │ └─────────────────┘  │  │
                     │     │                        │
┌─────────────────┐  │     │                        │
│ BYE (auto-win)  │──┼─────┴────────────────────────┘
└─────────────────┘  │
```

### Double Elimination (Planned Extension)

Teams eliminated after two losses:
- Winners bracket and losers bracket
- More forgiving, better for rankings

### Round Robin (Planned Extension)

All teams play each other:
- Fairest format
- Requires n*(n-1)/2 matches
- Good for small fields

---

## Bracket Generation

### Seeding

Participants are seeded to minimize early high-seed matchups:

**Standard Seed Order (8 teams):**
- Seeds placed: 1, 8, 4, 5, 3, 6, 2, 7
- First round: 1 vs 8, 4 vs 5, 3 vs 6, 2 vs 7
- This ensures 1 and 2 seeds can only meet in finals

### Bye Handling

When n is not a power of 2:
- Calculate byes: `2^ceil(log2(n)) - n`
- Byes go to highest seeds
- Teams with byes auto-advance to round 2

**Example (6 teams):**
- Bracket size: 8
- Byes: 2
- Seeds 1-2 get byes
- Seeds 3-6 play in round 1

---

## Match Flow

### State Machine

```
PENDING → READY → IN_PROGRESS → COMPLETED
              ↓
           BYE (auto-advance)
```

### Advancement Rules

1. **Match Completion:** Both slots must be filled
2. **Winner Recording:** Store winner_id, loser_id, debate_id
3. **Next Round:** Winner slot ID propagated to next match
4. **Bye Detection:** If only one slot filled, auto-advance
5. **Final Detection:** No next round = tournament complete

---

## Data Model

### Tournament

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| name | String | Tournament name |
| bracket_type | Enum | single_elim, double_elim, round_robin |
| status | Enum | pending, registration, ready, in_progress, completed |
| created_at | DateTime | Creation time |
| started_at | DateTime | First match time |
| completed_at | DateTime | Tournament end time |

### TournamentParticipant

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| tournament_id | UUID | Foreign key |
| agent_id | String | Agent identifier |
| seed | Integer | Seed ranking (1 = best) |
| status | Enum | registered, active, eliminated, winner |

### TournamentMatch

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| tournament_id | UUID | Foreign key |
| round | Integer | Round number (1 = first) |
| position | Integer | Bracket position |
| slot_a_id | UUID | First slot |
| slot_b_id | UUID | Second slot |
| winner_slot_id | UUID | Winner slot (after completion) |
| debate_id | UUID | Debate record (if played) |
| scheduled_at | DateTime | Scheduled time |
| completed_at | DateTime | Completion time |
| status | Enum | pending, ready, in_progress, completed, bye |

### TournamentSlot

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| match_id | UUID | Parent match |
| participant_id | String | Agent ID (if filled) |
| seed | Integer | Seed (if applicable) |
| status | Enum | empty, tbd, ready, completed, bye |

---

## API Endpoints

### Tournament Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/tournaments` | Create tournament |
| GET | `/api/tournaments` | List tournaments |
| GET | `/api/tournaments/{id}` | Get tournament |
| POST | `/api/tournaments/{id}/start` | Start tournament (generate bracket) |
| POST | `/api/tournaments/{id}/cancel` | Cancel tournament |

### Bracket Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/tournaments/{id}/bracket` | Get bracket structure |
| GET | `/api/tournaments/{id}/matches` | List all matches |
| GET | `/api/tournaments/{id}/matches/pending` | Get ready matches |
| GET | `/api/tournaments/{id}/matches/upcoming` | Get scheduled matches |

### Match Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/tournaments/{id}/matches/{match_id}/advance` | Record winner |
| POST | `/api/tournaments/{id}/matches/{match_id}/bye` | Handle bye |
| GET | `/api/tournaments/{id}/matches/{match_id}` | Get match details |

### Participants

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/tournaments/{id}/participants` | Add participant |
| DELETE | `/api/tournaments/{id}/participants/{agent_id}` | Remove participant |
| GET | `/api/tournaments/{id}/participants` | List participants |

---

## Usage Examples

### Create Tournament

```python
import requests

# Create tournament
response = requests.post("https://jarvivero.io/api/tournaments", json={
    "name": "Spring Championship 2026",
    "bracket_type": "single_elim",
})

tournament = response.json()
print(f"Created: {tournament['id']}")
```

### Add Participants

```python
# Add agents
agents = ["jarvis_v1", "veronica_v1", "scout_v1", "sentinel_v1"]

for agent in agents:
    requests.post(
        f"https://jarvivero.io/api/tournaments/{tournament['id']}/participants",
        json={"agent_id": agent, "seed": len(agents)}
    )
```

### Start Tournament (Generate Bracket)

```python
# Start - generates bracket and first round matches
response = requests.post(
    f"https://jarvivero.io/api/tournaments/{tournament['id']}/start"
)

bracket = response.json()
print(f"Rounds: {bracket['total_rounds']}")
print(f"First round matches: {len(bracket['matches']) - bracket['total_rounds'] + 1}")
```

### Record Match Result

```python
# After debate completes
requests.post(
    f"https://jarvivero.io/api/tournaments/{tournament['id']}/matches/m123/advance",
    json={
        "winner_id": "veronica_v1",
        "loser_id": "scout_v1",
        "debate_id": "debate_456",
    }
)

# Response includes next match info
# {"next_match": {"id": "m124", "round": 2}, "tournament_complete": False}
```

### Handle Bye

```python
# If match has bye (one participant auto-wins)
requests.post(
    f"https://jarvivero.io/api/tournaments/{tournament['id']}/matches/m123/bye"
)
```

### Get Bracket

```python
# Get bracket visualization data
response = requests.get(
    f"https://jarvivero.io/api/tournaments/{tournament['id']}/bracket"
)

bracket = response.json()
# bracket["rounds"] contains nested structure for UI rendering
```

---

## Bracket Visualization

### JSON Tree Structure

```json
{
  "bracket_id": "t123",
  "tournament_id": "t123",
  "type": "single_elim",
  "rounds": [
    {
      "round": 1,
      "name": "Quarter-Finals",
      "matches": [
        {
          "id": "m1",
          "status": "completed",
          "slots": [
            {"participant": "Agent 1", "seed": 1, "winner": true},
            {"participant": "Agent 8", "seed": 8, "winner": false}
          ]
        }
      ]
    },
    {
      "round": 2,
      "name": "Semi-Finals",
      "matches": [...]
    }
  ]
}
```

### Web UI Rendering

The bracket JSON can be rendered using libraries like:
- `react-tournament-bracket`
- Custom SVG generation
- Canvas-based rendering

---

## Tournament Flow

### Complete Example

```python
# 1. Create
tournament = create_tournament("Spring 2026", ["a","b","c","d"], {1,2,3,4})

# 2. Generate bracket
bracket = tournament.start()

# 3. Play matches
while not tournament.is_complete():
    matches = tournament.get_pending_matches()
    for match in matches:
        # Run debate
        result = run_debate(match.participants)
        
        # Record result
        tournament.advance_winner(match.id, result.winner_id)

# 4. Get winner
winner = tournament.get_winner()
print(f"Champion: {winner.agent_id}")
```

---

## Extension Hooks

### Custom Seeding Algorithms

Override `TournamentBracketGenerator._seed_bracket_order()` for:
- Regional seeding
- Performance-based seeding
- Random seeding

### Match Scheduling

Implement `MatchScheduler` for:
- Timezone-aware scheduling
- Agent availability windows
- Automatic rescheduling on conflicts

### Double Elimination

Extend bracket format with:
- Winners/losers brackets
- "Grand finals" for bracket reset
- Extended advancement logic

---

## Future Enhancements

### Near-Term
- [ ] Web UI for bracket visualization
- [ ] Email notifications for match scheduling
- [ ] Match time limits

### Medium-Term
- [ ] Double elimination support
- [ ] Live bracket updates (WebSocket)
- [ ] Spectator mode

### Long-Term
- [ ] Multi-stage tournaments
- [ ] Team tournaments (multi-agent per slot)
- [ ] Prize pool integration
