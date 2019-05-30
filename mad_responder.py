import os
import platform
import re
import sys
from datetime import datetime, timedelta
from time import time
from urllib.parse import parse_qs
from flask import Flask, g, render_template, request, jsonify
from flask.json import JSONEncoder
from flask_cors import CORS
from flask_swagger import swagger
from jwt import decode
import pymysql.cursors


# SQL statements
SQL = {
    'CVREL': "SELECT subject,relationship,object FROM cv_relationship_vw "
             + "WHERE subject_id=%s OR object_id=%s",
    'CVTERMREL': "SELECT subject,relationship,object FROM "
                 + "cv_term_relationship_vw WHERE subject_id=%s OR "
                 + "object_id=%s",
}

class CustomJSONEncoder(JSONEncoder):
    def default(self, obj):
        try:
            if isinstance(obj, datetime):
                return obj.strftime('%a, %-d %b %Y %H:%M:%S')
            iterable = iter(obj)
        except TypeError:
            pass
        else:
            return list(iterable)
        return JSONEncoder.default(self, obj)  # pylint: disable=E0202

__version__ = '0.1.0'
app = Flask(__name__)
app.json_encoder = CustomJSONEncoder
app.config.from_pyfile("config.cfg")
CORS(app)
conn = pymysql.connect(host=app.config['MYSQL_DATABASE_HOST'],
                       user=app.config['MYSQL_DATABASE_USER'],
                       password=app.config['MYSQL_DATABASE_PASSWORD'],
                       db=app.config['MYSQL_DATABASE_DB'],
                       cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()
app.config['STARTTIME'] = time()
app.config['STARTDT'] = datetime.now()
IDCOLUMN = 0
start_time = ''
CVTERMS = dict()


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

# *****************************************************************************
# * Flask                                                                     *
# *****************************************************************************


@app.before_request
def before_request():
    global start_time, CVTERMS
    start_time = time()
    g.db = conn
    g.c = cursor
    app.config['COUNTER'] += 1
    endpoint = request.endpoint if request.endpoint else '(Unknown)'
    app.config['ENDPOINTS'][endpoint] = app.config['ENDPOINTS'].get(endpoint, 0) + 1
    if request.method == 'OPTIONS':
        result = initializeResult()
        return generateResponse(result)
    if not len(CVTERMS):
        try:
            g.c.execute('SELECT cv,cv_term,id FROM cv_term_vw ORDER BY 1,2')
            rows = g.c.fetchall()
            for row in rows:
                if row['cv'] not in CVTERMS:
                    CVTERMS[row['cv']] = dict()
                CVTERMS[row['cv']][row['cv_term']] = row['id']
        except Exception as e:
            raise InvalidUsage(sqlError(e), 500)


# ******************************************************************************
# * Utility functions                                                          *
# ******************************************************************************


def sqlError(e):
    error_msg = ''
    try:
        error_msg = "MySQL error [%d]: %s" % (e.args[0], e.args[1])
    except IndexError:
        error_msg = "Error: %s" % e
    if error_msg:
        print(error_msg)
    return error_msg


def initializeResult():
    result = {"rest": {'requester': request.remote_addr,
                       'url': request.url,
                       'endpoint': request.endpoint,
                       'error': False,
                       'elapsed_time': '',
                       'row_count': 0}}
    if 'Authorization' in  request.headers:
        token = re.sub(r'Bearer\s+', '', request.headers['Authorization'])
        dtok = dict()
        try:
            dtok = decode(token, verify=False)
            if 'user_name' in dtok:
                result['rest']['user'] = dtok['user_name']
                app.config['USERS'][dtok['user_name']] = app.config['USERS'].get(dtok['user_name'], 0) + 1
        except:
            print("Invalid token received")
    if app.config['REQUIRE_AUTH'] and request.method in ['DELETE', 'POST']:
        if 'Authorization' not in request.headers:
            raise InvalidUsage('You must authorize to use this endpoint', 401)
        if not {'exp', 'user_name'} <= set(dtok):
            raise InvalidUsage('Invalid authorization token', 401)
        now = time()
        if now > dtok['exp']:
            raise InvalidUsage('Authorization token is expired', 401)
    return result


def addKeyValuePair(key, val, separator, sql, bind):
    eprefix = ''
    if not isinstance(key, str):
        key = key.decode('utf-8')
    if re.search(r'[!><]$', key):
        match = re.search(r'[!><]$', key)
        eprefix = match.group(0)
        key = re.sub(r'[!><]$', '', key)
    if not isinstance(val[0], str):
        val[0] = val[0].decode('utf-8')
    if '*' in val[0]:
        val[0] = val[0].replace('*', '%')
        if eprefix == '!':
            eprefix = ' NOT'
        else:
            eprefix = ''
        sql += separator + ' ' + key + eprefix + ' LIKE %s'
    else:
        sql += separator + ' ' + key + eprefix + '=%s'
    bind = bind + (val,)
    return sql, bind


def generateSQL(result, sql, query=False):
    bind = ()
    global IDCOLUMN
    IDCOLUMN = 0
    query_string = 'id='+str(query) if query else request.query_string
    order = ''
    if query_string:
        if not isinstance(query_string, str):
            query_string = query_string.decode('utf-8')
        pd = parse_qs(query_string)
        separator = ' AND' if ' WHERE ' in sql else ' WHERE'
        for key, val in pd.items():
            if key == '_sort':
                order = ' ORDER BY ' + val[0]
            elif key == '_columns':
                sql = sql.replace('*', val[0])
                varr = val[0].split(',')
                if 'id' in varr:
                    IDCOLUMN = 1
            elif key == '_distinct':
                if 'DISTINCT' not in sql:
                    sql = sql.replace('SELECT', 'SELECT DISTINCT')
            else:
                sql, bind = addKeyValuePair(key, val, separator, sql, bind)
                separator = ' AND'
    sql += order
    if bind:
        result['rest']['sql_statement'] = sql % bind
    else:
        result['rest']['sql_statement'] = sql
    return sql, bind


def executeSQL(result, sql, container, query=False):
    sql, bind = generateSQL(result, sql, query)
    if app.config['DEBUG']: # pragma: no cover
        if bind:
            print(sql % bind)
        else:
            print(sql)
    try:
        if bind:
            g.c.execute(sql, bind)
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
    raise InvalidUsage("No rows returned for query %s" % (sql,), 404)


def showColumns(result, table):
    result['columns'] = []
    try:
        g.c.execute("SHOW COLUMNS FROM " + table)
        rows = g.c.fetchall()
        if rows:
            result['columns'] = rows
            result['rest']['row_count'] = len(rows)
        return 1
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)


