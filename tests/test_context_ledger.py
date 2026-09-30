"""Tests for the context ledger (scripts/context_ledger.py, via leverage-sensor.py).

Stdlib unittest, no network. Every fixture is a transcript built from the record
shapes Claude Code 2.1.280 writes -- hook_success, hook_additional_context,
instructions, nested_memory, the harness listings, forkedFrom copies -- so each
source the ledger bills has a case that can turn it red.

CONTEXT_LEDGER_SCRIPTS points the suite at another scripts/ directory. The mutation
check (tests/context-ledger.test.sh) uses it to run these tests against a copy with
one rule removed, and requires the named test to fail.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("CONTEXT_LEDGER_SCRIPTS") or REPO / "scripts")
TEMPLATE = REPO / "assets" / "templates" / "leverage-setpoints.yaml"


def load_sensor(scripts=SCRIPTS):
    spec = importlib.util.spec_from_file_location("leverage_sensor_under_test",
                                                  scripts / "leverage-sensor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SENSOR = load_sensor()
KG_RE = re.compile(SENSOR.DEFAULT_KG_READ, re.IGNORECASE)

ROLE_X = ("[role-x intake — P17 reflex applied]\n"
          "Knowledge-graph constraints to honor (core_claim):\n"
          "  - Default deploy target is Railway.  ·  [research/entities/persona/railway-deploy-default.md]\n"
          "  - Auth is Better Auth.  ·  [research/entities/persona/auth-better-auth.md]\n")
BOARD = ("Shared board facts (ctx scope broomva, 3 events, last event 2026-09-29T23:05:44Z). "
         "These rows were recorded by hooks in other sessions of this scope.\n"
         "This session: 809eea3c, branch main, cwd ~/broomva.\n"
         "- session 3403ef08, Paseo agent f5307ce7, branch main, cwd ~/broomva, status DONE.\n")
MEMORY_INDEX = ("# Memory Index\n"
                "- [Railway default](feedback_railway.md) — deploy target\n"
                "- [Spec](project_spec.md) — see docs/specs/ctx-core.md\n")
CLAUDE_MD = "# ~/broomva — Unified Workspace\nRead research/entities/pattern/bstack-engine.md first.\n"


class T:
    """A transcript: records in order, written as JSONL."""

    def __init__(self, sid="sess-0001"):
        self.sid = sid
        self.recs = []

    def _add(self, rec, fork=False):
        rec.setdefault("sessionId", self.sid)
        if fork:
            rec["forkedFrom"] = {"sessionId": "parent-0001", "messageUuid": f"m{len(self.recs)}"}
        self.recs.append(rec)
        return self

    def prompt(self, text="do the thing", pid=None, fork=False):
        rec = {"type": "user", "message": {"role": "user", "content": text}}
        if pid:
            rec["promptId"] = pid
        return self._add(rec, fork)

    def tool_result(self, pid=None):
        rec = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}
        if pid:
            rec["promptId"] = pid
        return self._add(rec)

    def hook(self, event, content, stdout=None, command="/x/scripts/some-hook.sh", fork=False):
        att = {"type": "hook_success", "hookName": event, "hookEvent": event,
               "toolUseID": "u1", "content": content,
               "stdout": content + "\n" if stdout is None else stdout,
               "stderr": "", "exitCode": 0, "command": command}
        return self._add({"type": "attachment", "attachment": att}, fork)

    def hook_json(self, event, text, command="/x/scripts/ctx-hook.sh"):
        out = json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})
        return self.hook(event, "", stdout=out, command=command)

    def hac(self, event, text):
        return self._add({"type": "attachment", "attachment": {
            "type": "hook_additional_context", "content": [text], "hookName": event,
            "toolUseID": event, "hookEvent": event}})

    def instructions(self, *files):
        return self._add({"type": "attachment", "attachment": {
            "type": "instructions",
            "files": [{"path": p, "type": t, "content": c} for p, t, c in files]}})

    def nested(self, path, content, kind="Project"):
        return self._add({"type": "attachment", "attachment": {
            "type": "nested_memory", "path": path,
            "content": {"path": path, "type": kind, "content": content}}})

    def skill_listing(self, content):
        return self._add({"type": "attachment", "attachment": {
            "type": "skill_listing", "content": content, "skillCount": 2}})

    def tool(self, name, fork=False, **inp):
        return self._add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t", "name": name, "input": inp}]}}, fork)

    def say(self, text):
        return self._add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": text}, {"type": "text", "text": text}]}})

    def write(self, d, name=None):
        p = Path(d) / f"{name or self.sid}.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in self.recs))
        return str(p)


def nb(s):
    return len(s.encode("utf-8"))


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def ledger(self, *transcripts):
        paths = [t.write(self.dir, f"s{i}") for i, t in enumerate(transcripts)]
        return SENSOR.context_ledger_block(paths, KG_RE)

    def src(self, cl, key):
        self.assertIn(key, cl["sources"], f"{key} missing; have {list(cl['sources'])}")
        return cl["sources"][key]


# --- 1. injection bytes, by source -----------------------------------------------
class Sources(LedgerCase):
    def test_role_x_intake_content_and_stdout_counted_once(self):
        # The record carries the text in `content` AND `stdout`. Billing both doubles
        # every role-x turn -- the one rule the baseline flagged by name.
        cl = self.ledger(T().prompt(pid="p1").hook("UserPromptSubmit", ROLE_X))
        s = self.src(cl, "hook:UserPromptSubmit:role-x-intake")
        self.assertEqual((s["events"], s["bytes"]), (1, nb(ROLE_X)))
        self.assertEqual(cl["totals"]["bytes"], nb(ROLE_X))

    def test_ctx_board_json_and_its_twin_counted_once(self):
        # JSON additionalContext lands twice: in the hook_success stdout and again as
        # a hook_additional_context record. The two share no id.
        for order in ("json-first", "twin-first"):
            t = T().prompt(pid="p1")
            if order == "json-first":
                t.hook_json("SessionStart", BOARD).hac("SessionStart", BOARD)
            else:
                t.hac("SessionStart", BOARD).hook_json("SessionStart", BOARD)
            cl = self.ledger(t)
            s = self.src(cl, "hook:SessionStart:ctx-core-board")
            self.assertEqual((s["events"], s["bytes"]), (1, nb(BOARD)), order)

    def test_json_additional_context_with_no_twin_still_counts(self):
        cl = self.ledger(T().hook_json("SessionStart", BOARD))
        self.assertEqual(self.src(cl, "hook:SessionStart:ctx-core-board")["events"], 1)

    def test_repeated_identical_injections_all_count(self):
        # Pairing must not collapse a hook that says the same thing on two turns.
        t = T()
        for pid in ("p1", "p2"):
            t.prompt(pid=pid).hook_json("UserPromptSubmit", "same text").hac("UserPromptSubmit", "same text")
        cl = self.ledger(t)
        # The hook_success half names its command, so the pair is tagged by it.
        self.assertEqual(self.src(cl, "hook:UserPromptSubmit:ctx-hook.sh")["events"], 2)

    def test_known_headers_tag_their_source(self):
        cl = self.ledger(T()
                         .hook("SessionStart", "[self-improvement loop] 12 sessions / 7d\nAll graded.")
                         .hook("SessionStart", "[auth pre-flight] 1 credential surface needs attention"))
        self.src(cl, "hook:SessionStart:self-improvement-loop")
        self.src(cl, "hook:SessionStart:auth-preflight")

    def test_unknown_header_falls_back_to_script_name(self):
        cl = self.ledger(T().hook("SessionStart", "[bstack P7] Skill freshness check overdue",
                                  command='bash "${ROOT}/scripts/skill-freshness-hook.sh"'))
        self.src(cl, "hook:SessionStart:skill-freshness-hook.sh")

    def test_stop_stdout_is_recorded_not_injected(self):
        text = "Self-improvement loop — 32 sessions over 7d"
        cl = self.ledger(T().prompt(pid="p1").hook("Stop", text,
                                                   command="python3 -I /x/scripts/leverage-sensor.py"))
        self.assertEqual(cl["totals"]["bytes"], 0)
        self.assertEqual(cl["not_injected"]["hook:Stop:leverage-sensor.py"]["bytes"], nb(text))

    def test_system_message_and_empty_json_are_not_injected(self):
        cl = self.ledger(T().prompt(pid="p1")
                         .hook("SessionStart", "", stdout='{"systemMessage": "[bstack P7] overdue"}\n')
                         .hook("PreToolUse", "", stdout="{}\n"))
        self.assertEqual(cl["sources"], {})

    def test_instruction_files_are_billed(self):
        cl = self.ledger(T()
                         .instructions(("/w/CLAUDE.md", "Project", CLAUDE_MD),
                                       ("/h/.claude/projects/w/memory/MEMORY.md", "AutoMem", MEMORY_INDEX))
                         .prompt(pid="p1")
                         .nested("/w/core/CLAUDE.md", "# core\n" * 10))
        self.assertEqual(self.src(cl, "memory:Project")["bytes"], nb(CLAUDE_MD))
        self.assertEqual(self.src(cl, "memory:AutoMem")["bytes"], nb(MEMORY_INDEX))
        self.assertEqual(self.src(cl, "memory:nested-Project")["bytes"], nb("# core\n" * 10))
        self.assertIs(cl["memory_observable"], True)

    def test_memory_unobservable_is_said_not_zeroed(self):
        cl = self.ledger(T().prompt(pid="p1").hook("UserPromptSubmit", ROLE_X))
        self.assertIs(cl["memory_observable"], False)

    def test_harness_listing_is_billed(self):
        listing = "- kg\n- deep-research\n"
        cl = self.ledger(T().skill_listing(listing))
        self.assertEqual(self.src(cl, "harness:skill_listing")["bytes"], nb(listing))
        self.assertEqual(cl["totals"]["by_group"]["harness"]["bytes"], nb(listing))

    def test_fork_copies_are_skipped(self):
        # A fork starts with a copy of its parent's history. Those injections were
        # made (and billed) in the parent.
        cl = self.ledger(T()
                         .prompt(pid="p0", fork=True)
                         .hook("UserPromptSubmit", ROLE_X, fork=True)
                         .prompt(pid="p1")
                         .hook("UserPromptSubmit", ROLE_X))
        self.assertEqual(self.src(cl, "hook:UserPromptSubmit:role-x-intake")["events"], 1)
        self.assertEqual(cl["fork_copied_records_skipped"], 2)
        self.assertEqual(cl["turns"], 1)

    def test_bytes_are_utf8_not_characters(self):
        text = "[role-x intake — P17] → ok"
        cl = self.ledger(T().prompt(pid="p1").hook("UserPromptSubmit", text))
        self.assertEqual(cl["totals"]["bytes"], nb(text))
        self.assertGreater(nb(text), len(text))

    def test_token_estimate_is_labelled_as_one(self):
        cl = self.ledger(T().prompt(pid="p1").hook("UserPromptSubmit", ROLE_X))
        self.assertEqual(cl["totals"]["est_tokens"], nb(ROLE_X) // 4)
        self.assertIn("estimate", cl["token_estimate"])

    def test_per_turn_and_per_session_distributions(self):
        # 100 B before the first prompt folds into turn one: turns are 1100, 1000, 1000.
        t = T().hook("SessionStart", "[auth pre-flight] " + "a" * 82)
        for pid in ("p1", "p2", "p3"):
            t.prompt(pid=pid).hook("UserPromptSubmit", "[role-x intake]" + "b" * 985).tool_result(pid)
        cl = self.ledger(t)
        self.assertEqual(cl["turns"], 3)
        self.assertEqual(cl["totals"]["per_turn"], {"n": 3, "median": 1000, "p90": 1100})
        self.assertEqual(cl["totals"]["per_session"], {"n": 1, "median": 3100, "p90": 3100})
        rx = self.src(cl, "hook:UserPromptSubmit:role-x-intake")
        self.assertEqual(rx["per_event"]["median"], 1000)

    def test_turns_without_prompt_id_count_human_prompts(self):
        cl = self.ledger(T().prompt().tool_result().prompt().hook("UserPromptSubmit", ROLE_X))
        self.assertEqual(cl["turns"], 2)


# --- 2. pointer follow-through ----------------------------------------------------
class FollowThrough(LedgerCase):
    RAILWAY = "/Users/x/broomva/research/entities/persona/railway-deploy-default.md"

    def kg(self, cl):
        return cl["follow_through"]["by_kind"]["kg"]

    def test_true_positive_read_after_injection(self):
        cl = self.ledger(T().prompt(pid="p1").hook("UserPromptSubmit", ROLE_X)
                         .tool("Read", file_path=self.RAILWAY))
        self.assertEqual((self.kg(cl)["injected"], self.kg(cl)["followed"]), (2, 1))
        by = cl["follow_through"]["by_source"]["hook:UserPromptSubmit:role-x-intake"]["kg"]
        self.assertEqual(by["followed"], 1)

    def test_true_negative_pointer_never_opened(self):
        cl = self.ledger(T().prompt(pid="p1").hook("UserPromptSubmit", ROLE_X)
                         .tool("Read", file_path="/Users/x/broomva/README.md")
                         .tool("Grep", pattern="railway", path="/Users/x/broomva/src"))
        self.assertEqual((self.kg(cl)["injected"], self.kg(cl)["followed"]), (2, 0))

    def test_open_before_injection_is_not_follow_through(self):
        cl = self.ledger(T().prompt(pid="p1").tool("Read", file_path=self.RAILWAY)
                         .hook("UserPromptSubmit", ROLE_X))
        self.assertEqual(self.kg(cl)["followed"], 0)
        self.assertEqual(cl["follow_through"]["opened"]["kg"]["opened_before_injection_only"], 1)

    def test_shell_read_and_kg_load_count(self):
        cl = self.ledger(T().prompt(pid="p1").hook("UserPromptSubmit", ROLE_X)
                         .tool("Bash", command="sed -n '1,40p' research/entities/persona/railway-deploy-default.md")
                         .tool("Bash", command='python3 ~/.claude/skills/kg/scripts/kg.py load "auth-better-auth" --n 3'))
        self.assertEqual(self.kg(cl)["followed"], 2)

    def test_shell_writes_do_not_count(self):
        cl = self.ledger(T().prompt(pid="p1").hook("UserPromptSubmit", ROLE_X)
                         .tool("Bash", command="git add research/entities/persona/railway-deploy-default.md")
                         .tool("Bash", command="cat > research/entities/persona/auth-better-auth.md <<'EOF'\nbody\nEOF"))
        self.assertEqual(self.kg(cl)["followed"], 0)

    def test_self_directed_open_is_counted(self):
        cl = self.ledger(T().prompt(pid="p1").hook("UserPromptSubmit", ROLE_X)
                         .tool("Read", file_path="/w/research/entities/concept/never-injected.md"))
        op = cl["follow_through"]["opened"]["kg"]
        self.assertEqual((op["opened"], op["opened_never_injected"], op["self_directed_share"]), (1, 1, 1.0))

    def test_ctx_board_command_follows_ctx_pointers(self):
        t = T().prompt(pid="p1").hook_json("SessionStart", BOARD).hac("SessionStart", BOARD)
        cl = self.ledger(t)
        ctx = cl["follow_through"]["by_kind"]["ctx"]
        # the other session and its agent; never the reader's own "This session:" id
        self.assertEqual((ctx["injected"], ctx["followed"]), (2, 0))
        cl = self.ledger(t.tool("Bash", command="python3 -I ~/s/ctx-core/scripts/ctx.py board"))
        self.assertEqual(cl["follow_through"]["by_kind"]["ctx"]["followed"], 2)

    def test_ctx_id_in_an_mcp_id_field_follows(self):
        cl = self.ledger(T().prompt(pid="p1").hook_json("SessionStart", BOARD)
                         .tool("mcp__paseo__get_agent_status", agentId="f5307ce7-0000-4000-8000-000000000000"))
        self.assertEqual(cl["follow_through"]["by_kind"]["ctx"]["followed"], 1)

    def test_memory_index_link_follow_through(self):
        cl = self.ledger(T()
                         .instructions(("/h/.claude/projects/w/memory/MEMORY.md", "AutoMem", MEMORY_INDEX))
                         .prompt(pid="p1")
                         .tool("Read", file_path="/h/.claude/projects/w/memory/feedback_railway.md"))
        mem = cl["follow_through"]["by_kind"]["memory"]
        self.assertEqual((mem["injected"], mem["followed"]), (2, 1))
        self.assertEqual(cl["follow_through"]["by_kind"]["specs"]["injected"], 1)


# --- h ⟂ U: the ledger reads no prose ---------------------------------------------
PROSE_FIELDS = {"Agent": ("prompt", "description"), "Write": ("content",),
                "Edit": ("old_string", "new_string"), "SendMessage": ("message",),
                "WebFetch": ("prompt",), "mcp__paseo__send_agent_prompt": ("prompt",)}


def rewrite_prose(recs, fill):
    """Every assistant text/thinking block, every prose field of a tool input, and
    every user prompt's words replaced. Structure (types, paths, commands, ids) kept."""
    out = copy.deepcopy(recs)
    for r in out:
        if r.get("type") == "assistant":
            for it in r["message"]["content"]:
                if it.get("type") == "text":
                    it["text"] = fill
                elif it.get("type") == "thinking":
                    it["thinking"] = fill
                elif it.get("type") == "tool_use":
                    for k in PROSE_FIELDS.get(it["name"], ()):
                        if k in it["input"]:
                            it["input"][k] = fill
        elif r.get("type") == "user" and isinstance(r["message"]["content"], str):
            r["message"]["content"] = fill
    return out


