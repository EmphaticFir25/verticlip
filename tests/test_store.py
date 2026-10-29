import store


def make_clip(clip_id, score, job_id="job1"):
    return {"id": clip_id, "job_id": job_id, "score": score, "title": clip_id, "publish": []}


def test_job_round_trip(db):
    store.create_job("job1", status="queued", progress=0, message="Queued")
    store.update_job("job1", status="running", progress=40)
    job = store.get_job("job1")
    assert job["status"] == "running"
    assert job["progress"] == 40
    assert job["message"] == "Queued"
    assert job["clips"] == []


def test_missing_job_returns_none_and_update_raises(db):
    assert store.get_job("nope") is None
    try:
        store.update_job("nope", status="done")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def test_clips_come_back_sorted_by_score(db):
    store.create_job("job1", status="done")
    store.add_clips("job1", [make_clip("low", 30), make_clip("high", 90), make_clip("mid", 60)])
    assert [c["id"] for c in store.get_job("job1")["clips"]] == ["high", "mid", "low"]


def test_clips_are_scoped_to_their_job(db):
    store.create_job("job1", status="done")
    store.create_job("job2", status="done")
    store.add_clips("job1", [make_clip("a", 10, job_id="job1")])
    store.add_clips("job2", [make_clip("b", 10, job_id="job2")])
    assert [c["id"] for c in store.get_job("job1")["clips"]] == ["a"]
    assert store.get_clip("b")["job_id"] == "job2"


def test_publish_results_are_upserted_per_platform_and_account(db):
    store.create_job("job1", status="done")
    store.add_clips("job1", [make_clip("c1", 80)])
    store.set_publish_result("c1", "tiktok", "acct1", account_name="Creator", status="queued")
    store.set_publish_result("c1", "tiktok", "acct2", account_name="Other", status="queued")
    store.set_publish_result("c1", "tiktok", "acct1", status="uploaded", detail="sent to inbox")

    results = store.get_clip("c1")["publish"]
    assert len(results) == 2
    first = next(r for r in results if r["account_id"] == "acct1")
    assert first["status"] == "uploaded"
    assert first["account_name"] == "Creator"
    assert first["detail"] == "sent to inbox"


def test_publish_result_for_unknown_clip_raises(db):
    try:
        store.set_publish_result("ghost", "tiktok", "acct1", status="failed")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def test_data_survives_a_fresh_connection(db):
    """Each call opens its own connection, so this is what a restart looks like."""
    store.create_job("job1", status="done", message="Done")
    store.add_clips("job1", [make_clip("c1", 50)])
    store.set_publish_result("c1", "youtube_shorts", "yt1", status="uploaded", detail="video x (private)")
    assert db.exists()
    job = store.get_job("job1")
    assert job["message"] == "Done"
    assert job["clips"][0]["publish"][0]["detail"] == "video x (private)"


def test_jobs_list_newest_first_with_clips(db):
    store.create_job("old", status="done")
    store.create_job("new", status="done")
    store.add_clips("new", [make_clip("c1", 50, job_id="new")])
    jobs = store.list_jobs()
    assert [j["id"] for j in jobs] == ["new", "old"]
    assert jobs[0]["clips"][0]["id"] == "c1"
    assert jobs[0]["created"] > 0


def test_delete_job_removes_its_clips(db):
    store.create_job("job1", status="done")
    store.add_clips("job1", [make_clip("c1", 50)])
    assert store.delete_job("job1") is True
    assert store.get_job("job1") is None
    assert store.get_clip("c1") is None
    assert store.delete_job("job1") is False


def test_interrupt_unfinished_only_touches_queued_and_running(db):
    store.create_job("q", status="queued")
    store.create_job("r", status="running")
    store.create_job("d", status="done", message="Done")
    assert store.interrupt_unfinished() == 2
    assert store.get_job("q")["status"] == "error"
    assert store.get_job("r")["status"] == "error"
    assert store.get_job("d")["message"] == "Done"