def getCVIDs():
    try:
        g.c.execute('SELECT cv,cv_term,id FROM cv_term_vw ORDER BY 1,2')
        CVTERMS = g.c.fetchall()
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)


def getAdditionalCVData(sid):
    sid = str(sid)
    g.c.execute(SQL['CVREL'], (sid, sid))
    cvrel = g.c.fetchall()
    return cvrel


def getCVData(result, cvs):
    result['data'] = []
    try:
        for col in cvs:
            cv = col
            if ('id' in col) and (not IDCOLUMN):
                cvrel = getAdditionalCVData(col['id'])
                cv['relationships'] = list(cvrel)
            result['data'].append(cv)
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)


def getAdditionalCVTermData(sid):
    sid = str(sid)
    g.c.execute(SQL['CVTERMREL'], (sid, sid))
    cvrel = g.c.fetchall()
    return cvrel


def getCVTermData(result, cvterms):
    result['data'] = []
    try:
        for col in cvterms:
            cvterm = col
            if ('id' in col) and (not IDCOLUMN):
                cvtermrel = getAdditionalCVTermData(col['id'])
                cvterm['relationships'] = list(cvtermrel)
            result['data'].append(cvterm)
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)


def updateProperty(result, proptype):
    pd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            pd[i] = request.form[i]
    elif request.json:
        result['rest']['json'] = request.json
        pd = request.json
    missing = ''
    for p in ['id', 'cv', 'term', 'value']:
        if p not in pd:
            missing = missing + p + ' '
    if missing:
        raise InvalidUsage('Missing arguments: ' + missing)
    sql = 'SELECT id FROM %s WHERE ' % (proptype)
    sql += 'id=%s'
    bind = (pd['id'],)
    try:
        g.c.execute(sql, bind)
        rows = g.c.fetchall()
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)
    if len(rows) != 1:
        raise InvalidUsage(('Could not find %s ID %s' % (proptype, pd['id'])), 404)
    if pd['cv'] in CVTERMS and pd['term'] in CVTERMS[pd['cv']]:
        sql = 'INSERT INTO %s_property (%s_id,type_id,value) ' % (proptype, proptype)
        sql += 'VALUES(%s,%s,%s)'
        bind = (pd['id'], CVTERMS[pd['cv']][pd['term']], pd['value'],)
        result['rest']['sql_statement'] = sql % bind
        try:
            g.c.execute(sql, bind)
            result['rest']['row_count'] = g.c.rowcount
            result['rest']['inserted_id'] = g.c.lastrowid
            g.db.commit()
            return
        except Exception as e:
            raise InvalidUsage(sqlError(e), 500)
    raise InvalidUsage(('Could not find CV/term %s/%s' % (pd['cv'], pd['term'])), 404)


def generateResponse(result):
    global start_time
    result['rest']['elapsed_time'] = str(timedelta(seconds=(time() - start_time)))
    return jsonify(**result)


# *****************************************************************************
# * Endpoints                                                                 *
# *****************************************************************************


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
                           "user_counts": app.config['USERS'],
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
    Show the columns in the system processlist table, which may be used to
    filter results for the /processlist endpoints.
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
    Return a list of processlist entries (rows from the system processlist
    table). The caller can filter on any of the columns in the system
    processlist table. Inequalities (!=) and some relational operations
    (&lt;= and &gt;=) are supported. Wildcards are supported (use "*").
    Specific columns from the system processlist table can be returned with
    the _columns key. The returned list may be ordered by specifying a column
    with the _sort key. In both cases, multiple columns would be separated
    by a comma.
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
    executeSQL(result, 'SELECT * FROM information_schema.processlist', 'data')
    for row in result['data']:
        row['HOST'] = 'None' if row['HOST'] is None else row['HOST'].decode("utf-8")
    return generateResponse(result)


