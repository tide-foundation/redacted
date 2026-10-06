"""Keep Redacted's Tide sign-up free of optional Keycloak profile forms."""
from copy import deepcopy
from urllib.parse import quote

from fastapi import HTTPException


def configure_signup(admin):
    # Licensing may replace the imported user-profile settings. Apply this
    # after activation and before the first administrator enables multiAdmin.
    profile = admin.call('GET', '/users/profile')
    if not isinstance(profile, dict) or not isinstance(profile.get('attributes'), list):
        raise HTTPException(502, 'TideCloak did not return its user-profile settings.')
    desired = deepcopy(profile)
    for field in desired['attributes']:
        if field.get('name') in {'email', 'firstName', 'lastName'}:
            field.pop('required', None)
    if desired != profile:
        admin.call('PUT', '/users/profile', json=desired)
        admin.wait_for_approvals('optional sign-up details')
        current = admin.call('GET', '/users/profile')
        admin.require_applied(all('required' not in field for field in current['attributes']
            if field.get('name') in {'email', 'firstName', 'lastName'}), 'optional sign-up details')

    actions = admin.call('GET', '/authentication/required-actions') or []
    for action in actions:
        if action['alias'] in {'VERIFY_PROFILE', 'UPDATE_PROFILE'} and (action.get('enabled') or action.get('defaultAction')):
            path = '/authentication/required-actions/' + quote(action['alias'], safe='')
            admin.call('PUT', path, json={**action, 'enabled': False, 'defaultAction': False})
            admin.wait_for_approvals('sign-up profile actions')
            current = admin.call('GET', path)
            admin.require_applied(not current.get('enabled') and not current.get('defaultAction'), 'sign-up profile actions')

    idp = admin.call('GET', '/identity-provider/instances/tide')
    if not isinstance(idp, dict):
        raise HTTPException(502, 'The Tide identity provider is missing.')
    flows = {idp.get('firstBrokerLoginFlowAlias') or 'first broker login'}
    if idp.get('postBrokerLoginFlowAlias'):
        flows.add(idp['postBrokerLoginFlowAlias'])
    for alias in sorted(flows):
        path = '/authentication/flows/' + quote(alias, safe='') + '/executions'
        executions = admin.call('GET', path)
        if not isinstance(executions, list):
            raise HTTPException(502, 'TideCloak did not return its first-login flow.')
        for execution in executions:
            # Account uniqueness, linking confirmation and reauthentication
            # stay intact. Only the optional profile review is disabled.
            if execution.get('providerId') == 'idp-review-profile' and execution.get('requirement') != 'DISABLED':
                admin.call('PUT', path, json={**execution, 'requirement': 'DISABLED'})
                admin.wait_for_approvals('first-login profile review')
        current = admin.call('GET', path)
        admin.require_applied(all(e.get('requirement') == 'DISABLED' for e in current
            if e.get('providerId') == 'idp-review-profile'), 'first-login profile review')
