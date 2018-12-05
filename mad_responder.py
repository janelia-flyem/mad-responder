from datetime import datetime, timedelta
from flask import Flask, g, render_template, request, jsonify
from flask_cors import CORS
from flask_swagger import swagger
import os
import platform
import pymysql.cursors
import re
import sys
from time import time
from urllib.parse import parse_qs


# SQL statements
SQL = {
    'CVREL': "SELECT subject,relationship,object FROM cv_relationship_vw WHERE subject_id=%s OR object_id=%s",
    'CVTERMREL': "SELECT subject,relationship,object FROM cv_term_relationship_vw WHERE subject_id=%s OR object_id=%s",
}

__version__ = '0.1.0'
app = Flask(__name__)
app.config.from_pyfile("config.cfg")
CORS(app)
conn = pymysql.connect(host = app.config['MYSQL_DATABASE_HOST'],
	                   user = app.config['MYSQL_DATABASE_USER'],
	                   password = app.config['MYSQL_DATABASE_PASSWORD'],
	                   db = app.config['MYSQL_DATABASE_DB'],
	                   cursorclass = pymysql.cursors.DictCursor)
cursor = conn.cursor()
app.config['STARTTIME'] = time()
app.config['STARTDT'] = datetime.now()


@app.before_request
def before_request():
    global start_time
    start_time = time()
    g.db = conn
    g.c = cursor
    app.config['COUNTER'] += 1
    endpoint = request.endpoint if request.endpoint else '(Unknown)'
    app.config['ENDPOINTS'][endpoint] = app.config['ENDPOINTS'].get(endpoint, 0) + 1


@app.teardown_request
def teardown_request(exception):
    pass

# *****************************************************************************
# * Classes                                                                   *
# *****************************************************************************


class InvalidUsage(Exception):
    status_code = 400

    def __init__(self, message, status_code=None, payload=None):
        Exception.__init__(self)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        retval = dict(self.payload or ())
        retval['rest'] = {'error': self.message}
        return retval


# ******************************************************************************
# * Utility functions                                                          *
# ******************************************************************************


def sqlError (e):
    error_msg = ''
    try:
        error_msg = "MySQL error [%d]: %s" % (e.args[0], e.args[1])
    except IndexError:
        error_msg = "Error: %s" % e
    if error_msg:
        print(error_msg)
    return(error_msg)


def initializeResult():
    result = {"rest" : {'requester': request.remote_addr,
                        'url': request.url,
                        'endpoint': request.endpoint,
                        'error': False,
                        'elapsed_time': '',
                        'row_count': 0}}
    return result


def addKeyValuePair(key,val,separator,sql,bind):
    eprefix = ''
    if type(key) is not str:
        key = key.decode('utf-8')
    if re.search(r'[!><]$',key):
        match = re.search(r'[!><]$',key)
        eprefix = match.group(0)
        key = re.sub(r'[!><]$','',key)
    if type(val[0]) is not str:
        val[0] = val[0].decode('utf-8')
    if '*' in val[0]:
        val[0] = val[0].replace('*','%')
        if eprefix == '!':
            eprefix = ' NOT'
        else:
            eprefix = ''
        sql += separator + ' ' + key + eprefix + ' LIKE %s'
    else:
        sql += separator + ' ' + key + eprefix + '=%s'
    bind = bind + (val,)
    return sql,bind


def generateSQL(result,sql,query=False):
    bind = ()
    global IDCOLUMN
    IDCOLUMN = 0
    query_string = 'id='+str(query) if query else request.query_string
    order = ''
    if query_string:
        if type(query_string) is not str:
            query_string = query_string.decode('utf-8')
        pd = parse_qs(query_string)
        separator = ' AND' if ' WHERE ' in sql else ' WHERE'
        for key,val in pd.items():
            if key == '_sort':
                order = ' ORDER BY '  + val[0]
            elif key == '_columns':
                sql = sql.replace('*',val[0])
                varr = val[0].split(',')
                if 'id' in varr:
                    IDCOLUMN = 1
            elif key == '_distinct':
              if 'DISTINCT' not in sql:
                  sql = sql.replace('SELECT', 'SELECT DISTINCT')
            else:
                sql,bind = addKeyValuePair(key, val, separator, sql,bind)
                separator = ' AND'
    sql += order
    if (bind):
        result['rest']['sql_statement'] = sql % bind
    else:
        result['rest']['sql_statement'] = sql
    return sql,bind