@app.route('/processlist/host', methods=['GET'])
def getProcesslistHostInfo(): # pragma: no cover
    '''
    Get processlist information for this host
    Return a list of processlist entries (rows from the system processlist
    table) for this host.
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
        result['data'] = rows
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


# *****************************************************************************
# * Test endpoints                                                            *
# *****************************************************************************
@app.route('/test_sqlerror', methods=['GET'])
def testsqlerror():
    result = initializeResult()
    try:
        sql = "SELECT some_column FROM non_existent_table"
        result['rest']['sql_statement'] = sql
        g.c.execute(sql)
        rows = g.c.fetchall()
        return rows
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)


@app.route('/test_other_error', methods=['GET'])
def testothererror():
    result = initializeResult()
    try:
        testval = 4 / 0
        result['testval'] = testval
        return result
    except Exception as e:
        raise InvalidUsage(sqlError(e), 500)


# *****************************************************************************
# * CV/CV term endpoints                                                      *
# *****************************************************************************
@app.route('/cvs/columns', methods=['GET'])
def getCVColumns():
    '''
    Get columns from cv table
    Show the columns in the cv table, which may be used to filter results for
    the /cvs and /cv_ids endpoints.
    ---
    tags:
      - CV
    responses:
      200:
          description: Columns in cv table
    '''
    result = initializeResult()
    showColumns(result, "cv")
    return generateResponse(result)


@app.route('/cv_ids', methods=['GET'])
def getCVIds():
    '''
    Get CV IDs (with filtering)
    Return a list of CV IDs. The caller can filter on any of the columns in the
    cv table. Inequalities (!=) and some relational operations (&lt;= and &gt;=)
    are supported. Wildcards are supported (use "*"). The returned list may be
    ordered by specifying a column with the _sort key. Multiple columns should
    be separated by a comma.
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
    if executeSQL(result, 'SELECT id FROM cv', 'temp'):
        result['data'] = []
        for col in result['temp']:
            result['data'].append(col['id'])
        del result['temp']
    return generateResponse(result)


@app.route('/cvs/<string:sid>', methods=['GET'])
def getCVById(sid):
    '''
    Get CV information for a given ID
    Given an ID, return a row from the cv table. Specific columns from the cv
    table can be returned with the _columns key. Multiple columns should be
    separated by a comma.
    ---
    tags:
      - CV
    parameters:
      - in: path
        name: sid
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
    if executeSQL(result, 'SELECT * FROM cv', 'temp', sid):
        getCVData(result, result['temp'])
        del result['temp']
    return generateResponse(result)


@app.route('/cvs', methods=['GET'])
def getCVInfo():
    '''
    Get CV information (with filtering)
    Return a list of CVs (rows from the cv table). The caller can filter on
    any of the columns in the cv table. Inequalities (!=) and some relational
    operations (&lt;= and &gt;=) are supported. Wildcards are supported
    (use "*"). Specific columns from the cv table can be returned with the
    _columns key. The returned list may be ordered by specifying a column with
    the _sort key. In both cases, multiple columns would be separated by a
    comma.
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
    if executeSQL(result, 'SELECT * FROM cv', 'temp'):
        getCVData(result, result['temp'])
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
    pd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            pd[i] = request.form[i]
    missing = ''
    for p in ['name', 'definition']:
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
            bind = (pd['name'], pd['definition'], pd['display_name'],
                    pd['version'], pd['is_current'],)
            result['rest']['sql_statement'] = SQL['INSERT_CV'] % bind
            g.c.execute(SQL['INSERT_CV'], bind)
            result['rest']['row_count'] = g.c.rowcount
            result['rest']['inserted_id'] = g.c.lastrowid
            g.db.commit()
        except Exception as e:
            raise InvalidUsage(sqlError(e), 500)
    return generateResponse(result)


@app.route('/cvterms/columns', methods=['GET'])
def getCVTermColumns():
    '''
    Get columns from cv_term_vw table
    Show the columns in the cv_term_vw table, which may be used to filter
    results for the /cvterms and /cvterm_ids endpoints.
    ---
    tags:
      - CV
    responses:
      200:
          description: Columns in cv_term_vw table
    '''
    result = initializeResult()
    showColumns(result, "cv_term_vw")
    return generateResponse(result)


@app.route('/cvterm_ids', methods=['GET'])
def getCVTermIds():
    '''
    Get CV term IDs (with filtering)
    Return a list of CV term IDs. The caller can filter on any of the columns
    in the cv_term_vw table. Inequalities (!=) and some relational operations
    (&lt;= and &gt;=) are supported. Wildcards are supported (use "*"). The
    returned list may be ordered by specifying a column with the _sort key.
    Multiple columns should be separated by a comma.
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
    if executeSQL(result, 'SELECT id FROM cv_term_vw', 'temp'):
        result['data'] = []
        for col in result['temp']:
            result['data'].append(col['id'])
        del result['temp']
    return generateResponse(result)


@app.route('/cvterms/<string:sid>', methods=['GET'])
def getCVTermById(sid):
    '''
    Get CV term information for a given ID
    Given an ID, return a row from the cv_term_vw table. Specific columns from
    the cv_term_vw table can be returned with the _columns key. Multiple columns
    should be separated by a comma.
    ---
    tags:
      - CV
    parameters:
      - in: path
        name: sid
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
    if executeSQL(result, 'SELECT * FROM cv_term_vw', 'temp', sid):
        getCVTermData(result, result['temp'])
        del result['temp']
    return generateResponse(result)


