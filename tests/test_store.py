import sqlite3
import unittest
import tempfile
from pathlib import Path
from store import Store
class Tests(unittest.TestCase):
 def test_duplicate_and_reopen(self):
  s=Store(':memory:'); s.create(1,10,'Ajutor',1)
  with self.assertRaises(sqlite3.IntegrityError): s.create(1,11,'Bug',2)
  s.close(10); s.create(1,11,'Bug',2)
  self.assertEqual(s.active(1)['channel'],11)
 def test_claim(self):
  s=Store(':memory:'); s.create(1,10,'Ajutor',1)
  self.assertTrue(s.claim(10,2)); self.assertFalse(s.claim(10,3))
  s.close(10); self.assertFalse(s.claim(10,2))
 def test_persistence(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'test.db'; s=Store(p); s.create(1,10,'Ajutor',1); s.db.close()
   self.assertEqual(Store(p).active(1)['channel'],10)
