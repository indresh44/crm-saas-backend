"""Interactive terminal harness — drives the layer-6 agent loop with real Gemini.

Throwaway test plumbing. All reasoning lives in app/agent/; this file is a REPL:
    prompt for a goal -> run the loop -> handle every outcome -> back to prompt.

Run:
    python -m app.read_model.tools.ask

Outcomes handled at every turn:
    done            -> print, return to goal prompt
    awaiting_confirm-> show preview; ask yes/no/edit; on yes/edit commit, then
                       ask "done / more": done -> back to goal prompt; more ->
                       read what's next and re-invoke with THAT (never blindly
                       repeat the just-committed goal, or the same write would
                       run twice — v1 has no memory of having done it)
    ask_user        -> show question, read your reply, re-invoke with reply appended
                       (the only case where auto-re-invocation is valid: the
                       flow was explicitly waiting on the user)
    exhausted/error -> print, return to goal prompt
    crash           -> printed cleanly, returns to prompt (session never dies)

Exit: type `quit` (or `exit`, `:q`) at the goal prompt. Ctrl-C / Ctrl-D too.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from typing import Any
from uuid import UUID

import jwt as pyjwt
from sqlmodel import Session

from app.agent.confirm import ConfirmResult, confirm_prepared_action
from app.agent.loop import AgentRunResult, run_agent
from app.agent.observations import TurnRecord
from app.agent.serialize import synth_commit_record
from app.core.database import engine
from app.core.security import decode_access_token
from app.models.business import Business
from app.models.user import User
from app.repositories.user_repository import get_user_by_id


# =============================================================================
# EDIT THIS — Bearer JWT for the interactive session.
# Mint one with:
#   python -c "from sqlmodel import Session,select; from app.core.database import engine; \
#              from app.core.security import create_access_token; from app.models.user import User; \
#              s=Session(engine); u=s.exec(select(User).where(User.email=='vikram@vikraminteriors.demo')).first(); \
#              print(create_access_token(u.id,u.business_id,u.role.value))"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIwMTAwMDBkZi0yYmUzLTRhZjktODJkOC1mMjc3ZTNlMDdkMGIiLCJidXNpbmVzc19pZCI6ImEzODM4MDZhLWQyMjktNDhmOC05NWJmLTk3NDQ0YWZhNDkxMSIsInJvbGUiOiJvd25lciIsImlhdCI6MTc3OTU1NzI3MSwiZXhwIjoxNzc5NTU4MTcxLCJ0eXBlIjoiYWNjZXNzIn0.E8K_Y22KGO2HrNJcdED1FtiZ93WstSqLu9Zkmuep5V8"

# Optional: pre-filled first goal. Leave "" to be prompted on start.
STARTING_GOAL = ""
# =============================================================================


# ---------------------------------------------------------------------------
# Auth — same real JWT path
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Token could not be resolved to an active user/business."""


def _resolve_user(token: str, session: Session) -> User:
    token = token.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except pyjwt.ExpiredSignatureError:
        raise AuthError("access token has expired.")
    except pyjwt.InvalidTokenError as exc:
        raise AuthError(f"invalid access token ({exc}).")
    sub = payload.get("sub")
    if not sub:
        raise AuthError("token has no 'sub' (user id).")
    user = get_user_by_id(session, UUID(sub))
    if user is None:
        raise AuthError("user not found.")
    if not user.is_active:
        raise AuthError("account is deactivated.")
    return user


def resolve_business_id(token: str, session: Session) -> tuple[UUID, str]:
    """Back-compat helper (test_chat_api may use this)."""
    user = _resolve_user(token, session)
    biz = session.get(Business, user.business_id)
    tzname = biz.timezone if biz and biz.timezone else "Asia/Kolkata"
    return user.business_id, tzname


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------

