"""Unit tests for workspace directory resolution (`--dir` / `STREAM_WEAVER_DIR`) and `sw init`."""

from pathlib import Path

from typer.testing import CliRunner

from stream_weaver.cli.app import app, resolve_workspace_root

runner = CliRunner()


def test_resolve_workspace_root_priority(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit_stack"
    explicit.mkdir()
    env_stack = tmp_path / "env_stack"
    env_stack.mkdir()
    cwd_stack = tmp_path / "cwd_stack"
    (cwd_stack / "configs").mkdir(parents=True)
    plain_cwd = tmp_path / "plain_cwd"
    plain_cwd.mkdir()

    # 1. Explicit --dir wins over env and CWD
    assert (
        resolve_workspace_root(explicit, cwd=cwd_stack, env_override=str(env_stack))
        == explicit.resolve()
    )

    # 2. STREAM_WEAVER_DIR wins over CWD
    assert (
        resolve_workspace_root(None, cwd=cwd_stack, env_override=str(env_stack))
        == env_stack.resolve()
    )

    # 3. CWD containing configs/ wins over package fallback
    assert resolve_workspace_root(None, cwd=cwd_stack, env_override="") == cwd_stack.resolve()

    # 4. Plain CWD without configs/ falls back to package repository root
    pkg_root = Path(__file__).resolve().parents[2]
    assert resolve_workspace_root(None, cwd=plain_cwd, env_override="") == pkg_root


def test_sw_init_scaffolds_and_preserves_existing_env(tmp_path: Path) -> None:
    target = tmp_path / "my-stream-state"
    res = runner.invoke(app, ["init", str(target)])
    assert res.exit_code == 0, res.output
    assert (target / "configs").is_dir()
    assert (target / "architecture").is_dir()
    assert (target / ".env").is_file()
    assert (target / ".env.example").is_file()
    assert (target / ".gitignore").is_file()

    # Customize .env and verify re-running `sw init --force` NEVER clobbers .env
    custom_env = "AIOSTREAMS_URL=https://custom.example.com\nSECRET_RD_API_KEY=super_secret_token\n"
    (target / ".env").write_text(custom_env, encoding="utf-8")
    (target / ".env.example").write_text("# stale\n", encoding="utf-8")

    res_force = runner.invoke(app, ["init", str(target), "--force"])
    assert res_force.exit_code == 0, res_force.output
    assert (target / ".env").read_text(encoding="utf-8") == custom_env
    assert "AIOSTREAMS_URL=" in (target / ".env.example").read_text(encoding="utf-8")

    # Verify `sw diagram --dir <target>` works cleanly on a freshly initialized workspace
    res_diag = runner.invoke(app, ["diagram", "--dir", str(target)])
    assert res_diag.exit_code == 0, res_diag.output
    assert (target / "ARCHITECTURE.md").is_file()
    assert (target / "architecture" / "topology.c4").is_file()
