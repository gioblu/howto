# howto

Using `howto` you always have an expert available in your terminal. Need help in the terminal? Just ask in natural language! `howto` will help you using your favorite LLM provider.

## Features

- `howto config <host:port>`: configure the API provider
- `howto list`: list models from the provider
- `howto set <model>`: choose a model
- `howto mode <ask|yolo>`: choose confirmation behavior
- `howto alias <name> <prompt>`: store reusable aliases
- `howto raw <prompt>`: print raw provider output
- `howto repl`: interactive prompt loop
- `howto fix [history-file]`: ask the provider for a command to fix terminal history
- `howto save <prompt>`: persist a prompt response in the cache

## Storage

Configuration and cache are stored in `~/.howto/config.json` and `~/.howto/cache.json`.

## Install for shell access

The simplest way to make `howto` available everywhere is to install the package in editable mode:

```bash
cd /home/nonar/Software/howto
python3 -m pip install --user -e .
```

This installs the `howto` command from `pyproject.toml` into your user Python bin directory.

If `~/.local/bin` is not already on your `PATH`, add this to your shell profile:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### If pip install fails in an externally-managed environment

On Debian/Ubuntu systems, `python3 -m pip install --user -e .` may fail with an "externally-managed-environment" error. In that case, use a virtual environment instead:

```bash
cd /home/nonar/Software/howto
python3 -m venv ~/.venvs/howto
source ~/.venvs/howto/bin/activate
pip install -e .
```

Or, if you have `pipx` installed, use:

```bash
cd /home/nonar/Software/howto
pipx install --editable .
```

## Running tests

To verify the project and confirm changes, use the built-in unittest suite:

```bash
cd /home/nonar/Software/howto
python3 -m unittest discover -s tests -v
```

This will run all tests in `tests/test_howto.py` and report pass/fail status.
