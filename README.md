# tagtrace

tagtrace tells you what a new application version changes. You give it the `pulldeps.json` of the old and new versions. It finds the components that differ, collects their Git commits and Jira tickets, and asks an AI model for the affected areas, the risk and the testing effort.

## Install

You need [uv](https://docs.astral.sh/uv/) and `git`.

```sh
uv sync --extra anthropic --extra browser
cp tagtrace.example.yaml tagtrace.yaml    # then edit it
```

| Extra | Needed for |
|---|---|
| `anthropic` | `ai.provider: anthropic` |
| `openai` | `ai.provider: openai_compatible` (OpenAI and local models) |
| `browser` | `jira.auth: browser` |

## Use

```sh
uv run tagtrace run old/pulldeps.json new/pulldeps.json
```

`run` does two steps, which you can also run separately. This lets you inspect what will be sent to the model, and re-run the analysis without fetching everything again.

```sh
uv run tagtrace collect old/pulldeps.json new/pulldeps.json --out runs/my-release
uv run tagtrace analyze runs/my-release
```

Both steps take `--config` (default `tagtrace.yaml`). `collect` takes `--out` for the run folder, which defaults to `runs/<time>`. It stops if that folder already holds a run, so use a new name for each run.

| Exit code | Meaning |
|---|---|
| `0` | Complete |
| `1` | Fatal error, with the reason shown |
| `2` | Finished, but something is missing. `report.md` lists it first, and `collect` prints it instead. Also used for a wrong command line, such as a missing argument. |

## Input

```json
{
  "components": [
    { "name": "payments-core", "repo": "git@host:org/payments-core.git", "tag": "v1.4.2" }
  ]
}
```

Other fields are ignored. Each `name` must be unique within a file.

## Output

```
runs/<name>/
  manifest.json                   # changed, added, removed, moved and failed components
  components/<name>/
    commits_with_jira.json        # commits that mention a Jira key
    commits_without_jira.json     # all other commits
    jira_context.json             # the tickets those commits mention
  assessments/<name>.json         # the AI assessment of each component
  report.json                     # the full result
  report.md                       # the report to read
```

Folder names are the component names with unsafe characters replaced.

Run folders hold commit and ticket text from your company, so treat them as internal. The `runs/` folder is ignored by Git.

## How components are compared

| Difference | What happens |
|---|---|
| Different tag | Commits in the new tag but not the old are read as `added`. Commits in the old tag but not the new are read as `removed`, which catches downgrades and lost hotfixes. |
| Only in the new file, or only in the old file | Reported as added or removed, with no commits |
| Different `repo` | Reported as moved, with no commits, because the tags may not be comparable. The run counts as incomplete (exit code `2`), so check it by hand. Two spellings of one address, such as SSH and HTTPS, also count as different. |

## Git access

tagtrace runs your installed `git`, so your SSH keys and credential manager apply and any Git host works. It never asks for a password. Missing access makes that component fail. Bare clones are kept in `~/.cache/tagtrace/repos`.

## Jira access

A commit counts as linked when its message contains a key such as `ACTXXX-1234`. Without `jira.projects`, text such as `UTF-8` also counts as a key, and that commit is wrongly listed as linked. Set `jira.projects` to the ticket prefixes you use. tagtrace warns about this when Jira does not return such a key. A key Jira does not return stays on its commit and is listed in the report, because Jira gives the same answer for a wrong key and for a ticket you may not see.

### With a token

Set `auth: token` and `token`. Add `email` on Jira Cloud, and leave it out on Data Center.

### Without a token

Set `auth: browser`. At the start of each run, tagtrace checks the sign-in. If Jira answers 401 or 403, redirects to a login page, or sends a web page instead of data, tagtrace opens a browser window on your Jira. Other Jira errors, and a Jira that cannot be reached, stop the run without a window. In the window, you sign in, with SSO if your company uses it, and tagtrace continues by itself. Later runs use the saved session with no window, until Jira ends the session and the window opens again.

- It works on Jira Cloud and Data Center, and needs no token or admin setup.
- `browser` picks the browser: `chrome` (the default) or `msedge` if installed, or `chromium` after `uv run playwright install chromium`.
- The window has its own profile, so you sign in there even if you are already logged in to Jira in your normal browser.
- You have 5 minutes to finish signing in. Closing every tab of the window stops the run. Closing only some tabs does not.
- If `collect` runs where no window can open, such as CI, use a token.
- Some SSO setups tie a session to the browser, or refuse automated browsers. tagtrace then stops with an error, and a token is the alternative.

**Your session is saved on disk.** Files for each Jira address are kept in `~/.tagtrace/jira/`. The cookies for the Jira address are in `session.json`, which only your user can read. The `profile/` folder holds the browser profile, which keeps the SSO provider's login. Anyone who copies `session.json` can act as you in Jira until the session ends. Never commit or share `session.json` or `profile/`. Deleting `~/.tagtrace/jira/` makes tagtrace forget the session. To end the session on Jira itself, log out there.

If sign-in fails, tagtrace stops with an error. It does not switch to another sign-in method.

## AI analysis

Each component is assessed on its own, and all the results are then combined into a view of the whole release. Every assessment holds the affected areas, risk level, testing effort, recommended tests and gaps such as significant commits with no ticket.

- **What is sent:** commit messages, dates, changed file names with line counts, and Jira fields and comments. Author names, comment authors and repository URLs are not sent. Code diffs are not read at all.
- **Redaction:** for a remote model, emails, IP addresses, common secret formats and your `redact_patterns` are masked first. Pattern matching cannot find every secret, and people's names inside free text are **not** masked. `localhost`, `127.0.0.1` and `::1` are treated as local. A tunnel or proxy to a remote model that uses one of these addresses gets unmasked data. `redact: false` turns it off.
- **Citations:** each SHA and ticket the model cites is checked against the input. Citations that do not exist are removed, and the report states how many.
- **Large components:** nothing is cut. A component above `max_input_chars` is split into parts, and each part is assessed.
- **Failures:** a failed component is recorded and the run continues. There is no automatic switch to another provider.

With Claude, a declined request is retried by Anthropic on another model. The report states which model answered instead. Set `refusal_fallback: false` to turn this off, and do so for any `base_url` that is not the Claude API.

The model's output is a starting point for a human review, and it does not replace one.

## Development

```sh
uv sync
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

The tests need no network or API key. They use real temporary Git repositories, a mocked Jira, a scripted model, and the real model SDKs against a local fake server.
