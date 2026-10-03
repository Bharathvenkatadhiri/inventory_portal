"""The subscription lifecycle (billing.services) on the mock gateway: new
subscriptions, payments arriving by redirect or webhook (duplicated, out of
order, or not at all), prorated upgrades, scheduled downgrades, renewals,
past due and grace, cancellation, retired plans and price changes. Times
are passed explicitly, so every period and proration is checked to the
second."""
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import SubscriptionPlan, TeamMember
from marketplace.test_teams import PASSWORD, TEST_STORAGES, add_member, make_buyer
from plans import access, catalog

from . import services
from .gateways import get_gateway
from .gateways.base import FAILED, SUCCEEDED
from .gateways.mock import sign
from .models import Payment, WebhookEvent

MONTH = timedelta(days=30)
STARTER, BUSINESS = Decimal(catalog.price('starter')), Decimal(catalog.price('business'))


class BillingTestCase(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.owner, self.profile = make_buyer("billown", "9500000001")
        self.t0 = timezone.now().replace(microsecond=0)

    def sub(self):
        return SubscriptionPlan.objects.get(user_profile=self.owner)

    def start(self, plan, cycle='monthly', now=None):
        """Owner chooses a plan; returns the pending payment."""
        url, message = services.change_plan(self.owner, plan, cycle, lambda p: '/', now or self.t0)
        self.assertIsNotNone(url, message)
        return Payment.objects.latest('pk')

    def settle(self, payment, status=SUCCEEDED, now=None):
        """The payer finishes on the gateway; the redirect back reconciles."""
        get_gateway().record(payment.gateway_order_id, status, "Card declined (mock)." if status == FAILED else '')
        outcome = services.reconcile(payment, now or self.t0)
        payment.refresh_from_db()
        return outcome

    def subscribe(self, plan='starter', cycle='monthly', now=None):
        self.settle(self.start(plan, cycle, now), now=now)
        return self.sub()

    def entitled(self, now):
        return services.entitled_plan(self.sub(), now)


class NewSubscriptionTests(BillingTestCase):
    def test_paying_starts_a_period_from_the_moment_of_payment(self):  # UC-01, UC-04
        payment = self.start('starter')
        self.assertEqual(self.entitled(self.t0), 'free')  # nothing granted before payment
        paid_at = self.t0 + timedelta(minutes=3)
        self.settle(payment, now=paid_at)
        sub = self.sub()
        self.assertEqual((sub.plan_type, sub.billing_cycle, sub.price, sub.status), ('starter', 'monthly', STARTER, 'active'))
        self.assertEqual((sub.started_at, sub.current_period_start, sub.expires_at), (paid_at, paid_at, paid_at + MONTH))
        self.assertTrue(sub.auto_renew)
        self.assertTrue(sub.gateway_mandate_id)
        self.assertEqual((payment.status, payment.applied, payment.period_end), ('succeeded', True, paid_at + MONTH))
        self.assertIn("Payment received", mail.outbox[-1].subject)

    def test_yearly_is_365_days(self):
        sub = self.subscribe('business', 'yearly')
        self.assertEqual((sub.price, sub.expires_at), (Decimal(catalog.price('business', 'yearly')), self.t0 + timedelta(days=365)))

    def test_failed_payment_changes_nothing(self):  # UC-05
        payment = self.start('starter')
        self.assertEqual(self.settle(payment, FAILED), 'failed')
        sub = self.sub()
        self.assertEqual((sub.plan_type, sub.expires_at), ('free', None))
        self.assertEqual(payment.failure_reason, "Card declined (mock).")

    def test_closing_checkout_leaves_payment_pending_then_closed_by_the_job(self):  # UC-26
        payment = self.start('starter')
        self.assertEqual(self.settle(payment, status='pending'), 'pending')
        counts = services.run_due(timezone.now() + timedelta(minutes=31))
        payment.refresh_from_db()
        self.assertEqual((counts['pending_closed'], payment.status, self.sub().plan_type), (1, 'canceled', 'free'))

    def test_paid_but_never_returned_is_picked_up_by_the_job(self):  # UC-27
        payment = self.start('starter')
        get_gateway().record(payment.gateway_order_id, SUCCEEDED)  # paid; browser closed; webhook lost
        services.run_due(timezone.now() + timedelta(minutes=31))
        self.assertEqual(self.sub().plan_type, 'starter')

    def test_two_checkouts_paid_apply_once_and_flag_the_other_for_refund(self):
        first, second = self.start('starter'), self.start('starter')
        self.settle(first)
        self.assertEqual(self.settle(second), 'stale: flagged for refund')
        self.assertTrue(second.needs_refund)
        self.assertEqual(self.sub().expires_at, self.t0 + MONTH)  # not extended twice

    def test_free_to_paid_starts_a_new_usage_window(self):  # UC-34
        services.subscription_for_owner(self.owner)  # on Free
        SubscriptionPlan.objects.filter(user_profile=self.owner).update(current_period_start=self.t0 - timedelta(days=12))
        self.subscribe(now=self.t0)
        self.assertEqual(access.usage_window_start(self.profile, self.t0 + timedelta(days=1)), self.t0)

    def test_unknown_plan_is_refused(self):
        with self.assertRaises(services.BillingError):
            services.change_plan(self.owner, 'platinum', 'monthly', lambda p: '/', self.t0)


@TEST_STORAGES
class WebhookTests(BillingTestCase):
    def deliver(self, payment, event_id=None, signature=None):
        body, good = get_gateway().webhook_body(payment.gateway_order_id, event_id)
        return self.client.post(reverse('billing-webhook', kwargs={'gateway': 'mock'}), body,
                                content_type='application/json', HTTP_X_MOCK_SIGNATURE=signature or good)

    def test_webhook_alone_applies_the_payment(self):  # UC-06
        payment = self.start('starter')
        get_gateway().record(payment.gateway_order_id, SUCCEEDED)
        response = self.deliver(payment)
        self.assertEqual((response.status_code, response.content), (200, b'applied'))
        self.assertEqual(self.sub().plan_type, 'starter')

    def test_duplicate_webhooks_and_the_redirect_apply_once(self):  # UC-28
        payment = self.start('starter')
        get_gateway().record(payment.gateway_order_id, SUCCEEDED)
        self.deliver(payment, event_id='evt_1')
        self.assertEqual(self.deliver(payment, event_id='evt_1').content, b'duplicate')
        self.assertEqual(self.deliver(payment, event_id='evt_2').content, b'already applied')
        self.assertEqual(services.reconcile(payment), 'already applied')
        self.assertEqual(self.sub().expires_at - self.sub().current_period_start, MONTH)
        self.assertEqual(WebhookEvent.objects.count(), 2)
        self.assertEqual(sum("Payment received" in m.subject for m in mail.outbox), 1)

    def test_failure_arriving_after_success_is_ignored(self):  # UC-29
        payment = self.start('starter')
        get_gateway().record(payment.gateway_order_id, SUCCEEDED)
        self.deliver(payment, event_id='evt_ok')
        get_gateway().record(payment.gateway_order_id, FAILED, "late")
        self.assertEqual(self.deliver(payment, event_id='evt_late_fail').content, b'ignored: already final')
        payment.refresh_from_db()
        self.assertEqual((payment.status, self.sub().plan_type), ('succeeded', 'starter'))

    def test_bad_signature_is_rejected(self):
        payment = self.start('starter')
        get_gateway().record(payment.gateway_order_id, SUCCEEDED)
        self.assertEqual(self.deliver(payment, signature=sign(b'something else')).status_code, 400)
        self.assertEqual(self.sub().plan_type, 'free')

    def test_only_the_configured_gateway_is_accepted(self):
        response = self.client.post(reverse('billing-webhook', kwargs={'gateway': 'razorpay'}), b'{}', content_type='application/json')
        self.assertEqual(response.status_code, 404)


class UpgradeTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.subscribe('starter')
        self.expires = self.t0 + MONTH

    def expected(self, now):
        fraction = Decimal((self.expires - now).total_seconds()) / Decimal(MONTH.total_seconds())
        q = lambda d: d.quantize(Decimal('0.01'), ROUND_HALF_UP)
        return q(BUSINESS * fraction) - q(STARTER * fraction)

    def test_prorated_to_the_second_at_several_points(self):  # UC-09, UC-10
        sub = self.sub()
        for now in (self.t0 + timedelta(days=1), self.t0 + timedelta(days=15),
                    self.expires - timedelta(days=1), self.expires - timedelta(hours=1, seconds=7)):
            with self.subTest(now=now):
                self.assertEqual(services.quote(sub, 'business', 'monthly', now).amount, self.expected(now))
        self.assertEqual(services.quote(sub, 'business', 'monthly', self.t0 + timedelta(days=15)).amount, (BUSINESS - STARTER) / 2)

    def test_upgrade_applies_now_and_keeps_the_period_and_usage_window(self):  # UC-08, UC-35
        now = self.t0 + timedelta(days=10)
        payment = self.start('business', now=now)
        self.assertEqual((payment.kind, payment.amount), ('upgrade', self.expected(now)))
        self.assertEqual(self.entitled(now), 'starter')  # until paid
        self.settle(payment, now=now)
        sub = self.sub()
        self.assertEqual((sub.plan_type, sub.price, sub.current_period_start, sub.expires_at), ('business', BUSINESS, self.t0, self.expires))
        self.assertEqual(access.usage_window_start(self.profile, now), self.t0)

    def test_upgrade_seconds_before_expiry_needs_no_payment(self):
        now = self.expires - timedelta(seconds=5)
        url, message = services.change_plan(self.owner, 'business', 'monthly', lambda p: '/', now)
        self.assertIsNone(url)
        self.assertEqual(self.sub().plan_type, 'business')
        self.assertLess(Payment.objects.latest('pk').amount, services.MIN_CHARGE)

    def test_upgrade_paid_after_the_subscription_changed_is_not_applied(self):
        now = self.t0 + timedelta(days=10)
        payment = self.start('business', now=now)
        services.run_due(self.expires)  # renewed before the payer finished
        self.assertEqual(self.settle(payment, now=self.expires + timedelta(minutes=1)), 'stale: flagged for refund')
        self.assertEqual(self.sub().plan_type, 'starter')

    def test_then_downgrade_is_scheduled(self):  # UC-13
        self.settle(self.start('business', now=self.t0 + timedelta(days=5)), now=self.t0 + timedelta(days=5))
        url, _ = services.change_plan(self.owner, 'starter', 'monthly', lambda p: '/', self.t0 + timedelta(days=6))
        self.assertIsNone(url)
        sub = self.sub()
        self.assertEqual((sub.plan_type, sub.scheduled_plan_type), ('business', 'starter'))


class NoExpiryPlanTests(BillingTestCase):
    """A paid plan staff set without an expiry (or carried over from before
    expiries existed) has no period to prorate or schedule against."""
    def setUp(self):
        super().setUp()
        SubscriptionPlan.objects.create(user_profile=self.owner, plan_type='starter', price=STARTER)

    def test_choosing_a_paid_plan_starts_a_new_period(self):
        result = services.quote(self.sub(), 'business', 'monthly', self.t0)
        self.assertEqual((result.action, result.amount), ('new', BUSINESS))
        self.settle(self.start('business'))
        self.assertEqual((self.sub().plan_type, self.sub().expires_at), ('business', self.t0 + MONTH))

    def test_free_is_not_offered_as_a_scheduled_change(self):
        self.assertEqual(services.quote(self.sub(), 'free', 'monthly', self.t0).action, 'none')


class ScheduledChangeTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.subscribe('business')
        self.expires = self.t0 + MONTH

    def test_downgrade_applies_at_expiry_with_the_new_price(self):  # UC-11, UC-12
        services.change_plan(self.owner, 'starter', 'monthly', lambda p: '/', self.t0 + timedelta(days=3))
        self.assertEqual(self.entitled(self.expires - timedelta(seconds=1)), 'business')
        self.assertEqual(services.run_due(self.expires)['renewed'], 1)
        sub = self.sub()
        self.assertEqual((sub.plan_type, sub.price, sub.current_period_start, sub.expires_at, sub.scheduled_plan_type),
                         ('starter', STARTER, self.expires, self.expires + MONTH, ''))

    def test_cancelling_the_scheduled_downgrade_keeps_the_plan(self):  # UC-14
        services.change_plan(self.owner, 'starter', 'monthly', lambda p: '/', self.t0)
        services.cancel_scheduled_change(self.sub(), self.owner)
        services.run_due(self.expires)
        self.assertEqual(self.sub().plan_type, 'business')

    def test_switching_to_yearly_applies_at_renewal(self):
        services.change_plan(self.owner, 'business', 'yearly', lambda p: '/', self.t0)
        self.assertEqual(self.sub().billing_cycle, 'monthly')
        services.run_due(self.expires)
        sub = self.sub()
        self.assertEqual((sub.billing_cycle, sub.expires_at), ('yearly', self.expires + timedelta(days=365)))

    def test_moving_to_free_ends_the_plan_at_expiry(self):  # UC-15
        services.change_plan(self.owner, 'free', 'monthly', lambda p: '/', self.t0)
        self.assertFalse(self.sub().auto_renew)
        self.assertEqual(services.run_due(self.expires)['moved_to_free'], 1)
        sub = self.sub()
        self.assertEqual((sub.plan_type, sub.price, sub.expires_at, sub.current_period_start), ('free', 0, None, self.expires))


class RenewalTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.subscribe('starter')
        self.expires = self.t0 + MONTH

    def test_renewal_starts_the_next_period_and_usage_window(self):  # UC-16, UC-36
        self.assertEqual(services.run_due(self.expires - timedelta(seconds=1))['renewed'], 0)
        self.assertEqual(services.run_due(self.expires + timedelta(minutes=20))['renewed'], 1)
        sub = self.sub()
        self.assertEqual((sub.current_period_start, sub.expires_at), (self.expires, self.expires + MONTH))
        self.assertEqual(access.usage_window_start(self.profile, self.expires + timedelta(hours=1)), self.expires)
        self.assertEqual(services.run_due(self.expires + timedelta(hours=1))['renewed'], 0)  # once only

    @override_settings(BILLING_MOCK_RENEWAL_RESULT='fail')
    def test_failed_renewal_is_past_due_with_access_through_grace(self):  # UC-17
        self.assertEqual(services.run_due(self.expires)['renewal_failed'], 1)
        sub = self.sub()
        self.assertEqual((sub.status, sub.grace_until, sub.next_retry_at), ('past_due', self.expires + timedelta(days=3), self.expires + timedelta(days=1)))
        self.assertEqual(self.entitled(self.expires + timedelta(days=2)), 'starter')
        self.assertIn("renewal payment failed", mail.outbox[-1].subject)
        with self.assertRaises(services.BillingError):  # pay first, then change
            services.quote(sub, 'business', 'monthly', self.expires + timedelta(hours=1))

    def test_retry_that_succeeds_restores_the_subscription(self):  # UC-18
        with override_settings(BILLING_MOCK_RENEWAL_RESULT='fail'):
            services.run_due(self.expires)
        self.assertEqual(services.run_due(self.expires + timedelta(days=1))['retried'], 1)
        sub = self.sub()
        self.assertEqual((sub.status, sub.renewal_attempts, sub.current_period_start, sub.expires_at),
                         ('active', 0, self.expires, self.expires + MONTH))

    @override_settings(BILLING_MOCK_RENEWAL_RESULT='fail')
    def test_every_retry_failing_moves_to_free(self):  # UC-19
        services.run_due(self.expires)
        services.run_due(self.expires + timedelta(days=1))
        sub = self.sub()
        self.assertEqual((sub.status, sub.renewal_attempts, sub.next_retry_at), ('past_due', 2, self.expires + timedelta(days=3)))
        counts = services.run_due(self.expires + timedelta(days=3))
        self.assertEqual(counts['moved_to_free'], 1)
        self.assertEqual((self.sub().plan_type, self.sub().status), ('free', 'active'))
        self.assertIn("plan has ended", mail.outbox[-1].subject)

    @override_settings(BILLING_MOCK_RENEWAL_RESULT='fail')
    def test_owner_paying_the_overdue_renewal(self):
        services.run_due(self.expires)
        now = self.expires + timedelta(hours=5)
        payment = self.start('starter', now=now)
        self.assertEqual(payment.kind, 'renewal')
        self.settle(payment, FAILED, now=now)  # their own failed attempt doesn't restart the retries
        self.assertEqual((self.sub().renewal_attempts, self.sub().next_retry_at), (1, self.expires + timedelta(days=1)))
        self.settle(self.start('starter', now=now), now=now)
        sub = self.sub()
        self.assertEqual((sub.status, sub.current_period_start, sub.expires_at), ('active', self.expires, self.expires + MONTH))

    def test_renewal_charges_the_current_catalogue_price(self):  # UC-30
        services.run_due(self.expires - timedelta(days=1))
        with mock.patch('plans.catalog.price', side_effect=lambda plan, cycle='monthly': 1299 if plan == 'starter' else 0):
            services.run_due(self.expires)
        self.assertEqual(self.sub().price, Decimal('1299'))
        self.assertEqual(Payment.objects.filter(kind='renewal').get().amount, Decimal('1299'))

    def test_retired_plan_renews_into_its_successor(self):  # UC-31, UC-32
        with mock.patch.dict(catalog.RETIRED_PLANS, {'starter': 'business'}):
            self.assertEqual(self.entitled(self.t0 + timedelta(days=1)), 'starter')  # kept until renewal
            with self.assertRaises(services.BillingError):
                services.quote(SubscriptionPlan(plan_type='free'), 'starter', 'monthly')
            services.run_due(self.expires)
        self.assertEqual((self.sub().plan_type, self.sub().price), ('business', BUSINESS))


class ExpiryReminderTests(BillingTestCase):
    """The Owner is emailed 2 days before expiry and on the expiry date,
    once each per period."""
    def setUp(self):
        super().setUp()
        self.t0 = timezone.make_aware(datetime(2030, 1, 1, 12, 0))  # local noon, so "the expiry date" is unambiguous
        self.subscribe('starter')
        self.expires = self.t0 + MONTH
        mail.outbox.clear()

    def reminders(self, now):
        services.run_due(now)
        return [m for m in mail.outbox if "plan renews" in m.subject or "plan ends" in m.subject]

    def test_two_days_before_and_on_the_day_once_each(self):
        self.assertEqual(self.reminders(self.expires - timedelta(days=2, hours=1)), [])  # not yet
        sent = self.reminders(self.expires - timedelta(days=2) + timedelta(minutes=5))
        self.assertEqual([m.subject for m in sent], [f"Your MakeSetu Starter plan renews on {timezone.localtime(self.expires):%d %b}"])
        self.assertIn(f"Rs {STARTER}", sent[0].body)
        self.assertEqual(len(self.reminders(self.expires - timedelta(days=1))), 1)  # not repeated
        sent = self.reminders(self.expires - timedelta(hours=2))
        self.assertEqual([m.subject for m in sent][-1], "Your MakeSetu Starter plan renews today")
        self.assertEqual(len(self.reminders(self.expires - timedelta(hours=1))), 2)

    def test_next_period_gets_its_own_reminders(self):
        self.reminders(self.expires - timedelta(days=1))
        services.run_due(self.expires)  # renewed
        mail.outbox.clear()
        self.assertEqual(len(self.reminders(self.expires + MONTH - timedelta(days=1))), 1)

    def test_cancelled_plan_says_it_ends_and_moves_to_free(self):
        services.cancel(self.sub(), self.owner)
        sent = self.reminders(self.expires - timedelta(days=1))
        self.assertIn("plan ends on", sent[0].subject)
        self.assertIn("moves to the Free plan", sent[0].body)

    def test_scheduled_downgrade_is_named_with_its_price(self):
        self.settle(self.start('business', now=self.t0 + timedelta(days=1)), now=self.t0 + timedelta(days=1))
        services.change_plan(self.owner, 'starter', 'yearly', lambda p: '/', self.t0 + timedelta(days=2))
        mail.outbox.clear()
        body = self.reminders(self.expires - timedelta(days=1))[0].body
        self.assertIn(f"Rs {catalog.price('starter', 'yearly')}", body)
        self.assertIn("Starter (yearly), the change you scheduled", body)

    def test_free_and_past_due_get_none(self):
        with override_settings(BILLING_MOCK_RENEWAL_RESULT='fail'):
            services.run_due(self.expires)  # past due now
        mail.outbox.clear()
        self.assertEqual(self.reminders(self.expires + timedelta(hours=1)), [])


class CancelTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.subscribe('starter')
        self.expires = self.t0 + MONTH

    def test_cancel_keeps_access_until_expiry_then_free(self):  # UC-20
        services.cancel(self.sub(), self.owner)
        self.assertEqual(self.entitled(self.expires - timedelta(seconds=1)), 'starter')
        self.assertEqual(self.entitled(self.expires), 'free')
        services.run_due(self.expires)
        self.assertEqual(self.sub().plan_type, 'free')
        self.assertFalse(Payment.objects.filter(kind='renewal').exists())

    def test_reactivate_before_expiry_renews_as_before(self):  # UC-21
        services.cancel(self.sub(), self.owner)
        url, _ = services.change_plan(self.owner, 'starter', 'monthly', lambda p: '/', self.t0 + timedelta(days=4))
        self.assertIsNone(url)
        self.assertTrue(self.sub().auto_renew)
        services.run_due(self.expires)
        self.assertEqual(self.sub().expires_at, self.expires + MONTH)

    def test_buying_after_expiry_is_a_new_subscription(self):  # UC-22
        services.cancel(self.sub(), self.owner)
        services.run_due(self.expires)
        later = self.expires + timedelta(days=10)
        payment = self.start('starter', now=later)
        self.assertEqual((payment.kind, payment.amount), ('new', STARTER))
        self.settle(payment, now=later)
        self.assertEqual((self.sub().started_at, self.sub().expires_at), (later, later + MONTH))


@TEST_STORAGES
class BillingViewTests(BillingTestCase):
    def login(self, user):
        self.client.login(username=user.email, password=PASSWORD)

    def test_owner_pays_through_the_mock_checkout(self):
        self.login(self.owner)
        response = self.client.post(reverse('billing-change'), {'plan_type': 'starter', 'billing_cycle': 'monthly'})
        payment = Payment.objects.get()
        self.assertRedirects(response, reverse('billing-mock-checkout', kwargs={'order_id': payment.gateway_order_id}), fetch_redirect_response=False)
        self.assertContains(self.client.get(response['Location']), "Test checkout")
        response = self.client.post(response['Location'], {'outcome': 'pay_delay_webhook'})
        self.assertRedirects(response, reverse('billing-return', kwargs={'pk': payment.pk}), fetch_redirect_response=False)
        page = self.client.get(response['Location'], follow=True)
        self.assertContains(page, "Payment of ₹")
        self.assertEqual(self.sub().plan_type, 'starter')
        self.assertContains(page, "Renews automatically")

    def test_only_the_owner_changes_the_plan(self):
        admin = add_member(self.profile, "billadm", TeamMember.ADMIN)
        self.login(admin)
        self.client.post(reverse('billing-change'), {'plan_type': 'starter', 'billing_cycle': 'monthly'})
        self.assertFalse(Payment.objects.exists())
        self.client.post(reverse('billing-cancel'))
        self.assertContains(self.client.get(reverse('profile') + '?tab=billing'), "Only your company's owner can change the plan")

    def test_another_company_cannot_open_or_pay_this_checkout(self):
        payment = self.start('starter')
        other, _ = make_buyer("billother", "9500000002")
        self.login(other)
        url = reverse('billing-mock-checkout', kwargs={'order_id': payment.gateway_order_id})
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {'outcome': 'pay'}).status_code, 404)
        self.assertEqual(self.client.get(reverse('billing-return', kwargs={'pk': payment.pk})).status_code, 404)
        self.assertEqual(self.sub().plan_type, 'free')

    def test_billing_tab_and_dashboard_show_where_the_subscription_stands(self):
        self.login(self.owner)
        services.subscription_for_owner(self.owner)
        SubscriptionPlan.objects.filter(user_profile=self.owner).update(intended_plan_type='business', intended_billing_cycle='yearly')
        self.assertContains(self.client.get(reverse('home')), "Finish subscribing to Business")
        self.subscribe('business')
        services.change_plan(self.owner, 'starter', 'monthly', lambda p: '/')
        page = self.client.get(reverse('profile') + '?tab=billing')
        self.assertContains(page, "Changes to Starter (monthly)")
        self.assertContains(page, "Cancel scheduled change")
        self.assertNotContains(page, "Finish subscribing")  # paid now
        SubscriptionPlan.objects.filter(user_profile=self.owner).update(
            status='past_due', expires_at=timezone.now() - timedelta(hours=1),
            grace_until=timezone.now() + timedelta(days=2), next_retry_at=timezone.now() + timedelta(hours=23))
        page = self.client.get(reverse('profile') + '?tab=billing')
        self.assertContains(page, "Your renewal payment failed")
        self.assertContains(page, "Pay overdue renewal")
        self.assertContains(self.client.get(reverse('home')), "Your renewal payment failed")

    def test_stay_on_free_cannot_redirect_off_site(self):
        self.login(self.owner)
        response = self.client.post(reverse('billing-dismiss-intended'), {'next': '//evil.example.com/'})
        self.assertEqual(response['Location'], reverse('profile') + '?tab=billing')


