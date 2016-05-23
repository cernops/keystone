# Copyright 2013 OpenStack Foundation
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

"""Main entry point into the Endpoint Filter service."""

import abc

from oslo_config import cfg
from oslo_log import log
import six

from keystone.common import dependency
from keystone.common import extension
from keystone.common import manager
from keystone import exception


CONF = cfg.CONF
LOG = log.getLogger(__name__)

extension_data = {
    'name': 'OpenStack Keystone Endpoint Filter API',
    'namespace': 'http://docs.openstack.org/identity/api/ext/'
                 'OS-EP-FILTER/v1.0',
    'alias': 'OS-EP-FILTER',
    'updated': '2013-07-23T12:00:0-00:00',
    'description': 'OpenStack Keystone Endpoint Filter API.',
    'links': [
        {
            'rel': 'describedby',
            # TODO(ayoung): needs a description
            'type': 'text/html',
            'href': 'https://github.com/openstack/identity-api/blob/master'
                    '/openstack-identity-api/v3/src/markdown/'
                    'identity-api-v3-os-ep-filter-ext.md',
        }
    ]}
extension.register_admin_extension(extension_data['alias'], extension_data)


@dependency.provider('endpoint_filter_api')
@dependency.requires('catalog_api', 'resource_api')
class Manager(manager.Manager):
    """Default pivot point for the Endpoint Filter backend.

    See :mod:`keystone.common.manager.Manager` for more details on how this
    dynamically calls the backend.

    """

    driver_namespace = 'keystone.endpoint_filter'

    def __init__(self):
        super(Manager, self).__init__(CONF.endpoint_filter.driver)

    def _get_endpoint_groups_for_project(self, project_id):
        # recover the project endpoint group memberships and for each
        # membership recover the endpoint group
        if not project_id:
            return []

        self.resource_api.get_project(project_id)
        try:
            refs = self.driver.list_endpoint_groups_for_project(
                project_id)
            endpoint_groups = [self.driver.get_endpoint_group(
                ref['endpoint_group_id']) for ref in refs]
            return endpoint_groups
        except exception.EndpointGroupNotFound:
            return []

    def _get_endpoints_filtered_by_endpoint_group(self, endpoint_group_id):
        endpoints = self.catalog_api.list_endpoints()
        filters = self.driver.get_endpoint_group(endpoint_group_id)['filters']
        filtered_endpoints = []

        for endpoint in endpoints:
            is_candidate = True
            for key, value in filters.items():
                if not isinstance(value, list):
                    value = [value]
                if endpoint[key] not in value:
                    is_candidate = False
                    break
            if is_candidate:
                filtered_endpoints.append(endpoint)
        return filtered_endpoints

    def list_endpoints_for_project(self, project_id):
        """List all endpoints associated with a project.

        :param project_id: project identifier to check
        :type project_id: string
        :returns: a list of endpoint ids or an empty list.

        """
        refs = self.driver.list_endpoints_for_project(project_id)
        filtered_endpoints = {}
        for ref in refs:
            try:
                endpoint = self.catalog_api.get_endpoint(ref['endpoint_id'])
                filtered_endpoints.update({ref['endpoint_id']: endpoint})
            except exception.EndpointNotFound:
                # remove bad reference from association
                self.remove_endpoint_from_project(ref['endpoint_id'],
                                                  project_id)

        # need to recover endpoint_groups associated with project
        # then for each endpoint group return the endpoints.
        endpoint_groups = self._get_endpoint_groups_for_project(project_id)
        for endpoint_group in endpoint_groups:
            endpoint_refs = self._get_endpoints_filtered_by_endpoint_group(
                endpoint_group['id'])
            # now check if any endpoints for current endpoint group are not
            # contained in the list of filtered endpoints
            for endpoint_ref in endpoint_refs:
                if endpoint_ref['id'] not in filtered_endpoints:
                    filtered_endpoints[endpoint_ref['id']] = endpoint_ref

        return filtered_endpoints


