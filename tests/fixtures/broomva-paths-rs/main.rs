// The replay driver for the Rust path reader (BRO-3265). tests/test_broomva_paths.py
// compiles it next to a copy of scripts/lib/broomva_paths.rs and runs one reader call
// per process: `main <function> [args...]`, with `@none` for Python's None.
//
// Output, compared line for line with the Python reader's answer:
//   ok path <p> | ok none | ok str <s> | ok bool <true|false> | ok list, then `item <s>` lines
//   err <unknown|value|tree|os> | err layout <message>
mod broomva_paths;

use broomva_paths as bp;
use std::path::{Path, PathBuf};

enum Answer {
    Path(PathBuf),
    OptPath(Option<PathBuf>),
    Str(String),
    OptStr(Option<String>),
    Bool(bool),
    List(Vec<String>),
}

fn opt(arg: &str) -> Option<&str> {
    if arg == "@none" { None } else { Some(arg) }
}

fn call(op: &str, a: &[String]) -> bp::Result<Answer> {
    let arg = |i: usize| a.get(i).map(String::as_str).unwrap_or_else(|| panic!("{op}: argument {i} missing"));
    Ok(match op {
        "repo_key" => Answer::Str(bp::repo_key(Path::new(arg(0)))?),
        "state_dir" => Answer::Path(bp::state_dir(Path::new(arg(0)))?),
        "machine_root" => Answer::Path(bp::machine_root()?),
        "workspace_root" => Answer::Path(bp::workspace_root()?),
        "repo_root" => Answer::OptPath(bp::repo_root(opt(arg(0)).map(Path::new))?),
        "repo_rels" => Answer::List(bp::repo_rels(arg(0))?),
        "moved" => Answer::Bool(bp::moved(Path::new(arg(0)))?),
        "partially_moved" => Answer::List(bp::partially_moved(Path::new(arg(0)))?),
        "repo_path" => Answer::Path(bp::repo_path(arg(0), Path::new(arg(1)))?),
        "workspace_path" => Answer::Path(bp::workspace_path(arg(0))?),
        "machine_path" => Answer::Path(bp::machine_path(arg(0))?),
        "tree_path" => Answer::OptStr(bp::tree_path(arg(0), Path::new(arg(1)), arg(2))?),
        "shadowed" => Answer::List(bp::shadowed(Path::new(arg(0)), opt(arg(1)))?),
        "resolve" => Answer::Path(bp::resolve(arg(0), opt(arg(1)).map(Path::new))?),
        "all_names" => Answer::List(bp::all_names()),
        // The ported helpers, for the spelling and hashing cases.
        "pl" => Answer::Str(bp::pl(arg(0))),
        "normpath" => Answer::Str(bp::normpath(arg(0))),
        "parent" => Answer::Str(bp::parent(&bp::pl(arg(0)))),
        "join" => Answer::Str(bp::join(&bp::pl(arg(0)), arg(1))),
        "abspath" => Answer::Str(bp::abspath(arg(0))?),
        "realpath" => Answer::Str(bp::realpath(arg(0))?),
        "sha256" => Answer::Str(bp::sha256_hex(arg(0).as_bytes())),
        "hash" => Answer::Str(bp::REGISTRY_SHA256.to_string()),
        _ => panic!("unknown op {op}"),
    })
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some((op, rest)) = args.split_first() else { panic!("usage: main <function> [args...]") };
    match call(op, rest) {
        Ok(Answer::Path(p)) => println!("ok path {}", p.display()),
        Ok(Answer::OptPath(None)) | Ok(Answer::OptStr(None)) => println!("ok none"),
        Ok(Answer::OptPath(Some(p))) => println!("ok path {}", p.display()),
        Ok(Answer::Str(s)) | Ok(Answer::OptStr(Some(s))) => println!("ok str {s}"),
        Ok(Answer::Bool(b)) => println!("ok bool {b}"),
        Ok(Answer::List(items)) => {
            println!("ok list");
            for i in items {
                println!("item {i}");
            }
        }
        // A layout error names the rule that fired; the other kinds compare by kind alone.
        Err(e @ bp::Error::Layout(_)) => println!("err layout {e}"),
        Err(e) => println!("err {}", e.kind()),
    }
}
