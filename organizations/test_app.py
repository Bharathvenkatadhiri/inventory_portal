"""The signed-in ERP app's API (/api/v1, organizations.app_api) and the
Django pages around it: /app, invitations and the home redirect."""
import json
import tempfile
from datetime import timedelta
from pathlib import Path

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Company, ConsumerProfile
from billing.models import OrganizationSubscription
from core.models import User
from plans import erp

from . import catalog
from .models import BusinessRelationship, Invitation, Membership, Team
from .tests import GSTIN, PASSWORD, TEST_STORAGES, add_member, make_organization, verify_gstin_for_session

OTHER_GSTIN = "33BBBBB0000B1Z5"
THIRD_GSTIN = "29CCCCC0000C1Z5"


class ApiTestCase(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.company, self.admin = make_organization("Vaigai", GSTIN, "admin@vaigai.example", plan=erp.STARTER)
        self.login(self.admin.user)

    def login(self, user):
        self.client.logout()
        self.assertTrue(self.client.login(username=user.email, password=PASSWORD))

    def api(self, method, path, body=None):
        kwargs = {'content_type': 'application/json'}
        if body is not None:
            kwargs['data'] = json.dumps(body)
        return getattr(self.client, method)(f'/api/v1{path}', **kwargs)

    def lapse(self, company=None):
        OrganizationSubscription.objects.filter(company=company or self.company).update(
            status=OrganizationSubscription.LAPSED)


class MeTests(ApiTestCase):
    def test_me_describes_the_person_and_their_organization(self):
        me = self.api('get', '/me').json()
        self.assertEqual(me['user']['email'], 'admin@vaigai.example')
        self.assertEqual([m['company_name'] for m in me['memberships']], ['Vaigai'])
        active = me['active']
        self.assertEqual((active['company']['gstin'], active['role']['key']), (GSTIN, catalog.ORG_ADMIN))
        self.assertEqual(active['seats']['full'], 1)
        self.assertEqual(active['subscription']['status'], 'trialing')
        self.assertTrue(active['subscription']['entitled'])

    def test_switching_between_organizations(self):
        other, _ = make_organization("Annai", OTHER_GSTIN, "admin@annai.example")
        Membership.objects.create(user=self.admin.user, company=other, role=other.roles.get(key='accounts'))
        me = self.api('post', '/me/active-organization', {'company_id': other.pk}).json()
        self.assertEqual((me['active']['company']['name'], me['active']['role']['name']), ('Annai', 'Accounts'))
        self.assertEqual(self.api('get', '/me').json()['active']['company']['name'], 'Annai')
        third, _ = make_organization("Kaveri", THIRD_GSTIN, "admin@kaveri.example")
        self.assertEqual(self.api('post', '/me/active-organization', {'company_id': third.pk}).status_code, 404)

    def test_profile_edit(self):
        response = self.api('patch', '/me', {'first_name': '  Meena ', 'phone': '98765 43210'})
        self.assertEqual((response.json()['first_name'], response.json()['phone']), ('Meena', '9876543210'))
        self.assertEqual(self.api('patch', '/me', {'phone': '12'}).status_code, 400)

    def test_the_api_needs_a_signed_in_session_not_a_redirect(self):
        self.client.logout()
        response = self.api('get', '/me')
        self.assertEqual(response.status_code, 401)


@TEST_STORAGES
class AppPageTests(ApiTestCase):
    def test_anonymous_visitors_are_sent_to_sign_in(self):
        self.client.logout()
        response = self.client.get('/app/partners')
        self.assertRedirects(response, f"{reverse('login')}?next=/app/partners", fetch_redirect_response=False)

    def test_without_a_build_the_page_says_how_to_get_one(self):
        with override_settings(FRONTEND_DIST=Path(tempfile.mkdtemp())):
            response = self.client.get('/app/')
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "pnpm dev", status_code=503)

    def test_the_built_app_is_served_for_every_path_with_a_csrf_cookie(self):
        dist = Path(tempfile.mkdtemp())
        (dist / 'index.html').write_text('<div id="root"></div><!-- built -->', encoding='utf-8')
        with override_settings(FRONTEND_DIST=dist):
            for path in ('/app/', '/app/settings/people'):
                response = self.client.get(path)
                self.assertContains(response, '<!-- built -->')
                self.assertIn('csrftoken', response.cookies)

    def test_people_without_an_organization_are_told_so(self):
        self.admin.is_active = False
        self.admin.save()
        other_admin = add_member(self.company, 'second@vaigai.example', catalog.ORG_ADMIN)
        self.assertEqual(self.client.get('/app/').status_code, 403)
        self.assertEqual(self.client.get(reverse('home')).status_code, 403)
        self.login(other_admin.user)
        self.assertEqual(self.client.get(reverse('home')).status_code, 302)

    def test_marketplace_accounts_never_see_the_app(self):
        buyer = User.objects.create_user(username='b@example.com', email='b@example.com', password=PASSWORD, role='consumer')
        self.login(buyer)
        self.assertRedirects(self.client.get('/app/'), reverse('home'), fetch_redirect_response=False)
        self.assertEqual(self.api('get', '/organization').status_code, 403)


