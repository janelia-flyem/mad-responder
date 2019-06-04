from datetime import datetime, timedelta
import json
import os
import platform
import re
import sys
from time import time
from urllib.parse import parse_qs
import elasticsearch
from flask import Flask, g, render_template, request, jsonify
from flask.json import JSONEncoder
from flask_cors import CORS
from flask_swagger import swagger
from jwt import decode
from kafka import KafkaProducer
from kafka.errors import KafkaError
import pymysql.cursors
import requests


# SQL statements
SQL = {
    'CVREL': "SELECT subject,relationship,object FROM cv_relationship_vw "
             + "WHERE subject_id=%s OR object_id=%s",
    'CVTERMREL': "SELECT subject,relationship,object FROM "
                 + "cv_term_relationship_vw WHERE subject_id=%s OR "
                 + "object_id=%s",
}

class CustomJSONEncoder(JSONEncoder):
    def default(self, obj):   # pylint: disable=E0202, W0221
        try:
            if isinstance(obj, datetime):
                return obj.strftime('%a, %-d %b %Y %H:%M:%S')
            iterable = iter(obj)
        except TypeError:
            pass
        else:
            return list(iterable)
        return JSONEncoder.default(self, obj)

__version__ = '0.1.0'
app = Flask(__name__)
app.json_encoder = CustomJSONEncoder
app.config.from_pyfile("config.cfg")
CONFIG = {'config': {'url': app.config['CONFIG_ROOT']}}
CVTERMS = dict()
SERVER = dict()
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
START_TIME = ESEARCH = PRODUCER = ''


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
    global START_TIME, CVTERMS, CONFIG, ESEARCH, SERVER, PRODUCER
    START_TIME = time()
    g.db = conn
    g.c = cursor
    app.config['COUNTER'] += 1
    endpoint = request.endpoint if request.endpoint else '(Unknown)'
    app.config['ENDPOINTS'][endpoint] = app.config['ENDPOINTS'].get(endpoint, 0) + 1
    if request.method == 'OPTIONS':
        result = initialize_result()
        return generate_response(result)
    if not SERVER:
        data = call_responder('config', 'config/rest_services')
        CONFIG = data['config']
        data = call_responder('config', 'config/servers')
        SERVER = data['config']
        try:
            ESEARCH = elasticsearch.Elasticsearch(SERVER['elk-elastic']['address'])
        except Exception as ex: # pragma: no cover
            template = "An exception of type {0} occurred. Arguments:\n{1!r}"
            message = template.format(type(ex).__name__, ex.args)
            print(message)
            sys.exit(-1)
        PRODUCER = KafkaProducer(bootstrap_servers=SERVER['Kafka']['broker_list'])
        try:
            g.c.execute('SELECT cv,cv_term,id FROM cv_term_vw ORDER BY 1,2')
            rows = g.c.fetchall()
            for row in rows:
                if row['cv'] not in CVTERMS:
                    CVTERMS[row['cv']] = dict()
                CVTERMS[row['cv']][row['cv_term']] = row['id']
        except Exception as err:
            raise InvalidUsage(sql_error(err), 500)


# ******************************************************************************
# * Utility functions                                                          *
# ******************************************************************************


def call_responder(server, endpoint, post=''):
    url = CONFIG[server]['url'] + endpoint
    try:
        if post:
            print(post)
            headers = {'Content-Type': 'application/json'}
            req = requests.post(url, post, headers=headers)
            print(req.text)
        else:
            req = requests.get(url)
    except requests.exceptions.RequestException as err: # pragma no cover
        print(err)
        sys.exit(-1)
    if req.status_code == 200:
        return req.json()
    else:
        print("Could not send request to %s" % (url))
        print(req)
        sys.exit(-1)


def sql_error(err):
    error_msg = ''
    try:
        error_msg = "MySQL error [%d]: %s" % (err.args[0], err.args[1])
    except IndexError:
        error_msg = "Error: %s" % err
    if error_msg:
        print(error_msg)
    return error_msg


