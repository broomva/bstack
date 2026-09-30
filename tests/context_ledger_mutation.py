"""Mutation proof for the context ledger: each rule it states is load-bearing.

For each mutant: copy scripts/leverage-sensor.py and scripts/context_ledger.py into a
temp dir, remove exactly one rule, and run the one test named to kill it against the
copy (CONTEXT_LEDGER_SCRIPTS). The test MUST fail by ASSERTION: a mutant that does not
compile, or a test that errors, would also go red and prove nothing, so the mutant is
compiled first and the verdict must read `failures=`, never `errors=`. A positive
control runs every killing test against an unmutated copy first, so a red here is the
mutant's doing and not the copy's.

The first mutant is the one the rule was written for: count a hook_success record's
`stdout` on top of its `content`, which bills every role-x turn twice. Every other
mutant removes one rule the ledger, the sensor or doctor §29 states, grouped below as
billing, no-prose, pointers, liveness, sensor and doctor. A rule with no mutant here
is a rule nothing proves.

    python3 tests/context_ledger_mutation.py
"""

import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FILES = ("leverage-sensor.py", "context_ledger.py")

# (label, file, anchor, replacement, killing test)
L, S, D = "context_ledger.py", "leverage-sensor.py", "doctor.sh"
MUTANTS = (
    # -- billing: each injection once ------------------------------------------------
    ("dedup: stdout billed on top of content", L,
     '        return content, "content"\n',
     '        return content + (att.get("stdout") or ""), "content"\n',
     "Sources.test_role_x_intake_content_and_stdout_counted_once"),
    ("dedup: JSON additionalContext and its twin both billed", L,
     "                twin_of[j], twin_of[k] = k, j\n",
     "                pass\n",
     "Sources.test_ctx_board_json_and_its_twin_counted_once"),
    ("twin pairing crosses turns", L,
     '                key = (c["turn"], c["event"], c["text"].strip())\n',
     '                key = (c["event"], c["text"].strip())\n',
     "Sources.test_pairing_does_not_cross_turns"),
    ("a batched twin record billed as one text", L,
     "        entries = body if isinstance(body, list) else [body]\n",
     '        entries = ["\\n".join(e for e in body if isinstance(e, str))] if isinstance(body, list) else [body]\n',
     "Sources.test_batched_twin_record_bills_each_hook_once"),
    ("a block reason dropped", L,
     '    if payload.get("decision") == "block" and isinstance(reason, str) and reason.strip():\n',
     "    if False:\n",
     "Sources.test_block_reason_is_injected"),
    ("a systemMessage billed as injected", L,
     '    # `{}` and `{"systemMessage": ...}` reach the user\'s screen, not the model.\n    return "", None\n',
     '    msg = payload.get("systemMessage")\n    return (msg, "json") if isinstance(msg, str) and msg else ("", None)\n',
     "Sources.test_system_message_and_empty_json_are_not_injected"),
    ("fork copies billed again", L,
     '        if obj.get("forkedFrom"):\n',
     "        if False:\n",
     "Sources.test_fork_copies_are_skipped"),
    ("Stop stdout billed as injected", L,
     'PLAIN_STDOUT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit"})\n',
     'PLAIN_STDOUT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "Stop"})\n',
     "Sources.test_stop_stdout_is_recorded_not_injected"),
    # -- h ⟂ U: no prose read as a use -------------------------------------------------
    ("assistant text read as an action", L,
     '                if isinstance(it, dict) and it.get("type") == "tool_use":\n',
     '                if isinstance(it, dict) and it.get("type") == "text":\n'
     '                    self._on_tool_use("Bash", {"command": it.get("text") or ""}, idx, ts)\n'
     '                if isinstance(it, dict) and it.get("type") == "tool_use":\n',
     "NoProse.test_ledger_is_invariant_under_prose_replacement"),
    ("Agent prompt read as an action", L,
     '        if name == "Read":\n',
     '        if name == "Agent":\n'
     '            self._on_tool_use("Bash", {"command": str(inp.get("prompt") or "")}, idx, ts)\n'
     '        if name == "Read":\n',
     "NoProse.test_prose_naming_a_pointer_is_not_follow_through"),
    ("kg load matched anywhere in a command", L,
     "            for seg in shell_segments(cmd):\n",
     '            for m in re.finditer(r"kg(?:\\.py)?\\s+load\\s+(\\S+)", cmd):\n'
     "                self._kg_load([m.group(1)], idx, ts)\n"
     "            for seg in shell_segments(cmd):\n",
     "NoProse.test_commit_message_is_not_a_use"),
    ("shell reads dropped", L,
     '            for target in self.vocab["shell_read_targets"](cmd.lower()):\n',
     "            for target in ():\n",
     "FollowThrough.test_shell_read_and_kg_load_count"),
    ("shell reads leak into the strict figure", L,
     "        tables = (self.uses,) if tools_only else (self.uses, self.shell_uses)\n",
     "        tables = (self.uses, self.shell_uses)\n",
     "NoProse.test_shell_reads_carry_m5s_known_over_counts_only_in_the_inclusive_figure"),
    ("a heredoc body read as commands", L,
     '            if tok.startswith("<<"):\n                break\n',
     "",
     "NoProse.test_heredoc_body_is_not_a_use"),
    ("a comment swallows its newline (shlex default)", L,
     '    lex.commenters = ""\n',
     "",
     "NoProse.test_comment_does_not_join_prose"),
    ("Grep tool pattern read for ids", L,
     '            self._read_path(str(inp.get("path") or ""), idx, ts)\n        elif name == "Glob":\n',
     '            self._read_path(str(inp.get("path") or "") + " " + str(inp.get("pattern") or ""), idx, ts)\n'
     '        elif name == "Glob":\n',
     "NoProse.test_grep_pattern_is_not_a_use"),
    ("Skill free text read as a load", L,
     '                if args[:1] == ["load"]:\n                    self._kg_load(args[1:], idx, ts)\n            if skill in RESEARCH_SKILLS:\n',
     '                if args:\n                    self._kg_load(args, idx, ts)\n            if skill in RESEARCH_SKILLS:\n',
     "NoProse.test_skill_prose_args_are_not_a_use"),
    ("every MCP field read, prose included", L,
     '                if isinstance(v, str) and k.lower().endswith("id"):\n',
     "                if isinstance(v, str):\n",
     "NoProse.test_mcp_prose_field_is_not_a_use"),
    # -- pointers and follow-through ---------------------------------------------------
    ("a bare query word names a one-word slug", L,
     '                    ("-" in slug and slug in toks)\n',
     "                    (slug in toks)\n",
     "FollowThrough.test_single_word_slug_needs_its_type"),
    ("an open BEFORE the injection counts as follow-through", L,
     "        if u and later(u[1], u[2]):\n",
     "        if u:\n",
     "FollowThrough.test_open_before_injection_is_not_follow_through"),
    ("the board's own session row counts as a pointer", L,
     'CTX_ROW_RE = re.compile(r"^- session ([0-9a-f]{8})\\b(?:, Paseo agent ([0-9a-f]{8})\\b)?", re.MULTILINE)\n',
     'CTX_ROW_RE = re.compile(r"session:? ([0-9a-f]{8})\\b(?:, Paseo agent ([0-9a-f]{8})\\b)?", re.MULTILINE)\n',
     "FollowThrough.test_ctx_board_command_follows_ctx_pointers"),
    ("MEMORY.md itself counts as a pointer", L,
     "        found[\"memory\"].discard(MEMORY_INDEX)\n",
     "",
     "FollowThrough.test_memory_index_link_follow_through"),
    ("the specs naming template counts as a pointer", L,
     'SPECS_PATH_RE = re.compile(r"\\bdocs/specs/[\\w./-]+\\.(?:md|html?|pdf|txt|ya?ml|json|rst)\\b")\n',
     'SPECS_PATH_RE = re.compile(r"\\bdocs/specs/[\\w./-]*[\\w-]")\n',
     "FollowThrough.test_specs_pointer_names_a_file"),
    ("a harness catalog counts as a pointer", L,
     '    if source.startswith("harness:"):\n        return found, False\n',
     "",
     "Reflexes.test_harness_listing_is_not_a_pointer"),
    # -- liveness: a dark source must not read as live ---------------------------------
    ("a ledger with no billed byte reports live", L,
     "    elif total == 0 and not truncated:\n",
     "    elif False:\n",
     "Liveness.test_sessions_with_no_injected_byte_are_blind"),
    ("a shown record that bills nothing is not counted", L,
     "                self.unbilled_visible[at] = self.unbilled_visible.get(at, 0) + 1\n",
     "                pass\n",
     "Liveness.test_partial_when_a_shown_record_bills_nothing"),
    ("hook output with no event is not counted", L,
     '                    if c["event"] is None:\n                        self.unknown_event += 1\n',
     "",
     "Liveness.test_partial_on_hook_output_with_no_event"),
    ("an unknown shown type filed as known", L,
     "                bucket = (self.unattributed_visible if at in KNOWN_UNBILLED\n"
     "                          else self.unknown_visible)\n",
     "                bucket = self.unattributed_visible\n",
     "Liveness.test_unknown_visible_type_is_reported_not_alarmed"),
    ("files that parse to no record read as no_data", L,
     "    elif sessions and unparsed == len(sessions) and not truncated:\n",
     "    elif False:\n",
     "Liveness.test_files_with_no_parseable_record_are_blind"),
    ("an unparseable file among good ones ignored", L,
     "        s.unparsed = not (s.own_records or s.fork_copies or s.malformed) and os.path.getsize(path) > 0\n",
     "        s.unparsed = False\n",
     "Liveness.test_one_unparseable_file_is_partial"),
    ("the record guard re-raises on an unhashable type", L,
     "            if isinstance(at, str) and at in BILLED_TYPES:\n",
     "            if at in BILLED_TYPES:\n",
     "Liveness.test_unhashable_attachment_type_is_counted_not_fatal"),
    ("an unreadable part of a billed record skipped silently", L,
     "                    if text is None:\n                        self.unreadable_parts += 1\n                        continue\n",
     "                    if text is None:\n                        continue\n",
     "Liveness.test_unreadable_part_of_a_billed_record_is_partial"),
    ("the time budget ignored", L,
     "        if deadline is not None and time.monotonic() > deadline:\n",
     "        if False:\n",
     "Liveness.test_time_budget_cuts_loudly"),
    ("a partial window stores its headline", L,
     '    if block["status"] == "live":\n        # A partial window',
     '    if block["status"] in ("live", "partial"):\n        # A partial window',
     "Liveness.test_partial_stores_no_headline"),
    ("a workflow subagent assigned to the wrong parent", L,
     '    return parts[parts.index("subagents") - 1] if "subagents" in parts[1:] else None\n',
     "    return os.path.basename(os.path.dirname(os.path.dirname(path)))\n",
     "Subagents.test_workflow_subagent_maps_to_its_parent"),
    ("a Glob counted as an open", L,
     '                            idx, ts, opened=False)\n',
     '                            idx, ts, opened=True)\n',
     "FollowThrough.test_glob_is_a_search_not_an_open"),
    ("shell keywords not skipped", L,
     "        if _ASSIGN_RE.match(tok) or tok in _SHELL_KEYWORDS:\n",
     "        if _ASSIGN_RE.match(tok):\n",
     "FollowThrough.test_kg_load_inside_a_compound_command_counts"),
    ("redirect targets read as query words", L,
     '        if a and all(c in "<>&" for c in a):\n            skip = True\n            continue\n',
     "",
     "FollowThrough.test_redirects_are_not_query_words"),
    ("every source credited from the first injection", L,
     "                    b[\"followed\"] += s.used(kind, key, after_idx=first)\n",
     "                    b[\"followed\"] += hit\n",
     "FollowThrough.test_each_source_is_credited_from_its_own_injection"),
    ("plain PreToolUse stdout billed as injected", L,
     'PLAIN_STDOUT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit"})\n',
     'PLAIN_STDOUT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "PreToolUse"})\n',
     "Sources.test_plain_pre_tool_use_stdout_is_not_injected"),
    ("coverage counts records with nothing on them", L,
     "                if texts or shown:\n",
     "                if True:\n",
     "Sources.test_drift_coverage_counts_only_records_that_carry_text"),
    ("a corrupt file beside fork copies reads no_data", L,
     "    elif not live and not truncated and not unparsed:\n",
     "    elif not live and not truncated:\n",
     "Liveness.test_fork_only_window_with_a_corrupt_file_is_not_no_data"),
    ("empty files blamed on fork copies", L,
     "                  else \"the window's session files hold no record\")\n",
     "                  else \"every record in the window was a fork copy\")\n",
     "Liveness.test_empty_files_are_no_data_with_a_true_reason"),
    ("comment words read as a kg load query", L,
     '            elif tok.startswith("#"):\n                in_comment = True\n                continue\n',
     "",
     "NoProse.test_comment_words_are_not_query_words"),
    ("a blind reason claims schema drift with no session read", L,
     "        if live:\n            reason = (f\"read {len(live)} session(s)",
     "        if True:\n            reason = (f\"read {len(live)} session(s)",
     "Liveness.test_fork_only_window_with_a_corrupt_file_is_not_no_data"),
    # -- the sensor ----------------------------------------------------------------------
    ("a broken ledger takes the sensor down", S,
     "        return mod.analyze_context(files, kg_read_re, vocab, iter_lines, subagent_files, budget)\n    except Exception as e:\n",
     "        return mod.analyze_context(files, kg_read_re, vocab, iter_lines, subagent_files, budget)\n    except ZeroDivisionError as e:\n",
     "Cli.test_a_broken_ledger_is_an_error_and_spares_m1_to_m6"),
    ("the brief prints calibrating rows", S,
     "                and not (brief and is_calibrating(r))]\n",
     "                ]\n",
     "Cli.test_brief_leaves_calibrating_rows_out"),
    ("the brief hides a stood-down shield", S,
     '    return (row.get("status") == "shadow" and row.get("target") is None\n            and row.get("alert") is None)\n',
     '    return row.get("status") == "shadow"\n',
     "Cli.test_brief_still_discloses_a_stood_down_shield"),
    ("workflow subagent transcripts left out", S,
     '            os.path.join(root, "*", "subagents", "workflows", "*", "agent-*.jsonl"))\n',
     ")\n",
     "Cli.test_subagent_transcripts_are_found"),
    ("a workflow journal counted as a transcript", S,
     '"workflows", "*", "agent-*.jsonl"))\n',
     '"workflows", "*", "*.jsonl"))\n',
     "Cli.test_subagent_transcripts_are_found"),
    ("an existence check counted as a read (m5's detector)", S,
     '        if subs[0] == "cat-file" and {"-e", "-t", "-s"} & set(words):\n            return []\n',
     "",
     "FollowThrough.test_existence_checks_and_in_place_edits_are_not_reads"),
    ("an in-place sed edit counted as a read (m5's detector)", S,
     '    if verb == "sed" and any(w == "--in-place" or w.startswith("--in-place=") or\n',
     '    if False and any(w == "--in-place" or w.startswith("--in-place=") or\n',
     "FollowThrough.test_existence_checks_and_in_place_edits_are_not_reads"),
    ("one vanished file takes the file list down", S,
     "    return [f for f in glob.glob(glob_pat) if (_mtime(f) or 0) >= cutoff]\n",
     "    return [f for f in glob.glob(glob_pat) if os.path.getmtime(f) >= cutoff]\n",
     "Cli.test_a_vanished_file_is_skipped_not_the_list"),
    ("the human view drops the strict figure's label", S,
     "({v.get('followed_tools_only')} without shell reads)",
     "({v.get('followed_tools_only')})",
     "Cli.test_human_view_labels_the_strict_figure"),
    ("the ledger budget ignores time already spent", S,
     "    return max(0.0, min(default, LEDGER_DEADLINE_S - elapsed))\n",
     "    return default\n",
     "Cli.test_ledger_budget_counts_time_already_spent"),
    ("a long sed flag read as -i (m5's detector)", S,
     '(w.startswith("-") and not w.startswith("--") and "i" in w[1:])',
     '(w.startswith("-") and "i" in w[1:])',
     "FollowThrough.test_long_sed_flags_are_not_in_place_edits"),
    # -- doctor §29 --------------------------------------------------------------------
    ("doctor passes a blind ledger", D,
     '            gap "context ledger is BLIND — $_cl_detail" \\\n',
     '            ok "context ledger is BLIND — $_cl_detail" \\\n',
     "Doctor.test_blind_is_a_gap"),
    ("doctor passes a partial ledger", D,
     '            gap "context ledger is PARTIALLY BLIND — $_cl_detail" \\\n',
     '            ok "context ledger is PARTIALLY BLIND — $_cl_detail" \\\n',
     "Doctor.test_partial_is_a_gap"),
    ("doctor ignores a dead subagent block", D,
     '            SUBBLIND|SUBPARTIAL)\n                gap "context ledger\'s subagent block',
     '            SUBBLIND|SUBPARTIAL)\n                ok "context ledger\'s subagent block',
     "Doctor.test_dead_subagent_block_is_a_gap"),
    ("doctor omits the renames-undetected disclosure", D,
     '            emit("NOTE", "renamed or new attachment types are NOT detected (reported, not alarmed); "\n',
     '            emit("NOTE", "attachment types: "\n',
     "Doctor.test_live_note_says_renames_are_not_detected"),
    ("doctor passes a ledger that is not running", D,
     'emit("NOBLOCKFRESH" if newer else "NOBLOCK", ',
     'emit("NOBLOCK" if newer else "NOBLOCK", ',
     "Doctor.test_a_fresh_state_without_the_block_is_a_gap"),
    ("doctor gaps a state that predates the upgrade", D,
     'emit("NOBLOCKFRESH" if newer else "NOBLOCK", ',
     'emit("NOBLOCKFRESH" if True else "NOBLOCK", ',
     "Doctor.test_no_data_and_old_state_are_info"),
    ("doctor passes an all-null ledger", D,
     "            if not head or all(v is None for v in head.values()):\n",
     "            if False:\n",
     "Doctor.test_live_but_all_null_is_a_gap"),
)


