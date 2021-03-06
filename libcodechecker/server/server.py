# -------------------------------------------------------------------------
#                     The CodeChecker Infrastructure
#   This file is distributed under the University of Illinois Open Source
#   License. See LICENSE.TXT for details.
# -------------------------------------------------------------------------
"""
Main server starts a http server which handles Thrift client
and browser requests.
"""
import atexit
import base64
import datetime
import errno
from hashlib import sha256
from multiprocessing.pool import ThreadPool
import os
import posixpath
from random import sample
import stat
import socket
import ssl
import sys
import urllib

try:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
    from SimpleHTTPServer import SimpleHTTPRequestHandler
except ImportError:
    from http.server import HTTPServer, BaseHTTPRequestHandler, \
        SimpleHTTPRequestHandler

from sqlalchemy.orm import sessionmaker
from thrift.protocol import TJSONProtocol
from thrift.transport import TTransport

from Authentication_v6 import codeCheckerAuthentication as AuthAPI_v6
from codeCheckerDBAccess_v6 import codeCheckerDBAccess as ReportAPI_v6
from ProductManagement_v6 import codeCheckerProductService as ProductAPI_v6

from libcodechecker import session_manager
from libcodechecker.logger import LoggerFactory
from libcodechecker.util import get_tmp_dir_hash

from . import db_cleanup
from . import instance_manager
from . import permissions
from . import routing
from api.authentication import ThriftAuthHandler as AuthHandler_v6
from api.bad_api_version import ThriftAPIMismatchHandler as BadAPIHandler
from api.product_server import ThriftProductHandler as ProductHandler_v6
from api.report_server import ThriftRequestHandler as ReportHandler_v6
from database import database
from database.config_db_model import Product as ORMProduct
from database.run_db_model import IDENTIFIER as RUN_META

LOG = LoggerFactory.get_new_logger('SERVER')


