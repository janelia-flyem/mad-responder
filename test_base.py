from mad_responder import app
import unittest

ASSIGNMENT_ID = 155

class TestIntegrations(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()

# ******************************************************************************
# * Doc/diagnostic endpoints                                                   *
# ******************************************************************************
    def test_blank(self):
        response = self.app.get('/')
        self.assertEqual(response.status_code, 200)

    def test_spec(self):
        response = self.app.get('/spec')
        self.assertEqual(response.status_code, 200)

    def test_doc(self):
        response = self.app.get('/doc')
        self.assertEqual(response.status_code, 200)

    def test_stats(self):
        response = self.app.get('/stats')
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json['stats']['requests'], 0)

    def test_processlist_columns(self):
        response = self.app.get('/processlist/columns')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['columns']), 8)

    def test_processlist(self):
        response = self.app.get('/processlist')
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.json['processlist_data']), 1)

    def test_ping(self):
        response = self.app.get('/ping')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['rest']['error'], False)

# ******************************************************************************
# * Forced error endpoints                                                     *
# ******************************************************************************
    def test_sqlerror(self):
        response = self.app.get('/test_sqlerror')
        self.assertEqual(response.status_code, 500)
        self.assertIn("MySQL error", response.json['rest']['error'])

    def test_other_error(self):
        response = self.app.get('/test_other_error')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json['rest']['error'], "Error: division by zero")

# ******************************************************************************
# * Assignment endpoints                                                       *
# ******************************************************************************
    def test_assignment_ids(self):
        response = self.app.get('/assignment_ids?user=shinomiyaa')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['assignment_ids']), 2955)
        response = self.app.get('/assignment_ids?user=no_such_user')
        self.assertEqual(response.status_code, 404)

    def test_assignments(self):
        response = self.app.get('/assignments?user=shinomiyaa')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['assignment_data']), 2955)
        response = self.app.get('/assignments?user=no_such_user')
        self.assertEqual(response.status_code, 404)

    def test_assignment_columns(self):
        response = self.app.get('/assignments/columns')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['columns']), 11)

    def test_assignment_id(self):
        response = self.app.get('/assignments/' + str(ASSIGNMENT_ID))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['assignment_data'][0]['is_complete'], 1)
        response = self.app.get('/assignments/0')
        self.assertEqual(response.status_code, 404)

# ******************************************************************************

if __name__ == '__main__':
    unittest.main()