def run(scripts, test):
    env = dict(os.environ, CONTEXT_LEDGER_SCRIPTS=str(scripts), PYTHONDONTWRITEBYTECODE="1",
               CONTEXT_LEDGER_DOCTOR=str(scripts / "repo" / "scripts" / "doctor.sh"))
    r = subprocess.run([sys.executable, "-m", "unittest", f"tests.test_context_ledger.{test}"],
                       cwd=str(REPO), env=env, capture_output=True, text=True, timeout=300)
    verdict = re.findall(r"^(OK.*|FAILED \(.*\))$", r.stderr, re.MULTILINE)
    return r.returncode, verdict[-1] if verdict else "<no verdict>"


def copy_scripts(dest):
    """The two sensor files, plus doctor.sh and the lib/ it sources, under repo/scripts/
    so doctor resolves its own BSTACK_REPO inside the copy."""
    dest.mkdir()
    for f in FILES:
        shutil.copy(REPO / "scripts" / f, dest / f)
    shutil.copytree(REPO / "scripts" / "lib", dest / "repo" / "scripts" / "lib")
    shutil.copy(REPO / "scripts" / "doctor.sh", dest / "repo" / "scripts" / "doctor.sh")
    # doctor §29 compares the state's age against the ledger file beside it
    shutil.copy(REPO / "scripts" / "context_ledger.py", dest / "repo" / "scripts" / "context_ledger.py")
    return dest


