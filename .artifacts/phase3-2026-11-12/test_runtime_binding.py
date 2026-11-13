"""Pinned rounds must not silently follow a replacement Runtime."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parents[1]/'src')]
import real_round_aux as aux

class BindingTests(unittest.TestCase):
    def test_existing_round_keeps_original_runtime_after_active_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);old=d/'real-round-1';old.mkdir();new=d/'real-round-2';new.mkdir()
            self.assertEqual(aux.runtime_binding(d,old),{'generation':'runtime2'})
            aux.write_once(old/'runtime-binding.json',aux.runtime_binding(d,old))
            (d/'active-runtime-binding.json').write_text('{"generation":"runtime3"}')
            self.assertEqual(aux.runtime_binding(d,old),{'generation':'runtime2'})
            self.assertEqual(aux.runtime_binding(d,new),{'generation':'runtime3'})
            self.assertEqual(aux.runtime_config_path(d,aux.runtime_binding(d,old)),d/'colab-config-runtime2.json')

    def test_untrusted_generation_cannot_select_other_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp)
            for value in ({'generation':'../runtime3'},{'generation':'runtime1'}, {'generation':'runtime3','config':'.auth'}):
                (d/'active-runtime-binding.json').write_text(json.dumps(value))
                with self.assertRaises(ValueError):aux.runtime_binding(d)

if __name__=='__main__':unittest.main(verbosity=2)
