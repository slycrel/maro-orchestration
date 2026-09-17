from pcd_sidecar.pmi import PMICache, question_cache_key
from pcd_sidecar.schema import parse_question


def test_baseline_is_cached_across_calls(fake_engine):
    q = parse_question("q1", {"type": "noul", "instructions": "did it work?"})
    cache = PMICache(fake_engine, enabled=True)

    first = cache.baseline_logprobs(q)
    second = cache.baseline_logprobs(q)

    assert first == second
    # only one score_candidates call reached the engine -- the second
    # baseline_logprobs() call was served from cache.
    assert len(fake_engine.calls) == 1
    assert cache.cache_size() == 1


def test_baseline_calls_never_see_the_real_state(fake_engine):
    q = parse_question("q1", {"type": "noul", "instructions": "did it work?"})
    cache = PMICache(fake_engine, enabled=True)
    cache.baseline_logprobs(q)
    prompt_used, _candidates = fake_engine.calls[0]
    assert "no state given" in prompt_used
    assert "top secret state payload" not in prompt_used


def test_different_questions_get_different_cache_entries(fake_engine):
    q1 = parse_question("q1", {"type": "noul", "instructions": "did it work?"})
    q2 = parse_question("q1", {"type": "noul", "instructions": "is it blocked?"})
    cache = PMICache(fake_engine, enabled=True)

    cache.baseline_logprobs(q1)
    cache.baseline_logprobs(q2)

    assert cache.cache_size() == 2
    assert question_cache_key(q1) != question_cache_key(q2)
    assert len(fake_engine.calls) == 2


def test_same_question_shape_same_key_different_object_identity():
    q1 = parse_question("q1", {"type": "score", "instructions": "rate it", "criteria": ["low", "high"]})
    q2 = parse_question("q1", {"type": "score", "instructions": "rate it", "criteria": ["low", "high"]})
    assert question_cache_key(q1) == question_cache_key(q2)


def test_disabled_pmi_never_calls_engine(fake_engine):
    q = parse_question("q1", {"type": "noul", "instructions": "did it work?"})
    cache = PMICache(fake_engine, enabled=False)
    assert cache.baseline_logprobs(q) is None
    assert fake_engine.calls == []
