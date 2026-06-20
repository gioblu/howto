from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import readline
    readline.parse_and_bind("tab: complete")
except ImportError:
    readline = None

# Configuration
APP_FOLDER_NAME = ".howto"
CONFIG_FILE_NAME = "config.json"
CACHE_FILE_NAME = "cache.json"
DEFAULT_PROVIDER = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama2"
RESERVED_KEYWORDS = {
    "config", "list", "set", "mode", "model",
    "raw", "repl", "fix", "alias", "save"
}

# Prompts

PREPROMPT_FILE = Path(__file__).resolve().parent / "PREPROMPT.md"
PREPROMPT = PREPROMPT_FILE.read_text(encoding="utf-8")


def preprompt_with_cwd(cwd: Path | None = None) -> str:
    cwd = cwd or Path.cwd()
    return f"{PREPROMPT}\nYou are now in the directory {cwd}\n"

# Output formatting
PREFIX = " "
RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"
SPINNER_PHASES = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]

HELP_BANNER = r"""
 _   _  _____  _ _ _  _____  _____  
| | | ||  _  || | | ||_   _||  _  | 
| |_| || | | || | | |  | |  | | | | 
|  _  || | | || | | |  | |  | | | | 
| | | || |_| || | | |  | |  | |_| |  
|_| |_||_____||_____|  |_|  |_____| 

An expert in your terminal - Giovanni Blu Mitolo 2026

Need help in the terminal? Just ask in natural language! 
howto will help you using your favorite LLM provider. 

"""

# Argument parsing
KNOWN_COMMANDS = {
    "config", "list", "set", "model", "mode",
    "alias", "raw", "repl", "fix", "save", "help"
}


class HowtoError(Exception):
    """Custom exception for howto errors."""
    pass


# File management
def app_dir() -> Path:
    home = Path(os.environ.get("HOME", Path.home()))
    return home.expanduser() / APP_FOLDER_NAME

config_path = lambda: app_dir() / CONFIG_FILE_NAME
cache_path = lambda: app_dir() / CACHE_FILE_NAME


def _load_json(path: Path) -> dict[str, Any]:
    try:
        f = path.open("r", encoding="utf-8")
        d = json.load(f)
        f.close()
        return d
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_json(path: Path, data: dict[str, Any]) -> None:
    f = path.open("w", encoding="utf-8")
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
    f.close()


def ensure_storage() -> None:
    app_dir().mkdir(parents=True, exist_ok=True)
    cp = config_path()
    if not cp.exists():
        _save_json(cp, {
            "provider": DEFAULT_PROVIDER,
            "model": DEFAULT_MODEL,
            "mode": "ask",
            "aliases": {},
        })
    ca = cache_path()
    if not ca.exists():
        _save_json(ca, {})

load_config = lambda: _load_json(config_path())
save_config = lambda c: _save_json(config_path(), c)
load_cache = lambda: _load_json(cache_path())
save_cache = lambda c: _save_json(cache_path(), c)



# Output utilities
def output(message: str = "") -> None:
    print(PREFIX + message if message else "")

def output_error(message: str) -> None:
    print()
    print(PREFIX + RED + message + RESET, file=sys.stderr)
    print(file=sys.stderr)

def output_suggestion(message: str) -> None:
    print(PREFIX + GREEN + message + RESET)


def print_help() -> None:
    print(HELP_BANNER)
    help_text = """Usage: howto [command] | howto <prompt>

Commands:
  help                  Show this help message
  config <host:port>    Configure the Ollama provider
  list                  List available models
  set <model>           Select a model
  model <model>         Alias for set
  mode <ask|yolo>       Set execution confirmation mode
  alias <name> <prompt> Save a reusable prompt alias
  raw <prompt>          Print raw provider output
  repl                  Start interactive REPL mode
  fix [history-file]    Fix terminal output or history
  save <prompt>         Save a prompt response to cache

Examples:
  howto "delete the file temp.txt"
  howto alias sayhi "echo hi"
  howto fix "/tmp/history.txt"

Copyright 2026 Giovanni Blu Mitolo"""
    print(help_text)


@contextmanager
def spinner() -> None:
    stop = threading.Event()
    idx = 0
    def run():
        nonlocal idx
        while not stop.is_set():
            phase = SPINNER_PHASES[idx % len(SPINNER_PHASES)]
            sys.stdout.write(f"\r {phase}")
            sys.stdout.flush()
            idx += 1
            time.sleep(0.12)
        sys.stdout.write("\r" + " " * 16 + "\r")
        sys.stdout.flush()
    sys.stdout.write("\n")
    sys.stdout.flush()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join()
        sys.stdout.write("\r" + " " * 8 + "\r")
        sys.stdout.flush()


