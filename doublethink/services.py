'''
doublethink/services.py - rethinkdb service registry

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
import doublethink

class ServiceRegistry(object):
    '''
    Simple service registry which stores service information in the rethinkdb
    table 'services'.

    Services are responsible for keeping their status information up to date
    by calling `heartbeat(status_info)` periodically.

    `status_info` is a dict and must have at least the fields 'role', 'load',
    and 'ttl'. Certain other fields are populated automatically as in the
    example below. In addition, services may set arbitrary other fields.

    Some information about required fields:

       'role': The role of the service. `healthy_service()` and
           `healthy_services()` look up services using this field.
       'ttl': If a service's last heartbeat was more than 'ttl' seconds ago, it
           is considered to be "down". `healthy_services()` and
           `healthy_service()` never return entries for services that are
           considered "down". A sensible convention is to heartbeat 3 times per
           'ttl', that is, every `ttl/3` seconds.
       'load': An arbitrary numeric value. It is up to each service to populate
           this field in a way that makes sense to the particular service.
           `healthy_service(role)` returns the service with the lowest load
           for the supplied role. Thus load values need to be comparable to
           within the context of a single service, but comparing loads of
           services of different roles might not make any sense.

    About the 'id' field:

        The only way that the service registry uniquely identifies a particular
        instance of a service is using the 'id' field.

        Services can supply their own 'id', or let rethinkdb generate a random
        one.

        If a service provides its own 'id', it should make it something
        predictable and unique to each instance of the service. For example
        `'%s:%s:%s' % (role, host, port)` might work for some services.

        If, on the other hand, a server lets rethinkdb generate 'id', it will
        need to remember the result returned by calls to `heartbeat()` and
        supply the `id` value from there with every subsequent heartbeat.

    Example service registry entry, with notes:

        {
            'id': 'd0bed0be-d000-d000-f00d-abeefface0ff'     # generated by rethinkdb if not supplied
            'role': 'brozzler-worker',
            'load': 0.5, # load score
            'ttl': 60.0,
            'host': 'wbgrp-svc999.us.archive.org',           # set in svcreg.heartbeat() as a fallback
            'pid': 1234,                                     # set in svcreg.heartbeat() as a fallback
            'first_heartbeat': '2015-10-30T03:39:40.080814', # set in svcreg.heartbeat()
            'last_heartbeat': '2015-10-30T05:54:35.422866',  # set in svcreg.heartbeat()
            # ... plus anything else you want...
        }
    '''
    logger = logging.getLogger('doublethink.ServiceRegistry')

    def __init__(self, rr):
        '''
        Initialize the service registry.

        Creates the database table if it does not exist.

        Args:
            rr (doublethink.Rethinker): a doublethink.Rethinker, which must
                have `dbname` set
        '''
        self.rr = rr
        self._ensure_table()

    def _ensure_table(self):
        dbs = self.rr.db_list().run()
        assert self.rr.dbname
        if not self.rr.dbname in dbs:
            self.logger.info(
                    'creating rethinkdb database %s', repr(self.rr.dbname))
            self.rr.db_create(self.rr.dbname).run()
        tables = self.rr.table_list().run()
        if not 'services' in tables:
            self.logger.info(
                    "creating rethinkdb table 'services' in database %s",
                    repr(self.rr.dbname))
            self.rr.table_create(
                    'services', shards=1,
                    replicas=min(3, len(self.rr.servers))).run()
            self.rr.table('services').index_create('role').run()

    def heartbeat(self, status_info):
        '''
        Update service status, indicating "up"-ness.

        Args:
            status_info (dict): a dictionary representing the status of the
            service

        `status_info` must have at least the fields 'role', 'load', and
        'ttl'. Some additional fields are populated automatically by this
        method. If the field 'id' is absent, it will be generated by rethinkdb.

        See the ServiceRegistry class-level documentation for more information
        about the various fields.

        Returns:
            On success, returns the modified status info dict. On failure
            communicating with rethinkdb, returns `status_info` unmodified.

        Raises:
            Exception: if `status_info` is missing a required field, or a
                `status_info['ttl']` is not a number greater than zero
        '''
        for field in 'role', 'ttl', 'load':
            if not field in status_info:
                raise Exception(
                        'status_info is missing required field %s',
                        repr(field))
        val = status_info['ttl']
        if not (isinstance(val, float) or isinstance(val, int)) or val <= 0:
            raise Exception('ttl must be a number > 0')
        updated_status_info = dict(status_info)
        updated_status_info['last_heartbeat'] = r.now()
        if not 'first_heartbeat' in updated_status_info:
            updated_status_info['first_heartbeat'] = updated_status_info['last_heartbeat']
        if not 'host' in updated_status_info:
            updated_status_info['host'] = socket.gethostname()
        if not 'pid' in updated_status_info:
            updated_status_info['pid'] = os.getpid()
        try:
            result = self.rr.table('services').insert(
                    updated_status_info, conflict='replace',
                    return_changes=True).run()
            return result['changes'][0]['new_val'] # XXX check
        except:
            self.logger.error('error updating service registry', exc_info=True)
            return status_info

    def unregister(self, id):
        '''
        Remove the service with id `id` from the 'services' table.
        '''
        result = self.rr.table('services').get(id).delete().run()
        if result != {
                'deleted':1, 'errors':0,'inserted':0,
                'replaced':0,'skipped':0,'unchanged':0}:
            self.logger.warn(
                    'unexpected result attempting to delete id=%s from '
                    'rethinkdb services table: %s', id, result)

    def unique_service(self, role, candidate=None):
        '''
        Retrieve a unique service, possibly setting or heartbeating it first.

        A "unique service" is a service with only one instance for a given
        role. Uniqueness is enforced by using the role name as the primary key
        `{'id':role, ...}`.

        Args:
            role (str): role name
            candidate (dict): if supplied, candidate info for the unique
                service, explained below

        `candidate` normally represents "myself, this instance of the service".
        When a service supplies `candidate`, it is nominating itself for
        selection as the unique service, or retaining its claim to the role
        (heartbeating).

        If `candidate` is supplied:

            First, atomically in a single rethinkdb query, checks if there is
            already a unique healthy instance of this service in rethinkdb, and
            if not, sets `candidate` as the unique service.

            Looks at the result of that query to determine if `candidate` is
            the unique service or not. If it is, updates 'last_heartbeat' in
            rethinkdb.

            To determine whether `candidate` is the unique service, checks that
            all the fields other than 'first_heartbeat' and 'last_heartbeat'
            have the same value in `candidate` as in the value returned from
            rethinkdb.

            ***Important***: this means that the caller must ensure that none
            of the fields of the unique service ever change. Don't store things
            like 'load' or any other volatile value in there. If you try to do
            that, heartbeats will end up not being sent, and the unique service
            will flap among the candidates.

        Finally, retrieves the service from rethinkdb and returns it, if it is
        healthy.

        Returns:
            the unique service, if there is one and it is healthy, otherwise
            None
        '''
        # use the same concept of 'now' for all queries
        now = doublethink.utcnow()
        if candidate is not None:
            candidate['id'] = role

            if not 'ttl' in candidate:
                raise Exception("candidate is missing required field 'ttl'")
            val = candidate['ttl']
            if not (isinstance(val, float) or isinstance(val, int)) or val <= 0:
                raise Exception("'ttl' must be a number > 0")

            candidate['first_heartbeat'] = now
            candidate['last_heartbeat'] = now
            if not 'host' in candidate:
                candidate['host'] = socket.gethostname()
            if not 'pid' in candidate:
                candidate['pid'] = os.getpid()

            result = self.rr.table(
                    'services', read_mode='majority').get(role).replace(
                            lambda row: r.branch(
                                r.branch(
                                    row,
                                    row['last_heartbeat'] > now - row['ttl'],
                                    False),
                                row, candidate),
                            return_changes='always').run()
            new_val = result['changes'][0]['new_val']
            if all([new_val.get(k) == candidate[k] for k in candidate
                    if k not in ('first_heartbeat', 'last_heartbeat')]):
                # candidate is the unique_service, send a heartbeat
                del candidate['first_heartbeat'] # don't touch first_heartbeat
                self.rr.table('services').get(role).update(candidate).run()

        results = list(self.rr.table(
            'services', read_mode='majority').get_all(role).filter(
                lambda row: row['last_heartbeat'] > now - row['ttl']).run())
        if results:
            return results[0]
        else:
            return None

    def healthy_service(self, role):
        '''
        Find least loaded healthy service in the registry.

        A service is considered healthy if its 'last_heartbeat' was less than
        'ttl' seconds ago

        Args:
            role (str): role name

        Returns:
            the healthy service with the supplied `role` with the smallest
            value of 'load'
        '''
        try:
            result = self.rr.table('services').get_all(role, index='role').filter(
                    lambda svc: r.now().sub(svc["last_heartbeat"]) < svc["ttl"]
                ).order_by("load")[0].run()
            return result
        except r.ReqlNonExistenceError:
            return None

    def healthy_services(self, role=None):
        '''
        Look up healthy services in the registry.

        A service is considered healthy if its 'last_heartbeat' was less than
        'ttl' seconds ago

        Args:
            role (str, optional): role name

        Returns:
            If `role` is supplied, returns list of healthy services for the
            given role, otherwise returns list of all healthy services. May
            return an empty list.
        '''
        try:
            query = self.rr.table('services')
            if role:
                query = query.get_all(role, index='role')
            query = query.filter(
                lambda svc: r.now().sub(svc["last_heartbeat"]) < svc["ttl"]   #.default(20.0)
            ).order_by("load")
            result = query.run()
            return result
        except r.ReqlNonExistenceError:
            return []

    available_service = healthy_service
    available_services = healthy_services

    def purge_stale_services(self, ttls_until_deletion=2):
        query = self.rr.table('services').filter(
                lambda svc: r.now().sub(svc["last_heartbeat"]).gt(svc["ttl"] * ttls_until_deletion)
            ).delete()
        logging.debug("Running query: %s", query)
        result = query.run()
        logging.debug("Results: %s", result)
        return result