class OrganizationSettingsTests(ApiTestCase):
    def test_admin_edits_the_profile_and_sides(self):
        company = self.api('patch', '/organization', {'name': 'Vaigai Works', 'findable': False,
                                                      'selling_enabled': False}).json()
        self.assertEqual((company['name'], company['findable'], company['selling_enabled']), ('Vaigai Works', False, False))
        self.assertEqual(self.api('patch', '/organization', {'buying_enabled': False}).status_code, 400)

    def test_other_roles_can_read_but_not_change(self):
        self.login(add_member(self.company, 'buyer@vaigai.example', 'purchase_manager').user)
        self.assertEqual(self.api('get', '/organization').status_code, 200)
        response = self.api('patch', '/organization', {'name': 'Taken over'})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Purchase Manager", response.json()['detail'])
        self.assertEqual(self.api('get', '/organization/bank-accounts').status_code, 403)

    def test_addresses(self):
        created = self.api('post', '/organization/addresses', {
            'kind': 'works', 'line1': 'Plot 7, SIDCO', 'city': 'Madurai', 'state_code': '33', 'pincode': '625007',
            'is_default': True}).json()
        self.assertEqual(created['kind'], 'works')
        self.assertEqual(self.api('post', '/organization/addresses', {'kind': 'works', 'line1': 'x', 'pincode': '12'}).status_code, 400)
        self.assertEqual(self.api('put', f"/organization/addresses/{created['id']}",
                                  {**created, 'line1': 'Plot 8'}).json()['line1'], 'Plot 8')
        kinds = [a['kind'] for a in self.api('get', '/organization/addresses').json()]
        self.assertEqual(kinds, ['works'])
        self.assertEqual(self.api('delete', f"/organization/addresses/{created['id']}").status_code, 204)

    def test_bank_accounts_are_checked(self):
        good = {'account_name': 'Vaigai Precision', 'bank_name': 'Canara Bank', 'account_number': '1234 5678 9012',
                'ifsc': 'cnrb0001234'}
        account = self.api('post', '/organization/bank-accounts', good).json()
        self.assertEqual((account['ifsc'], account['account_number']), ('CNRB0001234', '123456789012'))
        self.assertEqual(self.api('post', '/organization/bank-accounts', {**good, 'ifsc': 'CNRB1234'}).status_code, 400)
        self.assertEqual(self.api('post', '/organization/bank-accounts', {**good, 'account_number': '12a'}).status_code, 400)

    def test_a_lapsed_organization_can_read_but_not_change_anything(self):
        self.lapse()
        self.assertEqual(self.api('get', '/organization').status_code, 200)
        response = self.api('patch', '/organization', {'name': 'x'})
        self.assertEqual(response.status_code, 402)
        self.assertIn("lapsed", response.json()['detail'])