def executeSQL(result,sql,container,query=False):
    sql,bind = generateSQL(result,sql,query)
    if app.config['DEBUG']:
        if (bind):
            print(sql % bind)
        else:
            print(sql)
    try:
        if (bind):
            g.c.execute(sql,bind)
        else:
            g.c.execute(sql)
        rows = g.c.fetchall()
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)
    result[container] = []
    if rows:
        result[container] = rows
        result['rest']['row_count'] = len(rows)
        return 1
    else:
        raise InvalidUsage("No rows returned for query %s" % (sql,), 404)
        return 0


def showColumns(result,table):
    result['columns'] = []
    try:
        g.c.execute("SHOW COLUMNS FROM "+table)
        rows = g.c.fetchall()
        if rows:
            result['columns'] = rows
            result['rest']['row_count'] = len(rows)
        return 1
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)
        return 0


def getAdditionalCVData(id):
    id = str(id)
    g.c.execute(SQL['CVREL'],(id,id))
    cvrel = g.c.fetchall()
    return cvrel


def getCVData(result,cvs):
    result['cv_data'] = []
    try:
        for c in cvs:
            cv = c
            if ('id' in c) and (not IDCOLUMN):
                cvrel = getAdditionalCVData(c['id'])
                cv['relationships'] = list(cvrel)
            result['cv_data'].append(cv)
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)

def getAdditionalCVTermData(id):
    id = str(id)
    g.c.execute(SQL['CVTERMREL'],(id,id))
    cvrel = g.c.fetchall()
    return cvrel


def getCVTermData(result,cvterms):
    result['cvterm_data'] = []
    try:
        for c in cvterms:
            cvterm = c
            if ('id' in c) and (not IDCOLUMN):
                cvtermrel = getAdditionalCVTermData(c['id'])
                cvterm['relationships'] = list(cvtermrel)
            result['cvterm_data'].append(cvterm)
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)


def generateResponse(result):
    global start_time
    result['rest']['elapsed_time'] = str(timedelta(seconds=(time()-start_time)))
    return jsonify(**result)


# ******************************************************************************
# * Endpoints                                                                  *
# ******************************************************************************


@app.errorhandler(InvalidUsage)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response


@app.route('/')
def showSwagger():
    return render_template('swagger_ui.html')


@app.route("/spec")
def spec():
    return getDocJson()


@app.route('/doc')
def getDocJson():
    swag = swagger(app)
    swag['info']['version'] = __version__
    swag['info']['title'] = "MAD Responder"
    return jsonify(swag)


@app.route("/stats")
def stats():
    '''
    Show stats
    Show uptime/requests statistics
    ---
    tags:
      - Diagnostics
    responses:
      200:
          description: Stats
      400:
          description: Stats could not be calculated
    '''
    result = initializeResult()
    db_connection = True
    try:
        g.db.ping(reconnect=False)
    except Exception as ex:
        template = "An exception of type {0} occurred. Arguments:{1!r}"
        message = template.format(type(ex).__name__, ex.args)
        result['rest']['error'] = 'Error: %s' % (message,)
        db_connection = False
    try:
        start = datetime.fromtimestamp(app.config['STARTTIME']).strftime('%Y-%m-%d %H:%M:%S')
        up_time = datetime.now() - app.config['STARTDT']
        result['stats'] = {"version": __version__,
                           "requests": app.config['COUNTER'],
                           "start_time": start,
                           "uptime": str(up_time),
                           "python": sys.version,
                           "pid": os.getpid(),
                           "endpoint_counts": app.config['ENDPOINTS'],
                           "database_connection": db_connection}
        if None in result['stats']['endpoint_counts']:
            del result['stats']['endpoint_counts']
    except Exception as ex:
        template = "An exception of type {0} occurred. Arguments:{1!r}"
        message = template.format(type(ex).__name__, ex.args)
        raise InvalidUsage('Error: %s' % (message,))
    return generateResponse(result)