def initialize_result():
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


def add_key_value_pair(key, val, separator, sql, bind):
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


def generate_sql(result, sql, query=False):
    bind = ()
    global IDCOLUMN
    IDCOLUMN = 0
    query_string = 'id='+str(query) if query else request.query_string
    order = ''
    if query_string:
        if not isinstance(query_string, str):
            query_string = query_string.decode('utf-8')
        ipd = parse_qs(query_string)
        separator = ' AND' if ' WHERE ' in sql else ' WHERE'
        for key, val in ipd.items():
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
                sql, bind = add_key_value_pair(key, val, separator, sql, bind)
                separator = ' AND'
    sql += order
    if bind:
        result['rest']['sql_statement'] = sql % bind
    else:
        result['rest']['sql_statement'] = sql
    return sql, bind


def execute_sql(result, sql, container, query=False):
    sql, bind = generate_sql(result, sql, query)
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
    except Exception as err:
        raise InvalidUsage(sql_error(err), 500)
    result[container] = []
    if rows:
        result[container] = rows
        result['rest']['row_count'] = len(rows)
        return 1
    raise InvalidUsage("No rows returned for query %s" % (sql,), 404)


def show_columns(result, table):
    result['columns'] = []
    try:
        g.c.execute("SHOW COLUMNS FROM " + table)
        rows = g.c.fetchall()
        if rows:
            result['columns'] = rows
            result['rest']['row_count'] = len(rows)
        return 1
    except Exception as err:
        raise InvalidUsage(sql_error(err), 500)


def get_additional_cv_data(sid):
    sid = str(sid)
    g.c.execute(SQL['CVREL'], (sid, sid))
    cvrel = g.c.fetchall()
    return cvrel


def get_cv_data(result, cvs):
    result['data'] = []
    try:
        for col in cvs:
            tcv = col
            if ('id' in col) and (not IDCOLUMN):
                cvrel = get_additional_cv_data(col['id'])
                tcv['relationships'] = list(cvrel)
            result['data'].append(tcv)
    except Exception as err:
        raise InvalidUsage(sql_error(err), 500)


def get_additional_cv_term_data(sid):
    sid = str(sid)
    g.c.execute(SQL['CVTERMREL'], (sid, sid))
    cvrel = g.c.fetchall()
    return cvrel


def get_cv_term_data(result, cvterms):
    result['data'] = []
    try:
        for col in cvterms:
            cvterm = col
            if ('id' in col) and (not IDCOLUMN):
                cvtermrel = get_additional_cv_term_data(col['id'])
                cvterm['relationships'] = list(cvtermrel)
            result['data'].append(cvterm)
    except Exception as err:
        raise InvalidUsage(sql_error(err), 500)


def update_property(result, proptype):
    ipd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            ipd[i] = request.form[i]
    elif request.json:
        result['rest']['json'] = request.json
        ipd = request.json
    missing = ''
    for ptmp in ['id', 'cv', 'term', 'value']:
        if ptmp not in ipd:
            missing = missing + ptmp + ' '
    if missing:
        raise InvalidUsage('Missing arguments: ' + missing)
    sql = 'SELECT id FROM %s WHERE ' % (proptype)
    sql += 'id=%s'
    bind = (ipd['id'],)
    try:
        g.c.execute(sql, bind)
        rows = g.c.fetchall()
    except Exception as err:
        raise InvalidUsage(sql_error(err), 500)
    if len(rows) != 1:
        raise InvalidUsage(('Could not find %s ID %s' % (proptype, ipd['id'])), 404)
    if ipd['cv'] in CVTERMS and ipd['term'] in CVTERMS[ipd['cv']]:
        sql = 'INSERT INTO %s_property (%s_id,type_id,value) ' % (proptype, proptype)
        sql += 'VALUES(%s,%s,%s)'
        bind = (ipd['id'], CVTERMS[ipd['cv']][ipd['term']], ipd['value'],)
        result['rest']['sql_statement'] = sql % bind
        try:
            g.c.execute(sql, bind)
            result['rest']['row_count'] = g.c.rowcount
            result['rest']['inserted_id'] = g.c.lastrowid
            g.db.commit()
            return
        except Exception as err:
            raise InvalidUsage(sql_error(err), 500)
    raise InvalidUsage(('Could not find CV/term %s/%s' % (ipd['cv'], ipd['term'])), 404)