class RequestHandler(SimpleHTTPRequestHandler):
    """
    Handle thrift and browser requests
    Simply modified and extended version of SimpleHTTPRequestHandler
    """
    auth_token = None

    def __init__(self, request, client_address, server):
        BaseHTTPRequestHandler.__init__(self,
                                        request,
                                        client_address,
                                        server)

    def log_message(self, msg_format, *args):
        """ Silencing http server. """
        return

    def __check_auth_in_request(self):
        """
        Wrapper to handle authentication needs from both GET and POST requests.
        Returns a session object if correct cookie is presented or creates a
        new session if the Authorization header and the correct credentials are
        present.
        """

        if not self.server.manager.isEnabled():
            return None

        success = None

        # Authentication can happen in two possible ways:
        #
        # The user either presents a valid session cookie -- in this case
        # checking if the session for the given cookie is valid.

        client_host, client_port = self.client_address

        for k in self.headers.getheaders("Cookie"):
            split = k.split("; ")
            for cookie in split:
                values = cookie.split("=")
                if len(values) == 2 and \
                        values[0] == session_manager.SESSION_COOKIE_NAME:
                    if self.server.manager.is_valid(values[1], True):
                        # The session cookie contains valid data.
                        success = self.server.manager.get_session(values[1],
                                                                  True)

        if success is None:
            # Session cookie was invalid (or not found...)
            # Attempt to see if the browser has sent us
            # an authentication request.
            authHeader = self.headers.getheader("Authorization")
            if authHeader is not None and authHeader.startswith("Basic "):
                authString = base64.decodestring(
                    self.headers.getheader("Authorization").
                    replace("Basic ", ""))

                session = self.server.manager.create_or_get_session(
                    authString)
                if session:
                    return session

        # Else, access is still not granted.
        if success is None:
            LOG.debug(client_host + ":" + str(client_port) +
                      " Invalid access, credentials not found " +
                      "- session refused.")
            return None

        return success

    def end_headers(self):
        # Sending the authentication cookie
        # in every response if any.
        # This will update the the session cookie
        # on the clients to the newest.
        if self.auth_token:
            self.send_header(
                "Set-Cookie",
                "{0}={1}; Path=/".format(
                    session_manager.SESSION_COOKIE_NAME,
                    self.auth_token))
        SimpleHTTPRequestHandler.end_headers(self)

    def do_GET(self):
        """
        Handles the webbrowser access (GET requests).
        """

        auth_session = self.__check_auth_in_request()
        LOG.info("{0}:{1} -- [{2}] GET {3}"
                 .format(self.client_address[0],
                         str(self.client_address[1]),
                         auth_session.user if auth_session else "Anonymous",
                         self.path))

        if self.server.manager.isEnabled() and not auth_session:
            realm = self.server.manager.getRealm()["realm"]
            error_body = self.server.manager.getRealm()["error"]

            self.send_response(401)  # 401 Unauthorised
            self.send_header("WWW-Authenticate",
                             'Basic realm="{0}"'.format(realm))
            self.send_header("Content-type", "text/plain")
            self.send_header("Content-length", str(len(error_body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(error_body)
            return
        else:
            if auth_session is not None:
                self.auth_token = auth_session.token
            product_endpoint, path = \
                routing.split_client_GET_request(self.path)

            if product_endpoint is not None and product_endpoint != '':
                if not self.server.get_product(product_endpoint):
                    LOG.info("Product endpoint '{0}' does not exist."
                             .format(product_endpoint))
                    self.send_error(
                        404,
                        "The product {0} does not exist."
                        .format(product_endpoint))
                    return

                if path == '' and not self.path.endswith('/'):
                    # /prod must be routed to /prod/index.html first, so later
                    # queries for web resources are '/prod/style...' as
                    # opposed to '/style...', which would result in "style"
                    # being considered product name.
                    LOG.debug("Redirecting user from /{0} to /{0}/index.html"
                              .format(product_endpoint))

                    # WARN: Browsers cache '308 Permanent Redirect' responses,
                    # in the event of debugging this, use Private Browsing!
                    self.send_response(308)
                    self.send_header("Location",
                                     self.path.replace(product_endpoint,
                                                       product_endpoint + '/',
                                                       1))
                    self.end_headers()
                    return
                else:
                    # Serves the main page and the resources:
                    # /prod/(index.html) -> /(index.html)
                    # /prod/styles/(...) -> /styles/(...)
                    LOG.debug("Product routing before " + self.path)
                    self.path = self.path.replace(
                        "{0}/".format(product_endpoint), "", 1)
                    LOG.debug("Product routing after: " + self.path)
            else:
                if self.path in ['/', '/index.html']:
                    only_product = self.server.get_only_product()
                    if only_product and only_product.connected:
                        LOG.debug("Redirecting '/' to ONLY product '/{0}'"
                                  .format(only_product.endpoint))

                        self.send_response(307)  # 307 Temporary Redirect
                        self.send_header("Location",
                                         '/{0}'.format(only_product.endpoint))
                        self.end_headers()
                        return

                    # Route homepage queries to serving the product list.
                    LOG.debug("Serving product list as homepage.")
                    self.path = '/products.html'
                else:
                    # The path requested does not specify a product: it is most
                    # likely a resource file.
                    LOG.debug("Serving resource '{0}'".format(self.path))

                self.send_response(200)  # 200 OK

            SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        """
        Handles POST queries, which are usually Thrift messages.
        """

        client_host, client_port = self.client_address
        auth_session = self.__check_auth_in_request()
        LOG.info("{0}:{1} -- [{2}] POST {3}"
                 .format(client_host,
                         str(client_port),
                         auth_session.user if auth_session else "Anonymous",
                         self.path))

        # Create new thrift handler.
        checker_md_docs = self.server.checker_md_docs
        checker_md_docs_map = self.server.checker_md_docs_map
        suppress_handler = self.server.suppress_handler
        version = self.server.version

        protocol_factory = TJSONProtocol.TJSONProtocolFactory()
        input_protocol_factory = protocol_factory
        output_protocol_factory = protocol_factory

        itrans = TTransport.TFileObjectTransport(self.rfile)
        itrans = TTransport.TBufferedTransport(itrans,
                                               int(self.headers[
                                                   'Content-Length']))
        otrans = TTransport.TMemoryBuffer()

        iprot = input_protocol_factory.getProtocol(itrans)
        oprot = output_protocol_factory.getProtocol(otrans)

        if self.server.manager.isEnabled() and \
                not self.path.endswith('/Authentication') and \
                not auth_session:
            # Bail out if the user is not authenticated...
            # This response has the possibility of melting down Thrift clients,
            # but the user is expected to properly authenticate first.

            LOG.debug(client_host + ":" + str(client_port) +
                      " Invalid access, credentials not found " +
                      "- session refused.")
            self.send_error(401)
            return

        # Authentication is handled, we may now respond to the user.
        try:
            product_endpoint, api_ver, request_endpoint = \
                routing.split_client_POST_request(self.path)

            product = None
            if product_endpoint:
                # The current request came through a product route, and not
                # to the main endpoint.
                product = self.server.get_product(product_endpoint)
                if product and not product.connected:
                    # If the product is not connected, try reconnecting...
                    LOG.debug("Request's product '{0}' is not connected! "
                              "Attempting reconnect..."
                              .format(product_endpoint))
                    product.connect()

                    if not product.connected:
                        # If the reconnection fails, send an error to the user.
                        LOG.debug("Product reconnection failed.")
                        self.send_error(  # 500 Internal Server Error
                            500, "Product '{0}' database connection failed!"
                                 .format(product_endpoint))
                        return
                elif not product:
                    LOG.debug("Requested product does not exist.")
                    self.send_error(
                        404, "The product {0} does not exist."
                             .format(product_endpoint))
                    return

            version_supported = routing.is_supported_version(api_ver)
            if version_supported:
                major_version, _ = version_supported

                if major_version == 6:
                    if request_endpoint == 'Authentication':
                        auth_handler = AuthHandler_v6(
                            self.server.manager,
                            auth_session,
                            self.server.config_session)
                        processor = AuthAPI_v6.Processor(auth_handler)
                    elif request_endpoint == 'Products':
                        prod_handler = ProductHandler_v6(
                            self.server,
                            auth_session,
                            self.server.config_session,
                            product,
                            version)
                        processor = ProductAPI_v6.Processor(prod_handler)
                    elif request_endpoint == 'CodeCheckerService':
                        # This endpoint is a product's report_server.
                        if not product:
                            LOG.debug("Requested CodeCheckerService on a "
                                      "nonexistent product.")
                            self.send_error(  # 404 Not Found
                                404,
                                "The specified product '{0}' does not exist!"
                                .format(product_endpoint))
                            return

                        acc_handler = ReportHandler_v6(
                            product.session_factory,
                            product,
                            auth_session,
                            self.server.config_session,
                            checker_md_docs,
                            checker_md_docs_map,
                            suppress_handler,
                            version)
                        processor = ReportAPI_v6.Processor(acc_handler)
                    else:
                        LOG.debug("This API endpoint does not exist.")
                        self.send_error(404,  # 404 Not Fount
                                        "No API endpoint named '{0}'."
                                        .format(self.path))
                        return
            else:
                if request_endpoint == 'Authentication':
                    # API-version checking is supported on the auth endpoint.
                    handler = BadAPIHandler(api_ver)
                    processor = AuthAPI_v6.Processor(handler)
                else:
                    # Send a custom, but valid Thrift error message to the
                    # client requesting this action.
                    LOG.debug("API version v{0} not supported by server."
                              .format(api_ver))
                    self.send_error(400,  # 400 Bad Request
                                    "This API version 'v{0}' is not supported."
                                    .format(api_ver))
                    return

            processor.process(iprot, oprot)
            result = otrans.getvalue()

            self.send_response(200)
            self.send_header("content-type", "application/x-thrift")
            self.send_header("Content-Length", len(result))
            self.end_headers()
            self.wfile.write(result)
            return

        except Exception as exn:
            import traceback
            traceback.print_exc()
            LOG.error(str(exn))
            self.send_error(404, "Request failed.")
            return

    def list_directory(self, path):
        """ Disable directory listing. """
        self.send_error(405, "No permission to list directory")
        return None

    def translate_path(self, path):
        """
        Modified version from SimpleHTTPRequestHandler.
        Path is set to www_root.
        """
        # Abandon query parameters.
        path = path.split('?', 1)[0]
        path = path.split('#', 1)[0]
        path = posixpath.normpath(urllib.unquote(path))
        words = path.split('/')
        words = filter(None, words)
        path = self.server.www_root
        for word in words:
            _, word = os.path.splitdrive(word)
            _, word = os.path.split(word)
            if word in (os.curdir, os.pardir):
                continue
            path = os.path.join(path, word)
        return path


class Product(object):
    """
    Represents a product, which is a distinct storage of analysis reports in
    a separate database (and database connection) with its own access control.
    """

    # The amount of SECONDS that need to pass after the last unsuccessful
    # connect() call so the next could be made.
    CONNECT_RETRY_TIMEOUT = 300

    def __init__(self, orm_object, context, check_env, db_cleanup):
        """
        Set up a new managed product object for the configuration given.
        """
        self.__id = orm_object.id
        self.__endpoint = orm_object.endpoint
        self.__connection_string = orm_object.connection
        self.__display_name = orm_object.display_name
        self.__driver_name = None
        self.__context = context
        self.__check_env = check_env
        self.__engine = None
        self.__session = None
        self.__connected = False
        self.__db_cleanup = db_cleanup

        self.__last_connect_attempt = None

    @property
    def id(self):
        return self.__id

    @property
    def endpoint(self):
        """
        Returns the accessible URL endpoint of the product.
        """
        return self.__endpoint

    @property
    def name(self):
        """
        Returns the display name of the product.
        """
        return self.__display_name

    @property
    def session_factory(self):
        """
        Returns the session maker on this product's database engine which
        can be used to initiate transactional connections.
        """
        return self.__session

    @property
    def driver_name(self):
        """
        Returns the name of the sql driver (sqlite, postgres).
        """
        return self.__driver_name

    @property
    def connected(self):
        """
        Returns whether the product has a valid connection to the managed
        database.
        """
        return self.__connected

    @property
    def last_connection_failure(self):
        """
        Returns the reason behind the last executed connection attempt's
        failure.
        """
        return self.__last_connect_attempt[1] if self.__last_connect_attempt \
            else None

    def connect(self):
        """
        Initiates the actual connection to the database configured for the
        product.
        """
        if self.__connected:
            return

        if self.__last_connect_attempt and \
                (datetime.datetime.now() - self.__last_connect_attempt[0]). \
                total_seconds() <= Product.CONNECT_RETRY_TIMEOUT:
            return

        LOG.debug("Connecting database for product '{0}'".
                  format(self.endpoint))

        # We need to connect to the database and perform setting up the
        # schema.
        LOG.debug("Configuring schema and migration...")
        sql_server = database.SQLServer.from_connection_string(
            self.__connection_string,
            RUN_META,
            self.__context.run_migration_root,
            interactive=False,
            env=self.__check_env)

        if isinstance(sql_server, database.PostgreSQLServer):
            self.__driver_name = 'postgresql'
        elif isinstance(sql_server, database.SQLiteDatabase):
            self.__driver_name = 'sqlite'

        try:
            sql_server.connect(self.__context.run_db_version_info, init=True)

            # Create the SQLAlchemy engine.
            LOG.debug("Connecting database engine")
            self.__engine = sql_server.create_engine()
            self.__session = sessionmaker(bind=self.__engine)

            self.__connected = True
            self.__last_connect_attempt = None
            LOG.debug("Database connected.")

            if self.__db_cleanup:
                db_cleanup.run_cleanup_jobs(self.__session)
        except Exception as ex:
            LOG.error("The database for product '{0}' cannot be connected to."
                      .format(self.endpoint))
            LOG.error(ex.message)
            self.__connected = False
            self.__last_connect_attempt = (datetime.datetime.now(), ex.message)

    def teardown(self):
        """
        Disposes the database connection to the product's backend.
        """
        if not self.__connected:
            return

        self.__engine.dispose()

        self.__session = None
        self.__engine = None


class CCSimpleHttpServer(HTTPServer):
    """
    Simple http server to handle requests from the clients.
    """

    daemon_threads = False

    def __init__(self,
                 server_address,
                 RequestHandlerClass,
                 config_directory,
                 product_db_sql_server,
                 db_cleanup,
                 pckg_data,
                 suppress_handler,
                 context,
                 check_env,
                 manager):

        LOG.debug("Initializing HTTP server...")

        self.config_directory = config_directory
        self.www_root = pckg_data['www_root']
        self.doc_root = pckg_data['doc_root']
        self.checker_md_docs = pckg_data['checker_md_docs']
        self.checker_md_docs_map = pckg_data['checker_md_docs_map']
        self.version = pckg_data['version']
        self.suppress_handler = suppress_handler
        self.context = context
        self.check_env = check_env
        self.manager = manager
        self.db_cleanup = db_cleanup
        self.__products = {}

        # Create a database engine for the configuration database.
        LOG.debug("Creating database engine for CONFIG DATABASE...")
        self.__engine = product_db_sql_server.create_engine()
        self.config_session = sessionmaker(bind=self.__engine)

        # Load the initial list of products and set up the server.
        sess = self.config_session()
        permissions.initialise_defaults('SYSTEM', {
            'config_db_session': sess
        })
        products = sess.query(ORMProduct).all()
        for product in products:
            self.add_product(product)
            permissions.initialise_defaults('PRODUCT', {
                'config_db_session': sess,
                'productID': product.id
            })
        sess.commit()
        sess.close()

        self.__request_handlers = ThreadPool(processes=10)
        try:
            HTTPServer.__init__(self, server_address,
                                RequestHandlerClass,
                                bind_and_activate=True)
            ssl_key_file = os.path.join(config_directory, "key.pem")
            ssl_cert_file = os.path.join(config_directory, "cert.pem")
            if os.path.isfile(ssl_key_file) and os.path.isfile(ssl_cert_file):
                LOG.info("Initiating SSL. Server listening on secure socket.")
                LOG.debug("Using cert file:"+ssl_cert_file)
                LOG.debug("Using key file:"+ssl_key_file)
                self.socket = ssl.wrap_socket(self.socket, server_side=True,
                                              keyfile=ssl_key_file,
                                              certfile=ssl_cert_file)

            else:
                LOG.info("SSL key or cert files not found. Cert: " +
                         ssl_cert_file +
                         "\n Key: " + ssl_key_file)
                LOG.info("Falling back to simple, insecure HTTP.")

        except Exception as e:
            LOG.error("Couldn't start the server: " + e.__str__())
            raise

    def process_request_thread(self, request, client_address):
        try:
            # Finish_request instantiates request handler class.
            self.finish_request(request, client_address)
            self.shutdown_request(request)
        except socket.error as serr:
            if serr[0] == errno.EPIPE:
                LOG.debug("Broken pipe")
                LOG.debug(serr)
                self.shutdown_request(request)

        except Exception as ex:
            LOG.debug(ex)
            self.handle_error(request, client_address)
            self.shutdown_request(request)

    def process_request(self, request, client_address):
        self.__request_handlers.apply_async(self.process_request_thread,
                                            (request, client_address))

    def add_product(self, orm_product):
        """
        Adds a product to the list of product databases connected to
        by the server.
        """
        if orm_product.endpoint in self.__products:
            raise Exception("This product is already configured!")

        LOG.info("Setting up product '{0}'".format(orm_product.endpoint))
        conn = Product(orm_product,
                       self.context,
                       self.check_env,
                       self.db_cleanup)
        self.__products[conn.endpoint] = conn

        conn.connect()

    def get_product(self, endpoint):
        """
        Get the product connection object for the given endpoint, or None.
        """
        return self.__products.get(endpoint, None)

    def get_only_product(self):
        """
        Returns the Product object for the only product connected to by the
        server, or None, if there are 0 or >= 2 products managed.
        """
        return self.__products.items()[0][1] if len(self.__products) == 1 \
            else None

    def remove_product(self, endpoint):
        product = self.get_product(endpoint)
        if not product:
            raise ValueError("The product with the given endpoint '{0}' does "
                             "not exist!".format(endpoint))

        LOG.info("Disconnecting product '{0}'".format(endpoint))
        product.teardown()

        del self.__products[endpoint]


def __make_root_file(root_file):
    """
    Generate a root username and password SHA. This hash is saved to the
    given file path, and is also returned.
    """

    LOG.debug("Generating initial superuser (root) credentials...")

    username = ''.join(sample("ABCDEFGHIJKLMNOPQRSTUVWXYZ", 6))
    password = get_tmp_dir_hash()[:8]

    LOG.info("A NEW superuser credential was generated for the server. "
             "This information IS SAVED, thus subsequent server starts "
             "WILL use these credentials. You WILL NOT get to see "
             "the credentials again, so MAKE SURE YOU REMEMBER THIS "
             "LOGIN!")

    # Highlight the message a bit more, as the server owner configuring the
    # server must know this root access initially.
    credential_msg = "The superuser's username is '{0}' with the " \
                     "password '{1}'".format(username, password)
    LOG.info("-" * len(credential_msg))
    LOG.info(credential_msg)
    LOG.info("-" * len(credential_msg))

    sha = sha256(username + ':' + password).hexdigest()
    with open(root_file, 'w') as f:
        LOG.debug("Save root SHA256 '{0}'".format(sha))
        f.write(sha)

    # This file should be only readable by the process owner, and noone else.
    os.chmod(root_file, stat.S_IRUSR)

    return sha


def start_server(config_directory, package_data, port, db_conn_string,
                 suppress_handler, listen_address, force_auth, db_cleanup,
                 context, check_env):
    """
    Start http server to handle web client and thrift requests.
    """
    LOG.debug("Starting CodeChecker server...")

    LOG.debug("Using suppress file '{0}'"
              .format(suppress_handler.suppress_file))

    server_addr = (listen_address, port)

    root_file = os.path.join(config_directory, 'root.user')
    if not os.path.exists(root_file):
        LOG.warning("Server started without 'root.user' present in "
                    "CONFIG_DIRECTORY!")
        root_sha = __make_root_file(root_file)
    else:
        LOG.debug("Root file was found. Loading...")
        try:
            with open(root_file, 'r') as f:
                root_sha = f.read()
            LOG.debug("Root digest is '{0}'".format(root_sha))
        except:
            LOG.info("Cannot open root file '{0}' even though it exists"
                     .format(root_file))
            root_sha = __make_root_file(root_file)

    try:
        manager = session_manager.SessionManager(root_sha, force_auth)
    except IOError, ValueError:
        LOG.error("The server's authentication config file is invalid!")
        sys.exit(1)

    http_server = CCSimpleHttpServer(server_addr,
                                     RequestHandler,
                                     config_directory,
                                     db_conn_string,
                                     db_cleanup,
                                     package_data,
                                     suppress_handler,
                                     context,
                                     check_env,
                                     manager)

    try:
        instance_manager.register(os.getpid(),
                                  os.path.abspath(
                                      context.codechecker_workspace),
                                  port)
    except IOError as ex:
        LOG.debug(ex.strerror)

    LOG.info("Server waiting for client requests on [{0}:{1}]"
             .format(listen_address, str(port)))

    def unregister_handler(pid):
        """
        Handle errors during instance unregistration.
        The workspace might be removed so updating the
        config content might fail.
        """
        try:
            instance_manager.unregister(pid)
        except IOError as ex:
            LOG.debug(ex.strerror)

    atexit.register(unregister_handler, os.getpid())
    http_server.serve_forever()
    LOG.info("Webserver quit.")


def add_initial_run_database(config_sql_server, product_connection):
    """
    Create a default run database as SQLite in the config directory,
    and add it to the list of products in the config database specified by
    db_conn_string.
    """

    # Connect to the configuration database
    LOG.debug("Creating database engine for CONFIG DATABASE...")
    __engine = config_sql_server.create_engine()
    product_session = sessionmaker(bind=__engine)

    # Load the initial list of products and create the connections.
    sess = product_session()
    products = sess.query(ORMProduct).all()
    if len(products) != 0:
        raise ValueError("Called create_initial_run_database on non-empty "
                         "config database -- you shouldn't have done this!")

    LOG.debug("Adding default product to the config db...")
    product = ORMProduct('Default', product_connection, 'Default',
                         "Default product created at server start.")
    sess.add(product)
    sess.commit()
    sess.close()

    LOG.debug("Default product set up.")