@app.route('/processlist/columns', methods=['GET'])
def getProcesslistColumns():
    '''
    Get columns from the system processlist table
    Show the columns in the system processlist table, which may be used to filter results for the /processlist endpoints.
    ---
    tags:
      - Diagnostics
    responses:
      200:
          description: Columns in system processlist table
    '''
    result = initializeResult()
    showColumns(result, "information_schema.processlist")
    return generateResponse(result)


@app.route('/processlist', methods=['GET'])
def getProcesslistInfo():
    '''
    Get processlist information (with filtering)
    Return a list of processlist entries (rows from the system processlist table). The caller can filter on any of the columns in the system processlist table. Inequalities (!=) and some relational operations (&lt;= and &gt;=) are supported. Wildcards are supported (use "*"). Specific columns from the system processlist table can be returned with the _columns key. The returned list may be ordered by specifying a column with the _sort key. In both cases, multiple columns would be separated by a comma.
    ---
    tags:
      - Diagnostics
    responses:
      200:
          description: List of information for one or database processes
      404:
          description: Processlist information not found
    '''
    result = initializeResult()
    executeSQL(result,'SELECT * FROM information_schema.processlist', 'processlist_data')
    for row in result['processlist_data']:
        row['HOST'] = 'None' if row['HOST'] is None else row['HOST'].decode("utf-8")
    return generateResponse(result)


@app.route('/processlist/host', methods=['GET'])
def getProcesslistHostInfo(): # pragma: no cover
    '''
    Get processlist information for this host
    Return a list of processlist entries (rows from the system processlist table) for this host.
    ---
    tags:
      - Diagnostics
    responses:
      200:
          description: Database process list information for the current host
      404:
          description: Processlist information not found
    '''
    result = initializeResult()
    hostname = platform.node() + '%'
    try:
        sql = "SELECT * FROM information_schema.processlist WHERE host LIKE %s"
        result['rest']['sql_statement'] = sql % hostname
        g.c.execute(sql, (hostname,))
        rows = g.c.fetchall()
        result['rest']['row_count'] = len(rows)
        for row in rows:
            row['HOST'] = 'None' if row['HOST'] is None else row['HOST'].decode("utf-8")
        result['processlist_data'] = rows
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)
    return generateResponse(result)


@app.route("/ping")
def pingdb():
    '''
    Ping the database connection
    Ping the database connection and reconnect if needed
    ---
    tags:
      - Diagnostics
    responses:
      200:
          description: Ping successful
      400:
          description: Ping unsuccessful
    '''
    result = initializeResult()
    try:
        g.db.ping()
    except Exception as e:
        raise InvalidUsage(sqlError(e), 400)
    return generateResponse(result)


# ******************************************************************************
# * Test endpoints                                                             *
# ******************************************************************************
@app.route('/test_sqlerror', methods=['GET'])
def testsqlerror():
    result = initializeResult()
    try:
        sql = "SELECT some_column FROM non_existent_table"
        result['rest']['sql_statement'] = sql
        g.c.execute(sql)
        rows = g.c.fetchall()
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)


@app.route('/test_other_error', methods=['GET'])
def testothererror():
    result = initializeResult()
    try:
        testval = 4 / 0
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)


# ******************************************************************************
# * CV/CV term endpoints                                                       *
# ******************************************************************************
@app.route('/cvs/columns', methods=['GET'])
def getCVColumns():
    '''
    Get columns from cv table
    Show the columns in the cv table, which may be used to filter results for the /cvs and /cv_ids endpoints.
    ---
    tags:
      - CV
    responses:
      200:
          description: Columns in cv table
    '''
    result = initializeResult()
    showColumns(result,"cv")
    return generateResponse(result)


@app.route('/cv_ids', methods=['GET'])
def getCVIds():
    '''
    Get CV IDs (with filtering)
    Return a list of CV IDs. The caller can filter on any of the columns in the cv table. Inequalities (!=) and some relational operations (&lt;= and &gt;=) are supported. Wildcards are supported (use "*"). The returned list may be ordered by specifying a column with the _sort key. Multiple columns should be separated by a comma.
    ---
    tags:
      - CV
    responses:
      200:
          description: List of one or more CV IDs
      404:
          description: CVs not found
    '''
    result = initializeResult()
    if executeSQL(result,'SELECT id FROM cv','temp'):
        result['cv_ids'] = []
        for c in result['temp']:
            result['cv_ids'].append(c['id'])
        del result['temp']
    return generateResponse(result)


