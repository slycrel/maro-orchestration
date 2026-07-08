"""Tests for memory_quality module using synthetic fixture data only.

These tests do NOT read the live workspace (not in ~/.maro/workspace/).
All data is synthetic and created in temporary directories.
"""

import tempfile
from pathlib import Path

import pytest

# Import test support from memory_quality
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from memory_quality import (
    _extract_salient_words,
    _generate_self_retrieval_queries,
    _generate_hand_written_queries,
    _load_into_adapters,
    _evaluate_adapter,
    _compute_metrics,
    EvalQuery,
)


class TestExtractSalientWords:
    def test_extracts_significant_words(self):
        text = "The quick brown fox jumps over the lazy dog"
        words = _extract_salient_words(text, n=3)
        assert len(words) <= 3
        assert 'quick' in words
        assert 'brown' in words
        assert 'fox' in words

    def test_filters_stopwords(self):
        text = "the the the the quick"
        words = _extract_salient_words(text, n=5)
        assert 'the' not in words
        assert 'quick' in words

    def test_handles_empty_text(self):
        words = _extract_salient_words("")
        assert words == []

    def test_respects_n_parameter(self):
        text = "one two three four five six seven"
        words = _extract_salient_words(text, n=2)
        assert len(words) == 2


class TestGenerateQueries:
    def test_self_retrieval_queries(self):
        items = [
            {
                'kind': 'lesson',
                'content': 'The quick brown fox jumps over the lazy dog',
                'scope': '',
                'trust': 1.0,
                'provenance': {},
                'meta': {},
            },
            {
                'kind': 'outcome',
                'content': 'Task completed successfully with all tests passing',
                'scope': '',
                'trust': 1.0,
                'provenance': {},
                'meta': {},
            },
        ]
        queries = _generate_self_retrieval_queries(items)
        assert len(queries) == 2
        assert all(q.kind == 'self' for q in queries)
        assert all(q.text for q in queries)  # All non-empty

    def test_hand_written_queries(self):
        queries = _generate_hand_written_queries()
        assert len(queries) >= 3
        assert all(q.kind == 'probe' for q in queries)
        assert all(q.text for q in queries)


class TestLoadIntoAdapters:
    def test_loads_items_into_both_stores(self):
        """Verify items are loaded into jsonl and sqlite adapters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            items = [
                {
                    'kind': 'lesson',
                    'content': 'lesson one content here',
                    'scope': '',
                    'trust': 1.0,
                    'provenance': {},
                    'meta': {},
                },
                {
                    'kind': 'outcome',
                    'content': 'outcome two content there',
                    'scope': '',
                    'trust': 0.9,
                    'provenance': {},
                    'meta': {},
                },
            ]

            jsonl_store, sqlite_store, item_ids = _load_into_adapters(items, tmpdir_path)

            # Check stores have items
            jsonl_stats = jsonl_store.stats()
            sqlite_stats = sqlite_store.stats()

            assert jsonl_stats['items'] == 2
            assert sqlite_stats['items'] == 2

            # Check we can recall
            jsonl_results = jsonl_store.recall('lesson content')
            sqlite_results = sqlite_store.recall('lesson content')

            assert len(jsonl_results) > 0
            assert len(sqlite_results) > 0

    def test_preserves_item_metadata(self):
        """Verify trust and kind are preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            items = [
                {
                    'kind': 'decision',
                    'content': 'we chose sqlite over postgres database',
                    'scope': '',  # Global scope to ensure visibility
                    'trust': 0.75,
                    'provenance': {'reason': 'performance'},
                    'meta': {},
                },
            ]

            jsonl_store, sqlite_store, _ = _load_into_adapters(items, tmpdir_path)

            # Verify in jsonl
            jsonl_item = jsonl_store.recall('chose sqlite')[0]
            assert jsonl_item.kind == 'decision'
            assert jsonl_item.trust == 0.75
            assert jsonl_item.scope == ''
            assert jsonl_item.provenance['reason'] == 'performance'

            # Verify in sqlite
            sqlite_item = sqlite_store.recall('chose sqlite')[0]
            assert sqlite_item.kind == 'decision'
            assert sqlite_item.trust == 0.75


class TestEvaluateAdapter:
    def test_evaluates_single_query(self):
        """Basic retrieval evaluation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            items = [
                {
                    'kind': 'lesson',
                    'content': 'testing is important for reliability',
                    'scope': '',
                    'trust': 1.0,
                    'provenance': {},
                    'meta': {},
                },
            ]

            store, _, _ = _load_into_adapters(items, tmpdir_path)

            queries = [
                EvalQuery(text='testing reliability', kind='probe'),
            ]

            # Manually add the _expected_index attribute
            queries[0]._expected_index = 0  # type: ignore

            results, latency = _evaluate_adapter(
                store, 'test-adapter', queries, items, {0: 'item0'})

            assert len(results) == 1
            assert results[0].adapter_name == 'test-adapter'
            assert results[0].latency_ms > 0
            assert latency > 0

    def test_tracks_hit_at_1_and_5(self):
        """Verify hit@1 and hit@5 tracking."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create 10 items to test ranking
            items = [
                {
                    'kind': 'lesson',
                    'content': f'item number {i} with unique words foobar{i}',
                    'scope': '',
                    'trust': 1.0,
                    'provenance': {},
                    'meta': {},
                }
                for i in range(10)
            ]

            store, _, _ = _load_into_adapters(items, tmpdir_path)

            # Query that should match item 0
            queries = [
                EvalQuery(text='item number 0 foobar0', kind='self'),
            ]
            queries[0]._expected_index = 0  # type: ignore

            results, _ = _evaluate_adapter(
                store, 'test-adapter', queries, items, {i: f'id{i}' for i in range(10)})

            # Item 0 should rank high
            assert len(results) == 1
            assert results[0].rank > 0  # Found
            if results[0].rank == 1:
                assert results[0].hit_at_1
                assert results[0].hit_at_5


class TestComputeMetrics:
    def test_computes_hit_rates(self):
        """Verify metric computation."""
        from memory_quality import EvalResult

        results = [
            EvalResult(
                adapter_name='test',
                query_text='q1',
                hit_at_1=True,
                hit_at_5=True,
                mrr=1.0,
                latency_ms=10.0,
                rank=1,
            ),
            EvalResult(
                adapter_name='test',
                query_text='q2',
                hit_at_1=False,
                hit_at_5=True,
                mrr=0.5,
                latency_ms=15.0,
                rank=2,
            ),
            EvalResult(
                adapter_name='test',
                query_text='q3',
                hit_at_1=False,
                hit_at_5=False,
                mrr=0.0,
                latency_ms=5.0,
                rank=0,
            ),
        ]

        metrics = _compute_metrics(results)

        assert metrics['count'] == 3
        assert metrics['hit_at_1'] == 1/3  # 1 out of 3
        assert metrics['hit_at_5'] == 2/3  # 2 out of 3
        assert metrics['mrr'] == (1.0 + 0.5 + 0.0) / 3
        assert metrics['median_latency_ms'] == 10.0

    def test_handles_empty_results(self):
        """Handles empty result set gracefully."""
        metrics = _compute_metrics([])
        assert metrics['count'] == 0
        assert metrics['hit_at_1'] == 0.0
        assert metrics['hit_at_5'] == 0.0
        assert metrics['mrr'] == 0.0
        assert metrics['median_latency_ms'] == 0.0