@app.route('/cvterms', methods=['GET'])
def getCVTermInfo():
    '''
    Get CV term information (with filtering)
    Return a list of CV terms (rows from the cv_term_vw table). The caller can
    filter on any of the columns in the cv_term_vw table. Inequalities (!=)
    and some relational operations (&lt;= and &gt;=) are supported. Wildcards
    are supported (use "*"). Specific columns from the cv_term_vw table can be
    returned with the _columns key. The returned list may be ordered by
    specifying a column with the _sort key. In both cases, multiple columns
    would be separated by a comma.
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
    if executeSQL(result, 'SELECT * FROM cv_term_vw', 'temp'):
        getCVTermData(result, result['temp'])
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
    pd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            pd[i] = request.form[i]
    missing = ''
    for p in ['cv', 'name', 'definition']:
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
            bind = (pd['cv'], pd['name'], pd['definition'],
                    pd['display_name'], pd['is_current'],
                    pd['data_type'],)
            result['rest']['sql_statement'] = SQL['INSERT_CVTERM'] % bind
            g.c.execute(SQL['INSERT_CVTERM'], bind)
            result['rest']['row_count'] = g.c.rowcount
            result['rest']['inserted_id'] = g.c.lastrowid
            g.db.commit()
        except Exception as e:
            raise InvalidUsage(sqlError(e), 500)
    return generateResponse(result)

# *****************************************************************************
# * Annotation endpoints                                                      *
# *****************************************************************************
@app.route('/annotations/columns', methods=['GET'])
def getAnnotationsColumns():
    '''
    Get columns from annotation_vw table
    Show the columns in the annotation_vw table, which may be used to filter
    results for the /annotations and /annotation_ids endpoints.
    ---
    tags:
      - Annotation
    responses:
      200:
          description: Columns in annotation_vw table
    '''
    result = initializeResult()
    showColumns(result, "annotation_vw")
    return generateResponse(result)


@app.route('/annotation_ids', methods=['GET'])
def getAnnotationIds():
    '''
    Get annotation IDs (with filtering)
    Return a list of annotation IDs. The caller can filter on any of the
    columns in the annotation_vw table. Inequalities (!=) and some relational
    operations (&lt;= and &gt;=) are supported. Wildcards are supported
    (use "*"). The returned list may be ordered by specifying a column with
    the _sort key. Multiple columns should be separated by a comma.
    ---
    tags:
      - Annotation
    responses:
      200:
          description: List of one or more annotation IDs
      404:
          description: Annotations not found
    '''
    result = initializeResult()
    if executeSQL(result, 'SELECT id FROM annotation_vw', 'temp'):
        result['data'] = []
        for col in result['temp']:
            result['data'].append(col['id'])
        del result['temp']
    return generateResponse(result)


@app.route('/annotations/<string:sid>', methods=['GET'])
def getAnnotationsById(sid):
    '''
    Get annotation information for a given ID
    Given an ID, return a row from the annotation_vw table. Specific columns
    from the annotation_vw table can be returned with the _columns key.
    Multiple columns should be separated by a comma.
    ---
    tags:
      - Annotation
    parameters:
      - in: path
        name: sid
        type: string
        required: true
        description: annotation ID
    responses:
      200:
          description: Information for one annotation
      404:
          description: Annotation ID not found
    '''
    result = initializeResult()
    executeSQL(result, 'SELECT * FROM annotation_vw', 'data', sid)
    return generateResponse(result)


@app.route('/annotations', methods=['GET'])
def getAnnotationInfo():
    '''
    Get annotation information (with filtering)
    Return a list of annotations (rows from the annotation_vw table). The
    caller can filter on any of the columns in the annotation_vw table.
    Inequalities (!=) and some relational operations (&lt;= and &gt;=) are
    supported. Wildcards are supported (use "*"). Specific columns from the
    annotation_vw table can be returned with the _columns key. The returned
    list may be ordered by specifying a column with the _sort key. In both
    cases, multiple columns would be separated by a comma.
    ---
    tags:
      - Annotation
    responses:
      200:
          description: List of information for one or more annotations
      404:
          description: Annotations not found
    '''
    result = initializeResult()
    executeSQL(result, 'SELECT * FROM annotation_vw', 'data')
    return generateResponse(result)


@app.route('/annotationprops/columns', methods=['GET'])
def getAnnotationpropColumns():
    '''
    Get columns from annotation_property_vw table
    Show the columns in the annotation_property_vw table, which may be used to
    filter results for the /annotationprops and /annotationprop_ids endpoints.
    ---
    tags:
      - Annotation
    responses:
      200:
          description: Columns in annotation_prop_vw table
    '''
    result = initializeResult()
    showColumns(result, "annotation_property_vw")
    return generateResponse(result)


@app.route('/annotationprop_ids', methods=['GET'])
def getAnnotationpropIds():
    '''
    Get annotation property IDs (with filtering)
    Return a list of annotation property IDs. The caller can filter on any of
    the columns in the annotation_property_vw table. Inequalities (!=) and
    some relational operations (&lt;= and &gt;=) are supported. Wildcards are
    supported (use "*"). The returned list may be ordered by specifying a
    column with the _sort key. Multiple columns should be separated by a
    comma.
    ---
    tags:
      - Annotation
    responses:
      200:
          description: List of one or more annotation property IDs
      404:
          description: Annotation properties not found
    '''
    result = initializeResult()
    if executeSQL(result, 'SELECT id FROM annotation_property_vw', 'temp'):
        result['data'] = []
        for c in result['temp']:
            result['data'].append(c['id'])
        del result['temp']
    return generateResponse(result)


@app.route('/annotationprops/<string:sid>', methods=['GET'])
def getAnnotationpropsById(sid):
    '''
    Get annotation property information for a given ID
    Given an ID, return a row from the annotation_property_vw table. Specific
    columns from the annotation_property_vw table can be returned with the
    _columns key. Multiple columns should be separated by a comma.
    ---
    tags:
      - Annotation
    parameters:
      - in: path
        name: sid
        type: string
        required: true
        description: annotation property ID
    responses:
      200:
          description: Information for one annotation property
      404:
          description: Annotation property ID not found
    '''
    result = initializeResult()
    executeSQL(result, 'SELECT * FROM annotation_property_vw', 'data', sid)
    return generateResponse(result)


@app.route('/annotationprops', methods=['GET'])
def getAnnotationpropInfo():
    '''
    Get annotation property information (with filtering)
    Return a list of annotation properties (rows from the
    annotation_property_vw table). The caller can filter on any of the columns
    in the annotation_property_vw table. Inequalities (!=) and some relational
    operations (&lt;= and &gt;=) are supported. Wildcards are supported
    (use "*"). Specific columns from the annotation_property_vw table can be
    returned with the _columns key. The returned list may be ordered by
    specifying a column with the _sort key. In both cases, multiple columns
    would be separated by a comma.
    ---
    tags:
      - Annotation
    responses:
      200:
          description: List of information for one or more annotation
                       properties
      404:
          description: Annotation properties not found
    '''
    result = initializeResult()
    executeSQL(result, 'SELECT * FROM annotation_property_vw', 'data')
    return generateResponse(result)


@app.route('/annotationprop', methods=['OPTIONS', 'POST'])
def updateAnnotationProperty(): # pragma: no cover
    '''
    Add/update an annotation property
    ---
    tags:
      - Annotation
    parameters:
      - in: query
        name: id
        type: string
        required: true
        description: annotation ID
      - in: query
        name: cv
        type: string
        required: true
        description: CV name
      - in: query
        name: term
        type: string
        required: true
        description: CV term
      - in: query
        name: value
        type: string
        required: true
        description: property value
    responses:
      200:
          description: Property added
      400:
          description: Missing arguments
    '''
    result = initializeResult()
    updateProperty(result, 'annotation')
    return generateResponse(result)


# *****************************************************************************
# * Assignment endpoints                                                      *
# *****************************************************************************
@app.route('/assignments/columns', methods=['GET'])
def getAssignmentColumns():
    '''
    Get columns from assignment_vw table
    Show the columns in the assignment_vw table, which may be used to filter
    results for the /assignments and /assignment_ids endpoints.
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
    Return a list of assignment IDs. The caller can filter on any of the
    columns in the assignment_vw table. Inequalities (!=) and some relational
    operations (&lt;= and &gt;=) are supported. Wildcards are supported
    (use "*"). The returned list may be ordered by specifying a column with
    the _sort key. Multiple columns should be separated by a comma.
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
    if executeSQL(result, 'SELECT id FROM assignment_vw', 'temp'):
        result['data'] = []
        for col in result['temp']:
            result['data'].append(col['id'])
        del result['temp']
    return generateResponse(result)