def generate_response(result):
    global START_TIME
    result['rest']['elapsed_time'] = str(timedelta(seconds=(time() - START_TIME)))
    return jsonify(**result)


def publish(result, message):
    message['uri'] = request.url
    message['client'] = 'mad_responder'
    message['user'] = result['rest']['user']
    message['host'] = os.uname()[1]
    message['status'] = 200
    message['time'] = int(time())
    future = PRODUCER.send(app.config['KAFKA_TOPIC'], json.dumps(message).encode('utf-8'))
    try:
        future.get(timeout=10)
    except KafkaError:
        print("Failed sending to Kafka!")


# *****************************************************************************
# * Endpoints                                                                 *
# *****************************************************************************


@app.errorhandler(InvalidUsage)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response


@app.route('/')
def show_swagger():
    return render_template('swagger_ui.html')


@app.route("/spec")
def spec():
    return get_doc_json()


@app.route('/doc')
def get_doc_json():
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
    result = initialize_result()
    db_connection = True
    try:
        g.db.ping(reconnect=False)
    except Exception as err:
        template = "An exception of type {0} occurred. Arguments:{1!r}"
        message = template.format(type(err).__name__, err.args)
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
    except Exception as err:
        template = "An exception of type {0} occurred. Arguments:{1!r}"
        message = template.format(type(err).__name__, err.args)
        raise InvalidUsage('Error: %s' % (message,))
    return generate_response(result)


@app.route('/processlist/columns', methods=['GET'])
def get_processlist_columns():
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
    result = initialize_result()
    show_columns(result, "information_schema.processlist")
    return generate_response(result)


@app.route('/processlist', methods=['GET'])
def get_processlist_info():
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM information_schema.processlist', 'data')
    for row in result['data']:
        row['HOST'] = 'None' if row['HOST'] is None else row['HOST'].decode("utf-8")
    return generate_response(result)


@app.route('/processlist/host', methods=['GET'])
def get_processlist_host_info(): # pragma: no cover
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
    result = initialize_result()
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
    except Exception as err:
        raise InvalidUsage(sql_error(err), 500)
    return generate_response(result)


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
    result = initialize_result()
    try:
        g.db.ping()
    except Exception as err:
        raise InvalidUsage(sql_error(err), 400)
    return generate_response(result)


# *****************************************************************************
# * Test endpoints                                                            *
# *****************************************************************************
@app.route('/test_sqlerror', methods=['GET'])
def testsqlerror():
    result = initialize_result()
    try:
        sql = "SELECT some_column FROM non_existent_table"
        result['rest']['sql_statement'] = sql
        g.c.execute(sql)
        rows = g.c.fetchall()
        return rows
    except Exception as err:
        raise InvalidUsage(sql_error(err), 500)


@app.route('/test_other_error', methods=['GET'])
def testothererror():
    result = initialize_result()
    try:
        testval = 4 / 0
        result['testval'] = testval
        return result
    except Exception as err:
        raise InvalidUsage(sql_error(err), 500)


# *****************************************************************************
# * CV/CV term endpoints                                                      *
# *****************************************************************************
@app.route('/cvs/columns', methods=['GET'])
def get_cv_columns():
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
    result = initialize_result()
    show_columns(result, "cv")
    return generate_response(result)