@app.route('/cvs/<string:id>', methods=['GET'])
def getCVById(id):
    '''
    Get CV information for a given ID
    Given an ID, return a row from the cv table. Specific columns from the cv table can be returned with the _columns key. Multiple columns should be separated by a comma.
    ---
    tags:
      - CV
    parameters:
      - in: path
        name: id
        type: string
        required: true
        description: CV ID
    responses:
      200:
          description: Information for one CV
      404:
          description: CV ID not found
    '''
    result = initializeResult()
    if executeSQL(result,'SELECT * FROM cv','temp',id):
        getCVData(result,result['temp'])
        del result['temp']
    return generateResponse(result)


@app.route('/cvs', methods=['GET'])
def getCVInfo():
    '''
    Get CV information (with filtering)
    Return a list of CVs (rows from the cv table). The caller can filter on any of the columns in the cv table. Inequalities (!=) and some relational operations (&lt;= and &gt;=) are supported. Wildcards are supported (use "*"). Specific columns from the cv table can be returned with the _columns key. The returned list may be ordered by specifying a column with the _sort key. In both cases, multiple columns would be separated by a comma.
    ---
    tags:
      - CV
    responses:
      200:
          description: List of information for one or more CVs
      404:
          description: CVs not found
    '''
    result = initializeResult()
    if executeSQL(result,'SELECT * FROM cv','temp'):
        getCVData(result,result['temp'])
        del result['temp']
    return generateResponse(result)


@app.route('/cv', methods=['OPTIONS', 'POST'])
def addCV(): # pragma: no cover
    '''
    Add CV
    ---
    tags:
      - CV
    parameters:
      - in: query
        name: name
        type: string
        required: true
        description: CV name
      - in: query
        name: definition
        type: string
        required: true
        description: CV description
      - in: query
        name: display_name
        type: string
        required: false
        description: CV display name (defaults to CV name)
      - in: query
        name: version
        type: string
        required: false
        description: CV version (defaults to 1)
      - in: query
        name: is_current
        type: string
        required: false
        description: is CV current? (defaults to 1)
    responses:
      200:
          description: CV added
      400:
          description: Missing arguments
    '''
    result = initializeResult()
    if request.method == 'OPTIONS':
        return generateResponse(result)
    pd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            pd[i] = request.form[i]
    missing = ''
    for p in ['name','definition']:
        if p not in pd:
            missing = missing + p + ' '
    if missing:
        raise InvalidUsage('Missing arguments: ' + missing)
    if 'display_name' not in pd:
      pd['display_name'] = pd['name']
    if 'version' not in pd:
      pd['version'] = 1
    if 'is_current' not in pd:
      pd['is_current'] = 1
    if not result['rest']['error']:
        try:
            bind = (pd['name'],pd['definition'],pd['display_name'],pd['version'],pd['is_current'],)
            result['rest']['sql_statement'] = SQL['INSERT_CV'] % bind
            g.c.execute(SQL['INSERT_CV'],bind)
            result['rest']['row_count'] = g.c.rowcount
            result['rest']['inserted_id'] = g.c.lastrowid
            g.db.commit()
        except Exception as e:
            raise InvalidUsage(sqlError(e), 500)
            g.db.rollback()
    return generateResponse(result)


@app.route('/cvterms/columns', methods=['GET'])
def getCVTermColumns():
    '''
    Get columns from cv_term_vw table
    Show the columns in the cv_term_vw table, which may be used to filter results for the /cvterms and /cvterm_ids endpoints.
    ---
    tags:
      - CV
    responses:
      200:
          description: Columns in cv_term_vw table
    '''
    result = initializeResult()
    showColumns(result,"cv_term_vw")
    return generateResponse(result)


