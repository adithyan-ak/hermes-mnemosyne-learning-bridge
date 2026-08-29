import hashlib
import json

import pytest

from mnemosyne_learning_bridge import pending


def test_pending_list_excludes_tampered_records(tmp_path) -> None:
    staged = pending.stage_mutation(
        hermes_home=tmp_path,
        tool="mnemosyne_update",
        payload={"memory_id": "mem-1", "content": "approved"},
    )
    path = tmp_path / "pending" / "memory" / f"{staged['pending_id']}.json"
    record = json.loads(path.read_text())
    record["summary"] = "Benign-looking replacement summary"
    path.write_text(json.dumps(record))
    path.chmod(0o600)

    assert pending.list_pending(hermes_home=tmp_path) == []


def test_expired_pending_record_is_removed_and_cannot_be_applied(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pending.time, "time", lambda: 100.0)
    staged = pending.stage_mutation(
        hermes_home=tmp_path,
        tool="mnemosyne_update",
        payload={"memory_id": "mem-1", "content": "corrected"},
    )
    record_path = tmp_path / "pending" / "memory" / f"{staged['pending_id']}.json"
    assert record_path.exists()

    monkeypatch.setattr(
        pending.time,
        "time",
        lambda: 100.0 + pending.PENDING_TTL_SECONDS + 1,
    )

    with pytest.raises(ValueError, match="expired"):
        pending.load_pending(hermes_home=tmp_path, pending_id=staged["pending_id"])

    assert not record_path.exists()


def test_future_dated_pending_record_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pending.time, "time", lambda: 100.0)
    staged = pending.stage_mutation(
        hermes_home=tmp_path,
        tool="mnemosyne_update",
        payload={"memory_id": "mem-1", "content": "updated"},
    )
    pending_path = tmp_path / "pending" / "memory" / f"{staged['pending_id']}.json"
    record = json.loads(pending_path.read_text())
    record["created_at"] = 10_000.0
    pending_path.write_text(json.dumps(record))

    with pytest.raises(ValueError, match="future"):
        pending.load_pending(hermes_home=tmp_path, pending_id=staged["pending_id"])


def test_payload_tampering_cannot_be_approved_under_original_pending_id(tmp_path) -> None:
    staged = pending.stage_mutation(
        hermes_home=tmp_path,
        tool="mnemosyne_update",
        payload={"memory_id": "mem-1", "content": "approved"},
    )
    pending_path = tmp_path / "pending" / "memory" / f"{staged['pending_id']}.json"
    record = json.loads(pending_path.read_text())
    record["payload"]["content"] = "attacker replacement"
    canonical = json.dumps(
        record["payload"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    record["payload_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    pending_path.write_text(json.dumps(record))

    with pytest.raises(ValueError, match="integrity"):
        pending.load_pending(hermes_home=tmp_path, pending_id=staged["pending_id"])


def test_pending_record_can_be_claimed_only_once(tmp_path) -> None:
    staged = pending.stage_mutation(
        hermes_home=tmp_path,
        tool="mnemosyne_update",
        payload={"memory_id": "mem-1", "content": "approved"},
    )

    record, claimed_path = pending.claim_pending(
        hermes_home=tmp_path,
        pending_id=staged["pending_id"],
    )

    assert record["id"] == staged["pending_id"]
    assert claimed_path.is_file()
    with pytest.raises(ValueError, match=r"already claimed|not found"):
        pending.claim_pending(hermes_home=tmp_path, pending_id=staged["pending_id"])