class NoProse(LedgerCase):
    def rich(self):
        return (T().instructions(("/w/CLAUDE.md", "Project", CLAUDE_MD),
                                 ("/h/.claude/projects/w/memory/MEMORY.md", "AutoMem", MEMORY_INDEX))
                .prompt("look at research/entities/persona/auth-better-auth.md", pid="p1")
                .hook("UserPromptSubmit", ROLE_X).hook_json("SessionStart", BOARD).hac("SessionStart", BOARD)
                .say("I read research/entities/persona/auth-better-auth.md and ran WebSearch; "
                     "see docs/specs/ctx-core.md and ~/.claude/projects/w/memory/project_spec.md, session 3403ef08")
                .tool("Agent", prompt="Read research/entities/persona/auth-better-auth.md then kg load it",
                      description="deep research 3403ef08", subagent_type="Explore")
                .tool("Write", file_path="/tmp/notes.md",
                      content="research/entities/persona/auth-better-auth.md docs/specs/ctx-core.md")
                .tool("SendMessage", to="peer", message="ctx board; open 3403ef08")
                .tool("Read", file_path="/Users/x/broomva/research/entities/persona/railway-deploy-default.md")
                .tool("Bash", command="cat docs/specs/ctx-core.md"))

    def test_prose_naming_a_pointer_is_not_follow_through(self):
        cl = self.ledger(T().prompt(pid="p1").hook("UserPromptSubmit", ROLE_X)
                         .say("Now reading research/entities/persona/auth-better-auth.md via kg load")
                         .tool("Agent", prompt="cat research/entities/persona/auth-better-auth.md",
                               description="d", subagent_type="Explore")
                         .tool("Write", file_path="/tmp/x.md",
                               content="research/entities/persona/auth-better-auth.md"))
        self.assertEqual(cl["follow_through"]["by_kind"]["kg"]["followed"], 0)
        self.assertEqual(cl["reflexes"]["any"]["with_reflex"], 0)

    def test_ledger_is_invariant_under_prose_replacement(self):
        base = self.rich()
        alt = T()
        alt.recs = rewrite_prose(base.recs, "unrelated words, nothing here names a path")
        a, b = self.ledger(base), self.ledger(alt)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        # Not vacuous: the fixture has live follow-through and reflexes to lose.
        self.assertGreater(a["follow_through"]["overall"]["followed"], 0)

    def test_invariance_check_can_see_a_structural_change(self):
        # Positive control: the same comparison DOES move when a structural field does.
        base = self.rich()
        alt = T()
        alt.recs = copy.deepcopy(base.recs)
        for r in alt.recs:
            for it in (r.get("message") or {}).get("content") or []:
                if isinstance(it, dict) and it.get("name") == "Read":
                    it["input"]["file_path"] = "/elsewhere/readme.md"
        self.assertNotEqual(json.dumps(self.ledger(base), sort_keys=True),
                            json.dumps(self.ledger(alt), sort_keys=True))


