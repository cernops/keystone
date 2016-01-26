# Copyright 2012 OpenStack Foundationc
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import itertools
import os.path

from oslo_config import cfg
from oslo_log import log
import uuid
import six

from keystone.catalog.backends import kvs
from keystone.catalog import core
from keystone import exception
from keystone.i18n import _LC


LOG = log.getLogger(__name__)

CONF = cfg.CONF


def parse_templates(template_lines):
    o = {}
    for line in template_lines:
        if ' = ' not in line:
            continue

        k, v = line.strip().split(' = ')
        if not k.startswith('catalog.'):
            continue

        parts = k.split('.')

        region = parts[1]
        # NOTE(termie): object-store insists on having a dash
        service = parts[2].replace('_', '-').rstrip()
        key = parts[3].rstrip()

        region_ref = o.get(region, {})
        service_ref = region_ref.get(service, {})
        service_ref[key] = v

        region_ref[service] = service_ref
        o[region] = region_ref

    return o


class Catalog(kvs.Catalog):
    """A backend that generates endpoints for the Catalog based on templates.

    It is usually configured via config entries that look like:

      catalog.$REGION.$SERVICE.$key = $value

    and is stored in a similar looking hierarchy. Where a value can contain
    values to be interpolated by standard python string interpolation that look
    like (the % is replaced by a $ due to paste attempting to interpolate on
    its own:

      http://localhost:$(public_port)s/

    When expanding the template it will pass in a dict made up of the conf
    instance plus a few additional key-values, notably tenant_id and user_id.

    It does not care what the keys and values are but it is worth noting that
    keystone_compat will expect certain keys to be there so that it can munge
    them into the output format keystone expects. These keys are:

      name - the name of the service, most likely repeated for all services of
             the same type, across regions.

      adminURL - the url of the admin endpoint

      publicURL - the url of the public endpoint

      internalURL - the url of the internal endpoint

    """

    service_properties = ['id', 'enabled', 'type', 'name', 'description']
    endpoint_properties = ['id', 'enabled', 'interface', 'region',
                           'service_id', 'url']

    def __init__(self, templates=None):
        super(Catalog, self).__init__()
        if templates:
            self.templates = templates
        else:
            template_file = CONF.catalog.template_file
            if not os.path.exists(template_file):
                template_file = CONF.find_file(template_file)
            self._load_templates(template_file)

    def _load_templates(self, template_file):
        try:
            self.templates = parse_templates(open(template_file))
        except IOError:
            LOG.critical(_LC('Unable to open template file %s'), template_file)
            raise

    def get_catalog(self, user_id, tenant_id):
        """Retrieve and format the V2 service catalog.

        :param user_id: The id of the user who has been authenticated for
            creating service catalog.
        :param tenant_id: The id of the project. 'tenant_id' will be None in
            the case this being called to create a catalog to go in a domain
            scoped token. In this case, any endpoint that requires a tenant_id
            as part of their URL will be skipped.

        :returns: A nested dict representing the service catalog or an
                  empty dict.

        """
        substitutions = dict(
            itertools.chain(CONF.items(), CONF.eventlet_server.items()))
        substitutions.update({'user_id': user_id})
        silent_keyerror_failures = []
        if tenant_id:
            substitutions.update({'tenant_id': tenant_id})
        else:
            silent_keyerror_failures = ['tenant_id']

        catalog = {}
        # TODO(davechen): If there is service with no endpoints, we should
        # skip the service instead of keeping it in the catalog.
        # see bug #1436704.
        for region, region_ref in self.templates.items():
            catalog[region] = {}
            for service, service_ref in region_ref.items():
                service_data = {}
                try:
                    for k, v in service_ref.items():
                        formatted_value = core.format_url(
                            v, substitutions,
                            silent_keyerror_failures=silent_keyerror_failures)
                        if formatted_value:
                            service_data[k] = formatted_value
                except exception.MalformedEndpoint:
                    continue  # this failure is already logged in format_url()
                catalog[region][service] = service_data

        return catalog

    # (marcos-fermin-lobo) Question: Should be allowed to Create/Update/Delete
    # endpoints, services and regions in this backend?

    # Region CRUD

    def create_region(self, region):
        raise exception.NotImplemented()

    def get_region(self, region_id):
        region_list = self.list_regions(None)
        for region in region_list:
            if region['id'] == region_id:
                return region
        raise exception.RegionNotFound(region_id=region_id)

    def list_regions(self, hints):
        region_list = []
        for region, region_ref in six.iteritems(self.templates):
            reg_ref = {'id': region, 'description': '',
                       'parent_region_id': '', }
            region_list.append(reg_ref)
        return region_list

    def delete_region(self, region_id):
        raise exception.NotImplemented()

    def update_region(self, region_id, region):
        raise exception.NotImplemented()

    # Endpoint CRUD

    def create_endpoint(self, endpoint_id, endpoint):
        raise exception.NotImplemented()

    def get_endpoint(self, endpoint_id):
        endpoint_list = self.list_endpoints([])
        for endpoint in endpoint_list:
            if endpoint['id'] == endpoint_id:
                return endpoint
        raise exception.EndpointNotFound(endpoint_id=endpoint_id)

    def update_endpoint(self, endpoint_id, endpoint):
        raise exception.NotImplemented()

    def delete_endpoint(self, endpoint_id):
       raise exception.NotImplemented()

    def extract_interface_from_string(self, line):
        """Function used to obtain the value of 'url' in
        default_catalog.templates line

        Here we recive a line like:
            catalog.RegionOne.compute.internalURL =
               http://%SERVICE_HOST%:8774/v2/$(tenant_id)s
        And the function return the value:
           http://%SERVICE_HOST%:8774/v2/$(tenant_id)s

        :param line: Line from default_catalog.templates file
        :type line: string
        :returns: URL value as string
       """
        if 'URL' in line:
            return line.split('URL')[0]
        return None

    def list_endpoints(self, hints):
        endpoint_list = []
        # (marcos-fermin-lobo) There are no 'id' parameteres on
        # default_catalog.templates specification and the clients are
        # expecting 'id' parameter so, here we would use a hash.
        for region, region_ref in six.iteritems(self.templates):
            for service, service_ref in six.iteritems(region_ref):
                dict = {'id': '',
                        'region': region, 'region_id': region, 'enabled': True,
                        'legacy_endpoint_id': '',
                        'service_id': uuid.uuid5(uuid.NAMESPACE_DNS, service).hex, }
                interfaces = {}
                for k, v in six.iteritems(service_ref):
                    if k in self.endpoint_properties:
                        dict[k] = v
                    else:
                        # (marcos-fermin-lobo) If k has 'URL' sub-string, we
                        # split the string and obtain the 'interface' and
                        # the 'url' values.
                        interface = self.extract_interface_from_string(k)
                        if interface is not None:
                            interfaces[k] = v
                for k,v in six.iteritems(interfaces):
                    name = service_ref['name']
                    interface = self.extract_interface_from_string(k)
                    endpoint_id = uuid.uuid5(uuid.NAMESPACE_DNS, region+service+name+interface).hex
                    dict['id'] = endpoint_id
                    dict['legacy_endpoint_id'] = endpoint_id
                    dict['interface'] = interface
                    dict['url'] = v
                    endpoint_list.append(dict.copy())
        return endpoint_list

    # Service CRUD

    def create_service(self, service_id, service):
        raise exception.NotImplemented()

    def list_services(self, hints):
        service_dict = {}
        # (marcos-fermin-lobo) There are no 'id' parameters on
        # default_catalog.templates specification and the clients are
        # expecting 'id' parameter so, here we would use a simple hash.
        for region, region_ref in six.iteritems(self.templates):
            for service, service_ref in six.iteritems(region_ref):
                dict = {'id': uuid.uuid5(uuid.NAMESPACE_DNS, service).hex,
                        'type': service, 'enabled': True, 'description': '', }
                for k, v in six.iteritems(service_ref):
                    if k in self.service_properties:
                        dict[k] = v
                service_dict[service] = dict
        return list(service_dict.values())

    def get_service(self, service_id):
        service_list = self.list_services([])
        for service in service_list:
            if service['id'] == service_id:
                return service
        raise exception.ServiceNotFound(service_id=service_id)

    def update_service(self, service_id, service):
        raise exception.NotImplemented()

    def delete_service(self, service_id):
        raise exception.NotImplemented()
