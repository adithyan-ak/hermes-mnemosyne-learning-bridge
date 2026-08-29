from pathlib import Path

import pytest

from mnemosyne_learning_bridge.project import normalize_project_id


def test_normalize_project_id_prefers_normalized_git_remote(tmp_path: Path) -> None:
    project = normalize_project_id(
        workdir=tmp_path,
        git_remote="git@github.com:NousResearch/hermes-agent.git",
    )
    assert project.startswith("remote:sha256:")
    assert len(project.removeprefix("remote:sha256:")) == 64


def test_normalize_project_id_fallback_is_stable_and_path_scoped(tmp_path: Path) -> None:
    first = normalize_project_id(workdir=tmp_path, git_remote="")
    second = normalize_project_id(workdir=tmp_path / ".", git_remote=None)
    other = normalize_project_id(workdir=tmp_path / "other", git_remote=None)

    assert first == second
    assert first.startswith("workspace:")
    assert other != first


def test_remote_identity_preserves_port_uniqueness(tmp_path: Path) -> None:
    default = normalize_project_id(
        workdir=tmp_path,
        git_remote="https://example.com/org/repo.git",
    )
    alternate_port = normalize_project_id(
        workdir=tmp_path,
        git_remote="https://example.com:8443/org/repo.git",
    )

    assert alternate_port != default


def test_remote_identity_does_not_collapse_distinct_path_characters(
    tmp_path: Path,
) -> None:
    plus = normalize_project_id(
        workdir=tmp_path,
        git_remote="https://example.com/org/foo+bar.git",
    )
    hyphen = normalize_project_id(
        workdir=tmp_path,
        git_remote="https://example.com/org/foo-bar.git",
    )

    assert plus != hyphen


def test_remote_identity_ignores_ssh_username(tmp_path: Path) -> None:
    alice = normalize_project_id(
        workdir=tmp_path,
        git_remote="alice@example.com:repos/app.git",
    )
    bob = normalize_project_id(
        workdir=tmp_path,
        git_remote="bob@example.com:repos/app.git",
    )

    assert alice == bob


def test_remote_identity_preserves_scp_absolute_vs_relative_path(tmp_path: Path) -> None:
    absolute = normalize_project_id(
        workdir=tmp_path,
        git_remote="git@example.com:/srv/app.git",
    )
    relative = normalize_project_id(
        workdir=tmp_path,
        git_remote="git@example.com:srv/app.git",
    )

    assert absolute != relative


def test_remote_identity_normalizes_equivalent_ssh_and_https_remotes(
    tmp_path: Path,
) -> None:
    https = normalize_project_id(
        workdir=tmp_path,
        git_remote="https://github.com/NousResearch/hermes-agent.git",
    )
    scp = normalize_project_id(
        workdir=tmp_path,
        git_remote="git@github.com:NousResearch/hermes-agent.git",
    )
    ssh = normalize_project_id(
        workdir=tmp_path,
        git_remote="ssh://git@github.com/NousResearch/hermes-agent.git",
    )

    assert https == scp == ssh


def test_remote_identity_preserves_query_and_nondefault_transport_port(
    tmp_path: Path,
) -> None:
    https_a = normalize_project_id(
        workdir=tmp_path,
        git_remote="https://example.com/repos/app.git?tenant=a",
    )
    https_b = normalize_project_id(
        workdir=tmp_path,
        git_remote="https://example.com/repos/app.git?tenant=b",
    )
    ssh_nondefault = normalize_project_id(
        workdir=tmp_path,
        git_remote="ssh://example.com:2222/repos/app.git?tenant=a",
    )

    assert len({https_a, https_b, ssh_nondefault}) == 3


def test_remote_identity_supports_file_url_and_absolute_path(tmp_path: Path) -> None:
    file_url = normalize_project_id(
        workdir=tmp_path,
        git_remote="file:///srv/repos/app.git",
    )
    absolute_path = normalize_project_id(
        workdir=tmp_path,
        git_remote="/srv/repos/app.git",
    )

    assert file_url.startswith("remote:sha256:")
    assert absolute_path.startswith("remote:sha256:")
    assert file_url != absolute_path


@pytest.mark.parametrize(
    "control",
    ["\x00", "\x01", "\x07", "\t", "\x0b", "\x0c", "\x1b", "\x7f"],
)
def test_remote_identity_rejects_all_control_characters(tmp_path: Path, control: str) -> None:
    with pytest.raises(ValueError, match="control"):
        normalize_project_id(
            workdir=tmp_path,
            git_remote=f"https://example.com/org/{control}repo.git",
        )


@pytest.mark.parametrize("remote", [" remote", "remote ", "\tremote", "remote\t"])
def test_remote_identity_rejects_boundary_whitespace(tmp_path: Path, remote: str) -> None:
    with pytest.raises(ValueError, match=r"whitespace|control"):
        normalize_project_id(workdir=tmp_path, git_remote=remote)
