"""Exploration stop signals reserve room for the same planner's final review.

Coverage means a candidate in each dimension, never proof of semantic quality.
This policy does not increase, reset or bypass the shared hard budget.
"""
from collections import Counter
from datetime import datetime

from app.domain.research import utc_now


class ResearchWrapUp(RuntimeError):
    """End exploration without stopping the ledger needed for bounded review."""


def remaining_seconds(run):
    return max(0.0, run['budget']['max_wall_seconds'] -
               (utc_now() - datetime.fromisoformat(run['created_at'])).total_seconds())


def review_seconds(run):
    return min(45.0, run['budget']['max_wall_seconds'] * .25)


def wrap_up_reason(run, dimensions, findings, snapshot, next_tokens=0):
    if snapshot['stopped_reason']:
        return None  # A hard stop must remain a hard stop; no review bypass.
    limits = run['budget']
    counts = Counter(entry['kind'] for entry in snapshot['reservations'])
    coverage = {item.content.dimension for item in findings}
    if set(dimensions) <= coverage and len(findings) >= run.get('scope', {}).get('target_notes', 1):
        return 'candidate_coverage_ready'
    if remaining_seconds(run) <= review_seconds(run):
        return 'time_reserve'
    # Keep a review request and its tools available when limits allow it.
    external_reserve = min(2, limits['max_external_calls'] // 4)
    if external_reserve and snapshot['external_calls'] >= limits['max_external_calls'] - external_reserve:
        return 'external_reserve'
    tool_reserve = min(3, limits['max_tool_calls'] // 4)
    if tool_reserve and counts['tool'] >= limits['max_tool_calls'] - tool_reserve:
        return 'tool_reserve'
    token_reserve = min(24000, limits['target_total_tokens'] * .25)
    if snapshot['admission_tokens'] and snapshot['admission_tokens'] + next_tokens + token_reserve >= limits['target_total_tokens']:
        return 'token_reserve'
    return None