@six.add_metaclass(abc.ABCMeta)
class EndpointFilterDriverV8(object):
    """Interface description for an Endpoint Filter driver."""

    @abc.abstractmethod
    def add_endpoint_to_project(self, endpoint_id, project_id):
        """Create an endpoint to project association.

        :param endpoint_id: identity of endpoint to associate
        :type endpoint_id: string
        :param project_id: identity of the project to be associated with
        :type project_id: string
        :raises: keystone.exception.Conflict,
        :returns: None.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def remove_endpoint_from_project(self, endpoint_id, project_id):
        """Removes an endpoint to project association.

        :param endpoint_id: identity of endpoint to remove
        :type endpoint_id: string
        :param project_id: identity of the project associated with
        :type project_id: string
        :raises: exception.NotFound
        :returns: None.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def check_endpoint_in_project(self, endpoint_id, project_id):
        """Checks if an endpoint is associated with a project.

        :param endpoint_id: identity of endpoint to check
        :type endpoint_id: string
        :param project_id: identity of the project associated with
        :type project_id: string
        :raises: exception.NotFound
        :returns: None.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def list_endpoints_for_project(self, project_id):
        """List all endpoints associated with a project.

        :param project_id: identity of the project to check
        :type project_id: string
        :returns: a list of identity endpoint ids or an empty list.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def list_projects_for_endpoint(self, endpoint_id):
        """List all projects associated with an endpoint.

        :param endpoint_id: identity of endpoint to check
        :type endpoint_id: string
        :returns: a list of projects or an empty list.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def delete_association_by_endpoint(self, endpoint_id):
        """Removes all the endpoints to project association with endpoint.

        :param endpoint_id: identity of endpoint to check
        :type endpoint_id: string
        :returns: None

        """
        raise exception.NotImplemented()

    @abc.abstractmethod
    def delete_association_by_project(self, project_id):
        """Removes all the endpoints to project association with project.

        :param project_id: identity of the project to check
        :type project_id: string
        :returns: None

        """
        raise exception.NotImplemented()

    @abc.abstractmethod
    def create_endpoint_group(self, endpoint_group):
        """Create an endpoint group.

        :param endpoint_group: endpoint group to create
        :type endpoint_group: dictionary
        :raises: keystone.exception.Conflict,
        :returns: an endpoint group representation.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def get_endpoint_group(self, endpoint_group_id):
        """Get an endpoint group.

        :param endpoint_group_id: identity of endpoint group to retrieve
        :type endpoint_group_id: string
        :raises: exception.NotFound
        :returns: an endpoint group representation.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def update_endpoint_group(self, endpoint_group_id, endpoint_group):
        """Update an endpoint group.

        :param endpoint_group_id: identity of endpoint group to retrieve
        :type endpoint_group_id: string
        :param endpoint_group: A full or partial endpoint_group
        :type endpoint_group: dictionary
        :raises: exception.NotFound
        :returns: an endpoint group representation.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def delete_endpoint_group(self, endpoint_group_id):
        """Delete an endpoint group.

        :param endpoint_group_id: identity of endpoint group to delete
        :type endpoint_group_id: string
        :raises: exception.NotFound
        :returns: None.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def add_endpoint_group_to_project(self, endpoint_group_id, project_id):
        """Adds an endpoint group to project association.

        :param endpoint_group_id: identity of endpoint to associate
        :type endpoint_group_id: string
        :param project_id: identity of project to associate
        :type project_id: string
        :raises: keystone.exception.Conflict,
        :returns: None.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def get_endpoint_group_in_project(self, endpoint_group_id, project_id):
        """Get endpoint group to project association.

        :param endpoint_group_id: identity of endpoint group to retrieve
        :type endpoint_group_id: string
        :param project_id: identity of project to associate
        :type project_id: string
        :raises: exception.NotFound
        :returns: a project endpoint group representation.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def list_endpoint_groups(self):
        """List all endpoint groups.

        :raises: exception.NotFound
        :returns: None.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def list_endpoint_groups_for_project(self, project_id):
        """List all endpoint group to project associations for a project.

        :param project_id: identity of project to associate
        :type project_id: string
        :raises: exception.NotFound
        :returns: None.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def list_projects_associated_with_endpoint_group(self, endpoint_group_id):
        """List all projects associated with endpoint group.

        :param endpoint_group_id: identity of endpoint to associate
        :type endpoint_group_id: string
        :raises: exception.NotFound
        :returns: None.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def remove_endpoint_group_from_project(self, endpoint_group_id,
                                           project_id):
        """Remove an endpoint to project association.

        :param endpoint_group_id: identity of endpoint to associate
        :type endpoint_group_id: string
        :param project_id: identity of project to associate
        :type project_id: string
        :raises: exception.NotFound
        :returns: None.

        """
        raise exception.NotImplemented()  # pragma: no cover

    @abc.abstractmethod
    def delete_endpoint_group_association_by_project(self, project_id):
        """Remove endpoint group to project associations.

        :param project_id: identity of the project to check
        :type project_id: string
        :returns: None

        """
        raise exception.NotImplemented()  # pragma: no cover


Driver = manager.create_legacy_driver(EndpointFilterDriverV8)
