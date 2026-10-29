"""C1: minimal fact-constrained speech contract for Minecraft task replies.

The model may choose wording, but it may not decide that an action has happened.
State comes only from TurnDecision, TaskRuntime, Policy, tool results and real
Action terminals.  This module is deliberately small: it maps state to the
claims that state permits and rewrites a reply when the wording outruns it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ActionState(StrEnum):
    UNSUPPORTED = "UNSUPPORTED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class SpeechClaimVerdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    REWRITTEN = "REWRITTEN"
    BLOCKED_UNSUPPORTED = "BLOCKED_UNSUPPORTED"
    BLOCKED_UNVERIFIED = "BLOCKED_UNVERIFIED"


_ALLOWED = {
    ActionState.UNSUPPORTED: "无法执行；这件事尚未开始",
    ActionState.PENDING_CONFIRMATION: "需要确认；确认前尚未执行",
    ActionState.RUNNING: "正在执行；尚无完成证据",
    ActionState.SUCCEEDED: "已有真实成功证据的结果",
    ActionState.FAILED: "失败及真实原因",
    ActionState.UNKNOWN: "结果未确认；需要核查",
}

_CANONICAL = {
    ActionState.UNSUPPORTED: "这件事我现在还做不到，所以没有开始动手。",
    ActionState.PENDING_CONFIRMATION: "这件事还需要你确认，确认前我不会开始。",
    ActionState.RUNNING: "这件事正在做，还没有完成。",
    ActionState.UNKNOWN: "这件事的结果我还没确认，需要核查。",
    ActionState.SUCCEEDED: "已经完成。",
    ActionState.FAILED: "这次没有成功。",
}

_BLOCKED_STATES = {
    ActionState.UNSUPPORTED: SpeechClaimVerdict.BLOCKED_UNSUPPORTED,
    ActionState.UNKNOWN: SpeechClaimVerdict.BLOCKED_UNVERIFIED,
}


@dataclass(frozen=True)
class SpeechClaim:
    action_state: ActionState
    allowed_claim: str
    speech_claim_verdict: SpeechClaimVerdict


def claim_for_state(state: ActionState | str) -> SpeechClaim:
    normalized = state if isinstance(state, ActionState) else ActionState(str(state))
    return SpeechClaim(normalized, _ALLOWED[normalized], SpeechClaimVerdict.SUPPORTED)


def finalize_speech(text: str, claim: SpeechClaim) -> tuple[str, SpeechClaim]:
    """Return (text, claim) after enforcing the state's allowed claim.

    SUCCEEDED/FAILED preserve the caller's factual wording (it carries real
    result detail); the other four states are rewritten to the canonical
    statement because any stronger wording would be unsupported.
    """
    body = str(text or "").strip()
    if claim.action_state in (ActionState.SUCCEEDED, ActionState.FAILED):
        final = body or _CANONICAL[claim.action_state]
    else:
        canonical = _CANONICAL[claim.action_state]
        if not body or body == canonical:
            final = canonical
        else:
            final = canonical
    verdict = _BLOCKED_STATES.get(claim.action_state)
    if verdict is None:
        verdict = SpeechClaimVerdict.SUPPORTED if final == body else SpeechClaimVerdict.REWRITTEN
    return final, SpeechClaim(claim.action_state, claim.allowed_claim, verdict)
