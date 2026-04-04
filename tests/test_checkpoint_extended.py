"""Tests for checkpoint.py extensions: export_human, branch_checkpoint, parent_loop_id."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_ckpt(loop_id="abc123", goal="Do something", steps=None, completed=None,
               parent_loop_id=""):
    from checkpoint import Checkpoint, CompletedStep
    if steps is None:
        steps = ["Step one", "Step two", "Step three"]
    if completed is None:
        completed = [
            CompletedStep(index=1, text="Step one", status="done", result="Result of step 1"),
            CompletedStep(index=2, text="Step two", status="blocked", result=""),
        ]
    return Checkpoint(
        loop_id=loop_id,
        goal=goal,
        project="myproject",
        steps=steps,
        completed=completed,
        parent_loop_id=parent_loop_id,
    )


# ---------------------------------------------------------------------------
# parent_loop_id field
# ---------------------------------------------------------------------------

class TestParentLoopId(unittest.TestCase):
    def test_default_empty(self):
        ckpt = _make_ckpt()
        self.assertEqual(ckpt.parent_loop_id, "")

    def test_to_dict_excludes_when_empty(self):
        ckpt = _make_ckpt()
        d = ckpt.to_dict()
        self.assertNotIn("parent_loop_id", d)

    def test_to_dict_includes_when_set(self):
        ckpt = _make_ckpt(parent_loop_id="parent123")
        d = ckpt.to_dict()
        self.assertEqual(d["parent_loop_id"], "parent123")

    def test_from_dict_round_trip(self):
        from checkpoint import Checkpoint
        ckpt = _make_ckpt(parent_loop_id="origin456")
        restored = Checkpoint.from_dict(ckpt.to_dict())
        self.assertEqual(restored.parent_loop_id, "origin456")

    def test_from_dict_missing_key_defaults_empty(self):
        from checkpoint import Checkpoint
        d = _make_ckpt().to_dict()
        d.pop("parent_loop_id", None)
        restored = Checkpoint.from_dict(d)
        self.assertEqual(restored.parent_loop_id, "")


# ---------------------------------------------------------------------------
# export_human
# ---------------------------------------------------------------------------

class TestExportHuman(unittest.TestCase):
    def _export(self, ckpt):
        from checkpoint import export_human
        with patch("checkpoint.load_checkpoint", return_value=ckpt):
            return export_human(ckpt.loop_id)

    def test_returns_string(self):
        md = self._export(_make_ckpt())
        self.assertIsInstance(md, str)

    def test_contains_goal(self):
        ckpt = _make_ckpt(goal="My unique mission goal")
        md = self._export(ckpt)
        self.assertIn("My unique mission goal", md)

    def test_contains_loop_id(self):
        ckpt = _make_ckpt(loop_id="deadbeef")
        md = self._export(ckpt)
        self.assertIn("deadbeef", md)

    def test_done_step_has_checkmark(self):
        md = self._export(_make_ckpt())
        self.assertIn("✓", md)

    def test_blocked_step_has_x(self):
        md = self._export(_make_ckpt())
        self.assertIn("✗", md)

    def test_pending_step_shown(self):
        md = self._export(_make_ckpt())
        # Step three is not in completed, should appear as pending
        self.assertIn("Step three", md)
        self.assertIn("pending", md)

    def test_result_text_included(self):
        md = self._export(_make_ckpt())
        self.assertIn("Result of step 1", md)

    def test_long_result_truncated(self):
        from checkpoint import CompletedStep
        long_result = "x" * 2000
        completed = [CompletedStep(index=1, text="Step one", status="done", result=long_result)]
        ckpt = _make_ckpt(completed=completed)
        md = self._export(ckpt)
        self.assertIn("truncated", md)

    def test_returns_none_for_missing(self):
        from checkpoint import export_human
        with patch("checkpoint.load_checkpoint", return_value=None):
            result = export_human("nonexistent")
        self.assertIsNone(result)

    def test_branch_info_shown(self):
        ckpt = _make_ckpt(parent_loop_id="origin123")
        md = self._export(ckpt)
        self.assertIn("origin123", md)

    def test_progress_line_present(self):
        md = self._export(_make_ckpt())
        # 1 done, 1 blocked, 1 pending out of 3 steps
        self.assertIn("1/3", md)

    def test_markdown_headers(self):
        md = self._export(_make_ckpt())
        self.assertIn("# Mission:", md)
        self.assertIn("## Steps", md)
        self.assertIn("### Step 1", md)


# ---------------------------------------------------------------------------
# branch_checkpoint
# ---------------------------------------------------------------------------

class TestBranchCheckpoint(unittest.TestCase):
    def _branch(self, source_ckpt):
        """Branch source_ckpt and return (new_loop_id, saved_ckpt_dict)."""
        from checkpoint import branch_checkpoint
        saved = {}

        def mock_load(loop_id):
            return source_ckpt if loop_id == source_ckpt.loop_id else None

        def mock_write(text, **_):
            saved["data"] = json.loads(text)

        with patch("checkpoint.load_checkpoint", side_effect=mock_load):
            with patch("checkpoint._checkpoint_path") as mock_path:
                mock_file = mock_path.return_value
                mock_file.write_text.side_effect = mock_write
                new_id = branch_checkpoint(source_ckpt.loop_id)

        return new_id, saved.get("data", {})

    def test_returns_new_loop_id(self):
        source = _make_ckpt(loop_id="source01")
        new_id, _ = self._branch(source)
        self.assertIsNotNone(new_id)
        self.assertNotEqual(new_id, "source01")

    def test_new_id_is_hex_string(self):
        source = _make_ckpt(loop_id="source01")
        new_id, _ = self._branch(source)
        self.assertTrue(all(c in "0123456789abcdef" for c in new_id))

    def test_parent_loop_id_set(self):
        source = _make_ckpt(loop_id="source01")
        _, data = self._branch(source)
        self.assertEqual(data.get("parent_loop_id"), "source01")

    def test_goal_copied(self):
        source = _make_ckpt(goal="Unique goal text")
        _, data = self._branch(source)
        self.assertEqual(data.get("goal"), "Unique goal text")

    def test_steps_copied(self):
        source = _make_ckpt()
        _, data = self._branch(source)
        self.assertEqual(data.get("steps"), source.steps)

    def test_completed_copied(self):
        source = _make_ckpt()
        _, data = self._branch(source)
        self.assertEqual(len(data.get("completed", [])), len(source.completed))

    def test_returns_none_for_missing_source(self):
        from checkpoint import branch_checkpoint
        with patch("checkpoint.load_checkpoint", return_value=None):
            result = branch_checkpoint("no_such_id")
        self.assertIsNone(result)

    def test_branch_is_independent(self):
        """Branch has different loop_id — resuming it won't touch the original."""
        source = _make_ckpt(loop_id="original00")
        new_id, data = self._branch(source)
        self.assertNotEqual(data.get("loop_id"), "original00")

    def test_branch_of_branch_tracks_chain(self):
        """Branching a branch records the immediate parent, not the root."""
        # First branch: parent = source
        source = _make_ckpt(loop_id="root")
        first_branch_id, first_data = self._branch(source)
        self.assertEqual(first_data.get("parent_loop_id"), "root")

        # Second branch from first branch
        from checkpoint import Checkpoint
        first_branch = Checkpoint.from_dict(first_data)
        first_branch.loop_id = first_branch_id

        second_id, second_data = self._branch(first_branch)
        # Immediate parent is first_branch, not root
        self.assertEqual(second_data.get("parent_loop_id"), first_branch_id)


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