@app.route('/cv_ids', methods=['GET'])
def get_cv_ids():
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
    result = initialize_result()
    if execute_sql(result, 'SELECT id FROM cv', 'temp'):
        result['data'] = []
        for col in result['temp']:
            result['data'].append(col['id'])
        del result['temp']
    return generate_response(result)


@app.route('/cvs/<string:sid>', methods=['GET'])
def get_cv_by_id(sid):
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
    result = initialize_result()
    if execute_sql(result, 'SELECT * FROM cv', 'temp', sid):
        get_cv_data(result, result['temp'])
        del result['temp']
    return generate_response(result)


@app.route('/cvs', methods=['GET'])
def get_cv_info():
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
    result = initialize_result()
    if execute_sql(result, 'SELECT * FROM cv', 'temp'):
        get_cv_data(result, result['temp'])
        del result['temp']
    return generate_response(result)


@app.route('/cv', methods=['OPTIONS', 'POST'])
def add_cv(): # pragma: no cover
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
    result = initialize_result()
    ipd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            ipd[i] = request.form[i]
    missing = ''
    for ptmp in ['name', 'definition']:
        if ptmp not in ipd:
            missing = missing + ptmp + ' '
    if missing:
        raise InvalidUsage('Missing arguments: ' + missing)
    if 'display_name' not in ipd:
        ipd['display_name'] = ipd['name']
    if 'version' not in ipd:
        ipd['version'] = 1
    if 'is_current' not in ipd:
        ipd['is_current'] = 1
    if not result['rest']['error']:
        try:
            bind = (ipd['name'], ipd['definition'], ipd['display_name'],
                    ipd['version'], ipd['is_current'],)
            result['rest']['sql_statement'] = SQL['INSERT_CV'] % bind
            g.c.execute(SQL['INSERT_CV'], bind)
            result['rest']['row_count'] = g.c.rowcount
            result['rest']['inserted_id'] = g.c.lastrowid
            g.db.commit()
        except Exception as err:
            raise InvalidUsage(sql_error(err), 500)
    return generate_response(result)


@app.route('/cvterms/columns', methods=['GET'])
def get_cv_term_columns():
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
    result = initialize_result()
    show_columns(result, "cv_term_vw")
    return generate_response(result)


@app.route('/cvterm_ids', methods=['GET'])
def get_cv_term_ids():
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
    result = initialize_result()
    if execute_sql(result, 'SELECT id FROM cv_term_vw', 'temp'):
        result['data'] = []
        for col in result['temp']:
            result['data'].append(col['id'])
        del result['temp']
    return generate_response(result)


@app.route('/cvterms/<string:sid>', methods=['GET'])
def get_cv_term_by_id(sid):
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
    result = initialize_result()
    if execute_sql(result, 'SELECT * FROM cv_term_vw', 'temp', sid):
        get_cv_term_data(result, result['temp'])
        del result['temp']
    return generate_response(result)


@app.route('/cvterms', methods=['GET'])
def get_cv_term_info():
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
    result = initialize_result()
    if execute_sql(result, 'SELECT * FROM cv_term_vw', 'temp'):
        get_cv_term_data(result, result['temp'])
        del result['temp']
    return generate_response(result)


@app.route('/cvterm', methods=['OPTIONS', 'POST'])
def add_cv_term(): # pragma: no cover
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
    result = initialize_result()
    ipd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            ipd[i] = request.form[i]
    missing = ''
    for ptmp in ['cv', 'name', 'definition']:
        if ptmp not in ipd:
            missing = missing + ptmp + ' '
    if missing:
        raise InvalidUsage('Missing arguments: ' + missing)
    if 'display_name' not in ipd:
        ipd['display_name'] = ipd['name']
    if 'is_current' not in ipd:
        ipd['is_current'] = 1
    if 'data_type' not in ipd:
        ipd['data_type'] = 'text'
    if not result['rest']['error']:
        try:
            bind = (ipd['cv'], ipd['name'], ipd['definition'],
                    ipd['display_name'], ipd['is_current'],
                    ipd['data_type'],)
            result['rest']['sql_statement'] = SQL['INSERT_CVTERM'] % bind
            g.c.execute(SQL['INSERT_CVTERM'], bind)
            result['rest']['row_count'] = g.c.rowcount
            result['rest']['inserted_id'] = g.c.lastrowid
            g.db.commit()
        except Exception as err:
            raise InvalidUsage(sql_error(err), 500)
    return generate_response(result)

