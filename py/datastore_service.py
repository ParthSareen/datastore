#!/usr/bin/env python

'''
If you're running this from this directory you can start the server with the following command:
./datastore_service.py localhost:8003

sample url looks like this:
http://localhost:8003/store?json=
'''

import sys
import json
import multiprocessing
import threading
from Queue import Queue
import socket
from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
from SocketServer import ThreadingMixIn
from cgi import urlparse
import psycopg2
import os
import time

actions = set(['store'])

#use a thread pool instead of just frittering off new threads for every request
class ThreadPoolMixIn(ThreadingMixIn):
  allow_reuse_address = True  # seems to fix socket.error on server restart

  def serve_forever(self):
    # set up the threadpool
    self.requests = Queue(multiprocessing.cpu_count())
    for x in range(multiprocessing.cpu_count()):
      t = threading.Thread(target = self.process_request_thread)
      t.setDaemon(1)
      t.start()
    # server main loop
    while True:
      self.handle_request()
    self.server_close()

  def make_thread_locals(self):
    #try to connect forever...
    credentials = (os.environ['POSTGRES_DB'], os.environ['POSTGRES_USER'], os.environ['POSTGRES_HOST'], os.environ['POSTGRES_PASSWORD'])
    while True:
      try:
        self.sql_conn = psycopg2.connect("dbname='%s' user='%s' host='%s' password='%s'" % credentials)
        break
      except Exception as e:
        sys.stderr.write('Failed to connect to database with: %s' % repr(e))
        sys.stderr.flush()
        time.sleep(5)

  def process_request_thread(self):
    self.make_thread_locals()
    while True:
      request, client_address = self.requests.get()
      ThreadingMixIn.process_request_thread(self, request, client_address)
    
  def handle_request(self):
    try:
      request, client_address = self.get_request()
    except socket.error:
      return
    if self.verify_request(request, client_address):
      self.requests.put((request, client_address))

#enable threaded server
class ThreadedHTTPServer(ThreadPoolMixIn, HTTPServer):
  pass

#custom handler for getting routes
class StoreHandler(BaseHTTPRequestHandler):

  #boiler plate parsing
  def parse_reports(self, post):
    #split the query from the path
    try:
      split = urlparse.urlsplit(self.path)
    except:
      raise Exception('Try a url that looks like /action?query_string')
    #path has the action in it
    try:
      if split.path.split('/')[-1] not in actions:
        raise
    except:
      raise Exception('Try a valid action: ' + str([k for k in actions]))
    #handle POST
    if post:
      body = self.rfile.read(int(self.headers['Content-Length'])).decode('utf-8')
      sys.stderr.write(body)
      sys.stderr.flush()
      return json.loads(body)
    #handle GET
    else:
      params = urlparse.parse_qs(split.query)
      if 'json' in params:      
        return json.loads(params['json'][0])
    raise Exception('No json provided')

  #parse the request because we dont get this for free!
  def handle_request(self, post):
    #get the reporter data
    reports = self.parse_reports(post)

    #TODO: build a query
    #for report in reports:
      
    #TODO: insert some data
    #self.server.sql_conn.some_function()

    #hand it back
    return 200, 'ok'

  #send an answer
  def answer(self, code, body):
    response = json.dumps({'response': body })
    self.send_response(code)

    #set some basic info
    self.send_header('Access-Control-Allow-Origin','*')
    self.send_header('Content-type', 'application/json;charset=utf-8')
    self.send_header('Content-length', len(response))
    self.end_headers()

    #hand it back
    self.wfile.write(response)

  #handle the request
  def do(self, post):
    try:
      code, body = self.handle_request(post)
      self.answer(code, body)
    except Exception as e:
      self.answer(400, str(e))

  def do_GET(self):
    self.do(False)
  def do_POST(self):
    self.do(True)

#program entry point
if __name__ == '__main__':

  #parse out the address to bind on
  try:
    address = sys.argv[1].split('/')[-1].split(':')
    address[1] = int(address[1])
    address = tuple(address)
    os.environ['POSTGRES_DB']
    os.environ['POSTGRES_USER']
    os.environ['POSTGRES_HOST']
    os.environ['POSTGRES_PASSWORD']
  except Exception as e:
    sys.stderr.write('Bad address or environment: {0}\n'.format(e))
    sys.stderr.flush()
    sys.exit(1)

  #setup the server
  StoreHandler.protocol_version = 'HTTP/1.0'
  httpd = ThreadedHTTPServer(address, StoreHandler)

  #wait until interrupt
  try:
    httpd.serve_forever()
  except KeyboardInterrupt:
    httpd.server_close()