class TestCheckpointCLI(unittest.TestCase):
    def _run_cli(self, args, ckpt=None):
        """Run _cli_main() with patched argv and load_checkpoint."""
        import io
        from unittest.mock import patch
        import checkpoint as cp

        output = io.StringIO()
        with patch.object(sys, "argv", ["poe-checkpoint"] + args):
            with patch("builtins.print", side_effect=lambda *a, **k: output.write(" ".join(str(x) for x in a) + "\n")):
                with patch("checkpoint.load_checkpoint", return_value=ckpt):
                    with patch("checkpoint.branch_checkpoint", return_value="newbranch1") as mock_branch:
                        with patch("checkpoint.export_human", return_value="# Export\n") as mock_export:
                            try:
                                cp._cli_main()
                            except SystemExit:
                                pass
        return output.getvalue(), mock_branch, mock_export

    def test_export_calls_export_human(self):
        _, _, mock_export = self._run_cli(["export", "abc123"])
        mock_export.assert_called_once_with("abc123")

    def test_branch_calls_branch_checkpoint(self):
        _, mock_branch, _ = self._run_cli(["branch", "source01"])
        mock_branch.assert_called_once_with("source01")

    def test_branch_prints_new_id(self):
        output, _, _ = self._run_cli(["branch", "source01"])
        self.assertIn("newbranch1", output)

    def test_export_not_found(self):
        import checkpoint as cp
        import io
        with patch.object(sys, "argv", ["poe-checkpoint", "export", "nope"]):
            with patch("checkpoint.export_human", return_value=None):
                with patch("builtins.print") as mock_print:
                    cp._cli_main()
        args = " ".join(str(a) for call in mock_print.call_args_list for a in call[0])
        self.assertIn("No checkpoint found", args)


if __name__ == "__main__":
    unittest.main()
