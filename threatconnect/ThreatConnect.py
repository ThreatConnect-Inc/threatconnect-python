""" standard """
import base64
import hashlib
import hmac
import logging
from pprint import pformat
import re
import socket
import time
import sys
import uuid
from datetime import datetime

""" third-party """
from requests import (exceptions, packages, Request, Session)
# disable ssl warning message
packages.urllib3.disable_warnings()

""" custom """
from threatconnect.ErrorCodes import ErrorCodes
from threatconnect.Config.FilterOperator import FilterSetOperator
from threatconnect.Config.ResourceType import ResourceType
from threatconnect.Config.ResourceProperties import ResourceProperties
from threatconnect.Config.PropertiesEnums import ApiStatus
from threatconnect.ReportEntry import ReportEntry
from threatconnect.Report import Report
from threatconnect.Resources.Adversaries import Adversaries
from threatconnect.Resources.Attributes import Attributes
from threatconnect.Resources.Bulk import Bulk
from threatconnect.Resources.BulkIndicators import BulkIndicators
from threatconnect.Resources.Documents import Documents
from threatconnect.Resources.Emails import Emails
from threatconnect.Resources.FileOccurrences import FileOccurrences
from threatconnect.Resources.Groups import Groups
from threatconnect.Resources.Incidents import Incidents
from threatconnect.Resources.Indicators import Indicators
from threatconnect.Resources.Owners import Owners
from threatconnect.Resources.Tags import Tags
from threatconnect.Resources.Threats import Threats
from threatconnect.Resources.SecurityLabels import SecurityLabels
from threatconnect.Resources.Signatures import Signatures
from threatconnect.Resources.Victims import Victims
from threatconnect.Resources.VictimAssets import VictimAssets

#
# logging
#
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
    filename='tc.log',
    filemode='w')