@TEST_STORAGES
class PeopleTests(ApiTestCase):
    def roles(self):
        return {r.key: r for r in self.company.roles.all()}

    def invite(self, email, role_key, **extra):
        return self.api('post', '/people/invitations', {
            'email': email, 'first_name': 'Ravi', 'role_id': self.roles()[role_key].pk, **extra})

    def test_inviting_emails_a_link_and_holds_a_seat(self):
        response = self.invite('Ravi@Vaigai.example', 'purchase_manager')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['email'], 'ravi@vaigai.example')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/accounts/invitations/', mail.outbox[0].body)
        people = self.api('get', '/people').json()
        self.assertEqual((people['seats']['full'], people['seats']['full_invited']), (1, 1))
        self.assertEqual(len(people['invitations']), 1)

    def test_full_users_stop_at_the_plan_limit_but_light_users_dont(self):
        self.invite('a@vaigai.example', 'accounts')
        self.invite('b@vaigai.example', 'sales_manager')
        response = self.invite('c@vaigai.example', 'qc')  # Starter: 3 full users, all used
        self.assertEqual(response.status_code, 400)
        self.assertIn("Move to Growth", response.json()['detail'])
        for i in range(7):  # 5 light users included; more are allowed and billed
            self.assertEqual(self.invite(f'op{i}@vaigai.example', 'machine_operator').status_code, 200)

    def test_duplicates_and_marketplace_addresses_are_refused(self):
        self.invite('a@vaigai.example', 'accounts')
        self.assertIn("already has an open invitation", self.invite('a@vaigai.example', 'qc').json()['detail'])
        self.assertIn("already in this organization", self.invite('admin@vaigai.example', 'qc').json()['detail'])
        User.objects.create_user(username='m@x.example', email='m@x.example', password=PASSWORD, role='manufacturer')
        self.assertIn("marketplace account", self.invite('m@x.example', 'qc').json()['detail'])

    def test_only_people_managers_invite(self):
        self.login(add_member(self.company, 'buyer@vaigai.example', 'purchase_manager').user)
        self.assertEqual(self.invite('x@vaigai.example', 'qc').status_code, 403)
        self.assertEqual(self.api('get', '/people').json()['invitations'], [])

    def test_resend_replaces_the_link_and_revoke_closes_it(self):
        invitation_id = self.invite('a@vaigai.example', 'accounts').json()['id']
        first_link = mail.outbox[-1].body.split('/accounts/invitations/')[1].split('/')[0]
        self.api('post', f'/people/invitations/{invitation_id}/resend')
        self.assertEqual(self.client.get(f'/accounts/invitations/{first_link}/').status_code, 404)
        self.assertEqual(self.api('delete', f'/people/invitations/{invitation_id}').status_code, 204)
        self.assertFalse(Invitation.objects.get(pk=invitation_id).is_open)

    def test_changing_roles_teams_and_access(self):
        member = add_member(self.company, 'buyer@vaigai.example', 'purchase_manager')
        team = Team.objects.create(company=self.company, name='Purchase')
        updated = self.api('patch', f'/people/members/{member.pk}', {
            'role_id': self.roles()['store_keeper'].pk, 'team_ids': [team.pk]}).json()
        self.assertEqual((updated['role']['key'], [t['name'] for t in updated['teams']]), ('store_keeper', ['Purchase']))
        self.assertFalse(self.api('patch', f'/people/members/{member.pk}', {'is_active': False}).json()['is_active'])

    def test_there_is_always_an_active_org_admin(self):
        response = self.api('patch', f'/people/members/{self.admin.pk}', {'is_active': False})
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least one active Org Admin", response.json()['detail'])
        self.assertEqual(self.api('patch', f'/people/members/{self.admin.pk}',
                                  {'role_id': self.roles()['accounts'].pk}).status_code, 400)

    def test_reactivating_needs_a_free_seat(self):
        a = add_member(self.company, 'a@vaigai.example', 'accounts')
        b = add_member(self.company, 'b@vaigai.example', 'accounts')
        self.api('patch', f'/people/members/{b.pk}', {'is_active': False})
        self.invite('c@vaigai.example', 'qc')  # takes the freed seat
        response = self.api('patch', f'/people/members/{b.pk}', {'is_active': True})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Your plan includes 3 users", response.json()['detail'])
        self.assertTrue(a.is_active)