# --- 3. retrieval reflexes --------------------------------------------------------
class Reflexes(LedgerCase):
    def test_split_by_whether_an_injection_pointed_there(self):
        pointed = T("a").prompt(pid="p1").hook("UserPromptSubmit", ROLE_X).tool(
            "Read", file_path="/w/research/entities/concept/x.md")
        unpointed_read = T("b").prompt(pid="p1").tool("Read", file_path="/w/research/entities/concept/y.md")
        unpointed_idle = T("c").prompt(pid="p1").tool("WebSearch", query="q")
        kg = self.ledger(pointed, unpointed_read, unpointed_idle)["reflexes"]["kg"]
        self.assertEqual(kg["pointed"], {"sessions": 1, "with_reflex": 1, "rate": 1.0})
        self.assertEqual(kg["unpointed"], {"sessions": 2, "with_reflex": 1, "rate": 0.5})

    def test_specs_memory_and_research_reflexes(self):
        cl = self.ledger(T().prompt(pid="p1")
                         .tool("Read", file_path="/w/docs/specs/ctx-core.md")
                         .tool("Bash", command="cat ~/.claude/projects/w/memory/feedback_x.md")
                         .tool("Skill", skill="deep-research", args="topic"))
        rf = cl["reflexes"]
        for kind in ("specs", "memory", "research"):
            self.assertEqual(rf[kind]["unpointed"]["with_reflex"], 1, kind)
        self.assertEqual(rf["kg"]["unpointed"]["with_reflex"], 0)

    def test_harness_listing_is_not_a_pointer(self):
        cl = self.ledger(T().skill_listing("- deep-research\n- kg\n").prompt(pid="p1"))
        self.assertEqual(cl["reflexes"]["research"]["pointed"]["sessions"], 0)


