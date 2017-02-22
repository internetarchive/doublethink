'''
rethinkstuff/services.py - rethinkdb service registry

Copyright (C) 2015-2017 Internet Archive

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
'''

import rethinkdb as r
import logging
import socket
import os

class ServiceRegistry(object):
    '''
    status_info is dict, should have at least these fields
    {
        'id': ...,   # generated by rethinkdb
        'role': 'brozzler-worker',
        'load': 0.5, # load score
        'heartbeat_interval': 20.0,
        'host': 'wbgrp-svc999.us.archive.org',           # set in svcreg.heartbeat() as a fallback
        'pid': 1234,                                     # set in svcreg.heartbeat() as a fallback
        'first_heartbeat': '2015-10-30T03:39:40.080814', # set in svcreg.heartbeat()
        'last_heartbeat': '2015-10-30T05:54:35.422866',  # set in svcreg.heartbeat()
        ... plus anything else you want...
    }
    '''

    logger = logging.getLogger('rethinkstuff.ServiceRegistry')

    def __init__(self, rethinker):
        self.r = rethinker
        self._ensure_table()

    def _ensure_table(self):
        dbs = self.r.db_list().run()
        if not self.r.dbname in dbs:
            self.logger.info('creating rethinkdb database %s', repr(self.r.dbname))
            self.r.db_create(self.r.dbname).run()
        tables = self.r.table_list().run()
        if not 'services' in tables:
            self.logger.info("creating rethinkdb table 'services' in database %s", repr(self.r.dbname))
            self.r.table_create('services', shards=1, replicas=min(3, len(self.r.servers))).run()
            # self.r.table('sites').index_create...?

    def heartbeat(self, status_info):
        '''
        Returns updated status info on success, un-updated status info on
        failure.
        '''
        updated_status_info = dict(status_info)
        updated_status_info['last_heartbeat'] = r.now()
        if not 'first_heartbeat' in updated_status_info:
            updated_status_info['first_heartbeat'] = updated_status_info['last_heartbeat']
        if not 'host' in updated_status_info:
            updated_status_info['host'] = socket.gethostname()
        if not 'pid' in updated_status_info:
            updated_status_info['pid'] = os.getpid()
        try:
            result = self.r.table('services').insert(
                    updated_status_info, conflict='replace',
                    return_changes=True).run()
            return result['changes'][0]['new_val'] # XXX check
        except:
            self.logger.error('error updating service registry', exc_info=True)
            return status_info

    def unregister(self, id):
        result = self.r.table('services').get(id).delete().run()
        if result != {'deleted':1,'errors':0,'inserted':0,'replaced':0,'skipped':0,'unchanged':0}:
            self.logger.warn('unexpected result attempting to delete id=%s from rethinkdb services table: %s', id, result)

    def available_service(self, role):
        try:
            result = self.r.table('services').filter({"role":role}).filter(
                lambda svc: r.now().sub(svc["last_heartbeat"]) < 3 * svc["heartbeat_interval"]   #.default(20.0)
            ).order_by("load")[0].run()
            return result
        except r.ReqlNonExistenceError:
            return None

    def available_services(self, role=None):
        try:
            query = self.r.table('services')
            if role:
                query = query.filter({"role":role})
            query = query.filter(
                lambda svc: r.now().sub(svc["last_heartbeat"]) < 3 * svc["heartbeat_interval"]   #.default(20.0)
            ).order_by("load")
            result = query.run()
            return result
        except r.ReqlNonExistenceError:
            return []

