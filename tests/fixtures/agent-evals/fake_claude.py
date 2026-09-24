"""A stand-in for `claude` in the agent_evals tests. It never calls a model.

Tests write a tiny executable wrapper into their temp dir that execs this file, so the
tool under test sees an ordinary binary. The behaviour is steered by the prompt (the
argv element after -p), one directive per line:

    write <path> <text>     create or overwrite a file with <text> + newline
    append <path> <text>    append <text> + newline
    delete <path>           remove a file
    branch <name>           git checkout -q -b <name>
    commit                  git add -A && git commit
    say <text>              a line of the reply (the `result` field)
    shell <cmd>             run <cmd> via /bin/sh; its stdout joins the reply (an agent
                            probing the repo: `git log --all`, `git show HEAD~1:…`)
    raw                     print the reply as plain text instead of JSON
    exit <n>                exit with status n after printing
    sleep <seconds>         sleep first (to exceed a timeout)

Every invocation appends one JSON line to $FAKE_CLAUDE_LOG recording argv, cwd and
whether the probe variables were visible, so a test can assert what the tool passed.
"""

import json
import os
import subprocess
import sys
import time


def git(*args):
    subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                    "-c", "user.name=fake",
                    "-c", "user.email=fake@example.invalid", *args], check=True,
                   stdin=subprocess.DEVNULL, capture_output=True)


def main(argv):
    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "argv": argv, "cwd": os.getcwd(),
                "saw_secret": "AGENT_EVALS_TEST_SECRET" in os.environ,
                "saw_git_dir": "GIT_DIR" in os.environ,
            }) + "\n")
    if argv[:1] == ["--version"]:
        print("9.9.9 (fake claude)")
        return 0
    prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
    reply, raw, code = [], False, 0
    for line in prompt.splitlines():
        op, _, rest = line.strip().partition(" ")
        if op == "sleep":
            time.sleep(float(rest))
        elif op in ("write", "append"):
            path, _, text = rest.partition(" ")
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w" if op == "write" else "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        elif op == "delete":
            os.remove(rest)
        elif op == "branch":
            git("checkout", "-q", "-b", rest)
        elif op == "commit":
            git("add", "-A")
            git("commit", "-q", "--no-verify", "-m", "fake")
        elif op == "say":
            reply.append(rest)
        elif op == "shell":
            r = subprocess.run(rest, shell=True, stdin=subprocess.DEVNULL,
                               capture_output=True)
            reply.append(r.stdout.decode("utf-8", "replace").rstrip("\n"))
        elif op == "raw":
            raw = True
        elif op == "exit":
            code = int(rest)
    text = "\n".join(reply)
    if raw:
        print(text)
    else:
        print(json.dumps({"type": "result", "subtype": "success", "is_error": code != 0,
                          "result": text, "num_turns": 1, "total_cost_usd": 0.0}))
    if code:
        print("fake claude: failing as instructed", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
