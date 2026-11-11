# DSH × CodeArts Desktop Task Queue

[简体中文](README.md) · [Installation](docs/installation.md) · [Tools and workflow](docs/usage.md) · [Verification scope](docs/verification.md)

Delegate a bounded task from DeepSeek Harness to the CodeArts desktop app on the same computer. Pick it up manually in the official MCP connector. After delivery, the originating DSH session receives a notification and independently checks the actual files and tests.

**`v0.1.0-alpha.3` is a community experimental release.** Live Windows desktop checks passed for submission, manual pickup, actual file delivery, automatic notification, independent DSH model review, rejection and revision, cancellation, and empty-queue handling. Automated checks also cover the queue, installer, stdio protocol, and relay. See the recorded versions and scope. [Live desktop verification](docs/desktop-verification-20261111.md)

## How it works

1. DSH submits a task with an existing absolute project directory, instructions and acceptance criteria.
2. You open that project in CodeArts Desktop and ask the `dsh-task-queue` connector to receive one task.
3. CodeArts uses its native tools and permissions, then reports artifact paths and actual test results through MCP.
4. The local relay notifies the originating DSH session.
5. DSH inspects the files, runs necessary tests, and records acceptance or returns the task with feedback.

The Python MCP processes share one local SQLite database. A dispatcher exposes submission, status, review and cancellation. An executor exposes claim, progress and delivery. The relay uses DSH host services to restore the originating session when needed.

Official clients manage account sign-in, credentials, model requests and billing. The queue stores task and delivery metadata locally. Idle notification checks read the local database about every two seconds. Actual execution and review consume the respective clients' normal model allowance.

## Windows quick start

Requirements include Python 3.11+, working DSH and CodeArts Desktop installations, and a DSH host with `node:sqlite`. Initialize the selected DSH profile by starting it once. Independent Node.js 24 is used for the JavaScript tests.

```powershell
git clone https://github.com/hsajdh/dsh-codearts-task-queue.git
Set-Location dsh-codearts-task-queue
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/configure.py --apply --profiles desktop
.\.venv\Scripts\python.exe scripts/configure.py --doctor --profiles desktop
```

Restart both clients. In CodeArts Desktop, open Extensions → Connectors → Installed connectors and confirm that `dsh-task-queue` is connected. Choose “Use connector”. Ask DSH to delegate a small, clearly scoped task, then ask CodeArts to receive one task with `claim_next.project_dir` set to the current workspace's absolute path. The scope includes task subdirectories. Omitting this optional argument uses the global queue order.

The installer backs up and updates the selected DSH patch files, the marked instructions in DSH's `AGENTS.md`, and the CodeArts connector. Existing YAML is reserialized and its original text is backed up. Installer tests cover concurrent-edit detection and rollback of partially applied changes.

Use `--profiles desktop web` after both profiles have been initialized. Custom roots can be supplied with `--dsh-home`, `--codearts-home`, `--data-dir` and `--database`. Running the configuration script without an action flag generates importable local configuration under `generated/`.

## Data and maintenance

Runtime data defaults to `~/.dsh-codearts-task-queue/`, containing `queue.sqlite`, `review.sqlite` and private configuration backups. Keep the cloned repository and its virtual environment at stable locations because clients reference absolute paths.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
node --test tests/test_review_relay.mjs
.\.venv\Scripts\python.exe scripts/configure.py --uninstall --profiles desktop
```

Uninstall removes the selected profiles' configuration entries. Shared connector and instruction cleanup takes place when the final configured profile is removed. Runtime history and backups are available in the selected data directory for your own retention decisions.

## Compatibility and account risk

Live Windows client integration was checked with DSH desktop `0.2.2-rc.1` (EXE product version `0.2.2.0`) and CodeArts Desktop `0.2.9`. The relay's session restoration uses DSH host interfaces that can change during its developer preview. Other versions and desktop operating systems need their own validation.

Huawei documents custom local MCP connectors and asks users to assess third-party implementations. Account enforcement remains subject to platform terms and actual usage. The current implementation supports a lower-risk assessment based on architecture, with uncertainty about enforcement for this exact integration. Read the verification and data-flow notes before using sensitive projects. [Official connector documentation](https://support.huaweicloud.com/usermanual-codeartsdesktop/desktop_0015.html) [Service terms](https://www.huaweicloud.com/declaration/tsa-codearts-doer.html) [Architecture and data](docs/architecture-and-safety.md)

## License and related work

MIT for the project's own code. Dependencies retain their respective licenses. Cross-agent delegation and independent review are established community patterns, including `dsh-baton` and `codex-deepseek-harness-bridge`. [License](LICENSE) [Third-party notices](THIRD_PARTY_NOTICES.md) [dsh-baton](https://github.com/JialaoLiu/dsh-baton) [codex-deepseek-harness-bridge](https://github.com/WiscOBKenobi/codex-deepseek-harness-bridge)