@TEST_STORAGES
class InvitationAcceptTests(ApiTestCase):
    def invite(self, email, role_key='accounts', teams=()):
        role = self.company.roles.get(key=role_key)
        response = self.api('post', '/people/invitations', {'email': email, 'first_name': 'Ravi', 'role_id': role.pk,
                                                           'team_ids': [t.pk for t in teams]})
        self.assertEqual(response.status_code, 200)
        return '/accounts/invitations/' + mail.outbox[-1].body.split('/accounts/invitations/')[1].split('/')[0] + '/'

    def test_someone_new_sets_a_password_and_lands_in_the_app(self):
        team = Team.objects.create(company=self.company, name='Accounts')
        link = self.invite('ravi@vaigai.example', teams=[team])
        self.client.logout()
        page = self.client.get(link)
        self.assertContains(page, "Set up your login")
        response = self.client.post(link, {'first_name': 'Ravi', 'last_name': 'K', 'password1': PASSWORD,
                                           'password2': PASSWORD})
        self.assertRedirects(response, reverse('erp-app'), fetch_redirect_response=False)
        user = User.objects.get(email='ravi@vaigai.example')
        self.assertEqual((user.role, user.email_verified), ('member', True))
        membership = Membership.objects.get(user=user)
        self.assertEqual((membership.company, membership.role.key), (self.company, 'accounts'))
        self.assertTrue(team.members.filter(pk=user.pk).exists())
        self.assertEqual(self.api('get', '/me').json()['active']['company']['id'], self.company.pk)  # signed in
        self.assertEqual(self.client.get(link).status_code, 404)  # used up

    def test_someone_already_on_makesetu_signs_in_and_accepts(self):
        other, other_admin = make_organization("Annai", OTHER_GSTIN, "admin@annai.example")
        link = self.invite('admin@annai.example')
        self.client.logout()
        self.assertContains(self.client.get(link), "Sign in to accept")
        self.login(other_admin.user)
        self.assertContains(self.client.get(link), "Join Vaigai")
        self.assertRedirects(self.client.post(link), reverse('erp-app'), fetch_redirect_response=False)
        self.assertEqual(Membership.objects.filter(user=other_admin.user).count(), 2)
        self.assertEqual(self.api('get', '/me').json()['active']['company']['name'], 'Vaigai')

    def test_the_wrong_account_or_an_expired_link_cant_accept(self):
        link = self.invite('ravi@vaigai.example')
        self.assertContains(self.client.get(link), "signed in as someone else")
        Invitation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.client.logout()
        self.assertEqual(self.client.get(link).status_code, 404)


class RolesApiTests(ApiTestCase):
    def test_creating_and_editing_a_custom_role(self):
        role = self.api('post', '/roles', {'name': 'Dispatch Clerk', 'user_type': 'full',
                                           'permissions': ['dispatch.post', 'stock.view']}).json()
        self.assertEqual((role['key'], role['is_system'], role['permissions']),
                         ('dispatch-clerk', False, ['dispatch.post', 'stock.view']))
        updated = self.api('patch', f"/roles/{role['id']}", {'permissions': ['dispatch.post']}).json()
        self.assertEqual(updated['permissions'], ['dispatch.post'])
        self.assertEqual(self.api('delete', f"/roles/{role['id']}").status_code, 204)

    def test_light_roles_only_take_light_permissions(self):
        response = self.api('post', '/roles', {'name': 'Gate Plus', 'user_type': 'light', 'permissions': ['po.approve']})
        self.assertEqual(response.status_code, 400)
        self.assertIn("can't have: po.approve", response.json()['detail'])
        self.assertEqual(self.api('post', '/roles', {'name': 'Gate', 'user_type': 'light',
                                                     'permissions': ['gate_entry.record']}).status_code, 200)

    def test_protected_roles(self):
        roles = {r['key']: r for r in self.api('get', '/roles').json()}
        self.assertTrue(roles['org_admin']['locked'])
        self.assertEqual(self.api('patch', f"/roles/{roles['org_admin']['id']}", {'permissions': []}).status_code, 400)
        self.assertEqual(self.api('delete', f"/roles/{roles['qc']['id']}").status_code, 400)  # a starting role
        self.assertEqual(self.api('post', '/roles', {'name': 'QC'}).status_code, 400)  # name taken

    def test_permission_list_says_what_the_plan_and_sides_allow(self):
        perms = {p['code']: p for p in self.api('get', '/organization/permissions').json()}
        self.assertEqual((perms['work_order.manage']['included'], perms['work_order.manage']['needs_plan']), (False, 'Growth'))
        self.assertTrue(perms['production.record']['light_allowed'])
        self.assertTrue(perms['po.create']['side_enabled'])


