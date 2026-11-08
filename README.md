# tendermate-shared

Shared code for TenderMate services, consumed by `tendermate-backend`, `tendermate-searching-agent`
and `tendermate-import-agent` as a git dependency pinned to a release tag.

- `proto/search_service.proto`: the gRPC `SearchService` contract (backend → searching-agent).
- `tendermate_shared.grpc`: betterproto stubs generated from the proto and committed to git.
- `tendermate_shared.agents`: `BaseAgent` scaffolding, `AgentInput` / `AgentResult`;
  `agents.telemetry.LlmTelemetryCallback` (the `llm.*` metrics of every chat-model call).
- `tendermate_shared.build_info`: `load_build_info(service, path)`, the release identity read
  from `build_info.json` (version, git SHA, Sentry release).
- `tendermate_shared.observability`:
  - `report_handled_issue`, `job_id_var`, `JobIdFilter`;
  - `metrics`: `count` / `distribution` / `gauge` sent to Sentry and Prometheus at once;
  - `telemetry`: the tm-obs half of each service's `init_telemetry` (OTLP export of metrics and
    logs, process runtime metrics, event-loop lag, Pyroscope profiles, the Phoenix span filter);
  - `rpc`: OpenTelemetry for the grpclib `SearchService`, client (backend) and server (agent)
    halves, plus the `x-internal-key` metadata name.

  `metrics` and `rpc` need only `opentelemetry-api` (a core dependency, a no-op without an
  SDK). `telemetry` and Sentry capture need the `observability` extra: services depend on
  `tendermate-shared[observability]`.

## Development

```bash
make install-dev   # uv sync --group dev
make proto         # regenerate src/grpc after editing proto/search_service.proto
make lint
uv run pytest
```

After editing the proto, run `make proto` and commit `src/grpc/`: the `Proto stubs freshness`
check fails the PR if the committed stubs are out of date.

## Releases

Every push to `main` is a release (`.github/workflows/release.yml`): the version in
`pyproject.toml` is bumped, tagged `vX.Y.Z` and published as a GitHub Release with the wheel and
sdist. The bump level comes from the commit messages since the last tag:

| Commit message                          | Bump  | Example         |
|-----------------------------------------|-------|-----------------|
| `<type>!: …` or `BREAKING CHANGE` in body | major | 0.4.0 → 1.0.0 |
| `feat: …` / `feat(scope): …`            | minor | 0.4.0 → 0.5.0   |
| anything else                           | patch | 0.4.0 → 0.4.1   |

To release a specific version, set it in the PR with `uv version X.Y.Z`: a version that differs
from the last tag is released as-is.

## Using a new version in a service

Services pin a tag and never pick up a new release on their own. After a release (check the
[Releases](https://github.com/TenderMate/tendermate-shared/releases) page for the number), open a
PR in each service that needs it:

```bash
git switch -c chore/shared-v0.5.0 origin/develop
uv add --raw "tendermate-shared @ git+https://github.com/TenderMate/tendermate-shared.git@v0.5.0"
uv run pytest
git commit -am "build: bump tendermate-shared to v0.5.0"
```

This changes only the dependency line in `pyproject.toml` and the pinned commit in `uv.lock`;
Dockerfiles, CI and Amvera need no changes (images install exactly what `uv.lock` pins). Merging
the PR through `develop` → `main` releases and deploys the service. Its `/health` then reports
`"shared_version": "0.5.0"`.

- Use `uv add --raw`. Plain `uv add` moves the URL into `[tool.uv.sources]` and leaves a bare
  `tendermate-shared` in the dependencies, which a non-uv install would look up on PyPI.
  `uv lock --upgrade-package tendermate-shared` does not move the tag.
- Not every service has to move at once: bump the ones that need the change, but don't let them
  drift apart for long (`/health` shows who runs what).
- Changing the gRPC contract: adding fields is compatible in any deploy order. Removing or
  incompatibly changing a field is a major release; deploy the server (searching-agent) before
  the client (backend).