def _section(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def _print_agent_run(result: AgentRunResult) -> None:
    """Per-turn breakdown + final state + token usage."""
    for tr in result.history:
        _section(f"TURN {tr.turn}")
        print(f"thought:     {tr.thought}")
        print(f"action:")
        print(_indent(json.dumps(tr.action, indent=2), 2))
        print(f"observation: {tr.observation_summary}")

    _section(f"FINAL ({result.kind})")
    if result.kind == "done":
        print(result.answer)
    elif result.kind == "awaiting_confirm":
        print(f"prepared_action_id: {result.prepared_action_id}")
        print(f"preview:            {result.preview}")
        print(f"editable fields:    {list(result.editable_fields)}")
    elif result.kind == "ask_user":
        print(f"agent's question:   {result.question}")
    elif result.kind in ("error", "exhausted"):
        print(json.dumps(result.error, indent=2))

    _section("TOKEN USAGE (this run)")
    print(json.dumps(result.tokens.to_dict(), indent=2))


def _print_commit_result(cr: ConfirmResult) -> None:
    if cr.ok:
        _section("COMMIT RESULT")
        print(json.dumps(cr.result, indent=2, default=str))
    else:
        _section("COMMIT FAILED")
        print(json.dumps(cr.error, indent=2))


def _indent(s: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in s.splitlines())


# ---------------------------------------------------------------------------
# Input prompts (Ctrl-C / Ctrl-D treated gracefully)
# ---------------------------------------------------------------------------

def _prompt_goal() -> str | None:
    """Loop until we get a non-blank goal or the user wants to quit. Returns
    None when the user is done with the session."""
    while True:
        try:
            s = input("\n>>> goal (or 'quit'): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not s:
            continue
        if s.lower() in ("quit", "exit", ":q", ":quit"):
            return None
        return s


def _prompt_confirm_choice() -> str:
    """Returns one of: 'yes', 'no', 'edit'. EOF/Ctrl-C -> 'no'."""
    while True:
        try:
            s = input("\nConfirm? (yes / no / edit) [no]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "no"
        if s in ("", "n", "no", "cancel"):
            return "no"
        if s in ("y", "yes", "ok", "go"):
            return "yes"
        if s in ("e", "edit"):
            return "edit"
        print(f"(didn't catch '{s}' — type yes, no, or edit)")


def _prompt_edits() -> dict[str, str]:
    """Read field=value lines until blank. Returns the edits dict."""
    print("Enter field=value pairs (blank line to finish):")
    edits: dict[str, str] = {}
    while True:
        try:
            line = input("  ").rstrip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            break
        if "=" not in line:
            print("  (skipping — expected field=value)")
            continue
        k, v = line.split("=", 1)
        edits[k.strip()] = v
    return edits


def _prompt_user_reply() -> str:
    try:
        return input("\nYour answer to the agent: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _prompt_post_commit_choice() -> str:
    """After a successful commit, ask whether this goal is finished.
    Returns 'done' or 'more'. EOF/Ctrl-C -> 'done' (safer default: don't
    accidentally re-run the just-committed write)."""
    while True:
        try:
            s = input("\nDone with this goal, or is there more to do? (done / more) [done]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "done"
        if s in ("", "d", "done", "finish", "stop"):
            return "done"
        if s in ("m", "more", "continue", "next"):
            return "more"
        print(f"(didn't catch '{s}' — type done or more)")


def _prompt_next_goal() -> str:
    """Read the follow-up goal after the user picked 'more'. Blank -> ''."""
    try:
        return input("\nWhat else? (type the next instruction): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


# ---------------------------------------------------------------------------
# Drive ONE goal end-to-end
# ---------------------------------------------------------------------------

def _run_one_goal(token: str, goal: str) -> None:
    """Run the agent for `goal`, handle every outcome, looping internally for
    awaiting_confirm / ask_user. Returns when the goal is finished, cancelled,
    or hits an error/exhausted state. Never raises — crashes are printed and
    swallowed so the outer session keeps going.

    Holds `task_history` locally across re-invocations within this goal — that
    is the Stage 1 in-task continuity. It resets to () on every new top-level
    goal because this function is invoked fresh from session_loop each time
    (the task boundary)."""
    _section("GOAL")
    print(goal)

    try:
        with Session(engine) as session:
            try:
                user = _resolve_user(token, session)
            except AuthError as exc:
                print(f"\nAUTH FAILED: {exc}")
                return

            current_goal = goal
            task_history: tuple[TurnRecord, ...] = ()
            round_no = 0
            while True:
                round_no += 1
                if round_no > 1:
                    _section(f"AGENT RE-INVOCATION (round {round_no})")
                    print(f"goal:\n{current_goal}")
                    print(f"carrying {len(task_history)} prior turn(s) as memory")

                # --- Agent run ---
                try:
                    result = asyncio.run(run_agent(
                        session, user, current_goal,
                        prior_history=task_history,
                    ))
                except KeyboardInterrupt:
                    print("\n[interrupted — returning to goal prompt]")
                    return
                except Exception as exc:
                    print(f"\n[CRASH in agent — returning to goal prompt]")
                    print(f"  {type(exc).__name__}: {exc}")
                    traceback.print_exc()
                    return

                _print_agent_run(result)

                # --- Terminal outcomes: back to goal prompt (drop history) ---
                if result.kind in ("done", "error", "exhausted"):
                    return

                # --- ask_user: take a reply, re-invoke with it appended ---
                # Carry full result.history as prior_history (no synthesis —
                # the ask_user turn is already in the transcript; the user's
                # reply is folded into the goal text).
                if result.kind == "ask_user":
                    reply = _prompt_user_reply()
                    if not reply:
                        print("(no reply — abandoning this goal)")
                        return
                    current_goal = (
                        f"{goal}\n\n[your earlier reply to the agent's question "
                        f"'{result.question}']: {reply}"
                    )
                    task_history = result.history
                    continue

                # --- awaiting_confirm: yes / no / edit ---
                if result.kind == "awaiting_confirm":
                    choice = _prompt_confirm_choice()
                    if choice == "no":
                        print("(cancelled — returning to goal prompt)")
                        return
                    edits: dict[str, str] = {}
                    if choice == "edit":
                        edits = _prompt_edits()

                    try:
                        cr = confirm_prepared_action(
                            session, user,
                            action_id=result.prepared_action_id,
                            edits=edits,
                        )
                    except KeyboardInterrupt:
                        print("\n[interrupted during commit — returning to goal prompt]")
                        return
                    except Exception as exc:
                        print(f"\n[CRASH during commit — returning to goal prompt]")
                        print(f"  {type(exc).__name__}: {exc}")
                        traceback.print_exc()
                        return

                    _print_commit_result(cr)
                    if not cr.ok:
                        return    # commit failed -> stop, user can try a new goal

                    # The goal's write just landed. Do NOT auto-re-invoke with
                    # the same goal — the next LLM call would redo the whole
                    # task and the user confirming again would create a
                    # duplicate. Two protections together close this:
                    #   1. We only re-invoke if the user explicitly asks for
                    #      more, with a NEW instruction (handled below).
                    #   2. When we do re-invoke, we carry a synthetic COMMITTED
                    #      turn so the LLM sees the write is already done; the
                    #      system-prompt CONTINUATION rule then forbids re-
                    #      preparing it.
                    next_step = _prompt_post_commit_choice()
                    if next_step == "done":
                        return
                    follow_up = _prompt_next_goal()
                    if not follow_up:
                        print("(no follow-up — returning to goal prompt)")
                        return
                    committed = synth_commit_record(
                        result.history,
                        prepared_action_id=result.prepared_action_id,
                        capability=str(result.history[-1].action.get("capability", "")),
                        cr=cr,
                    )
                    task_history = result.history + (committed,)
                    current_goal = follow_up
                    continue

                # Unknown kind — shouldn't happen.
                print(f"\n[unknown result.kind: {result.kind!r} — abandoning]")
                return
    except Exception as exc:
        # Belt + braces: anything that escapes the inner handlers shouldn't kill the session.
        print(f"\n[UNEXPECTED — returning to goal prompt]")
        print(f"  {type(exc).__name__}: {exc}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Top-level session loop
# ---------------------------------------------------------------------------

def session_loop() -> None:
    print("=" * 72)
    print("  ask.py — interactive agent loop (real Gemini)")
    print("=" * 72)
    print("Type a goal at the prompt, or 'quit' to exit.")
    print("Awaiting-confirm prompts accept: yes / no / edit.")

    pending_goal: str | None = STARTING_GOAL.strip() or None
    while True:
        if pending_goal:
            goal: str | None = pending_goal
            pending_goal = None
            print(f"\n>>> (from STARTING_GOAL): {goal}")
        else:
            goal = _prompt_goal()
        if goal is None:
            print("\nbye.")
            return
        _run_one_goal(TOKEN, goal)


# ---------------------------------------------------------------------------
# Backward-compat shim for test_chat_api.py
# ---------------------------------------------------------------------------

async def answer_question(token: str, question: str, session: Session) -> dict[str, Any]:
    """COMPAT for the /test-chat endpoint. Maps a single agent run into the
    legacy {answer, generated_query, raw_rows, validation_error} shape."""
    user = _resolve_user(token, session)
    result = await run_agent(session, user, question)

    legacy: dict[str, Any] = {
        "answer": None, "generated_query": None,
        "raw_rows": [], "validation_error": None,
    }
    for tr in reversed(result.history):
        if tr.action.get("type") == "read":
            legacy["generated_query"] = tr.action.get("query")
            legacy["raw_rows"] = tr.observation_raw.get("rows", [])
            break

    if result.kind == "done":
        legacy["answer"] = result.answer
    elif result.kind == "awaiting_confirm":
        legacy["answer"] = (
            f"PREPARED action_id={result.prepared_action_id}\n"
            f"Preview: {result.preview}\n"
            f"(editable: {list(result.editable_fields)})"
        )
    elif result.kind == "ask_user":
        legacy["answer"] = f"ASK USER: {result.question}"
    else:
        legacy["validation_error"] = result.error
    return legacy


if __name__ == "__main__":
    session_loop()
