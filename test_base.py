from mad_responder import app
import unittest

ANNOTATION_ID = 352848
ANNOTATIONPROP_ID = 20713727
ASSIGNMENT_ID = 155
ASSIGNMENTPROP_ID = 70397
MEDIA_ID = 109
MEDIAPROP_ID = 134443
OPENED = 0
STARTED = 0

class TestDiagnostics(unittest.TestCase):
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

    def test_ping(self):
        response = self.app.get('/ping')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['rest']['error'], False)

class TestProcesses(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()

    def test_processlist(self):
        response = self.app.get('/processlist')
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.json['data']), 1)


class TestErrors(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()

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
        self.assertIn(response.json['rest']['error'], ["Error: division by zero", "MySQL error [1146]: Table 'mad.cv_term_vw' doesn't exist"])

class TestContent(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()

# ******************************************************************************
# * CV endpoints                                                               *
# ******************************************************************************
    def test_cv_ids(self):
        response = self.app.get('/cv_ids?name=body_type')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0], 70)
        response = self.app.get('/cv_ids?name=aint_no_such_cv')
        self.assertEqual(response.status_code, 404)

    def test_cvs(self):
        response = self.app.get('/cvs?id=70')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['name'], 'body_type')
        response = self.app.get('/cvs?id=70&_columns=display_name')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['display_name'], 'Body Type')
        response = self.app.get('/cvs?_columns=name&_sort=name')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['name'], 'assignment_types')
        response = self.app.get('/cvs?id=0')
        self.assertEqual(response.status_code, 404)

    def test_cvs_columns(self):
        response = self.app.get('/cvs/columns')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['columns']), 7)

    def test_cvs_id(self):
        response = self.app.get('/cvs/70')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['name'], 'body_type')
        response = self.app.get('/cvs/0')
        self.assertEqual(response.status_code, 404)

    def test_cvterm_ids(self):
        response = self.app.get('/cvterm_ids?cv_term=substack')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0], 1824)
        response = self.app.get('/cvterm_ids?cv_term=aint_no_such_cvterm')
        self.assertEqual(response.status_code, 404)

    def test_cverms(self):
        response = self.app.get('/cvterms?id=1824')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['cv_term'], 'substack')
        response = self.app.get('/cvterms?id=0')
        self.assertEqual(response.status_code, 404)

    def test_cvterms_columns(self):
        response = self.app.get('/cvterms/columns')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['columns']), 9)

    def test_cvterms_id(self):
        response = self.app.get('/cvterms/1824')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['cv_term'], 'substack')
        response = self.app.get('/cvterms/0')
        self.assertEqual(response.status_code, 404)

# ******************************************************************************
# * Annotation endpoints                                                       *
# ******************************************************************************
    def test_annotation_ids(self):
        response = self.app.get('/annotation_ids?media=00084_2328-2952_6339-6963_3385-4009')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 2)
        response = self.app.get('/annotation_ids?media=no_such_media')
        self.assertEqual(response.status_code, 404)

    def test_annotations(self):
        response = self.app.get('/annotations?media=00084_2328-2952_6339-6963_3385-4009')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 2)
        response = self.app.get('/annotations?media=no_such_media')
        self.assertEqual(response.status_code, 404)

    def test_annotation_columns(self):
        response = self.app.get('/annotations/columns')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['columns']), 11)

    def test_annotation_id(self):
        response = self.app.get('/annotations/' + str(ANNOTATION_ID))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['media'], 'hb_focused')
        response = self.app.get('/annotations/0')
        self.assertEqual(response.status_code, 404)

    def test_annotationprop_ids(self):
        response = self.app.get('/annotationprop_ids?type=manager_assignment_note')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 3)
        response = self.app.get('/annotationprop_ids?type=no_such_type')
        self.assertEqual(response.status_code, 404)

    def test_annotationprops(self):
        response = self.app.get('/annotationprops?type=manager_assignment_note')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 3)
        response = self.app.get('/annotationprops?type=no_such_type')
        self.assertEqual(response.status_code, 404)

    def test_annotationprop_columns(self):
        response = self.app.get('/annotationprops/columns')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['columns']), 8)

    def test_annotationprop_id(self):
        response = self.app.get('/annotationprops/' + str(ANNOTATIONPROP_ID))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['type'], 'blocks_annotated')
        response = self.app.get('/annotationprops/0')
        self.assertEqual(response.status_code, 404)

