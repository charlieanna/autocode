import tempfile
import unittest
from pathlib import Path

from tenants import Service


class TenantTests(unittest.TestCase):
    def test_roles_and_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db'; service=Service(path)
            service.create_tenant('acme','alice'); service.grant('alice','acme','bob','editor')
            service.put('bob','acme','readme','hello')
            service=Service(path)
            self.assertEqual(service.get('alice','acme','readme'),'hello')
            self.assertEqual(service.documents('bob','acme'),{'readme':'hello'})
