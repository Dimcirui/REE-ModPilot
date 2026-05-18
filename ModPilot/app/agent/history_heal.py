"""Defensive normaliser for Anthropic tool_use / tool_result pairing.

Sits between `AgentLoop._global_history` and every `llm.chat()` call so a
malformed message sequence never hits the API.  Pure, in-place, idempotent.

Extracted from `app.agent.loop` so the heuristics can be tested and reasoned
about in isolation — the loop itself is busy enough.
"""

from __future__ import annotations


def heal_history(history: list[dict]) -> int:
    """Defensive: ensure assistant `tool_use` blocks and the user `tool_result`
    blocks immediately after them are id-consistent.  Anthropic rejects
    requests violating EITHER direction with a 400:

      - "tool_use ids were found without `tool_result` blocks immediately after"
        — orphan tool_use (no following tool_result for some id)
      - "unexpected `tool_use_id` found in `tool_result` blocks: ... Each
        `tool_result` block must have a corresponding `tool_use` block in the
        previous message"
        — orphan tool_result (id doesn't match any preceding tool_use)

    The exact code path that produces orphan blocks has not been pinned down
    (suspected provider-side `response.content_blocks` vs `response.tool_calls`
    id mismatch when ids are synthesized client-side).  This runs as a backstop
    before every LLM call so the API never sees a malformed history regardless
    of how it got that way.

    Returns the total number of repairs performed (0 = clean history).
    Idempotent and in-place.

    Heuristics:
      - Missing tool_result for a tool_use_id → inject a placeholder.
      - tool_result for an id with no matching tool_use → DROP that block (the
        alternative — synthesize a fake tool_use — would corrupt the LLM's
        view of what it actually called).
      - tool_use with no following user message at all → insert a synthetic
        user message with placeholder tool_results.
      - Plain-text user message immediately after tool_use → insert synthetic
        tool_result message in between (the plain text otherwise gets parsed
        as the "next message" slot that Anthropic checks against tool_use).
    """
    healed = 0
    i = 0
    while i < len(history):
        msg = history[i]
        if msg.get("role") != "assistant":
            i += 1
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            i += 1
            continue
        tool_use_ids = [
            blk.get("id")
            for blk in content
            if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("id")
        ]

        next_msg = history[i + 1] if i + 1 < len(history) else None
        next_is_user_blocks = (
            next_msg is not None
            and next_msg.get("role") == "user"
            and isinstance(next_msg.get("content"), list)
        )

        if not tool_use_ids:
            # Assistant message without tool_use — but next user message might
            # still carry orphan tool_result blocks (e.g. leaked from a prior
            # round when provider ids got desynced).  Drop them.
            if next_is_user_blocks:
                kept = [
                    blk
                    for blk in next_msg["content"]
                    if not (
                        isinstance(blk, dict)
                        and blk.get("type") == "tool_result"
                    )
                ]
                if len(kept) != len(next_msg["content"]):
                    healed += len(next_msg["content"]) - len(kept)
                    # Preserve a non-empty content list so Anthropic doesn't
                    # reject "empty content"; if everything was orphan
                    # tool_results, leave a marker text block.
                    next_msg["content"] = kept or [
                        {"type": "text", "text": "[orphan tool_results dropped by heal_history]"}
                    ]
            i += 1
            continue

        # Assistant message HAS tool_use blocks — next user message must have
        # exactly-matching tool_result blocks (and nothing else of the type).
        if next_is_user_blocks:
            assert next_msg is not None
            valid_blocks: list[dict] = []
            present_ids: set[str] = set()
            for blk in next_msg["content"]:
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    tid = blk.get("tool_use_id")
                    if tid in tool_use_ids:
                        valid_blocks.append(blk)
                        present_ids.add(tid)
                    else:
                        # Orphan tool_result — drop it (would trip the
                        # "unexpected tool_use_id" 400).
                        healed += 1
                else:
                    valid_blocks.append(blk)
            # Inject placeholders for any tool_use_ids without a result.
            missing = [tid for tid in tool_use_ids if tid not in present_ids]
            for tid in missing:
                valid_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": "[orphaned tool_use — placeholder result injected by heal_history]",
                    }
                )
                healed += 1
            next_msg["content"] = valid_blocks
        else:
            # No following user-tool_result message at all (next is either
            # missing, plain-text user, or assistant) — insert one.
            history.insert(
                i + 1,
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tid,
                            "content": "[orphaned tool_use — placeholder result injected by heal_history]",
                        }
                        for tid in tool_use_ids
                    ],
                },
            )
            healed += len(tool_use_ids)
        i += 1
    return healed
