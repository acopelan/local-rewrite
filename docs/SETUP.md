# Full stack setup

The setup I use: draft with a cloud model inside a terminal agent, run the
final pass locally, reach all of it from a phone.

```
iPhone (Termius) ── Tailscale ──▶ Mac: tmux "write"
                                    └─ pi ⇄ cloud model           drafting → draft.md
                                         └─ rewrite.py ⇄ Ollama   final pass → final.md
```

## 1. Local model (Mac)

```
brew install node tmux
# install Ollama from ollama.com
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
ollama cp qwen2.5:7b rewrite-pinned     # pin it so a future pull can't change it
```

Smoke test:

```
echo "The crew inspected all four engines on Monday before the vessel left Haifa." \
  | REWRITE_MODEL=rewrite-pinned python3 rewrite.py -
```

## 2. Agent (pi)

```
npm install -g @mariozechner/pi-coding-agent
mkdir ~/writing && cp rewrite.py examples/AGENTS.md ~/writing/
cd ~/writing && tmux new -s write
pi      # /login, then /model to pick your drafting model
```

pi loads `AGENTS.md` from the working directory. The example tells the agent
to run the rewrite only when asked, never to edit `final.md` itself, and how to
report each exit code.

## 3. Phone access

1. Install Tailscale on the Mac and the iPhone, same account.
2. Mac: System Settings → General → Sharing → Remote Login, your user only.
3. iPhone: install Termius, add a host with the Mac's Tailscale name and your
   username, generate an ED25519 key, and use "Export to host".
4. Connect and run `tmux attach -t write`. Detach with Ctrl+B, D.
5. Keep the Mac awake: Battery → Options → Wake for network access.

Never port-forward SSH. Tailscale is the only way in.
