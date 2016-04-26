#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2016 Yohei Nishikubo.
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import webapp2
import logging

from google.appengine.ext import ndb
from google.appengine.api import taskqueue

import json
import hmac
import hashlib

from google.appengine.api import urlfetch

import jsm
import time
from datetime import datetime, timedelta, tzinfo

LINE_ENDPOINT = 'https://trialbot-api.line.me' # given value by LINE. This should be changed in future?

"""
Datastore configurations
"""
class Setting(ndb.Model):
    name = ndb.StringProperty(indexed=True)
    value = ndb.StringProperty(indexed=True)

class Signature(ndb.Model):
    given = ndb.TextProperty()
    calcurated = ndb.TextProperty()
    is_valid = ndb.BooleanProperty()
    address = ndb.StringProperty()
    created_at = ndb.DateTimeProperty(auto_now_add=True)

    @classmethod
    def create(self, given, body, address):
        calcurated = hmac.new(str(Setting.get_by_id('channel_secret').value), body, hashlib.sha256).digest().encode('base64').rstrip()
        is_valid = False
        if given == calcurated:
            is_valid = True
        signature = Signature(given=given, calcurated=calcurated, is_valid=is_valid, address=address)
        signature.put()
        return signature

class Message(ndb.Model):
    text = ndb.TextProperty()
    sender = ndb.StringProperty(indexed=True)
    content_from = ndb.StringProperty(indexed=True)
    created_at = ndb.DateTimeProperty(auto_now_add=True)
    body = ndb.TextProperty()

    @classmethod
    def create(self, string):
        try:
            params = json.loads(string)
            message = Message.get_or_insert(params['id'])
            message.text = params['content']['text']

            # for getting stamp's text.
            # if message.text is None:
            #     message.text = params['content']['contentMetadata']['STKTXT']

            message.sender = params['from']
            message.content_from = params['content']['from']
            message.body = string # mainly for debug.
            message.put()

            taskqueue.add(queue_name='send', url='/tasks/generate', params={'to': params['content']['from'], 'text': message.text})
        except:
            logging.debug(string)

"""
tzinfo for Japanese Standard Time
"""
class JST(tzinfo):
    def utcoffset(self, dt):
        return timedelta(hours=9)

    def dst(self, dt):
        return timedelta(0)

    def tzname(self, dt):
        return 'JST'

"""
functions
"""
def _get_price(text):
    try:
        q = jsm.Quotes()
        price = q.get_price(int(text))
        utime = int(time.mktime(price.date.timetuple()))
        price_date = datetime.fromtimestamp(utime, JST())

        return 'Stock price of {2} at {0} is JPY{1}'.format(price_date.strftime('%Y/%m/%d %H:%M:%S'), '{:,.02f}'.format(price.close), text)

        """
        for historical data
        """
        # prices = q.get_historical_prices(int(text), jsm.DAILY)
        # return "\n".join(map(lambda p:"{0}\tJPY{1}".format(p.date.strftime('%Y/%m/%d'), "{:,.02f}".format(p.close)), prices))
    except:
        return ''


def _get_headers():
    return {
        'X-Line-ChannelID': Setting.get_by_id('channel_id').value,
        'X-Line-ChannelSecret': Setting.get_by_id('channel_secret').value,
        'X-Line-Trusted-User-With-ACL': Setting.get_by_id('mid').value,
        'Content-Type': 'application/json; charset=UTF-8'
    }

def _get_like_content(to):
    data = {
        'to': [to],
        'toChannel': '1383378250', # fixed value by LINE
        'eventType': '138311608800106203', # fixed value by LINE
        'content': {
        'contentType': 8,
        'toType': 1,
        'contentMetadata': {
            'STKPKGID': '1',
            'STKTXT': '[ビシッ]',
            'AT_RECV_MODE': '2',
            'STKVER': '100',
            'STKID': '13'
            }
        }
        }
    return data

def _generate_message(to, text):
    output = _get_price(text) # you can make any output with the input text like getting a search result with with the input text as a query.
    taskqueue.add(queue_name='send', url='/tasks/send', params={'to': to, 'output': output})


def _send_message(to, output):
    data = _get_like_content(to) # reply with stamp for messages without texts like stamps or images.

    if output != '':
        data = {
        'to': [to],
        'toChannel': '1383378250', # fixed value by LINE
        'eventType': '138311608800106203', # fixed value by LINE
        'content':  {
            'contentType': 1,
            'toType': 1,
            'text': output
        }
        }

    logging.debug(json.dumps(data))

    result = urlfetch.fetch(url=LINE_ENDPOINT + '/v1/events',
    payload=json.dumps(data),
    method=urlfetch.POST,
    headers=_get_headers())
    logging.debug(result.content)
    pass

"""
HTTP Handlers
"""
class ConfigHandler(webapp2.RequestHandler):
    def post(self):
        response = {}
        for name in ['channel_id', 'channel_secret', 'mid', 'fb_validation_token']:
            setting = Setting.get_or_insert(name)
            setting.name = name
            setting.value = self.request.get(name).encode('utf8')
            response[name] = setting.value
            setting.put()
        self.response.write(json.dumps(response))

class GenerateMessageHandler(webapp2.RequestHandler):
    def post(self):
        _generate_message(self.request.get('to'), self.request.get('text'))
        self.response.write('ok')

class SendMessageHandler(webapp2.RequestHandler):
    def post(self):
        _send_message(self.request.get('to'), self.request.get('output'))
        self.response.write('ok')

class ParseMessageHandler(webapp2.RequestHandler):
    def post(self):
        Message.create(self.request.get('message'))

        self.response.write('ok')

class ReceiveHandler(webapp2.RequestHandler):
    def post(self):
        given = self.request.get('signature')
        body = self.request.get('body').encode('utf8')
        address = self.request.get('address')

        signature = Signature.create(given, body, address)

        if signature.is_valid:
            params = json.loads(body)
            for param in params['result']:
                taskqueue.add(queue_name='parse', url='/tasks/parse', params={'message': json.dumps(param)})
        else:
            # TODO handling mismatch signature error.
            pass
        self.response.write('ok')

class CallbackHandler(webapp2.RequestHandler):
    def get(self):
        self.response.write('Hello world!')
    def post(self):
        params = {
        'signature': self.request.headers['X-Line-Channelsignature'].rstrip(),
        'body': self.request.body,
        'address': self.request.remote_addr
        }
        taskqueue.add(queue_name='receive', url='/tasks/receive', params=params)
        self.response.write('Thanks, LINE!')

class WebhookHandler(webapp2.RequestHandler):
    def get(self):
        validation_token = Setting.get_by_id('fb_validation_token')

        if validation_token is not None:
            if validation_token.value == self.request.get('hub.verify_token'):
                self.response.write(self.request.get('hub.challenge'))
        else:
            self.response.write('Error, wrong validation token')

    def post(self):
        pass

"""
Path to Handler Settings
"""
app = webapp2.WSGIApplication([
    ('/webhook', WebhookHandler),
    ('/callback', CallbackHandler),
    ('/admin/config', ConfigHandler),
    ('/tasks/receive', ReceiveHandler),
    ('/tasks/parse', ParseMessageHandler),
    ('/tasks/generate', GenerateMessageHandler),
    ('/tasks/send', SendMessageHandler)
], debug=True)