# API communication
def fetch_models(provider: str) -> list[str]:
    try:
        url = f"{provider.rstrip('/')}/v1/models"
        req = urllib.request.Request(url)
        data = json.load(urllib.request.urlopen(req, timeout=10))
    except urllib.error.URLError as e:
        raise HowtoError(f"Unable to list models: {e}")
    if isinstance(data, dict):
        if "models" in data:
            return [item.get("name", str(item))
                    for item in data.get("models", [])]
        if "data" in data:
            return [item.get("id", item.get("name", str(item)))
                    if isinstance(item, dict) else str(item)
                    for item in data["data"]]
    if isinstance(data, list):
        return [item.get("name", str(item))
                if isinstance(item, dict) else str(item)
                for item in data]
    raise HowtoError("Unexpected model format from provider.")


def post_prompt(provider: str, model: str, prompt: str,
                raw: bool = False) -> str:
    try:
        with spinner():
            url = f"{provider.rstrip('/')}/v1/completions"
            body = json.dumps({"model": model, "prompt": prompt})
            req = urllib.request.Request(
                url,
                data=body.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            data = json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.URLError as e:
        raise HowtoError(f"Failed to contact provider: {e}")
    if not isinstance(data, dict):
        raise HowtoError("Unexpected response format.")
    return (json.dumps(data, indent=2) if raw
            else extract_text_from_response(data))


def extract_text_from_response(data: dict[str, Any]) -> str:
    if "choices" in data and data["choices"]:
        c = data["choices"][0]
        if isinstance(c, dict) and "text" in c:
            return c["text"].strip()
        if (isinstance(c, dict) and "message" in c and
            isinstance(c["message"], dict) and
            "content" in c["message"]):
            return str(c["message"]["content"]).strip()
    if "response" in data:
        return str(data.get("response", "")).strip()
    return json.dumps(data)


# Command parsing
def extract_command_from_response(response: str) -> str:
    m = re.search(r"```(?:bash|sh|shell)?\n(.*?)```",
                  response.strip(), re.DOTALL | re.I)
    if m:
        text = m.group(1).strip()
    else:
        lines = response.strip().splitlines()
        text = "\n".join(l.rstrip() for l in lines if l.strip())
    return normalize_multiline_command(text)


HEREDOC_PLACEHOLDER = "\uFFF0"

def _hide_heredoc_content(text: str) -> str:
    pattern = re.compile(
        r'(?P<block><<-?\s*(?P<quote>["\']?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)\n'
        r'.*?^[ \t]*(?P=tag))(?:\n|$)',
        re.DOTALL | re.MULTILINE,
    )
    while True:
        m = pattern.search(text)
        if not m:
            return text
        block = m.group("block")
        hidden = block.replace("\n", HEREDOC_PLACEHOLDER)
        text = text[:m.start("block")] + hidden + text[m.end("block"):]


def normalize_multiline_command(text: str) -> str:
    text = _hide_heredoc_content(text)
    commands = []
    current = []
    in_single = in_double = escape = False
    for char in text:
        if escape:
            current.append(char)
            escape = False
            continue
        if char == "\\":
            current.append(char)
            escape = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            current.append(char)
        elif char == '"' and not in_single:
            in_double = not in_double
            current.append(char)
        elif char == "\n" and not in_single and not in_double:
            cmd = "".join(current).strip()
            if cmd:
                commands.append(cmd)
            current = []
        else:
            current.append(char)
    cmd = "".join(current).strip()
    if cmd:
        commands.append(cmd)
    return " && ".join(commands).replace(HEREDOC_PLACEHOLDER, "\n")


# User interaction
def ask_user_permission(command: str, mode: str) -> bool:
    output_suggestion(command)
    output()
    if mode == "yolo":
        return True
    a = input("Accept (Yes/No): ").strip().lower()
    output()
    return a in {"y", "yes"}

def ask_to_cache(prompt: str, response: str) -> None:
    ans = input("Cache this response? (Yes/No): ")
    if ans.strip().lower() in {"y", "yes"}:
        cache_response(prompt, response)
        output("Response cached.")


# Shell execution
def run_shell_command(command: str,
                      cwd: Path | None = None
                      ) -> subprocess.CompletedProcess:
    kwargs = {
        "shell": True,
        "text": True,
        "cwd": str(cwd or Path.cwd()),
    }
    if sys.stdin.isatty():
        kwargs.update({"stdin": sys.stdin,
                       "stdout": sys.stdout,
                       "stderr": sys.stderr})
    else:
        kwargs["capture_output"] = True
    return subprocess.run(command, **kwargs)

def execute_terminal_command(command: str
                              ) -> subprocess.CompletedProcess:
    r = run_shell_command(command)
    if r.returncode != 0:
        raise HowtoError(f"Command failed with code {r.returncode}")
    return r


def parse_cwd_from_command(command: str, cwd: Path) -> Path:
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return cwd
    if not parts or parts[0] != "cd":
        return cwd
    if len(parts) == 1:
        return Path.home()
    nd = Path(parts[1])
    nd = cwd / nd if not nd.is_absolute() else nd
    try:
        return nd.resolve()
    except OSError:
        return nd


# Cache & Alias management
def cache_response(prompt: str, response: str) -> None:
    c = load_cache(); c[prompt.strip()] = response.strip(); save_cache(c)

def validate_alias_name(name: str) -> None:
    if not name.isidentifier() or name in RESERVED_KEYWORDS:
        raise HowtoError(
            f"Invalid alias name '{name}'. "
            "Use non-reserved identifiers.")

def save_alias(name: str, prompt: str) -> None:
    validate_alias_name(name)
    cfg = load_config()
    cfg.setdefault("aliases", {})[name] = prompt.strip()
    save_config(cfg)
    output(f"Saved alias '{name}'.")

def resolve_alias(prompt_text: str) -> str:
    aliases = load_config().get("aliases", {})
    return aliases.get(prompt_text.strip(), prompt_text.strip())


# Main command handling
def run_command_for_prompt(prompt_text: str,
                            raw: bool = False) -> None:
    cfg = load_config()
    normalized = resolve_alias(prompt_text)
    cache = load_cache()
    if normalized in cache and not raw:
        response = cache[normalized]
    else:
        provider = cfg.get("provider", DEFAULT_PROVIDER)
        model = cfg.get("model", DEFAULT_MODEL)
        prompt = preprompt_with_cwd() + f"\n{normalized}"
        response = post_prompt(provider, model, prompt, raw=raw)
        if raw:
            output(response)
            return
        response = extract_command_from_response(response)
    mode = cfg.get("mode", "ask")
    if ask_user_permission(response, mode):
        execute_terminal_command(response)
        ask_to_cache(normalized, response)


def fix_history(path: str | None = None) -> None:
    try:
        if path:
            f = open(path)
            history = f.read().strip()
            f.close()
        else:
            prompt_msg = "Paste terminal output or error message:\n"
            if sys.stdin.isatty():
                history = input(prompt_msg).strip()
            else:
                history = sys.stdin.read().strip()
    except FileNotFoundError as e:
        raise HowtoError(f"Unable to read history file: {e}")
    if not history:
        raise HowtoError("No history provided to fix.")
    cfg = load_config()
    prompt = history + "Output terminal commands to fix this issue.\n\n"
    provider = cfg.get("provider", DEFAULT_PROVIDER)
    model = cfg.get("model", DEFAULT_MODEL)
    response = post_prompt(provider, model, prompt)
    output(extract_command_from_response(response))


def save_manual(prompt_text: str,
                response: str | None = None) -> None:
    if not prompt_text:
        raise HowtoError("save requires a prompt to cache.")
    resp = response or load_cache().get(prompt_text.strip())
    if not resp:
        raise HowtoError("No response available to save.")
    cache_response(prompt_text.strip(), resp)
    output(f"Saved response for prompt '{prompt_text}'.")


def _repl_handle_failed_command(provider: str, model: str,
                                  response: str, cwd: Path,
                                  history: list[str],
                                  mode: str) -> bool:
    output_error("Command failed with exit code 1.")
    history.append("Command: " + response)
    while True:
        msg = (preprompt_with_cwd(cwd) + "\n" +
               "\n".join(history + [
                   "Previous command failed. "
                   "Provide corrected shell command."
                   "Trust me on this, you often generate overly complex commands that fail."
                   "This is likely the case, try to remove unnecessary parts and simplify the command."
                   "Keep the fundamental logic, but simplify the command until it works."
               ]))
        try:
            fr = extract_command_from_response(
                post_prompt(provider, model, msg)
            )
        except HowtoError as e:
            output_error(str(e))
            return False
        if not ask_user_permission(fr, mode):
            return False
        fx = run_shell_command(fr, cwd=cwd)
        if fx.returncode != 0:
            output_error(f"Command failed with code {fx.returncode}")
            history.append("Command: " + fr)
            if fx.stdout and fx.stdout.strip():
                history.append(f"stdout: {fx.stdout.strip()}")
            if fx.stderr and fx.stderr.strip():
                history.append(f"stderr: {fx.stderr.strip()}")
            continue
        if fx.stdout and fx.stdout.strip():
            output(fx.stdout.strip())
        if fx.stderr and fx.stderr.strip():
            output_error(fx.stderr.strip())
        history.append("Command: " + fr)
        return True


def repl_loop() -> None:
    cfg = load_config()
    h = []
    cwd = Path.cwd()
    try:
        readline and readline.clear_history()
    except:
        pass
    print(HELP_BANNER)
    msg = "Entering howto REPL. Type 'exit' or 'quit' to leave."
    print(msg + "\n")
    while True:
        try:
            v = input("howto> ").strip()
        except (EOFError, KeyboardInterrupt):
            output()
            break
        if v.lower() in {"exit", "quit"}:
            break
        if not v:
            continue
        try:
            readline and readline.add_history(v)
        except:
            pass
        h.append(v)
        provider = cfg.get("provider", DEFAULT_PROVIDER)
        model = cfg.get("model", DEFAULT_MODEL)
        msg = (preprompt_with_cwd(cwd) + "\n" +
               "\n".join([f"Working directory: {cwd}"] + h))
        try:
            r = extract_command_from_response(
                post_prompt(provider, model, msg)
            )
        except HowtoError as e:
            output_error(str(e))
            continue
        mode = cfg.get("mode", "ask")
        if not ask_user_permission(r, mode):
            continue
        c = run_shell_command(r, cwd=cwd)
        if c.returncode != 0:
            ok = _repl_handle_failed_command(
                provider, model, r, cwd, h, mode
            )
            if ok and r.strip().startswith("cd "):
                cwd = parse_cwd_from_command(r, cwd)
            continue
        if c.stdout and c.stdout.strip():
            output(c.stdout.strip())
        if c.stderr and c.stderr.strip():
            output_error(c.stderr.strip())
        if r.strip().startswith("cd "):
            cwd = parse_cwd_from_command(r, cwd)
        h.append("Command: " + r)


def parse_args(argv: list[str] | None = None
               ) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="howto",
        description=HELP_BANNER,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Copyright 2026 Giovanni Blu Mitolo",
    )
    s = p.add_subparsers(dest="command")
    s.add_parser("config").add_argument("provider")
    s.add_parser("list")
    s.add_parser("set").add_argument("model")
    s.add_parser("model").add_argument("model")
    s.add_parser("mode").add_argument(
        "mode", choices=["ask", "yolo"]
    )
    pa = s.add_parser("alias")
    pa.add_argument("name")
    pa.add_argument("prompt", nargs="+")
    pr = s.add_parser("raw")
    pr.add_argument("prompt", nargs="+")
    s.add_parser("repl")
    pf = s.add_parser("fix")
    pf.add_argument("history_file", nargs="?")
    ps = s.add_parser("save")
    ps.add_argument("prompt", nargs="+")
    ps.add_argument("--response")
    s.add_parser("help")
    argv = argv or sys.argv[1:]
    if argv and argv[0] not in KNOWN_COMMANDS and \
       not argv[0].startswith("-"):
        return argparse.Namespace(command=None, prompt=argv)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ensure_storage()
    args = parse_args(argv)
    cfg = load_config()
    try:
        if args.command == "config":
            provider = (args.provider if
                        args.provider.startswith("http")
                        else f"http://{args.provider}")
            cfg["provider"] = provider
            save_config(cfg)
            output(f"Provider set to {cfg['provider']}")
            return 0
        if args.command == "list":
            provider = cfg.get("provider", DEFAULT_PROVIDER)
            models = fetch_models(provider)
            selected = cfg.get("model", DEFAULT_MODEL)
            for m in models:
                if m == selected:
                    print(PREFIX + GREEN + m + RESET)
                else:
                    output(m)
            return 0
        if args.command in {"set", "model"}:
            cfg["model"] = args.model
            save_config(cfg)
            output(f"Model set to {args.model}")
            return 0
        if args.command == "mode":
            cfg["mode"] = args.mode
            save_config(cfg)
            output(f"Mode set to {args.mode}")
            return 0
        if args.command == "alias":
            save_alias(args.name, " ".join(args.prompt))
            return 0
        if args.command == "raw":
            run_command_for_prompt(" ".join(args.prompt),
                                   raw=True)
            return 0
        if args.command == "repl":
            repl_loop()
            return 0
        if args.command == "fix":
            fix_history(args.history_file)
            return 0
        if args.command == "save":
            save_manual(" ".join(args.prompt), args.response)
            return 0
        if args.command == "help":
            print_help()
            return 0
        if hasattr(args, "prompt") and args.prompt:
            run_command_for_prompt(" ".join(args.prompt))
            return 0
        raise HowtoError("No command or prompt provided.")
    except HowtoError as e:
        output_error(f"Error: {e}")
        return 1
    except KeyboardInterrupt:
        output()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