# *****************************************************************************
# * Annotation endpoints                                                      *
# *****************************************************************************
@app.route('/annotations/columns', methods=['GET'])
def get_annotations_columns():
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
    result = initialize_result()
    show_columns(result, "annotation_vw")
    return generate_response(result)


@app.route('/annotation_ids', methods=['GET'])
def get_annotation_ids():
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
    result = initialize_result()
    if execute_sql(result, 'SELECT id FROM annotation_vw', 'temp'):
        result['data'] = []
        for col in result['temp']:
            result['data'].append(col['id'])
        del result['temp']
    return generate_response(result)


@app.route('/annotations/<string:sid>', methods=['GET'])
def get_annotations_by_id(sid):
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM annotation_vw', 'data', sid)
    return generate_response(result)


@app.route('/annotations', methods=['GET'])
def get_annotation_info():
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM annotation_vw', 'data')
    return generate_response(result)


@app.route('/annotationprops/columns', methods=['GET'])
def get_annotationprop_columns():
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
    result = initialize_result()
    show_columns(result, "annotation_property_vw")
    return generate_response(result)


@app.route('/annotationprop_ids', methods=['GET'])
def get_annotationprop_ids():
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
    result = initialize_result()
    if execute_sql(result, 'SELECT id FROM annotation_property_vw', 'temp'):
        result['data'] = []
        for rtmp in result['temp']:
            result['data'].append(rtmp['id'])
        del result['temp']
    return generate_response(result)


@app.route('/annotationprops/<string:sid>', methods=['GET'])
def get_annotationprops_by_id(sid):
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM annotation_property_vw', 'data', sid)
    return generate_response(result)


@app.route('/annotationprops', methods=['GET'])
def get_annotationprop_info():
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM annotation_property_vw', 'data')
    return generate_response(result)


@app.route('/annotationprop', methods=['OPTIONS', 'POST'])
def update_annotation_property(): # pragma: no cover
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
    result = initialize_result()
    update_property(result, 'annotation')
    return generate_response(result)


# *****************************************************************************
# * Assignment endpoints                                                      *
# *****************************************************************************
@app.route('/assignments/columns', methods=['GET'])
def get_assignment_columns():
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
    result = initialize_result()
    show_columns(result, "assignment_vw")
    return generate_response(result)


@app.route('/assignment_ids', methods=['GET'])
def get_assignment_ids():
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
    result = initialize_result()
    if execute_sql(result, 'SELECT id FROM assignment_vw', 'temp'):
        result['data'] = []
        for col in result['temp']:
            result['data'].append(col['id'])
        del result['temp']
    return generate_response(result)


@app.route('/assignments/<string:sid>', methods=['GET'])
def get_assignments_by_id(sid):
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM assignment_vw', 'data', sid)
    return generate_response(result)


@app.route('/assignments', methods=['GET'])
def get_assignment_info():
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM assignment_vw', 'data')
    return generate_response(result)


@app.route('/assignments_completed', methods=['GET'])
def get_assignment_completed_info():
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM assignment_vw WHERE is_complete=1', 'data')
    return generate_response(result)


@app.route('/assignments_open', methods=['GET'])
def get_assignment_open():
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
    result = initialize_result()
    execute_sql(result, "SELECT * FROM assignment_vw WHERE is_complete=0 AND start_date='0000-00-00'", 'data')
    return generate_response(result)


@app.route('/assignments_remaining', methods=['GET'])
def get_assignment_remaining_info():
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM assignment_vw WHERE is_complete=0', 'data')
    return generate_response(result)