@app.route('/cvterm_ids', methods=['GET'])
def getCVTermIds():
    '''
    Get CV term IDs (with filtering)
    Return a list of CV term IDs. The caller can filter on any of the columns in the cv_term_vw table. Inequalities (!=) and some relational operations (&lt;= and &gt;=) are supported. Wildcards are supported (use "*"). The returned list may be ordered by specifying a column with the _sort key. Multiple columns should be separated by a comma.
    ---
    tags:
      - CV
    responses:
      200:
          description: List of one or more CV term IDs
      404:
          description: CV terms not found
    '''
    result = initializeResult()
    if executeSQL(result,'SELECT id FROM cv_term_vw','temp'):
        result['cvterm_ids'] = []
        for c in result['temp']:
            result['cvterm_ids'].append(c['id'])
        del result['temp']
    return generateResponse(result)


@app.route('/cvterms/<string:id>', methods=['GET'])
def getCVTermById(id):
    '''
    Get CV term information for a given ID
    Given an ID, return a row from the cv_term_vw table. Specific columns from the cv_term_vw table can be returned with the _columns key. Multiple columns should be separated by a comma.
    ---
    tags:
      - CV
    parameters:
      - in: path
        name: id
        type: string
        required: true
        description: CV term ID
    responses:
      200:
          description: Information for one CV term
      404:
          description: CV term ID not found
    '''
    result = initializeResult()
    if executeSQL(result,'SELECT * FROM cv_term_vw','temp',id):
        getCVTermData(result,result['temp'])
        del result['temp']
    return generateResponse(result)


@app.route('/cvterms', methods=['GET'])
def getCVTermInfo():
    '''
    Get CV term information (with filtering)
    Return a list of CV terms (rows from the cv_term_vw table). The caller can filter on any of the columns in the cv_term_vw table. Inequalities (!=) and some relational operations (&lt;= and &gt;=) are supported. Wildcards are supported (use "*"). Specific columns from the cv_term_vw table can be returned with the _columns key. The returned list may be ordered by specifying a column with the _sort key. In both cases, multiple columns would be separated by a comma.
    ---
    tags:
      - CV
    responses:
      200:
          description: List of information for one or more CV terms
      404:
          description: CV terms not found
    '''
    result = initializeResult()
    if executeSQL(result,'SELECT * FROM cv_term_vw','temp'):
        getCVTermData(result,result['temp'])
        del result['temp']
    return generateResponse(result)


@app.route('/cvterm', methods=['OPTIONS', 'POST'])
def addCVTerm(): # pragma: no cover
    '''
    Add CV term
    ---
    tags:
      - CV
    parameters:
      - in: query
        name: cv
        type: string
        required: true
        description: CV name
      - in: query
        name: name
        type: string
        required: true
        description: CV term name
      - in: query
        name: definition
        type: string
        required: true
        description: CV term description
      - in: query
        name: display_name
        type: string
        required: false
        description: CV term display name (defaults to CV term name)
      - in: query
        name: is_current
        type: string
        required: false
        description: is CV term current? (defaults to 1)
      - in: query
        name: data_type
        type: string
        required: false
        description: data type (defaults to text)
    responses:
      200:
          description: CV term added
      400:
          description: Missing arguments
    '''
    result = initializeResult()
    if request.method == 'OPTIONS':
        return generateResponse(result)
    pd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            pd[i] = request.form[i]
    missing = ''
    for p in ['cv','name','definition']:
        if p not in pd:
            missing = missing + p + ' '
    if missing:
        raise InvalidUsage('Missing arguments: ' + missing)
    if 'display_name' not in pd:
      pd['display_name'] = pd['name']
    if 'is_current' not in pd:
      pd['is_current'] = 1
    if 'data_type' not in pd:
      pd['data_type'] = 'text'
    if not result['rest']['error']:
        try:
            bind = (pd['cv'],pd['name'],pd['definition'],pd['display_name'],pd['is_current'],pd['data_type'],)
            result['rest']['sql_statement'] = SQL['INSERT_CVTERM'] % bind
            g.c.execute(SQL['INSERT_CVTERM'],bind)
            result['rest']['row_count'] = g.c.rowcount
            result['rest']['inserted_id'] = g.c.lastrowid
            g.db.commit()
        except Exception as e:
            raise InvalidUsage(sqlError(e), 500)
            g.db.rollback()
    return generateResponse(result)


