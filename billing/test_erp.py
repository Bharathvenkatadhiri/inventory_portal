"""ERP organization subscriptions (billing.erp): trial, paying, upgrades,
downgrades, renewals, failures and lapsing, through the API and the mock
gateway's checkout."""
import json
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from organizations.tests import GSTIN, PASSWORD, TEST_STORAGES, add_member, make_organization
from plans import erp as plans

from . import erp
from .gateways import get_gateway
from .models import OrganizationPayment, OrganizationSubscription
from .services import process_event
from .views import _SignedRequest

Sub = OrganizationSubscription


@TEST_STORAGES
@override_settings(BILLING_GATEWAY='mock', BILLING_MOCK_RENEWAL_RESULT='succeed')
class ERPBillingTestCase(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.company, self.admin = make_organization("Vaigai", GSTIN, "admin@vaigai.example", plan=plans.STARTER)
        self.client.login(username=self.admin.user.email, password=PASSWORD)

    def sub(self):
        return Sub.objects.get(company=self.company)

    def api(self, method, path, body=None):
        kwargs = {'content_type': 'application/json'}
        if body is not None:
            kwargs['data'] = json.dumps(body)
        return getattr(self.client, method)(f'/api/v1{path}', **kwargs)

    def choose(self, plan):
        return self.api('post', '/billing/plan', {'plan': plan}).json()

    def pay(self, checkout_url, outcome='pay'):
        """The payer on the mock checkout page."""
        return self.client.post(checkout_url, {'outcome': outcome})

    def make_active(self, plan=plans.STARTER, now=None):
        """A paid period from now (as if paid after the trial)."""
        now = now or timezone.now()
        s = self.sub()
        s.status, s.plan, s.current_period_start, s.expires_at = Sub.ACTIVE, plan, now, now + erp.PERIOD
        s.plan_price = s.price = Decimal(plans.PLANS[plan]['monthly'])
        s.auto_renew, s.gateway_mandate_id = True, 'mock_mandate_test'
        s.save()
        self.company.erp_plan = plan
        self.company.save()
        return s


class TrialTests(ERPBillingTestCase):
    def test_a_new_organization_starts_a_trial_on_its_chosen_plan(self):
        s = self.sub()
        self.assertEqual((s.status, s.plan), (Sub.TRIALING, plans.STARTER))
        self.assertAlmostEqual((s.trial_ends_at - timezone.now()).days, 13, delta=1)
        self.assertTrue(erp.is_entitled(s))

    def test_switching_plans_during_the_trial_is_free(self):
        result = self.choose(plans.GROWTH)
        self.assertEqual((result['redirect_url'], result['message']), (None, "Your trial is now on Growth."))
        self.company.refresh_from_db()
        self.assertEqual((self.sub().plan, self.company.erp_plan), (plans.GROWTH, plans.GROWTH))
        self.assertFalse(OrganizationPayment.objects.exists())

    def test_paying_during_the_trial_keeps_the_trial_days(self):
        trial_end = self.sub().trial_ends_at
        result = self.choose(plans.STARTER)
        self.assertTrue(result['redirect_url'].startswith('/billing/mock/checkout/'))
        response = self.pay(result['redirect_url'])
        payment = OrganizationPayment.objects.get()
        self.assertRedirects(response, reverse('billing-erp-return', kwargs={'pk': payment.pk}), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(response['Location']), f'/app/settings/billing?payment={payment.pk}&status=paid',
                             fetch_redirect_response=False)
        s = self.sub()
        self.assertEqual((s.status, s.current_period_start, s.expires_at), (Sub.ACTIVE, trial_end, trial_end + erp.PERIOD))
        self.assertEqual((s.price, s.auto_renew), (Decimal('1500.00'), True))

    def test_the_trial_ends_unpaid_and_the_organization_goes_read_only(self):
        Sub.objects.filter(pk=self.sub().pk).update(trial_ends_at=timezone.now() - timedelta(minutes=1))
        counts = erp.run_due()
        self.assertEqual(counts['erp_lapsed'], 1)
        self.assertEqual(self.sub().status, Sub.LAPSED)
        self.assertIn("has ended", mail.outbox[-1].subject)
        self.assertEqual(self.api('patch', '/organization', {'name': 'x'}).status_code, 402)
        self.assertEqual(self.api('get', '/organization').status_code, 200)
        # Paying is how it gets going again: a period from now.
        before = timezone.now()
        self.pay(self.choose(plans.GROWTH)['redirect_url'])
        s = self.sub()
        self.assertEqual((s.status, s.plan), (Sub.ACTIVE, plans.GROWTH))
        self.assertGreaterEqual(s.current_period_start, before)
        self.assertEqual(self.api('patch', '/organization', {'name': 'Vaigai Works'}).status_code, 200)

    def test_a_reminder_goes_once_before_the_trial_ends(self):
        Sub.objects.filter(pk=self.sub().pk).update(trial_ends_at=timezone.now() + timedelta(days=2))
        self.assertEqual(erp.run_due()['erp_trial_reminders'], 1)
        self.assertEqual(erp.run_due()['erp_trial_reminders'], 0)
        self.assertIn("trial ends", mail.outbox[-1].subject)


class PaidSubscriptionTests(ERPBillingTestCase):
    def test_an_upgrade_charges_the_difference_for_the_time_left(self):
        now = timezone.now()
        self.make_active(plans.STARTER, now=now - timedelta(days=15))  # half the period used
        quote = erp.quote(self.sub(), plans.GROWTH, now)
        self.assertEqual(quote.action, 'upgrade')
        self.assertAlmostEqual(float(quote.amount), (4000 - 1500) / 2, delta=1)
        self.pay(self.choose(plans.GROWTH)['redirect_url'])
        s = self.sub()
        self.assertEqual((s.plan, s.plan_price), (plans.GROWTH, Decimal('4000')))
        self.company.refresh_from_db()
        self.assertEqual(self.company.erp_plan, plans.GROWTH)

    def test_a_downgrade_waits_for_the_end_of_the_period_and_renews_lower(self):
        self.make_active(plans.GROWTH)
        result = self.choose(plans.STARTER)
        self.assertIn("You'll move to Starter", result['message'])
        self.assertEqual((self.sub().plan, self.sub().scheduled_plan), (plans.GROWTH, plans.STARTER))
        Sub.objects.filter(pk=self.sub().pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(erp.run_due()['erp_renewed'], 1)
        s = self.sub()
        self.assertEqual((s.plan, s.price, s.scheduled_plan), (plans.STARTER, Decimal('1500.00'), ''))
        self.assertIn("Payment received", mail.outbox[-1].subject)  # automatic renewals are emailed

    def test_renewals_charge_extra_light_users(self):
        self.make_active(plans.STARTER)
        for i in range(7):  # Starter includes 5 light users
            add_member(self.company, f'op{i}@vaigai.example', 'machine_operator')
        Sub.objects.filter(pk=self.sub().pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        erp.run_due()
        payment = OrganizationPayment.objects.get(kind=OrganizationPayment.RENEWAL)
        self.assertEqual((payment.amount, payment.extra_light_users), (Decimal('1800.00'), 2))
        self.assertEqual(self.sub().extra_light_users, 2)

    @override_settings(BILLING_MOCK_RENEWAL_RESULT='fail', BILLING_RETRY_DAYS=[1], BILLING_GRACE_DAYS=3)
    def test_a_failed_renewal_keeps_access_through_grace_then_lapses(self):
        self.make_active(plans.STARTER)
        expired = timezone.now() - timedelta(minutes=1)
        Sub.objects.filter(pk=self.sub().pk).update(expires_at=expired)
        self.assertEqual(erp.run_due()['erp_renewal_failed'], 1)
        s = self.sub()
        self.assertEqual(s.status, Sub.PAST_DUE)
        self.assertTrue(erp.is_entitled(s))
        self.assertIn("couldn't renew", mail.outbox[-1].subject)
        erp.run_due(now=expired + timedelta(days=1, minutes=5))  # the retry fails too: no retries left
        self.assertEqual(self.sub().status, Sub.LAPSED)

    def test_cancel_and_reactivate(self):
        s = self.make_active(plans.STARTER)
        self.assertIn("won't renew", self.api('post', '/billing/cancel').json()['message'])
        self.assertFalse(self.sub().auto_renew)
        self.api('post', '/billing/reactivate')
        self.assertTrue(self.sub().auto_renew)
        self.api('post', '/billing/cancel')
        Sub.objects.filter(pk=s.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(erp.run_due()['erp_lapsed'], 1)


class PaymentSafetyTests(ERPBillingTestCase):
    def test_the_webhook_applies_an_erp_payment_once(self):
        url = self.choose(plans.STARTER)['redirect_url']
        payment = OrganizationPayment.objects.get()
        self.pay(url, outcome='pay_delay_webhook')  # paid, webhook not delivered yet
        gateway = get_gateway()
        body, signature = gateway.webhook_body(payment.gateway_order_id, event_id='evt-1')
        event = gateway.parse_webhook(_SignedRequest(body, signature))
        self.assertEqual(process_event('mock', event), 'applied')
        self.assertEqual(process_event('mock', event), 'duplicate')
        self.assertEqual(self.sub().status, Sub.ACTIVE)

    def test_a_declined_payment_changes_nothing(self):
        self.pay(self.choose(plans.STARTER)['redirect_url'], outcome='fail')
        self.assertEqual(OrganizationPayment.objects.get().status, OrganizationPayment.FAILED)
        self.assertEqual(self.sub().status, Sub.TRIALING)

    def test_only_billing_managers_see_billing_or_its_checkout(self):
        url = self.choose(plans.STARTER)['redirect_url']
        clerk = add_member(self.company, 'clerk@vaigai.example', 'accounts')
        self.client.logout()
        self.client.login(username=clerk.user.email, password=PASSWORD)
        self.assertEqual(self.api('get', '/billing').status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_the_billing_page_lists_plans_with_what_each_choice_does(self):
        data = self.api('get', '/billing').json()
        self.assertEqual(data['subscription']['status'], 'trialing')
        actions = {p['key']: p['action'] for p in data['plans']}
        self.assertEqual(actions, {'starter': 'subscribe', 'growth': 'switch', 'pro': 'switch'})