def target(d, fname):
    return d / "repo" / "scripts" / fname if fname == "doctor.sh" else d / fname


def main():
    passed = failed = 0

    def report(ok, label):
        nonlocal passed, failed
        print(f"  [{'pass' if ok else 'FAIL'}] {label}")
        if ok:
            passed += 1
        else:
            failed += 1

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pristine = copy_scripts(tmp / "pristine")
        for _, _, _, _, test in MUTANTS:
            rc, verdict = run(pristine, test)
            report(rc == 0 and verdict == "OK", f"positive control: {test} passes on an unmutated copy")
        for i, (label, fname, anchor, repl, test) in enumerate(MUTANTS):
            d = copy_scripts(tmp / f"m{i}")
            path = target(d, fname)
            text = path.read_text()
            if text.count(anchor) != 1:
                report(False, f"{label}: anchor found {text.count(anchor)}x in {fname} (expected 1)")
                continue
            path.write_text(text.replace(anchor, repl))
            if fname.endswith(".py"):
                try:
                    py_compile.compile(str(path), cfile=str(tmp / f"m{i}.pyc"), doraise=True)
                except py_compile.PyCompileError as e:
                    report(False, f"{label}: the mutant does not compile ({e.msg.splitlines()[-1]})")
                    continue
            elif subprocess.run(["bash", "-n", str(path)]).returncode != 0:
                report(False, f"{label}: the mutant is not valid bash")
                continue
            rc, verdict = run(d, test)
            report(rc != 0 and verdict == "FAILED (failures=1)",
                   f"mutant killed — {label} → {test} fails by assertion ({verdict})")
    print(f"\n  {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