class TeamsApiTests(ApiTestCase):
    def test_teams_with_a_head_and_members(self):
        buyer = add_member(self.company, 'buyer@vaigai.example', 'purchase_manager')
        team = self.api('post', '/teams', {'name': 'Purchase', 'head_id': buyer.user.pk, 'member_ids': [self.admin.user.pk]}).json()
        self.assertEqual(team['head']['id'], buyer.user.pk)
        self.assertEqual(len(team['members']), 2)  # the head is a member too
        renamed = self.api('put', f"/teams/{team['id']}", {'name': 'Procurement', 'member_ids': []}).json()
        self.assertEqual((renamed['name'], renamed['head'], renamed['members']), ('Procurement', None, []))

    def test_starter_has_two_teams_and_only_members_join(self):
        self.api('post', '/teams', {'name': 'Purchase'})
        self.api('post', '/teams', {'name': 'Store'})
        response = self.api('post', '/teams', {'name': 'QC'})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Move to Growth", response.json()['detail'])
        _, outsider = make_organization("Other", OTHER_GSTIN, "admin@other.example")
        team_id = self.api('get', '/teams').json()[0]['id']
        self.assertEqual(self.api('put', f'/teams/{team_id}', {'name': 'Purchase', 'member_ids': [outsider.user.pk]}).status_code, 400)


class PartnersApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.annai, self.annai_admin = make_organization("Annai Precision", OTHER_GSTIN, "admin@annai.example")

    def connect(self, relation, **target):
        return self.api('post', '/partners', {'relation': relation, **target})

    def test_search_finds_organizations_by_name_unless_they_opt_out(self):
        results = self.api('get', '/partners/search?q=annai').json()
        self.assertEqual([r['name'] for r in results], ['Annai Precision'])
        self.assertNotIn('gstin', results[0])
        Company.objects.filter(pk=self.annai.pk).update(findable=False)
        self.assertEqual(self.api('get', '/partners/search?q=annai').json(), [])
        self.assertEqual(len(self.api('get', f'/partners/search?q={OTHER_GSTIN}').json()), 1)  # by exact GSTIN
        self.assertEqual(self.api('get', '/partners/search?q=vaigai').json(), [])  # never yourself
        self.assertEqual(self.api('get', '/partners/search?q=an').json(), [])  # too short

    def test_a_supplier_accepts_our_request(self):
        partner = self.connect('supplier', company_id=self.annai.pk).json()['partner']
        self.assertEqual((partner['status'], partner['waiting_on']), ('invited', 'them'))
        self.assertIn('admin@annai.example', mail.outbox[-1].to)
        self.assertEqual(self.api('post', f"/partners/{partner['id']}/accept").status_code, 400)  # not ours to accept
        self.login(self.annai_admin.user)
        theirs = self.api('get', '/partners').json()[0]
        self.assertEqual((theirs['relation'], theirs['waiting_on']), ('customer', 'us'))
        accepted = self.api('post', f"/partners/{partner['id']}/accept").json()
        self.assertEqual(accepted['status'], 'active')

    def test_selling_to_a_buyer_waits_for_their_vendor_approval(self):
        partner = self.connect('customer', company_id=self.annai.pk).json()['partner']
        self.assertEqual(partner['status'], 'pending_approval')
        self.login(self.annai_admin.user)
        self.assertEqual(self.api('post', f"/partners/{partner['id']}/decline").status_code, 204)
        self.assertFalse(BusinessRelationship.objects.exists())

    def test_a_company_not_on_makesetu_is_added_by_gstin_and_active_at_once(self):
        response = self.connect('supplier', gstin=THIRD_GSTIN, email='sales@kaveri.example').json()
        self.assertEqual((response['partner']['status'], response['partner']['partner']['on_network']), ('active', False))
        self.assertTrue(response['invite_email_sent'])
        self.assertIn('sales@kaveri.example', mail.outbox[-1].to)
        unclaimed = Company.objects.get(gstin=THIRD_GSTIN)
        self.assertFalse(unclaimed.memberships.exists())
        # Another organization adding the same GSTIN reuses the company.
        self.login(self.annai_admin.user)
        self.connect('customer', gstin=THIRD_GSTIN)
        self.assertEqual(Company.objects.filter(gstin=THIRD_GSTIN).count(), 1)

    def test_rules(self):
        self.connect('supplier', company_id=self.annai.pk)
        self.assertIn("already on your list", self.connect('supplier', gstin=OTHER_GSTIN).json()['detail'])
        self.assertIn("your own company", self.connect('customer', gstin=GSTIN).json()['detail'])
        Company.objects.filter(pk=self.company.pk).update(selling_enabled=False)
        self.assertIn("Switch on selling", self.connect('customer', company_id=self.annai.pk).json()['detail'])
        self.assertEqual(self.connect('vendor', company_id=self.annai.pk).status_code, 400)

    def test_blocking_and_terms(self):
        partner_id = self.connect('supplier', gstin=THIRD_GSTIN).json()['partner']['id']
        terms = self.api('patch', f'/partners/{partner_id}', {'vendor_code': 'V-0042', 'payment_terms_days': 45}).json()
        self.assertEqual((terms['vendor_code'], terms['payment_terms_days']), ('V-0042', 45))
        refused = self.api('patch', f'/partners/{partner_id}', {'credit_limit': '500000'})  # the supplier's term
        self.assertEqual(refused.status_code, 400)
        blocked = self.api('post', f'/partners/{partner_id}/block').json()
        self.assertEqual((blocked['status'], blocked['blocked_by_us']), ('blocked', True))
        self.assertEqual(self.api('post', f'/partners/{partner_id}/unblock').json()['status'], 'active')

    def test_only_the_blocking_side_unblocks(self):
        partner_id = self.connect('supplier', company_id=self.annai.pk).json()['partner']['id']
        self.login(self.annai_admin.user)
        self.api('post', f'/partners/{partner_id}/accept')
        self.api('post', f'/partners/{partner_id}/block')
        self.login(self.admin.user)
        self.assertEqual(self.api('post', f'/partners/{partner_id}/unblock').status_code, 400)

    def test_only_partner_managers_connect_but_everyone_sees_the_list(self):
        self.connect('supplier', company_id=self.annai.pk)
        self.login(add_member(self.company, 'buyer@vaigai.example', 'purchase_manager').user)
        self.assertEqual(len(self.api('get', '/partners').json()), 1)
        self.assertEqual(self.connect('supplier', gstin=THIRD_GSTIN).status_code, 403)