# ******************************************************************************
# * Assignment endpoints                                                       *
# ******************************************************************************
@app.route('/assignments/columns', methods=['GET'])
def getAssignmentColumns():
    '''
    Get columns from assignment_vw table
    Show the columns in the assignment_vw table, which may be used to filter results for the /assignments and /assignment_ids endpoints.
    ---
    tags:
      - Assignment
    responses:
      200:
          description: Columns in assignment_vw table
    '''
    result = initializeResult()
    showColumns(result, "assignment_vw")
    return generateResponse(result)


@app.route('/assignment_ids', methods=['GET'])
def getAssignmentIds():
    '''
    Get assignment IDs (with filtering)
    Return a list of assignment IDs. The caller can filter on any of the columns in the assignment_vw table. Inequalities (!=) and some relational operations (&lt;= and &gt;=) are supported. Wildcards are supported (use "*"). The returned list may be ordered by specifying a column with the _sort key. Multiple columns should be separated by a comma.
    ---
    tags:
      - Assignment
    responses:
      200:
          description: List of one or more assignment IDs
      404:
          description: Assignments not found
    '''
    result = initializeResult()
    if executeSQL(result,'SELECT id FROM assignment_vw', 'temp'):
        result['assignment_ids'] = []
        for c in result['temp']:
            result['assignment_ids'].append(c['id'])
        del result['temp']
    return generateResponse(result)


@app.route('/assignments/<string:id>', methods=['GET'])
def getAssignmentsById(id):
    '''
    Get assignment information for a given ID
    Given an ID, return a row from the assignment_vw table. Specific columns from the assignment_vw table can be returned with the _columns key. Multiple columns should be separated by a comma.
    ---
    tags:
      - Assignment
    parameters:
      - in: path
        name: id
        type: string
        required: true
        description: assignment ID
    responses:
      200:
          description: Information for one assignment
      404:
          description: Assignment ID not found
    '''
    result = initializeResult()
    executeSQL(result,'SELECT * FROM assignment_vw', 'assignment_data',id)
    return generateResponse(result)


@app.route('/assignments', methods=['GET'])
def getAssignmentInfo():
    '''
    Get assignment information (with filtering)
    Return a list of assignments (rows from the assignment_vw table). The caller can filter on any of the columns
    in the assignment_vw table. Inequalities (!=) and some relational operations (&lt;= and &gt;=) are supported.
    Wildcards are supported (use "*"). Specific columns from the assignment_vw table can be returned with the _columns
    key. The returned list may be ordered by specifying a column with the _sort key. In both cases, multiple columns
    would be separated by a comma.
    ---
    tags:
      - Assignment
    responses:
      200:
          description: List of information for one or more assignments
      404:
          description: Assignments not found
    '''
    result = initializeResult()
    executeSQL(result,'SELECT * FROM assignment_vw', 'assignment_data')
    return generateResponse(result)


@app.route('/assignments_completed', methods=['GET'])
def getAssignmentCompletedInfo():
    '''
    Get completed assignment information (with filtering)
    Return a list of assignments (rows from the assignment_vw table) that have been completed. The caller
    can filter on any of the columns in the assignment_vw table. Inequalities (!=) and some relational operations
    (&lt;= and &gt;=) are supported. Wildcards are supported (use "*"). Specific columns from the assignment_vw
    table can be returned with the _columns key. The returned list may be ordered by specifying a column with the
    _sort key. In both cases, multiple columns would be separated by a comma.
    ---
    tags:
      - Assignment
    responses:
      200:
          description: List of information for one or more completed assignments
      404:
          description: Assignments not found
    '''
    result = initializeResult()
    executeSQL(result,'SELECT * FROM assignment_vw WHERE is_complete=1', 'assignment_data')
    return generateResponse(result)


@app.route('/assignments_remaining', methods=['GET'])
def getAssignmentRemainingInfo():
    '''
    Get remaining assignment information (with filtering)
    Return a list of assignments (rows from the assignment_vw table) that haven't been completed yet. The caller
    can filter on any of the columns in the assignment_vw table. Inequalities (!=) and some relational operations
    (&lt;= and &gt;=) are supported. Wildcards are supported (use "*"). Specific columns from the assignment_vw
    table can be returned with the _columns key. The returned list may be ordered by specifying a column with the
    _sort key. In both cases, multiple columns would be separated by a comma.
    ---
    tags:
      - Assignment
    responses:
      200:
          description: List of information for one or more remaining assignments
      404:
          description: Assignments not found
    '''
    result = initializeResult()
    executeSQL(result,'SELECT * FROM assignment_vw WHERE is_complete=0', 'assignment_data')
    return generateResponse(result)