@app.route('/assignments/<string:sid>', methods=['GET'])
def getAssignmentsById(sid):
    '''
    Get assignment information for a given ID
    Given an ID, return a row from the assignment_vw table. Specific columns
    from the assignment_vw table can be returned with the _columns key.
    Multiple columns should be separated by a comma.
    ---
    tags:
      - Assignment
    parameters:
      - in: path
        name: sid
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
    executeSQL(result, 'SELECT * FROM assignment_vw', 'data', sid)
    return generateResponse(result)


@app.route('/assignments', methods=['GET'])
def getAssignmentInfo():
    '''
    Get assignment information (with filtering)
    Return a list of assignments (rows from the assignment_vw table). The
    caller can filter on any of the columns in the assignment_vw table.
    Inequalities (!=) and some relational operations (&lt;= and &gt;=) are
    supported. Wildcards are supported (use "*"). Specific columns from the
    assignment_vw table can be returned with the _columns key. The returned
    list may be ordered by specifying a column with the _sort key. In both
    cases, multiple columns would be separated by a comma.
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
    executeSQL(result, 'SELECT * FROM assignment_vw', 'data')
    return generateResponse(result)


@app.route('/assignments_completed', methods=['GET'])
def getAssignmentCompletedInfo():
    '''
    Get completed assignment information (with filtering)
    Return a list of assignments (rows from the assignment_vw table) that have
    been completed. The caller can filter on any of the columns in the
    assignment_vw table. Inequalities (!=) and some relational operations
    (&lt;= and &gt;=) are supported. Wildcards are supported (use "*"). Specific
    columns from the assignment_vw table can be returned with the _columns key.
    The returned list may be ordered by specifying a column with the _sort key.
    In both cases, multiple columns would be separated by a comma.
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
    executeSQL(result, 'SELECT * FROM assignment_vw WHERE is_complete=1', 'data')
    return generateResponse(result)


@app.route('/assignments_open', methods=['GET'])
def getAssignmentOpen():
    '''
    Get open assignment information (with filtering)
    Return a list of assignments (rows from the assignment_vw table) that
    haven't been started yet. The caller can filter on any of the columns in
    the assignment_vw table. Inequalities (!=) and some relational operations
    (&lt;= and &gt;=) are supported. Wildcards are supported (use "*").
    Specific columns from the assignment_vw table can be returned with the
    _columns key. The returned list may be ordered by specifying a column with
    the _sort key. In both cases, multiple columns would be separated by a
    comma.
    ---
    tags:
      - Assignment
    responses:
      200:
          description: List of information for one or more open assignments
      404:
          description: Assignments not found
    '''
    result = initializeResult()
    executeSQL(result, "SELECT * FROM assignment_vw WHERE is_complete=0 AND start_date='0000-00-00'", 'data')
    return generateResponse(result)


@app.route('/assignments_remaining', methods=['GET'])
def getAssignmentRemainingInfo():
    '''
    Get remaining assignment information (with filtering)
    Return a list of assignments (rows from the assignment_vw table) that
    haven't been completed yet. The caller can filter on any of the columns
    in the assignment_vw table. Inequalities (!=) and some relational
    operations (&lt;= and &gt;=) are supported. Wildcards are supported
    (use "*"). Specific columns from the assignment_vw table can be returned
    with the _columns key. The returned list may be ordered by specifying a
    column with the _sort key. In both cases, multiple columns would be
    separated by a comma.
    ---
    tags:
      - Assignment
    responses:
      200:
          description: List of information for one or more remaining
                       assignments
      404:
          description: Assignments not found
    '''
    result = initializeResult()
    executeSQL(result, 'SELECT * FROM assignment_vw WHERE is_complete=0', 'data')
    return generateResponse(result)


@app.route('/assignments_started', methods=['GET'])
def getAssignmentStarted():
    '''
    Get started assignment information (with filtering)
    Return a list of assignments (rows from the assignment_vw table) that have
    been started but not completed. The caller can filter on any of the columns
    in the assignment_vw table. Inequalities (!=) and some relational
    operations (&lt;= and &gt;=) are supported. Wildcards are supported
    (use "*"). Specific columns from the assignment_vw table can be returned
    with the _columns key. The returned list may be ordered by specifying a
    column with the _sort key. In both cases, multiple columns would be
    separated by a comma.
    ---
    tags:
      - Assignment
    responses:
      200:
          description: List of information for one or more started assignments
      404:
          description: Assignments not found
    '''
    result = initializeResult()
    executeSQL(result, "SELECT * FROM assignment_vw WHERE is_complete=0 AND start_date>'0000-00-00'", 'data')
    return generateResponse(result)


@app.route('/assignmentprops/columns', methods=['GET'])
def getAssignmentpropColumns():
    '''
    Get columns from assignment_property_vw table
    Show the columns in the assignment_property_vw table, which may be used to
    filter results for the /assignmentprops and /assignmentprop_ids endpoints.
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
    Return a list of assignment property IDs. The caller can filter on any of
    the columns in the assignment_property_vw table. Inequalities (!=) and
    some relational operations (&lt;= and &gt;=) are supported. Wildcards are
    supported (use "*"). The returned list may be ordered by specifying a
    column with the _sort key. Multiple columns should be separated by a
    comma.
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
    if executeSQL(result, 'SELECT id FROM assignment_property_vw', 'temp'):
        result['data'] = []
        for c in result['temp']:
            result['data'].append(c['id'])
        del result['temp']
    return generateResponse(result)