@app.route('/assignments_started', methods=['GET'])
def get_assignment_started():
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
    result = initialize_result()
    execute_sql(result, "SELECT * FROM assignment_vw WHERE is_complete=0 AND start_date>'0000-00-00'", 'data')
    return generate_response(result)


@app.route('/assignmentprops/columns', methods=['GET'])
def get_assignmentprop_columns():
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
    result = initialize_result()
    show_columns(result, "assignment_property_vw")
    return generate_response(result)


@app.route('/assignmentprop_ids', methods=['GET'])
def get_assignmentprop_ids():
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
    result = initialize_result()
    if execute_sql(result, 'SELECT id FROM assignment_property_vw', 'temp'):
        result['data'] = []
        for rtmp in result['temp']:
            result['data'].append(rtmp['id'])
        del result['temp']
    return generate_response(result)


@app.route('/assignmentprops/<string:sid>', methods=['GET'])
def get_assignmentprops_by_id(sid):
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM assignment_property_vw', 'data', sid)
    return generate_response(result)


@app.route('/assignmentprops', methods=['GET'])
def get_assignmentprop_info():
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM assignment_property_vw', 'data')
    return generate_response(result)


@app.route('/start_assignment', methods=['OPTIONS', 'POST'])
def start_assignment(): # pragma: no cover
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
    result = initialize_result()
    ipd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            ipd[i] = request.form[i]
    if 'id' not in ipd:
        raise InvalidUsage('Missing arguments: id')
    if not result['rest']['error']:
        try:
            if 'note' in ipd:
                stmt = 'UPDATE assignment SET start_date=NOW(),note=%s WHERE id=%s'
                bind = (ipd['note'], ipd['id'],)
            else:
                stmt = 'UPDATE assignment SET start_date=NOW() WHERE id=%s'
                bind = (ipd['id'],)
            result['rest']['sql_statement'] = stmt % bind
            g.c.execute(stmt, bind)
            result['rest']['row_count'] = g.c.rowcount
            g.db.commit()
        except Exception as err:
            raise InvalidUsage(sql_error(err), 500)
    if result['rest']['row_count'] == 0:
        raise InvalidUsage("Assignment ID %s was not found" % (ipd['id']), 404)
    message = {"category": "assignment", "operation": "start", "mad_id": ipd['id']}
    if 'note' in ipd:
        message['note'] = ipd['note']
    publish(result, message)
    return generate_response(result)


@app.route('/complete_assignment', methods=['OPTIONS', 'POST'])
def complete_assignment(): # pragma: no cover
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
    result = initialize_result()
    ipd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            ipd[i] = request.form[i]
    if 'id' not in ipd:
        raise InvalidUsage('Missing arguments: id')
    if not result['rest']['error']:
        try:
            if 'note' in ipd:
                stmt = 'UPDATE assignment SET complete_date=NOW(),note=%s WHERE id=%s'
                bind = (ipd['note'], ipd['id'],)
            else:
                stmt = 'UPDATE assignment SET complete_date=NOW() WHERE id=%s'
                bind = (ipd['id'],)
            result['rest']['sql_statement'] = stmt % bind
            g.c.execute(stmt, bind)
            result['rest']['row_count'] = g.c.rowcount
            g.db.commit()
        except Exception as err:
            raise InvalidUsage(sql_error(err), 500)
    if result['rest']['row_count'] == 0:
        raise InvalidUsage("Assignment ID %s was not found" % (ipd['id']), 404)
    message = {"category": "assignment", "operation": "complete", "mad_id": ipd['id']}
    if 'note' in ipd:
        message['note'] = ipd['note']
    publish(result, message)
    return generate_response(result)