# --- liveness and wiring ----------------------------------------------------------
class Liveness(LedgerCase):
    def test_no_files_is_no_data(self):
        cl = SENSOR.context_ledger_block([], KG_RE)
        self.assertEqual(cl["status"], "no_data")

    def test_sessions_with_no_injected_byte_are_blind(self):
        cl = self.ledger(T().prompt(pid="p1").tool("Read", file_path="/w/a.py").tool_result("p1"))
        self.assertEqual(cl["status"], "blind")
        self.assertIn("0 injected bytes", cl["status_reason"])
        self.assertTrue(all(v is None for v in cl["headline"].values()))

    def test_live_ledger_fills_its_headline(self):
        cl = self.ledger(T().prompt(pid="p1").hook("UserPromptSubmit", ROLE_X)
                         .tool("Read", file_path=FollowThrough.RAILWAY))
        self.assertEqual(cl["status"], "live")
        self.assertEqual(cl["headline"], {
            "cl1_injected_bytes_per_turn_p50": nb(ROLE_X),
            "cl2_kg_pointer_follow_through_rate": 0.5,
            "cl3_retrieval_reflex_session_rate": 1.0})


class Cli(unittest.TestCase):
    """The sensor as the hooks run it: `python3 -I`, a workspace, a transcript glob."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = self.root / "ws"
        (self.ws / ".control").mkdir(parents=True)
        self.tr = self.root / "tr"
        self.tr.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def run_sensor(self, scripts=SCRIPTS, *extra):
        out = subprocess.run(
            ["python3", "-I", str(scripts / "leverage-sensor.py"), "--workspace", str(self.ws),
             "--transcripts", str(self.tr / "*.jsonl"), "--window", "3650", "--no-store", *extra],
            capture_output=True, text=True, cwd=str(self.root), timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def live_transcript(self):
        (T().prompt(pid="p1").hook("UserPromptSubmit", ROLE_X)
         .tool("Read", file_path=FollowThrough.RAILWAY).tool_result("p1")
         .write(self.tr))

    def test_block_and_shadow_rows_reach_the_record(self):
        self.live_transcript()
        shutil.copy(TEMPLATE, self.ws / ".control" / "leverage-setpoints.yaml")
        rec = json.loads(self.run_sensor(SCRIPTS, "--json"))
        self.assertEqual(rec["context_ledger"]["status"], "live")
        rows = {r["key"]: r for r in rec["results"]}
        for key in ("cl1_injected_bytes_per_turn_p50", "cl2_kg_pointer_follow_through_rate",
                    "cl3_retrieval_reflex_session_rate"):
            self.assertEqual(rows[key]["status"], "shadow", key)
            self.assertEqual(rows[key]["level"], "L2", key)
        self.assertFalse(rec["worst"] and rec["worst"]["key"].startswith("cl"))

    def test_brief_carries_no_ledger_breakdown(self):
        self.live_transcript()
        brief = self.run_sensor(SCRIPTS, "--brief")
        self.assertNotIn("context ledger", brief)
        self.assertIn("context ledger", self.run_sensor(SCRIPTS))

    def test_blind_sensor_nulls_the_headline_too(self):
        # A read with no tool_result and no edit is a blind sensor read; the ledger's
        # headline must not survive it as a measurement.
        T().prompt(pid="p1").hook("UserPromptSubmit", ROLE_X).write(self.tr)
        rec = json.loads(self.run_sensor(SCRIPTS, "--json"))
        self.assertFalse(rec["closure"]["sensor_live"])
        self.assertIsNone(rec["metrics"]["cl1_injected_bytes_per_turn_p50"])

    def test_a_broken_ledger_is_an_error_and_spares_m1_to_m6(self):
        self.live_transcript()
        broken = self.root / "scripts"
        broken.mkdir()
        shutil.copy(SCRIPTS / "leverage-sensor.py", broken / "leverage-sensor.py")
        (broken / "context_ledger.py").write_text("raise RuntimeError('ledger import exploded')\n")
        rec = json.loads(self.run_sensor(broken, "--json"))
        self.assertEqual(rec["context_ledger"]["status"], "error")
        self.assertIn("ledger import exploded", rec["context_ledger"]["status_reason"])
        self.assertIsNotNone(rec["metrics"]["m2_tool_error_rate"])
        self.assertNotIn("cl1_injected_bytes_per_turn_p50", rec["metrics"])


class Doctor(unittest.TestCase):
    """doctor §29 fails on a DEAD ledger and never grades a value (BRO-1696)."""

    DOCTOR = REPO / "scripts" / "doctor.sh"

    def section(self, state):
        with tempfile.TemporaryDirectory() as ws:
            (Path(ws) / ".control").mkdir()
            if state is not None:
                body = state if isinstance(state, str) else json.dumps(state)
                (Path(ws) / ".control" / "leverage-state.json").write_text(body)
            env = dict(os.environ, BROOMVA_WORKSPACE=ws)
            out = subprocess.run(["bash", str(self.DOCTOR)], capture_output=True, text=True,
                                 env=env, cwd=ws, timeout=120).stdout
        lines = out.splitlines()
        start = next((i for i, ln in enumerate(lines) if ln.startswith("29. Context ledger")), None)
        self.assertIsNotNone(start, "doctor never rendered §29")
        body = []
        for ln in lines[start + 1:]:
            if not ln.strip():
                break
            body.append(ln)
        return "\n".join(body)

    def ledger(self, **kw):
        cl = {"schema": 1, "status": "live", "status_reason": None, "sessions": 3, "turns": 9,
              "headline": {"cl1_injected_bytes_per_turn_p50": 5000.0,
                           "cl2_kg_pointer_follow_through_rate": 0.01,
                           "cl3_retrieval_reflex_session_rate": 0.5},
              "totals": {"per_session": {"median": 90000}, "per_turn": {"median": 5000.0}},
              "follow_through": {"by_kind": {"kg": {"followed": 1, "injected": 99}}}}
        cl.update(kw)
        return {"context_ledger": cl}

    def test_live_is_ok_and_names_the_numbers(self):
        out = self.section(self.ledger())
        self.assertIn("[ok] context ledger (shadow, not graded)", out)
        self.assertIn("1/99", out)

    def test_blind_is_a_gap(self):
        out = self.section(self.ledger(status="blind", status_reason="read 4 sessions, 0 injected bytes"))
        self.assertIn("[gap] context ledger is BLIND", out)

    def test_error_is_a_gap(self):
        out = self.section(self.ledger(status="error", status_reason="RuntimeError: boom"))
        self.assertIn("[gap] context ledger FAILED — RuntimeError: boom", out)

    def test_live_but_all_null_is_a_gap(self):
        out = self.section(self.ledger(headline=dict.fromkeys(
            ("cl1_injected_bytes_per_turn_p50", "cl2_kg_pointer_follow_through_rate",
             "cl3_retrieval_reflex_session_rate"))))
        self.assertIn("[gap] context ledger is DEAD", out)

    def test_unknown_status_is_a_gap(self):
        self.assertIn("[gap]", self.section(self.ledger(status="liveish")))

    def test_malformed_block_is_a_gap(self):
        self.assertIn("[gap] context ledger state unreadable", self.section({"context_ledger": [1, 2]}))
        self.assertIn("[gap] context ledger state unreadable", self.section("{not json"))

    def test_no_data_and_old_state_are_info(self):
        self.assertIn("[info] context ledger: no session read",
                      self.section(self.ledger(status="no_data", status_reason="no session file")))
        out = self.section({"measured_at": "2026-09-29T00:00:00+00:00", "metrics": {}})
        self.assertIn("[info] leverage-state.json has no context_ledger block", out)
        self.assertNotIn("[gap]", out)


if __name__ == "__main__":
    unittest.main()