@app.route('/assignmentprops/<string:sid>', methods=['GET'])
def getAssignmentpropsById(sid):
    '''
    Get assignment property information for a given ID
    Given an ID, return a row from the assignment_property_vw table. Specific
    columns from the assignment_property_vw table can be returned with the
    _columns key. Multiple columns should be separated by a comma.
    ---
    tags:
      - Assignment
    parameters:
      - in: path
        name: sid
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
    executeSQL(result, 'SELECT * FROM assignment_property_vw', 'data', sid)
    return generateResponse(result)


@app.route('/assignmentprops', methods=['GET'])
def getAssignmentpropInfo():
    '''
    Get assignment property information (with filtering)
    Return a list of assignment properties (rows from the
    assignment_property_vw table). The caller can filter on any of the columns
    in the assignment_property_vw table. Inequalities (!=) and some relational
    operations (&lt;= and &gt;=) are supported. Wildcards are supported
    (use "*"). Specific columns from the assignment_property_vw table can be
    returned with the _columns key. The returned list may be ordered by
    specifying a column with the _sort key. In both cases, multiple columns
    would be separated by a comma.
    ---
    tags:
      - Assignment
    responses:
      200:
          description: List of information for one or more assignment
                       properties
      404:
          description: Assignment properties not found
    '''
    result = initializeResult()
    executeSQL(result, 'SELECT * FROM assignment_property_vw', 'data')
    return generateResponse(result)


@app.route('/start_assignment', methods=['OPTIONS', 'POST'])
def startAssignment(): # pragma: no cover
    '''
    Start an assignment
    ---
    tags:
      - Assignment
    parameters:
      - in: query
        name: id
        type: string
        required: true
        description: assignment ID
      - in: query
        name: note
        type: string
        required: false
        description: note
    responses:
      200:
          description: Assignment started
      400:
          description: Assignment not started
    '''
    result = initializeResult()
    pd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            pd[i] = request.form[i]
    if 'id' not in pd:
        raise InvalidUsage('Missing arguments: id')
    if not result['rest']['error']:
        try:
            if 'note' in pd:
                stmt = 'UPDATE assignment SET start_date=NOW(),note=%s WHERE id=%s'
                bind = (pd['note'], pd['id'],)
            else:
                stmt = 'UPDATE assignment SET start_date=NOW() WHERE id=%s'
                bind = (pd['id'],)
            result['rest']['sql_statement'] = stmt % bind
            g.c.execute(stmt, bind)
            result['rest']['row_count'] = g.c.rowcount
            g.db.commit()
        except Exception as e:
            raise InvalidUsage(sqlError(e), 500)
    return generateResponse(result)


@app.route('/complete_assignment', methods=['OPTIONS', 'POST'])
def completeAssignment(): # pragma: no cover
    '''
    Complete an assignment
    ---
    tags:
      - Assignment
    parameters:
      - in: query
        name: id
        type: string
        required: true
        description: assignment ID
      - in: query
        name: note
        type: string
        required: false
        description: note
    responses:
      200:
          description: Assignment completed
      400:
          description: Assignment not completed
    '''
    result = initializeResult()
    pd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            pd[i] = request.form[i]
    if 'id' not in pd:
        raise InvalidUsage('Missing arguments: id')
    if not result['rest']['error']:
        try:
            if 'note' in pd:
                stmt = 'UPDATE assignment SET complete_date=NOW(),note=%s WHERE id=%s'
                bind = (pd['note'], pd['id'],)
            else:
                stmt = 'UPDATE assignment SET complete_date=NOW() WHERE id=%s'
                bind = (pd['id'],)
            result['rest']['sql_statement'] = stmt % bind
            g.c.execute(stmt, bind)
            result['rest']['row_count'] = g.c.rowcount
            g.db.commit()
        except Exception as e:
            raise InvalidUsage(sqlError(e), 500)
    return generateResponse(result)


@app.route('/reset_assignment', methods=['OPTIONS', 'POST'])
def resetAssignment(): # pragma: no cover
    '''
    Reset an assignment (remove start and completion times)
    ---
    tags:
      - Assignment
    parameters:
      - in: query
        name: id
        type: string
        required: true
        description: assignment ID
      - in: query
        name: note
        type: string
        required: false
        description: note
    responses:
      200:
          description: Assignment reset
      400:
          description: Assignment not reset
    '''
    result = initializeResult()
    pd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            pd[i] = request.form[i]
    if 'id' not in pd:
        raise InvalidUsage('Missing arguments: id')
    if not result['rest']['error']:
        try:
            if 'note' in pd:
                stmt = "UPDATE assignment SET start_date=0,complete_date=0,is_complete=0,note=%s WHERE id=%s"
                bind = (pd['note'], pd['id'],)
            else:
                stmt = 'UPDATE assignment SET start_date=0,complete_date=0,is_complete=0 WHERE id=%s'
                bind = (pd['id'],)
            result['rest']['sql_statement'] = stmt % bind
            g.c.execute(stmt, bind)
            result['rest']['row_count'] = g.c.rowcount
            g.db.commit()
        except Exception as e:
            raise InvalidUsage(sqlError(e), 500)
    return generateResponse(result)


# *****************************************************************************
# * Media endpoints                                                           *
# *****************************************************************************
@app.route('/media/columns', methods=['GET'])
def getMediaColumns():
    '''
    Get columns from media_vw table
    Show the columns in the media_vw table, which may be used to filter
    results for the /media and /media_ids endpoints.
    ---
    tags:
      - Media
    responses:
      200:
          description: Columns in media_vw table
    '''
    result = initializeResult()
    showColumns(result, "media_vw")
    return generateResponse(result)


@app.route('/media_ids', methods=['GET'])
def getMediaIds():
    '''
    Get media IDs (with filtering)
    Return a list of media IDs. The caller can filter on any of the
    columns in the media_vw table. Inequalities (!=) and some relational
    operations (&lt;= and &gt;=) are supported. Wildcards are supported
    (use "*"). The returned list may be ordered by specifying a column with
    the _sort key. Multiple columns should be separated by a comma.
    ---
    tags:
      - Media
    responses:
      200:
          description: List of one or more media IDs
      404:
          description: Media not found
    '''
    result = initializeResult()
    if executeSQL(result, 'SELECT id FROM media_vw', 'temp'):
        result['data'] = []
        for col in result['temp']:
            result['data'].append(col['id'])
        del result['temp']
    return generateResponse(result)


@app.route('/media/<string:sid>', methods=['GET'])
def getMediaById(sid):
    '''
    Get media information for a given ID
    Given an ID, return a row from the media_vw table. Specific columns
    from the media_vw table can be returned with the _columns key.
    Multiple columns should be separated by a comma.
    ---
    tags:
      - Media
    parameters:
      - in: path
        name: sid
        type: string
        required: true
        description: media ID
    responses:
      200:
          description: Information for one media
      404:
          description: Media ID not found
    '''
    result = initializeResult()
    executeSQL(result, 'SELECT * FROM media_vw', 'data', sid)
    return generateResponse(result)


@app.route('/media', methods=['GET'])
def getMediaInfo():
    '''
    Get media information (with filtering)
    Return a list of media (rows from the media_vw table). The
    caller can filter on any of the columns in the media_vw table.
    Inequalities (!=) and some relational operations (&lt;= and &gt;=) are
    supported. Wildcards are supported (use "*"). Specific columns from the
    media_vw table can be returned with the _columns key. The returned
    list may be ordered by specifying a column with the _sort key. In both
    cases, multiple columns would be separated by a comma.
    ---
    tags:
      - Media
    responses:
      200:
          description: List of information for one or more media
      404:
          description: Media not found
    '''
    result = initializeResult()
    executeSQL(result, 'SELECT * FROM media_vw', 'data')
    return generateResponse(result)


@app.route('/mediaprops/columns', methods=['GET'])
def getMediapropColumns():
    '''
    Get columns from media_property_vw table
    Show the columns in the media_property_vw table, which may be used to
    filter results for the /mediaprops and /mediaprop_ids endpoints.
    ---
    tags:
      - Media
    responses:
      200:
          description: Columns in media_prop_vw table
    '''
    result = initializeResult()
    showColumns(result, "media_property_vw")
    return generateResponse(result)


@app.route('/mediaprop_ids', methods=['GET'])
def getMediapropIds():
    '''
    Get media property IDs (with filtering)
    Return a list of media property IDs. The caller can filter on any of
    the columns in the media_property_vw table. Inequalities (!=) and
    some relational operations (&lt;= and &gt;=) are supported. Wildcards are
    supported (use "*"). The returned list may be ordered by specifying a
    column with the _sort key. Multiple columns should be separated by a
    comma.
    ---
    tags:
      - Media
    responses:
      200:
          description: List of one or more media property IDs
      404:
          description: Media properties not found
    '''
    result = initializeResult()
    if executeSQL(result, 'SELECT id FROM media_property_vw', 'temp'):
        result['data'] = []
        for col in result['temp']:
            result['data'].append(col['id'])
        del result['temp']
    return generateResponse(result)


@app.route('/mediaprops/<string:sid>', methods=['GET'])
def get_mediaprops_by_id(sid):
    '''
    Get media property information for a given ID
    Given an ID, return a row from the media_property_vw table. Specific
    columns from the media_property_vw table can be returned with the
    _columns key. Multiple columns should be separated by a comma.
    ---
    tags:
      - Media
    parameters:
      - in: path
        name: sid
        type: string
        required: true
        description: media property ID
    responses:
      200:
          description: Information for one media property
      404:
          description: Media property ID not found
    '''
    result = initializeResult()
    executeSQL(result, 'SELECT * FROM media_property_vw', 'data', sid)
    return generateResponse(result)


@app.route('/mediaprops', methods=['GET'])
def get_mediaprop_info():
    '''
    Get media property information (with filtering)
    Return a list of media properties (rows from the
    media_property_vw table). The caller can filter on any of the columns
    in the media_property_vw table. Inequalities (!=) and some relational
    operations (&lt;= and &gt;=) are supported. Wildcards are supported
    (use "*"). Specific columns from the media_property_vw table can be
    returned with the _columns key. The returned list may be ordered by
    specifying a column with the _sort key. In both cases, multiple columns
    would be separated by a comma.
    ---
    tags:
      - Media
    responses:
      200:
          description: List of information for one or more media
                       properties
      404:
          description: Media properties not found
    '''
    result = initializeResult()
    executeSQL(result, 'SELECT * FROM media_property_vw', 'data')
    return generateResponse(result)


# *****************************************************************************
# * DVID endpoints                                                            *
# *****************************************************************************
@app.route('/dvid_instances', methods=['GET'])
def get_dvid_info():
    '''
    Get DVID url/UUID information (with filtering)
    Return a list of DVID instances along with their properties (rows from the
    dvid_url_uuid_vw table). The caller can filter on any of the columns in
    the dvid_url_uuid_vw table. Inequalities (!=) and some relational
    operations (&lt;= and &gt;=) are supported. Wildcards are supported
    (use "*"). Specific columns from the dvid_url_uuid_vw table can be
    returned with the _columns key. The returned list may be ordered by
    specifying a column with the _sort key. In both cases, multiple columns
    would be separated by a comma.
    ---
    tags:
      - DVID
    responses:
      200:
          description: List of information for one or more DVID instances
      404:
          description: DVID instances not found
    '''
    result = initializeResult()
    executeSQL(result, 'SELECT * FROM dvid_url_uuid_vw', 'data')
    return generateResponse(result)


# *****************************************************************************
# * User endpoints                                                            *
# *****************************************************************************
@app.route('/users', methods=['GET'])
def getUserInfo():
    '''
    Get user information (with filtering)
    Return a list of users along with their properties (rows from the
    user_property_vw table). The caller can filter on any of the columns in
    the user_property_vw table. Inequalities (!=) and some relational
    operations (&lt;= and &gt;=) are supported. Wildcards are supported
    (use "*"). Specific columns from the user_property_vw table can be
    returned with the _columns key. The returned list may be ordered by
    specifying a column with the _sort key. In both cases, multiple columns
    would be separated by a comma.
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
    executeSQL(result, 'SELECT * FROM user_property_vw', 'data')
    return generateResponse(result)


# *****************************************************************************


if __name__ == '__main__':
    app.run(debug=True)