@app.route('/reset_assignment', methods=['OPTIONS', 'POST'])
def reset_assignment(): # pragma: no cover
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
    result = initialize_result()
    ipd = dict()
    if request.form:
        result['rest']['form'] = request.form
        for i in request.form:
            ipd[i] = request.form[i]
    if 'id' not in ipd:
        raise InvalidUsage('Missing arguments: id')
    if not result['rest']['error']:
        try:
            if 'note' in ipd:
                stmt = "UPDATE assignment SET start_date=0,complete_date=0,is_complete=0,note=%s WHERE id=%s"
                bind = (ipd['note'], ipd['id'],)
            else:
                stmt = 'UPDATE assignment SET start_date=0,complete_date=0,is_complete=0 WHERE id=%s'
                bind = (ipd['id'],)
            result['rest']['sql_statement'] = stmt % bind
            g.c.execute(stmt, bind)
        except Exception as err:
            raise InvalidUsage(sql_error(err), 500)
    if g.c.rowcount == 0:
        raise InvalidUsage("Assignment ID %s was not found" % (ipd['id']), 404)
    # Remove from ElasticSearch
    es_deletes = 0
    payload = {"query": {"term": {"mad_id": ipd['id']}}}
    try:
        searchres = ESEARCH.search(index='mad_activity-*', body=payload)
    except elasticsearch.NotFoundError:
        raise InvalidUsage("Index " + index + " does not exist", 404)
    except Exception as esex: # pragma no cover
        raise InvalidUsage(str(esex))
    for hit in searchres['hits']['hits']:
        try:
            delres = ESEARCH.delete(index=hit['_index'], doc_type='doc', id=hit['_id'])
            es_deletes += 1
        except Exception as esex: # pragma no cover
            raise InvalidUsage(str(esex))
    result['rest']['elasticsearch_deletes'] = es_deletes
    result['rest']['row_count'] = g.c.rowcount
    g.db.commit()
    # Publish to Kafka
    message = {"category": "assignment", "operation": "reset", "mad_id": ipd['id']}
    if 'note' in ipd:
        message['note'] = ipd['note']
    publish(result, message)
    return generate_response(result)


# *****************************************************************************
# * Media endpoints                                                           *
# *****************************************************************************
@app.route('/media/columns', methods=['GET'])
def get_media_columns():
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
    result = initialize_result()
    show_columns(result, "media_vw")
    return generate_response(result)


@app.route('/media_ids', methods=['GET'])
def get_media_ids():
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
    result = initialize_result()
    if execute_sql(result, 'SELECT id FROM media_vw', 'temp'):
        result['data'] = []
        for col in result['temp']:
            result['data'].append(col['id'])
        del result['temp']
    return generate_response(result)


@app.route('/media/<string:sid>', methods=['GET'])
def get_media_by_id(sid):
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM media_vw', 'data', sid)
    return generate_response(result)


@app.route('/media', methods=['GET'])
def get_media_info():
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM media_vw', 'data')
    return generate_response(result)


@app.route('/mediaprops/columns', methods=['GET'])
def get_mediaprop_columns():
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
    result = initialize_result()
    show_columns(result, "media_property_vw")
    return generate_response(result)


@app.route('/mediaprop_ids', methods=['GET'])
def get_mediaprop_ids():
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
    result = initialize_result()
    if execute_sql(result, 'SELECT id FROM media_property_vw', 'temp'):
        result['data'] = []
        for col in result['temp']:
            result['data'].append(col['id'])
        del result['temp']
    return generate_response(result)


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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM media_property_vw', 'data', sid)
    return generate_response(result)


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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM media_property_vw', 'data')
    return generate_response(result)


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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM dvid_url_uuid_vw', 'data')
    return generate_response(result)


# *****************************************************************************
# * User endpoints                                                            *
# *****************************************************************************
@app.route('/users', methods=['GET'])
def get_user_info():
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
    result = initialize_result()
    execute_sql(result, 'SELECT * FROM user_property_vw', 'data')
    return generate_response(result)


# *****************************************************************************


if __name__ == '__main__':
    app.run(debug=True)
