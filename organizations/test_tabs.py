"""Several tabs of one browser share a session. Each tab says which
organization it's showing (X-Organization) and who it was loaded for
(X-User-Id), so a tab left open can't save into the organization another
tab switched to, or act as someone who signed in later in another tab."""
import json

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from plans import erp

from . import workspace
from .models import Address, Membership
from .tests import GSTIN, PASSWORD, TEST_STORAGES, add_member, make_organization

OTHER_GSTIN = "33BBBBB0000B1Z5"
THIRD_GSTIN = "29CCCCC0000C1Z5"


class TabTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.vaigai, admin = make_organization("Vaigai", GSTIN, "admin@vaigai.example", plan=erp.GROWTH)
        self.kaveri, other_admin = make_organization("Kaveri", OTHER_GSTIN, "admin@kaveri.example", plan=erp.GROWTH)
        self.stranger_org, _ = make_organization("Stranger", THIRD_GSTIN, "admin@stranger.example")
        # One person, Org Admin in two organizations.
        self.user = admin.user
        Membership.objects.create(user=self.user, company=self.kaveri, role=self.kaveri.roles.get(key='org_admin'))
        self.assertTrue(self.client.login(username=self.user.email, password=PASSWORD))

    def api(self, method, path, body=None, organization=None, user=None):
        headers = {}
        if organization is not None:
            headers['HTTP_X_ORGANIZATION'] = str(organization)
        if user is not None:
            headers['HTTP_X_USER_ID'] = str(user)
        kwargs = {'content_type': 'application/json', **headers}
        if body is not None:
            kwargs['data'] = json.dumps(body)
        return getattr(self.client, method)(f'/api/v1{path}', **kwargs)

    def active_company(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['active']['company']['id']


class OrganizationPerTabTests(TabTestCase):
    def test_without_the_header_the_session_default_is_used(self):
        self.assertEqual(self.active_company(self.api('get', '/me')), self.vaigai.pk)

    def test_each_tab_works_on_the_organization_it_names(self):
        self.assertEqual(self.active_company(self.api('get', '/me', organization=self.kaveri.pk)), self.kaveri.pk)
        self.assertEqual(self.active_company(self.api('get', '/me', organization=self.vaigai.pk)), self.vaigai.pk)

    def test_a_save_from_an_old_tab_stays_in_its_organization_after_another_tab_switches(self):
        # Tab A shows Vaigai. Tab B switches to Kaveri.
        response = self.api('post', '/me/active-organization', {'company_id': self.kaveri.pk}, organization=self.vaigai.pk)
        self.assertEqual(self.active_company(response), self.kaveri.pk)  # the switch answers for the new organization
        self.assertEqual(self.client.session[workspace.SESSION_KEY], self.kaveri.pk)  # default for new tabs
        # Tab A, still showing Vaigai, adds an address: it lands in Vaigai.
        response = self.api('post', '/organization/addresses', {'kind': 'warehouse', 'line1': 'Tab A godown'},
                            organization=self.vaigai.pk)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(Address.objects.filter(company=self.vaigai, line1='Tab A godown').exists())
        self.assertFalse(Address.objects.filter(company=self.kaveri, line1='Tab A godown').exists())

    def test_an_organization_the_person_isnt_in_is_refused_not_swapped(self):
        for value in (self.stranger_org.pk, 'not-a-number', 999999):
            response = self.api('get', '/me', organization=value)
            self.assertEqual(response.status_code, 409, value)
            self.assertEqual(response.json()['code'], 'organization')
        response = self.api('post', '/organization/addresses', {'kind': 'warehouse', 'line1': 'Nope'},
                            organization=self.stranger_org.pk)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Address.objects.filter(line1='Nope').exists())

    def test_removed_from_the_tabs_organization(self):
        Membership.objects.filter(user=self.user, company=self.kaveri).update(is_active=False)
        response = self.api('get', '/organization', organization=self.kaveri.pk)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'organization')

    def test_switching_to_an_organization_the_person_isnt_in_is_refused(self):
        response = self.api('post', '/me/active-organization', {'company_id': self.stranger_org.pk})
        self.assertEqual(response.status_code, 404)
        self.assertNotEqual(self.client.session.get(workspace.SESSION_KEY), self.stranger_org.pk)


class UserPerTabTests(TabTestCase):
    def test_matching_user_is_fine(self):
        self.assertEqual(self.api('get', '/me', user=self.user.pk).status_code, 200)

    def test_someone_else_signed_in_from_another_tab(self):
        # The tab was loaded for self.user; another tab then signed in as someone else.
        other = add_member(self.vaigai, "store@vaigai.example", "store").user
        self.client.logout()
        self.assertTrue(self.client.login(username=other.email, password=PASSWORD))
        response = self.api('post', '/organization/addresses', {'kind': 'warehouse', 'line1': 'Wrong person'},
                            organization=self.vaigai.pk, user=self.user.pk)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'user')
        self.assertFalse(Address.objects.filter(line1='Wrong person').exists())

    def test_signed_out_is_still_401(self):
        self.client.logout()
        self.assertEqual(self.api('get', '/me', user=self.user.pk, organization=self.vaigai.pk).status_code, 401)

    def test_csrf_is_still_checked(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.login(username=self.user.email, password=PASSWORD)
        response = client.post('/api/v1/organization/addresses', data=json.dumps({'kind': 'warehouse', 'line1': 'x'}),
                               content_type='application/json', HTTP_X_ORGANIZATION=str(self.vaigai.pk))
        self.assertEqual(response.status_code, 403)


@TEST_STORAGES
class SessionBroadcastTests(TestCase):
    def test_pages_announce_who_they_were_rendered_for(self):
        page = self.client.get(reverse('login'))
        self.assertContains(page, "postMessage({user: null})")
        _company, admin = make_organization("Vaigai", GSTIN, "admin@vaigai.example")
        self.client.login(username=admin.user.email, password=PASSWORD)
        page = self.client.get(reverse('password_change'))
        self.assertContains(page, f"postMessage({{user: {admin.user.pk}}})")
