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

    def test_same_document_name_remains_tenant_scoped(self):
        with tempfile.TemporaryDirectory() as folder:
            service=Service(Path(folder)/'db')
            service.create_tenant('one','alice'); service.create_tenant('two','bob')
            service.put('alice','one','settings','first'); service.put('bob','two','settings','second')
            self.assertEqual(service.get('alice','one','settings'),'first')
            with self.assertRaises(PermissionError): service.get('alice','two','settings')
            self.assertEqual(service.get('bob','two','settings'),'second')