@TEST_STORAGES
class SignupPlanTests(TestCase):
    """UC-02/03: a plan picked on the pricing page is remembered through
    sign-up and offered for payment; the company starts on Free."""

    def setUp(self):
        cache.clear()
        mail.outbox.clear()

    def register(self, query):
        self.client.get(reverse('register') + query)
        self.client.post(reverse('register') + query, {
            "username": "planpick", "first_name": "Plan", "last_name": "Picker",
            "password1": "A-strong-passw0rd", "password2": "A-strong-passw0rd",
            "email": "planpick@example.com", "account_type": "buyer",
        })
        code = next(l.strip() for l in mail.outbox[-1].body.splitlines() if l.strip().isdigit() and len(l.strip()) == 6)
        self.client.post(reverse('verify-email'), {"code": code})
        return SubscriptionPlan.objects.get(user_profile__email="planpick@example.com")

    def test_chosen_plan_is_recorded_as_intended_not_granted(self):
        sub = self.register('?plan=business&cycle=yearly')
        self.assertEqual((sub.plan_type, sub.intended_plan_type, sub.intended_billing_cycle), ('free', 'business', 'yearly'))

    def test_free_or_unknown_plan_records_nothing(self):
        sub = self.register('?plan=platinum')
        self.assertEqual(sub.intended_plan_type, '')
