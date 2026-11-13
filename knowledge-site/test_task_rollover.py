from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server import NoteStore
from task_rollover import task_blocks


class RolloverTests(unittest.TestCase):
    def test_pending_tasks_preserve_details_but_not_examples_or_completed(self):
        source = '# Week\n\n- [ ] Pending\n  details\n  - detail bullet\n  - [x] done child\n  - [ ] next child\n- [X] Done\n- [x] Parent\n  - [ ] child of done\n\n```md\n- [ ] fake\n```\n\n    - [ ] code example\n\n## 完成记录\n- [ ] log example\n'
        blocks = task_blocks(source)
        self.assertEqual(blocks, ['- [ ] Pending\n  details\n  - detail bullet\n  - [ ] next child', '- Parent\n  - [ ] child of done'])

    def test_existing_week_backfill_dedup_concurrent_and_no_resurrection(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / 'content')
            with patch('server.current_week_start', return_value=date(2026, 11, 2)):
                previous = store.ensure_task_lists()['weekly']
            previous = store.save(dict(previous, content='# Previous\r\n- [ ] pending\r\n- [ ] pending\r\n- [ ] already here\r\n- [x] done\r\n'))
            original = store.note_path(previous['id']).read_bytes()
            existing = store.save({'title': '一周待办 · 2026-11-09', 'category': '待办清单', 'content': '# Current\n- [x] already here\n\n## 完成记录\nkeep records\n'}, create=True)
            with patch('server.current_week_start', return_value=date(2026, 11, 9)):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(lambda _: store.ensure_task_lists(), range(8)))
                current = results[0]['weekly']
                self.assertTrue(all(item['weekly'] == current for item in results))
                self.assertEqual(current['content'].count('- [ ] pending'), 1)
                self.assertNotIn('- [ ] already here', current['content'])
                self.assertNotIn('- [x] done', current['content'])
                self.assertIn('keep records', current['content'])
                self.assertTrue(current['content'].startswith(existing['content'].split('## 完成记录')[0]))
                removed = store.save(dict(current, content=current['content'].replace('- [ ] pending', '')))
                self.assertEqual(store.ensure_task_lists()['weekly'], removed)
                restarted = NoteStore(store.root)
                self.assertEqual(restarted.ensure_task_lists()['weekly'], removed)
            self.assertEqual(store.note_path(previous['id']).read_bytes(), original)

    def test_skipped_week_and_next_week_carry_unfinished_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / 'content')
            with patch('server.current_week_start', return_value=date(2026, 10, 26)):
                note = store.ensure_task_lists()['weekly']
                store.save(dict(note, content='- [ ] survive\n- [x] finished\n'))
            with patch('server.current_week_start', return_value=date(2026, 11, 9)):
                note = store.ensure_task_lists()['weekly']
                self.assertIn('- [ ] survive', note['content'])
                self.assertNotIn('finished', note['content'])
                store.save(dict(note, content=note['content'].replace('- [ ] survive', '- [x] survive')))
            with patch('server.current_week_start', return_value=date(2026, 11, 16)):
                self.assertNotIn('survive', store.ensure_task_lists()['weekly']['content'])

    def test_deleted_newer_week_does_not_resurrect_older_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / 'content')
            with patch('server.current_week_start', return_value=date(2026, 10, 26)):
                note = store.ensure_task_lists()['weekly']
                store.save(dict(note, content='- [ ] old task\n'))
            with patch('server.current_week_start', return_value=date(2026, 11, 2)):
                store.delete(store.ensure_task_lists()['weekly'])
            with patch('server.current_week_start', return_value=date(2026, 11, 9)):
                self.assertNotIn('old task', store.ensure_task_lists()['weekly']['content'])


if __name__ == '__main__':
    unittest.main()