@app.route('/assignmentprops/columns', methods=['GET'])
def getAssignmentpropColumns():
    '''
    Get columns from assignment_property_vw table
    Show the columns in the assignment_property_vw table, which may be used to filter results for the
    /assignmentprops and /assignmentprop_ids endpoints.
    ---
    tags:
      - Assignment
    responses:
      200:
          description: Columns in assignment_prop_vw table
    '''
    result = initializeResult()
    showColumns(result, "assignment_property_vw")
    return generateResponse(result)


@app.route('/assignmentprop_ids', methods=['GET'])
def getAssignmentpropIds():
    '''
    Get assignment property IDs (with filtering)
    Return a list of assignment property IDs. The caller can filter on any of the columns in the
    assignment_property_vw table. Inequalities (!=) and some relational operations (&lt;= and &gt;=)
    are supported. Wildcards are supported (use "*"). The returned list may be ordered by specifying
    a column with the _sort key. Multiple columns should be separated by a comma.
    ---
    tags:
      - Assignment
    responses:
      200:
          description: List of one or more assignment property IDs
      404:
          description: Assignment properties not found
    '''
    result = initializeResult()
    if executeSQL(result,'SELECT id FROM assignment_property_vw', 'temp'):
        result['assignmentprop_ids'] = []
        for c in result['temp']:
            result['assignmentprop_ids'].append(c['id'])
        del result['temp']
    return generateResponse(result)


@app.route('/assignmentprops/<string:id>', methods=['GET'])
def getAssignmentpropsById(id):
    '''
    Get assignment property information for a given ID
    Given an ID, return a row from the assignment_property_vw table. Specific columns from the
    assignment_property_vw table can be returned with the _columns key. Multiple columns should
    be separated by a comma.
    ---
    tags:
      - Assignment
    parameters:
      - in: path
        name: id
        type: string
        required: true
        description: assignment property ID
    responses:
      200:
          description: Information for one assignment property
      404:
          description: Assignment property ID not found
    '''
    result = initializeResult()
    executeSQL(result,'SELECT * FROM assignment_property_vw', 'assignmentprop_data',id)
    return generateResponse(result)


@app.route('/assignmentprops', methods=['GET'])
def getAssignmentpropInfo():
    '''
    Get assignment property information (with filtering)
    Return a list of assignment properties (rows from the assignment_property_vw table). The caller
    can filter on any of the columns in the assignment_property_vw table. Inequalities (!=) and some
    relational operations (&lt;= and &gt;=) are supported. Wildcards are supported (use "*"). Specific
    columns from the assignment_property_vw table can be returned with the _columns key. The returned
    list may be ordered by specifying a column with the _sort key. In both cases, multiple columns
    would be separated by a comma.
    ---
    tags:
      - Assignment
    responses:
      200:
          description: List of information for one or more assignment properties
      404:
          description: Assignment properties not found
    '''
    result = initializeResult()
    executeSQL(result,'SELECT * FROM assignment_property_vw', 'assignmentprop_data')
    return generateResponse(result)


# ******************************************************************************
# * User endpoints                                                             *
# ******************************************************************************
@app.route('/users', methods=['GET'])
def getUserInfo():
    '''
    Get user information (with filtering)
    Return a list of users along with their properties (rows from the user_property_vw table). The caller can filter
    on any of the columns in the user_property_vw table. Inequalities (!=) and some relational operations
    (&lt;= and &gt;=) are supported. Wildcards are supported (use "*"). Specific columns from the user_property_vw
    table can be returned with the _columns key. The returned list may be ordered by specifying a column with
    the _sort key. In both cases, multiple columns would be separated by a comma.
    ---
    tags:
      - User
    responses:
      200:
          description: List of information for one or more users
      404:
          description: Users not found
    '''
    result = initializeResult()
    executeSQL(result,'SELECT * FROM user_property_vw', 'user_data')
    return generateResponse(result)


# ******************************************************************************


if __name__ == '__main__':
    app.run(debug=True)