@TEST_STORAGES
class ClaimingAnUnclaimedCompanyTests(ApiTestCase):
    def test_registering_with_the_gstin_brings_the_relationships_along(self):
        self.api('post', '/partners', {'relation': 'supplier', 'gstin': THIRD_GSTIN})
        self.client.logout()
        newcomer = User.objects.create_user(username='k@kaveri.example', email='k@kaveri.example', password=PASSWORD,
                                            role='member', email_verified=True)
        session = self.client.session
        session['session_user_id'] = newcomer.pk
        session.save()
        verify_gstin_for_session(self.client, gstin=THIRD_GSTIN, legal_name="Kaveri Castings Private Limited")
        self.client.post(reverse('register-organization'), {'buying_enabled': 'on', 'selling_enabled': 'on', 'plan': 'starter'})
        company = Company.objects.get(gstin=THIRD_GSTIN)
        self.assertTrue(company.memberships.filter(user=newcomer).exists())
        self.login(newcomer)
        theirs = self.api('get', '/partners').json()
        self.assertEqual([(p['relation'], p['partner']['name'], p['status']) for p in theirs], [('customer', 'Vaigai', 'active')])

    def test_a_marketplace_company_still_cant_be_registered_again(self):
        company = Company.objects.create(legal_name="Buyer Co", gstin=THIRD_GSTIN, gst_verified=True)
        ConsumerProfile.objects.create(
            user=User.objects.create_user(username='c@x.example', email='c@x.example', password=PASSWORD),
            company=company, Name="Buyer Co", type_of_business="electronics", city="Pune", state="MH", country="India",
            phone="9400000001", email="buyer@x.example", EORI_number="E1", VAT_number="V1",
        )
        # It can still be added as an off-network partner.
        self.assertEqual(self.api('post', '/partners', {'relation': 'customer', 'gstin': THIRD_GSTIN}).json()['partner']['status'], 'active')
