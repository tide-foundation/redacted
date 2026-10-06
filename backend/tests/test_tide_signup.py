from copy import deepcopy
import json

import httpx
import pytest

from backend.tide_setup import Admin, Pending, HTTPException
from backend.tide_signup import configure_signup


def test_profile_details_are_optional_and_account_link_checks_remain():
    profile = {'attributes': [{'name': name, 'required': {'roles': ['user', 'admin']},
        'validations': {'length': {'max': 255}}} for name in ['username', 'email', 'firstName', 'lastName']],
        'unmanagedAttributePolicy': 'ENABLED'}
    actions = [{'alias': 'VERIFY_PROFILE', 'enabled': True, 'defaultAction': True},
               {'alias': 'UPDATE_PROFILE', 'enabled': True, 'defaultAction': False},
               {'alias': 'VERIFY_EMAIL', 'enabled': True, 'defaultAction': False}]
    executions = [{'id': 'review', 'providerId': 'idp-review-profile', 'requirement': 'REQUIRED'},
                  {'id': 'link', 'providerId': 'idp-confirm-link', 'requirement': 'REQUIRED'},
                  {'id': 'unique', 'providerId': 'idp-create-user-if-unique', 'requirement': 'ALTERNATIVE'}]
    writes = []
    def handle(req):
        path = req.url.path.removeprefix('/admin/realms/test')
        if req.method == 'PUT':
            writes.append(path)
            body = json.loads(req.content)
            if path == '/users/profile': profile.update(body)
            elif path.startswith('/authentication/required-actions/'):
                next(a for a in actions if a['alias'] == body['alias']).update(body)
            elif path.endswith('/executions'):
                next(e for e in executions if e['id'] == body['id']).update(body)
            else: raise AssertionError(path)
            return httpx.Response(204)
        if path == '/users/profile': return httpx.Response(200, json=profile)
        if path == '/authentication/required-actions': return httpx.Response(200, json=actions)
        if path.startswith('/authentication/required-actions/'):
            return httpx.Response(200, json=next(a for a in actions if a['alias'] == path.split('/')[-1]))
        if path == '/identity-provider/instances/tide':
            return httpx.Response(200, json={'firstBrokerLoginFlowAlias': 'custom first login'})
        if path == '/authentication/flows/custom first login/executions': return httpx.Response(200, json=executions)
        if path == '/iga/change-requests': return httpx.Response(200, json=[])
        raise AssertionError(path)
    with httpx.Client(base_url='http://localhost:8080', transport=httpx.MockTransport(handle)) as client:
        configure_signup(Admin(client, 'test'))
        original_writes = writes[:]
        configure_signup(Admin(client, 'test'))
    assert writes == original_writes
    assert 'required' in profile['attributes'][0]
    assert all('required' not in a for a in profile['attributes'][1:])
    assert all(a['validations'] == {'length': {'max': 255}} for a in profile['attributes'])
    assert actions[-1]['enabled']
    assert executions[0]['requirement'] == 'DISABLED'
    assert executions[1]['requirement'] == 'REQUIRED'
    assert executions[2]['requirement'] == 'ALTERNATIVE'


@pytest.mark.parametrize('pending', [False, True])
def test_unapplied_or_governed_profile_changes_do_not_silently_pass(pending):
    profile = {'attributes': [{'name': 'email', 'required': {'roles': ['user']}}]}
    def handle(req):
        if req.url.path.endswith('/users/profile'):
            return httpx.Response(200, json=deepcopy(profile)) if req.method == 'GET' else httpx.Response(202)
        if req.url.path.endswith('/iga/change-requests'):
            return httpx.Response(200, json=[{'id': 'signed-change'}] if pending else [])
        raise AssertionError(req.url.path)
    with httpx.Client(base_url='http://localhost:8080', transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(Pending if pending else HTTPException):
            configure_signup(Admin(client, 'test'))