# ******************************************************************************
# * Assignment endpoints                                                       *
# ******************************************************************************
    def test_assignment_ids(self):
        response = self.app.get('/assignment_ids?user=shinomiyaa')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 2955)
        response = self.app.get('/assignment_ids?user=no_such_user')
        self.assertEqual(response.status_code, 404)

    def test_assignments(self):
        response = self.app.get('/assignments?user=shinomiyaa')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 2955)
        response = self.app.get('/assignments?user=no_such_user')
        self.assertEqual(response.status_code, 404)

    def test_assignment_columns(self):
        response = self.app.get('/assignments/columns')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['columns']), 11)

    def test_assignment_id(self):
        response = self.app.get('/assignments/' + str(ASSIGNMENT_ID))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['is_complete'], 1)
        response = self.app.get('/assignments/0')
        self.assertEqual(response.status_code, 404)

    def test_assignments_completed(self):
        response = self.app.get('/assignments_completed?annotation=psd_annot')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 1044)

    def test_assignments_open(self):
        response = self.app.get('/assignments_open')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 1)
        OPENED = len(response.json['data'])

    def test_assignments_started(self):
        response = self.app.get('/assignments_started')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 1)
        STARTED = len(response.json['data'])

    def test_assignments_remaining(self):
        response = self.app.get('/assignments_remaining')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), OPENED + STARTED)

    def test_assignmentprop_ids(self):
        response = self.app.get('/assignmentprop_ids?type=tbars_missing_psds')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 25)
        response = self.app.get('/assignmentprop_ids?type=no_such_type')
        self.assertEqual(response.status_code, 404)

    def test_assignmentprops(self):
        response = self.app.get('/assignmentprops?type=tbars_missing_psds')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 25)
        response = self.app.get('/assignmentprops?type=no_such_type')
        self.assertEqual(response.status_code, 404)

    def test_assignmentprop_columns(self):
        response = self.app.get('/assignmentprops/columns')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['columns']), 7)

    def test_assignmentprop_id(self):
        response = self.app.get('/assignmentprops/' + str(ASSIGNMENTPROP_ID))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['type'], 'assign_dvid_url')
        response = self.app.get('/assignmentprops/0')
        self.assertEqual(response.status_code, 404)

# ******************************************************************************
# * DVID endpoints                                                             *
# ******************************************************************************
    def test_dvid_instances(self):
        response = self.app.get('/dvid_instances?media=cx')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['url'], 'http://emdata2.int.janelia.org:8000')
        response = self.app.get('/dvid_instances?media=no_such_media')
        self.assertEqual(response.status_code, 404)

# ******************************************************************************
# * Media endpoints                                                            *
# ******************************************************************************
    def test_media_ids(self):
        response = self.app.get('/media_ids?type=stack')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 9)
        response = self.app.get('/media_ids?type=no_such_type')
        self.assertEqual(response.status_code, 404)

    def test_media(self):
        response = self.app.get('/media?type=stack')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 9)
        response = self.app.get('/media?type=no_such_type')
        self.assertEqual(response.status_code, 404)

    def test_media_columns(self):
        response = self.app.get('/media/columns')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['columns']), 6)

    def test_media_id(self):
        response = self.app.get('/media/' + str(MEDIA_ID))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['type'], 'stack')
        response = self.app.get('/media/0')
        self.assertEqual(response.status_code, 404)

    def test_mediaprop_ids(self):
        response = self.app.get('/mediaprop_ids?media=hb_recleave')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 9)
        response = self.app.get('/mediaprop_ids?media=no_such_media')
        self.assertEqual(response.status_code, 404)

    def test_mediaprops(self):
        response = self.app.get('/mediaprops?media=hb_recleave')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json['data']), 9)
        response = self.app.get('/mediaprops?media=no_such_media')
        self.assertEqual(response.status_code, 404)

    def test_mediaprop_columns(self):
        response = self.app.get('/mediaprops/columns')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['columns']), 6)

    def test_mediaprop_id(self):
        response = self.app.get('/mediaprops/' + str(MEDIAPROP_ID))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data'][0]['media'], 'hb_recleave')
        response = self.app.get('/mediaprops/0')
        self.assertEqual(response.status_code, 404)

# ******************************************************************************

if __name__ == '__main__':
    unittest.main()
