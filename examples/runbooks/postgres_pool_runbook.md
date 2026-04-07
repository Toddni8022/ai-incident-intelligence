# Runbook: PostgreSQL connection pool exhaustion

## Symptoms

- Application logs: `connection pool exhausted`, `too many connections`, elevated latency on DB-backed APIs.
- May coincide with deploys, traffic spikes, or a stuck worker holding connections.

## Immediate checks

1. Compare `active` vs `max` pool settings in app config and DB `max_connections`.
2. Look for connection leaks (unclosed sessions) in recent code changes.
3. Check replica lag and slow queries that hold connections longer.

## Mitigation

- Temporarily raise pool max **only** if DB headroom allows; document the change.
- Scale app instances **down** if the issue is too many processes each opening full pools.
- Fail over or restart **only** after identifying leak or deadlock.

## Escalation

- DBA / platform if `FATAL: too many connections` persists after app-side fixes.