class ThreatConnect:
    """ """

    def __init__(self, api_aid, api_sec, api_org, api_url, api_max_results=200, base_uri='v2'):
        """ """
        # credentials
        self._api_aid = api_aid
        self._api_sec = api_sec

        # user defined values
        self._api_org = api_org
        self._api_url = api_url
        self._api_max_results = api_max_results
        self.base_uri = base_uri

        # config items
        self.api_request_timeout = 30
        self._report = []
        self._verify_ssl = False

        # initialize request session handle
        self._session = Session()

        # instantiate report object
        self.report = Report()

        # get all owner names
        # self._owners = self.get_owners().get_owner_names()

    def api_build_request(self, resource_obj, request_object, owners=None):
        """ """
        # pd('api_build_request', header=True)

        #
        # initialize vars
        #
        obj_list = []
        if owners is None or not owners:
            owners = [self._api_org]  # set owners to default org
        else:
            owners = list(owners)  # get copy of owners list for pop
        count = len(owners)
        modified_since = None
        request_payload = {}
        result_start = 0
        result_remaining = 0

        #
        # resource object values
        #
        body = request_object.body
        content_type = request_object.content_type
        http_method = request_object.http_method
        owner_allowed = request_object.owner_allowed
        resource_pagination = request_object.resource_pagination
        resource_type = request_object.resource_type
        request_uri = request_object.request_uri

        #
        # ReportEntry (create a report entry for this request)
        #
        report_entry = ReportEntry()
        report_entry.set_action(request_object.name)
        report_entry.set_resource_type(resource_obj.resource_type)
        report_entry.add_data({'HTTP Method': http_method})
        report_entry.add_data({'Max Results': self._api_max_results})
        report_entry.add_data({'Owners': str(owners)})
        report_entry.add_data({'Owner Allowed': owner_allowed})
        report_entry.add_data({'Request URI': request_uri})
        report_entry.add_data({'Request Body': body})
        report_entry.add_data({'Resource Pagination': resource_pagination})
        report_entry.add_data({'Resource Type': resource_type})

        # TODO: what would happen if this was always set to request object value?
        if resource_type.name in [
                'INDICATORS', 'ADDRESSES', 'EMAIL_ADDRESSES', 'FILES', 'HOSTS', 'URLS']:
            modified_since = resource_obj.get_modified_since()

        # update resource object with max results
        # ???moved to report resource_obj.set_max_results(self._api_max_results)

        # append uri to resource object
        # ???moved to report resource_obj.add_uris(request_uri)

        # iterate through all owners and results
        if owner_allowed or resource_pagination:
            # DEBUG
            # pd('owner or resource_pagination allowed')

            if modified_since is not None:
                request_payload['modifiedSince'] = modified_since
                # ReportEntry
                report_entry.add_data({'Modified Since': modified_since})

            # if request_object.owner_allowed:
            #     # if len(list(request_object.owners)) > 0:
            #     # owners = list(request_object.owners)
            #     count = len(owners)

            for x in xrange(count):
                retrieve_data = True

                # only add_obj owner parameter if owners is allowed
                if owner_allowed:
                    owner = owners.pop(0)
                    request_payload['owner'] = owner

                    # DEBUG
                    # pd(' owner', owner)
                    # pd('request_payload', request_payload)

                # only add_obj result parameters if resource_pagination is allowed
                if resource_pagination:
                    result_limit = int(self._api_max_results)
                    result_remaining = result_limit
                    result_start = 0

                while retrieve_data:
                    # set retrieve data to False to prevent loop for non paginating request
                    retrieve_data = False

                    # only add_obj result parameters if resource_pagination is allowed
                    if request_object.resource_pagination:
                        request_payload['resultLimit'] = result_limit
                        request_payload['resultStart'] = result_start

                        # DEBUG
                        # pd('result_limit', result_limit)
                        # pd('result_start', result_start)

                    #
                    # api request
                    #
                    api_response = self._api_request(
                        request_uri, request_payload=request_payload, http_method=http_method, body=body)
                    api_response.encoding = 'utf-8'

                    # ReportEntry
                    report_entry.set_status_code(api_response.status_code)
                    report_entry.add_request_url(api_response.url)

                    # break is status is not valid
                    if api_response.status_code not in [200, 201, 202]:
                        # ReportEntry
                        report_entry.set_status('Failure')
                        report_entry.set_status_code(api_response.status_code)
                        report_entry.add_data({'Failure Message': api_response.content})
                        # Logging
                        logging.error('status_code: %s' % api_response.status_code)
                        logging.error('Failure Message: %s' % api_response.content)
                        break

                    #
                    # CSV Special Case
                    #
                    if re.findall('bulk/csv$', request_object.request_uri):
                        obj_list.extend(
                            self._api_process_response_csv(resource_obj, api_response.content))
                        break

                    #
                    # parse response
                    #
                    api_response_dict = api_response.json()
                    resource_obj.current_url = api_response.url

                    # update group object with api response data
                    resource_obj.add_api_response(api_response.content)
                    resource_obj.add_status_code(api_response.status_code)
                    resource_obj.add_error_message(api_response.content)

                    #
                    # bulk indicators
                    #

                    # indicator response has no status so it must come first
                    if 'indicator' in api_response_dict:

                        #
                        # process response
                        #
                        obj_list.extend(self._api_process_response(
                            resource_obj, api_response, request_object))

                    #
                    # non Success status
                    #
                    elif api_response_dict['status'] != 'Success':
                        # ReportEntry
                        report_entry.set_status(api_response_dict['status'])
                        report_entry.add_data(
                            {'Failure Message': api_response_dict['message']})

                    #
                    # normal response
                    #
                    elif 'data' in api_response_dict:
                        # ReportEntry
                        report_entry.set_status(api_response_dict['status'])

                        # update resource object
                        resource_obj.add_status(ApiStatus[api_response_dict['status'].upper()])

                        #
                        # process response
                        #
                        obj_list.extend(self._api_process_response(
                            resource_obj, api_response, request_object))

                        # add_obj resource_pagination if required
                        if request_object.resource_pagination:
                            # get the number of results returned by the api
                            if result_start == 0:
                                result_remaining = api_response_dict['data']['resultCount']

                            result_remaining -= result_limit

                            # flip retrieve data flag if there are more results to pull
                            if result_remaining > 0:
                                retrieve_data = True

                            # increment the start position
                            result_start += result_limit
                    else:
                        resource_obj.add_error_message(api_response.content)

        elif content_type == 'application/octet-stream':
            #
            # api request
            #
            api_response = self._api_request(
                request_uri, request_payload={}, http_method=http_method,
                body=body, content_type=content_type)

            # ReportEntry
            report_entry.set_status_code(api_response.status_code)
            report_entry.add_request_url(api_response.url)

            if api_response.status_code not in [200, 201, 202]:
                # ReportEntry
                report_entry.set_status('Failure')
                report_entry.add_data({'Failure Message': api_response.content})
                # Logging
                logging.error('Failure Message: %s' % api_response.content)
            else:
                report_entry.set_status('Success')

            return api_response.content
        else:
            #
            # api request
            #
            api_response = self._api_request(
                request_uri, request_payload={}, http_method=http_method, body=body)
            api_response.encoding = 'utf-8'

            # ReportData
            report_entry.set_status_code(api_response.status_code)
            report_entry.add_request_url(api_response.url)

            # break is status is not valid
            if api_response.status_code not in [200, 201, 202]:
                if api_response.status_code == 404:
                    # failure_message = api_response.json()['message']
                    failure_message = api_response.content
                else:
                    failure_message = api_response.content
                # ReportEntry
                report_entry.set_status('Failure')
                report_entry.add_data({'Failure Message': failure_message})
                # Logging
                logging.error('Failure Message: %s' % failure_message)
            else:
                api_response_dict = api_response.json()
                resource_obj.current_url = api_response.url

                # ReportEntry
                report_entry.set_status(api_response_dict['status'])
                report_entry.add_request_url(api_response.url)

                # update group object with api response data
                resource_obj.add_api_response(api_response.content)
                resource_obj.add_status_code(api_response.status_code)
                resource_obj.add_status(ApiStatus[api_response_dict['status'].upper()])

                # no need to process data for deletes or if no data exists
                if http_method != 'DELETE' and 'data' in api_response_dict:
                    #
                    # process response
                    #
                    processed_data = self._api_process_response(
                        resource_obj, api_response, request_object)

                    obj_list.extend(processed_data)

        # ReportData
        report_entry.add_data({'Result Count': len(obj_list)})

        # Report
        self.report.add_unfiltered_results(len(obj_list))
        self.report.add(report_entry)

        return obj_list

    def _api_process_response(self, resource_obj, api_response, request_object):
        """ """
        # DEBUG
        # pd('api_process_response', header=True)
        resource_object_id = request_object.resource_object_id
        obj_list = []

        # convert json response to dict
        api_response_dict = api_response.json()
        api_response_url = api_response.url

        # update group object with result data
        current_filter = resource_obj.get_current_filter()

        # use resource type from resource object to get the resource properties
        # resource_type = resource_obj.resource_type
        properties = ResourceProperties[request_object.resource_type.name].value(
            base_uri=self.base_uri)
        resource_key = properties.resource_key

        # bulk indicator
        if 'indicator' in api_response_dict:
            response_data = api_response_dict['indicator']
        else:
            response_data = api_response_dict['data'][resource_key]

        # DEBUG
        # pd('current_filter', current_filter)
        # pd('resource_type', resource_type)
        # pd('resource_key', resource_key)

        # wrap single response item in a list
        if isinstance(response_data, dict):
            response_data = [response_data]

        result_count = len(response_data)
        resource_obj.add_result_count(result_count)

        # DEBUG
        # pd('result_count', result_count)

        # update group object with result data
        for data in response_data:
            if resource_object_id is not None:
                # if this is an existing resource pull it from Resource object
                # so that it can be updated
                data_obj = resource_obj.get_resource_by_identity(resource_object_id)
            else:
                # create new resource object
                data_obj = properties.resource_object
            data_methods = data_obj.get_data_methods()

            # set values for each resource parameter
            for attrib, obj_method in data_methods.items():
                # DEBUG
                if attrib in data:
                    obj_method(data[attrib])

            #
            # bulk
            #
            resource_type = data_obj.resource_type
            if 500 <= resource_type.value <= 599:

                #
                # attributes
                #

                # check for attributes in bulk download
                if 'attribute' in data:
                    attribute_properties = ResourceProperties.ATTRIBUTES.value(
                        base_uri=self.base_uri)
                    for attribute in data['attribute']:
                        attribute_data_obj = attribute_properties.resource_object
                        attribute_data_methods = attribute_data_obj.get_data_methods()

                        for attrib, obj_method in attribute_data_methods.items():
                            if attrib in attribute:
                                obj_method(attribute[attrib])

                        data_obj.add_attribute_object(attribute_data_obj)

                #
                # tag
                #
                if 'tag' in data:
                    tag_properties = ResourceProperties.TAGS.value(
                        base_uri=self.base_uri)
                    for tag in data['tag']:
                        tag_data_obj = tag_properties.resource_object
                        tag_data_methods = tag_data_obj.get_data_methods()

                        for t, obj_method in tag_data_methods.items():
                            if t in tag:
                                obj_method(tag[t])

                        data_obj.add_tag_object(tag_data_obj)

            data_obj.validate()

            # get resource object id of newly created object or of the previously
            # created object

            # TODO: does this work with matching ids from different owners???
            # a better way may be to hash all values if the order in which they are
            # concatenated is always the same.

            # if the object supports id then use the id
            if hasattr(data_obj, 'get_id'):
                index = data_obj.get_id()
            elif hasattr(data_obj, 'get_name'):
                index = data_obj.get_name()
            else:
                # all object should either support get_id or get_name.
                logging.critical('Critical failure in someone\'s logic.')
                sys.exit(1)

            # add the resource to the master resource object list to make intersections
            # and joins simple when processing filters
            roi = resource_obj.add_master_resource_obj(data_obj, index)

            # get stored object by the returned object id
            stored_obj = resource_obj.get_resource_by_identity(roi)

            # update the api response url and current filter
            stored_obj.add_request_url(api_response_url)
            stored_obj.set_request_object(request_object)
            data_obj.set_phase('new')  # set phase to new

            stored_obj.add_matched_filter(current_filter)

            # append the object to obj_list to be returned for further filtering
            obj_list.append(stored_obj)

        return obj_list

    def _api_process_response_csv(self, resource_obj, csv_data):
        """ """
        obj_list = []

        properties = ResourceProperties.INDICATORS.value(
            base_uri=self.base_uri)

        headers = True
        for line in csv_data.split('\n'):
            if headers:
                # Type,Value,Rating,Confidence
                headers = False
                continue
            elif len(line) == 0:
                continue

            # temporary id
            resource_id = uuid.uuid4().int

            (indicator_type, indicator, rating, confidence) = line.split(',')
            data_obj = properties.resource_object
            data_obj.set_id(resource_id)
            data_obj.set_type(indicator_type)
            data_obj.set_indicator(indicator)
            if confidence != 'null':
                data_obj.set_confidence(int(confidence))
            if rating != 'null':
                data_obj.set_rating(rating)

            # add the resource to the master resource object list to make intersections
            # and joins simple when processing filters
            roi = resource_obj.add_master_resource_obj(data_obj, resource_id)

            # get stored object by the returned object id
            stored_obj = resource_obj.get_resource_by_identity(roi)

            # append the object to obj_list to be returned
            obj_list.append(stored_obj)

        return obj_list

    def _api_request(
            self, request_uri, request_payload, http_method='GET', body=None,
            activity_log='false', content_type='application/json'):
        """ """
        start = datetime.now()
        # DEBUG
        logging.debug('_api_request')
        logging.debug('request_uri: %s' % request_uri)
        logging.debug('request_payload: %s' % pformat(request_payload))
        logging.debug('http_method: %s' % http_method)
        logging.debug('body: %s' % body)
        logging.debug('activity_log: %s' % activity_log)
        logging.debug('content_type: %s' % content_type)

        # Report (count api calls)
        self.report.add_api_call()

        # Decide whether or not to suppress all activity logs
        request_payload.setdefault('createActivityLog', activity_log)

        url = '%s%s' % (self._api_url, request_uri)

        # api request
        api_request = Request(
            http_method, url, data=body, params=request_payload)
        request_prepped = api_request.prepare()
        # get path url to add_obj to header (required for hmac)
        path_url = request_prepped.path_url
        # TODO: add_obj content-type to api_request_header method
        api_headers = self._api_request_headers(http_method, path_url)

        # POST -> add resource, tag or attribute
        # PUT -> update resource, tag or attribute
        # Not all POST or PUT request will have a json body.
        if http_method in ['POST', 'PUT'] and body is not None:
            api_headers['Content-Type'] = content_type
            api_headers['Content-Length'] = len(body)
        request_prepped.prepare_headers(api_headers)

        #
        # api request
        #
        retries = 3
        sleep = 15
        for i in range(0, retries, 1):
            try:
                api_response = self._session.send(
                    request_prepped, verify=self._verify_ssl, timeout=self.api_request_timeout)
                break
            except exceptions.ReadTimeout as e:
                logging.error('Error: %s' % e)
                logging.error('The server may be experiencing delays at the moment.')
                logging.info('Pausing for %s seconds to give server time to catch up.' % sleep)
                time.sleep(sleep)
                logging.info('Retry %s ....' % i)
                if i == retries:
                    logging.error('Exiting Application.')
                    sys.exit(1)
            except exceptions.ConnectionError as e:
                logging.error('Error: %s' % e)
                logging.error('Connection Error. The server may be down.')
                logging.info('Pausing for %s seconds to give server time to catch up.' % sleep)
                time.sleep(sleep)
                logging.info('Retry %s ....' % i)
                if i == retries:
                    logging.error('Exiting Application.')
                    sys.exit(1)
            except socket.error as e:
                logging.error('Error: %s' % e)
                logging.error('Socket Error. The application could not get a network socket')
                logging.error('Exiting Application.')
                sys.exit(1)
            # else:
            #     logging.debug('%s' % api_response.content)
            #     logging.error('Error: unhandled exception')
            #     logging.error('Exiting Application.')
            #     sys.exit(1)

        # DEBUG
        # pd('dir', dir(api_response))
        # pd('url', api_response.url)
        # pd('text', api_response.text)
        # pd('content', api_response.content)
        # pd('status_code', api_response.status_code)

        # pd('path_url', path_url)

        # Report
        self.report.add_request_time(datetime.now() - start)
        return api_response

    def _api_request_headers(self, http_method, api_uri):
        """ """
        timestamp = int(time.time())
        signature = "%s:%s:%d" % (api_uri, http_method, timestamp)
        hmac_signature = hmac.new(self._api_sec, signature, digestmod=hashlib.sha256).digest()
        authorization = 'TC %s:%s' % (self._api_aid, base64.b64encode(hmac_signature))

        return {'Timestamp': timestamp, 'Authorization': authorization}

    def get_filtered_resource(self, resource_obj, filter_objs):
        """ """
        # DEBUG
        # pd('get_filterd_resource', header=True)
        data_set = None

        if not filter_objs:
            # owners = [self._api_org]
            # resource_obj.add_owners(owners)

            #
            # build api call (no filters)
            #
            data_set = self.api_build_request(resource_obj, resource_obj.request_object)
        else:
            first_run = True
            for filter_obj in filter_objs:
                # DEBUG
                # pd('filter_obj', filter_objs)
                if resource_obj.resource_type != ResourceType.OWNERS:
                    resource_obj.add_owners(filter_obj.get_owners())
                set_operator = filter_obj.get_filter_operator()

                obj_list = []
                # iterate through each filter method
                if len(filter_obj) > 0:
                    # request object are for api filters
                    for request_obj in filter_obj:
                        # DEBUG
                        # pd('request_obj', request_obj)
                        resource_obj.set_current_filter(request_obj.name)
                        resource_obj.set_owner_allowed(request_obj.owner_allowed)
                        resource_obj.set_resource_pagination(request_obj.resource_pagination)
                        resource_obj.set_request_uri(request_obj.request_uri)
                        resource_obj.set_resource_type(request_obj.resource_type)
                        obj_list.extend(self.api_build_request(
                            resource_obj, request_obj, filter_obj.get_owners()))
                else:
                    # resource_obj.set_owner_allowed(filter_obj.get_owner_allowed())
                    # resource_obj.set_resource_pagination(filter_obj.get_resource_pagination())
                    # resource_obj.set_request_uri(filter_obj.get_request_uri())
                    # resource_obj.set_resource_type(filter_obj.resource_type)

                    obj_list.extend(self.api_build_request(
                        resource_obj, filter_obj.request_object, filter_obj.get_owners()))
                    # obj_list.extend(self.api_build_request(resource_obj, resource_obj.request_obj))

                #
                # post filters
                #
                # pf_obj_set = set()
                # for pf_obj in filter_obj.get_post_filters():
                # filter_method = getattr(resource_obj, pf_obj.method)
                #     pf_obj_set.update(filter_method(pf_obj.filter, pf_obj.operator))

                pf_obj_set = set(obj_list)
                for pf_obj in filter_obj.get_post_filters():
                    # current post filter method
                    filter_method = getattr(resource_obj, pf_obj.method)

                    # current post filter results
                    post_filter_results = set(filter_method(pf_obj.filter, pf_obj.operator))
                    pf_obj_set = pf_obj_set.intersection(post_filter_results)

                if filter_obj.get_post_filters_len() > 0:
                    obj_list = list(pf_obj_set)

                # intersection pf_obj list with obj_list to apply filters
                # to current result set
                # if filter_obj.get_post_filters_len() > 0:
                #     obj_list = pf_obj_set.intersection(obj_list)

                if first_run:
                    data_set = set(obj_list)
                    first_run = False
                    continue

                if set_operator is FilterSetOperator.AND:
                    data_set = data_set.intersection(obj_list)
                elif set_operator is FilterSetOperator.OR:
                    data_set.update(set(obj_list))

        # Report
        self.report.add_filtered_results(len(data_set))

        # add_obj data objects to group object
        for obj in data_set:
            resource_obj.add_obj(obj)

    def adversaries(self):
        """ """
        return Adversaries(self)

    def attributes(self):
        """ """
        return Attributes(self)

    def bulk(self):
        """ """
        return Bulk(self)

    def bulk_indicators(self):
        """ """
        return BulkIndicators(self)

    def documents(self):
        """ """
        return Documents(self)

    def emails(self):
        """ """
        return Emails(self)

    def file_occurrences(self):
        """ """
        return FileOccurrences(self)

    def groups(self):
        """ """
        return Groups(self)

    def incidents(self):
        """ """
        return Incidents(self)

    def indicators(self):
        """ """
        return Indicators(self)

    def owners(self):
        """ """
        return Owners(self)

    def security_labels(self):
        """ """
        return SecurityLabels(self)

    def signatures(self):
        """ """
        return Signatures(self)

    def tags(self):
        """ """
        return Tags(self)

    def threats(self):
        """ """
        return Threats(self)

    def victims(self):
        """ """
        return Victims(self)

    def victim_assets(self):
        """ """
        return VictimAssets(self)

    def set_max_results(self, max_results):
        """ """
        # validate the max_results is an integer
        if isinstance(max_results, int):
            print(ErrorCodes.e0100.value)
        else:
            self._api_max_results = max_results